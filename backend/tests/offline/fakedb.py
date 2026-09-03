"""
An in-memory stand-in for Supabase, so the real FastAPI app can be exercised
offline.

The point is not to mock the database away. It is to keep the parts that catch
bugs: this reads backend/db/schema.sql and every migration, extracts the
FOREIGN KEY declarations, and enforces them -- raising the same postgrest-shaped
error, with the same SQLSTATE, that production raises. The failure that started
all of this,

    insert or update on table "boards" violates foreign key constraint
    "boards_confirmed_by_fkey" ... code 23503

is reproducible here, in a test, in milliseconds.

It also models the piece of infrastructure that turned out to matter most: the
`handle_new_user` trigger. Construct with trigger_enabled=False and creating an
auth user leaves no profiles row, exactly as a database missing
triggers_and_security.sql behaves.

WHAT IT DOES NOT DO: row-level security. Policies are enforced by Postgres, not
by this, so any endpoint whose safety rests on RLS alone will look safe here and
is NOT covered. That is a real gap and the live suites remain the only check on
it -- see the note in test_integration.py.
"""
import os
import re
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
SCHEMA_DIR = os.path.join(BACKEND, "db")

# column-level:  <col> <TYPE> ... REFERENCES public.<table>(<col>)
_COL_FK = re.compile(
    r"^\s*(?:ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?)?([a-z_]+)\s+[A-Z]+"
    r"[^,;]*?REFERENCES\s+(?:public\.)?([a-z_.]+)\s*\(\s*([a-z_]+)\s*\)",
    re.IGNORECASE,
)
_CREATE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?([a-z_]+)",
                     re.IGNORECASE)
_ALTER = re.compile(r"ALTER TABLE (?:IF EXISTS )?(?:public\.)?([a-z_]+)",
                    re.IGNORECASE)


def parse_foreign_keys(paths):
    """{(table, column): (ref_table, ref_column)} read from the real SQL."""
    fks = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        current = None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("--"):
                    continue
                m = _CREATE.search(line)
                if m:
                    current = m.group(1)
                    continue
                m = _ALTER.search(line)
                if m:
                    current = m.group(1)
                m = _COL_FK.match(line)
                if m and current:
                    col, ref_table, ref_col = m.group(1), m.group(2), m.group(3)
                    fks[(current, col)] = (ref_table.split(".")[-1], ref_col)
    return fks


def schema_files():
    files = [os.path.join(SCHEMA_DIR, "schema.sql")]
    mig = os.path.join(SCHEMA_DIR, "migrations")
    if os.path.isdir(mig):
        for name in sorted(os.listdir(mig)):
            # APPLY_PENDING is a convenience concatenation of the others.
            if name.endswith(".sql") and not name.startswith("APPLY"):
                files.append(os.path.join(mig, name))
    return files


class PostgrestError(Exception):
    """
    Shaped like the error the supabase client raises, because the app's error
    handling stringifies it straight into an HTTP response body.
    """

    def __init__(self, message, code, details=None, hint=None):
        self.message = message
        self.code = code
        self.details = details
        self.hint = hint
        super().__init__(str({"message": message, "code": code,
                              "hint": hint, "details": details}))


class Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def _now():
    return datetime.now(timezone.utc).isoformat()


class Query:
    """The fluent builder surface the application actually uses."""

    def __init__(self, db, table, op, payload=None, columns="*", count=None):
        self.db = db
        self.table = table
        self.op = op
        self.payload = payload
        self.columns = columns
        self.count_mode = count
        self.filters = []
        self._order = None
        self._desc = False
        self._limit = None
        self._range = None

    # -- filters ----------------------------------------------------------
    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def or_(self, expression):
        self.filters.append(("or", expression, None))
        return self

    def order(self, col, desc=False, **kw):
        self._order = col
        self._desc = desc or kw.get("descending", False)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def single(self):
        self._limit = 1
        return self

    maybe_single = single

    # -- evaluation -------------------------------------------------------
    def _matches(self, row):
        for kind, col, val in self.filters:
            if kind == "eq":
                if str(row.get(col)) != str(val):
                    return False
            elif kind == "neq":
                if str(row.get(col)) == str(val):
                    return False
            elif kind == "in":
                if row.get(col) not in val and str(row.get(col)) not in [str(v) for v in val]:
                    return False
            elif kind == "is":
                want = None if val in (None, "null") else val
                if row.get(col) != want:
                    return False
            elif kind == "or":
                if not self._or_matches(row, col):
                    return False
        return True

    @staticmethod
    def _or_matches(row, expression):
        # "profile_id.is.null,profile_id.eq.<uuid>"
        for clause in expression.split(","):
            parts = clause.split(".", 2)
            if len(parts) != 3:
                continue
            col, op, val = parts
            if op == "is" and val == "null" and row.get(col) is None:
                return True
            if op == "eq" and str(row.get(col)) == val:
                return True
            if op == "not" and row.get(col) is not None:
                return True
        return False

    def _embed(self, row):
        """Minimal support for `*, alias:table(cols)` PostgREST embeds."""
        if "(" not in (self.columns or ""):
            return row
        out = dict(row)
        for alias, target in re.findall(r"([a-z_]+):([a-z_]+)\(", self.columns):
            fk = row.get(alias + "_id") or row.get(target.rstrip("s") + "_id")
            related = None
            if fk is not None:
                for candidate in self.db.tables.get(target, []):
                    if str(candidate.get("id")) == str(fk):
                        related = dict(candidate)
                        break
            out[alias] = related
        return out

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])

        if self.op == "select":
            found = [r for r in rows if self._matches(r)]
            total = len(found)
            if self._order:
                found = sorted(
                    found,
                    key=lambda r: (r.get(self._order) is None,
                                   str(r.get(self._order))),
                    reverse=self._desc)
            if self._range:
                start, end = self._range
                found = found[start:end + 1]
            elif self._limit is not None:
                found = found[:self._limit]
            return Result([self._embed(dict(r)) for r in found],
                          total if self.count_mode else None)

        if self.op in ("insert", "upsert"):
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            written = []
            for item in items:
                row = dict(item)
                row.setdefault("id", str(uuid.uuid4()))
                row.setdefault("created_at", _now())
                existing = None
                for r in rows:
                    if str(r.get("id")) == str(row["id"]):
                        existing = r
                        break
                if existing is not None:
                    if self.op == "insert":
                        raise PostgrestError(
                            'duplicate key value violates unique constraint '
                            '"%s_pkey"' % self.table, "23505",
                            details="Key (id)=(%s) already exists." % row["id"])
                    existing.update(row)
                    written.append(existing)
                    continue
                self.db.enforce_foreign_keys(self.table, row)
                rows.append(row)
                written.append(row)
            return Result([dict(r) for r in written])

        if self.op == "update":
            touched = []
            for row in rows:
                if self._matches(row):
                    merged = dict(row)
                    merged.update(self.payload or {})
                    self.db.enforce_foreign_keys(self.table, merged,
                                                 changed=self.payload or {})
                    row.update(self.payload or {})
                    touched.append(row)
            return Result([dict(r) for r in touched])

        if self.op == "delete":
            keep, removed = [], []
            for row in rows:
                (removed if self._matches(row) else keep).append(row)
            self.db.tables[self.table] = keep
            for row in removed:
                self.db.cascade_delete(self.table, row)
            return Result([dict(r) for r in removed])

        raise AssertionError("unsupported operation %r" % self.op)


class Table:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def select(self, columns="*", count=None, **kw):
        return Query(self.db, self.name, "select", columns=columns, count=count)

    def insert(self, payload, **kw):
        return Query(self.db, self.name, "insert", payload=payload)

    def upsert(self, payload, **kw):
        return Query(self.db, self.name, "upsert", payload=payload)

    def update(self, payload, **kw):
        return Query(self.db, self.name, "update", payload=payload)

    def delete(self, **kw):
        return Query(self.db, self.name, "delete")


class _AdminAuth:
    def __init__(self, db):
        self.db = db

    def create_user(self, attrs):
        email = attrs.get("email")
        for u in self.db.auth_users:
            if u["email"] == email:
                raise PostgrestError(
                    "A user with this email address has already been registered",
                    "email_exists")
        user = {
            "id": str(uuid.uuid4()),
            "email": email,
            "user_metadata": dict(attrs.get("user_metadata") or {}),
            "app_metadata": dict(attrs.get("app_metadata") or {}),
        }
        self.db.auth_users.append(user)
        # The handle_new_user trigger in db/triggers_and_security.sql. Turn it
        # off to reproduce a database where that file was never applied.
        if self.db.trigger_enabled:
            meta = user["user_metadata"]
            self.db.tables.setdefault("profiles", []).append({
                "id": user["id"],
                "name": meta.get("name") or "User",
                "email": email,
                "role": (user["app_metadata"].get("role")
                         or meta.get("role") or "player"),
                "rating": meta.get("rating") or 1500,
                "club": meta.get("club"),
                "city": meta.get("city"),
                "phone": meta.get("phone"),
                "created_at": _now(),
            })
        return _Obj(user=_Obj(**user))

    def update_user_by_id(self, user_id, attributes=None, **kw):
        attributes = attributes or kw
        for u in self.db.auth_users:
            if u["id"] == user_id:
                for key in ("user_metadata", "app_metadata"):
                    if key in attributes:
                        u[key].update(attributes[key] or {})
                if "email" in attributes:
                    u["email"] = attributes["email"]
                if "password" in attributes:
                    u["password"] = attributes["password"]
                return _Obj(user=_Obj(**u))
        raise PostgrestError("User not found", "user_not_found")

    def delete_user(self, user_id):
        before = len(self.db.auth_users)
        self.db.auth_users = [u for u in self.db.auth_users if u["id"] != user_id]
        if len(self.db.auth_users) == before:
            raise PostgrestError("User not found", "user_not_found")
        # profiles.id REFERENCES auth.users(id) ON DELETE CASCADE
        self.db.tables["profiles"] = [
            p for p in self.db.tables.get("profiles", [])
            if str(p.get("id")) != str(user_id)]
        return _Obj(user=None)

    def list_users(self, page=1, per_page=50, **kw):
        start = (page - 1) * per_page
        return [_Obj(**u) for u in self.db.auth_users[start:start + per_page]]

    def generate_link(self, params):
        return _Obj(properties=_Obj(action_link="https://example.test/link"))


class _Auth:
    def __init__(self, db):
        self.db = db
        self.admin = _AdminAuth(db)

    def _find(self, user_id):
        for u in self.db.auth_users:
            if u["id"] == user_id:
                return u
        return None

    def get_user(self, token=None):
        token = token or self.db.bound_token
        user_id = (token or "").replace("tok:", "")
        user = self._find(user_id)
        if not user:
            raise PostgrestError("invalid claim: missing sub claim", "invalid_token")
        return _Obj(user=_Obj(**user))

    def sign_in_with_password(self, credentials):
        email = credentials.get("email")
        for u in self.db.auth_users:
            if u["email"] == email:
                return _Obj(session=_Obj(
                    access_token="tok:" + u["id"],
                    refresh_token="ref:" + u["id"],
                    expires_at=4102444800,
                ), user=_Obj(**u))
        raise PostgrestError("Invalid login credentials", "invalid_credentials")

    def refresh_session(self, refresh_token=None, **kw):
        user_id = (refresh_token or "").replace("ref:", "")
        if not self._find(user_id):
            raise PostgrestError("Invalid Refresh Token", "invalid_token")
        return _Obj(session=_Obj(access_token="tok:" + user_id,
                                 refresh_token="ref:" + user_id,
                                 expires_at=4102444800))

    def reset_password_for_email(self, email, options=None):
        self.db.password_resets.append({"email": email, "options": options})
        return _Obj(ok=True)


class _Obj:
    """Attribute access over a dict, like the client's response models."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def __repr__(self):
        return "_Obj(%r)" % self.__dict__


class _Postgrest:
    def __init__(self, db):
        self.db = db

    def auth(self, token):
        self.db.bound_token = token


class FakeSupabase:
    def __init__(self, trigger_enabled=True):
        self.tables = {}
        self.auth_users = []
        self.password_resets = []
        self.trigger_enabled = trigger_enabled
        self.bound_token = None
        self.foreign_keys = parse_foreign_keys(schema_files())
        self.auth = _Auth(self)
        self.postgrest = _Postgrest(self)
        self.rpc_calls = []

    # -- constraints ------------------------------------------------------
    def enforce_foreign_keys(self, table, row, changed=None):
        for (t, col), (ref_table, ref_col) in self.foreign_keys.items():
            if t != table or col not in row:
                continue
            if changed is not None and col not in changed:
                continue
            value = row.get(col)
            if value is None:
                continue
            if ref_table == "users":            # auth.users
                pool = [{"id": u["id"]} for u in self.auth_users]
            else:
                pool = self.tables.get(ref_table, [])
            if not any(str(r.get(ref_col)) == str(value) for r in pool):
                raise PostgrestError(
                    'insert or update on table "%s" violates foreign key '
                    'constraint "%s_%s_fkey"' % (table, table, col),
                    "23503",
                    details='Key (%s)=(%s) is not present in table "%s".'
                            % (col, value, ref_table))

    def cascade_delete(self, table, row):
        for (t, col), (ref_table, ref_col) in self.foreign_keys.items():
            if ref_table != table:
                continue
            children = self.tables.get(t, [])
            self.tables[t] = [c for c in children
                              if str(c.get(col)) != str(row.get(ref_col))]

    # -- client surface ---------------------------------------------------
    def table(self, name):
        return Table(self, name)

    def rpc(self, name, params=None):
        self.rpc_calls.append((name, params))
        return _Rpc(self, name, params)

    # -- helpers for tests -------------------------------------------------
    def seed(self, table, rows):
        self.tables.setdefault(table, []).extend(dict(r) for r in rows)

    def rows(self, table):
        return [dict(r) for r in self.tables.get(table, [])]

    def orphan_profile(self, user_id):
        """Delete the profiles row while the auth user lives on."""
        self.tables["profiles"] = [
            p for p in self.tables.get("profiles", [])
            if str(p.get("id")) != str(user_id)]


class _Rpc:
    def __init__(self, db, name, params):
        self.db = db
        self.name = name
        self.params = params or {}

    def execute(self):
        if self.name == "apply_board_result":
            return self._apply_board_result()
        # Unknown RPCs behave like a database without that migration applied.
        raise PostgrestError(
            'Could not find the function public.%s' % self.name, "PGRST202")

    def _apply_board_result(self):
        """
        The migration-002 function, faithfully enough to matter.

        It really writes -- board row, match aggregates, audit row, next board
        -- and it validates every foreign key BEFORE applying anything, because
        the real one runs inside a transaction and a violation rolls the whole
        thing back rather than leaving a half-scored board.
        """
        p = self.params
        match_id = p.get("p_match_id")
        board_number = p.get("p_board_number")
        set_number = p.get("p_set_number") or 1
        board_patch = p.get("p_board_patch") or {}
        match_patch = p.get("p_match_patch") or {}
        audit = p.get("p_audit") or {}

        boards = self.db.tables.get("boards", [])
        target = None
        for b in boards:
            if (str(b.get("match_id")) == str(match_id)
                    and b.get("board_number") == board_number
                    and (b.get("set_number") or 1) == set_number):
                target = b
                break
        if target is None:
            raise PostgrestError("board_not_found", "P0001")

        match = None
        for m in self.db.tables.get("matches", []):
            if str(m.get("id")) == str(match_id):
                match = m
                break

        audit_row = {
            "id": str(uuid.uuid4()),
            "match_id": match_id,
            "admin_id": audit.get("admin_id"),
            "admin_name": audit.get("admin_name", "System"),
            "board_number": board_number,
            "previous_score": {"player1": target.get("player1_score", 0),
                               "player2": target.get("player2_score", 0)},
            "new_score": audit.get("new_score", {}),
            "reason": audit.get("reason", "Score update"),
            "timestamp": _now(),
        }

        # -- validate everything first (the transaction boundary) ------------
        merged_board = dict(target)
        merged_board.update(board_patch)
        self.db.enforce_foreign_keys("boards", merged_board, changed=board_patch)
        if match is not None and match_patch:
            merged_match = dict(match)
            merged_match.update(match_patch)
            self.db.enforce_foreign_keys("matches", merged_match,
                                         changed=match_patch)
        self.db.enforce_foreign_keys("score_audit_logs", audit_row)

        # -- then apply --------------------------------------------------
        target.update(board_patch)
        if match is not None:
            match.update(match_patch)
        self.db.tables.setdefault("score_audit_logs", []).append(audit_row)

        next_board = p.get("p_next_board_number")
        if next_board is not None and match_patch.get("status") != "completed":
            for b in boards:
                if (str(b.get("match_id")) == str(match_id)
                        and b.get("board_number") == next_board
                        and (b.get("set_number") or 1) == set_number
                        and b.get("status") == "pending"):
                    b["status"] = "in_progress"

        return Result(dict(target))

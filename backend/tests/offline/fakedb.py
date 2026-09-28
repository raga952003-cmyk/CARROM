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
from copy import deepcopy
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
_ON_DELETE = re.compile(r"ON\s+DELETE\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION)",
                        re.IGNORECASE)
_CREATE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?([a-z_]+)",
                     re.IGNORECASE)
_ALTER = re.compile(r"ALTER TABLE (?:IF EXISTS )?(?:public\.)?([a-z_]+)",
                    re.IGNORECASE)


def parse_foreign_keys(paths):
    """
    {(table, column): (ref_table, ref_column, on_delete)} read from the real SQL.

    `on_delete` is "cascade", "set null" or "" (no action), taken from the
    constraint itself. It used to be dropped, and every foreign key was treated
    as ON DELETE CASCADE -- which is wrong for exactly the one that matters
    most: matches.next_match_id is ON DELETE SET NULL, so deleting a
    knockout match in Postgres orphans the matches that feed it, while the fake
    deleted them outright. A guard against orphaning a bracket could not be
    tested against a database that removed the evidence.
    """
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
                    action = ""
                    hit = _ON_DELETE.search(line)
                    if hit:
                        action = hit.group(1).lower().replace("  ", " ")
                    fks[(current, col)] = (ref_table.split(".")[-1], ref_col, action)
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
                if self.table == "payment_proofs":
                    row.setdefault("status", "pending")
                    row.setdefault("submitted_at", _now())
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
            "password": attrs.get("password"),
            "email_confirmed": attrs.get("email_confirm", True),
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
                "role": "admin" if user["app_metadata"].get("role") == "admin" else "player",
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

    def sign_up(self, credentials):
        """Model Supabase signup with Confirm email enabled."""
        user = self.admin.create_user({
            "email": credentials["email"],
            "password": credentials["password"],
            "user_metadata": (credentials.get("options") or {}).get("data") or {},
            "app_metadata": {"role": "player"},
            "email_confirm": False,
        }).user
        return _Obj(user=user, session=None)

    def confirm_email(self, email):
        """Offline stand-in for following the confirmation link in an inbox."""
        for user in self.db.auth_users:
            if user["email"] == email:
                user["email_confirmed"] = True
                return
        raise PostgrestError("User not found", "user_not_found")

    def sign_in_with_password(self, credentials):
        email = credentials.get("email")
        for u in self.db.auth_users:
            if u["email"] == email:
                if u.get("password") and u["password"] != credentials.get("password"):
                    raise PostgrestError("Invalid login credentials", "invalid_credentials")
                if not u.get("email_confirmed", True):
                    raise PostgrestError("Email not confirmed", "email_not_confirmed")
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
        self.registration_auto_approval_ready = True
        self.registration_draw_atomicity_ready = True

    # -- constraints ------------------------------------------------------
    def enforce_foreign_keys(self, table, row, changed=None):
        for (t, col), (ref_table, ref_col, _action) in self.foreign_keys.items():
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
        """
        Apply what each constraint actually says, not CASCADE for all of them.

        SET NULL is the difference between a bracket that loses its links and
        one that loses its matches, and the app has a guard that only makes
        sense against the first.
        """
        for (t, col), (ref_table, ref_col, action) in self.foreign_keys.items():
            if ref_table != table:
                continue
            children = self.tables.get(t, [])
            hits = [c for c in children if str(c.get(col)) == str(row.get(ref_col))]
            if not hits:
                continue
            if action == "set null":
                for c in hits:
                    c[col] = None
                continue
            if action in ("restrict", "no action", ""):
                # Postgres would refuse the delete. Nothing in this app relies
                # on that, so leave the rows alone rather than inventing an
                # error the real database would raise at a different moment.
                continue
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
        if self.name == "registration_auto_approval_ready":
            return Result(self.db.registration_auto_approval_ready)
        if self.name == "apply_board_result":
            return self._apply_board_result()
        if self.name == "apply_board_result_with_next_set":
            return self._apply_board_result_with_next_set()
        if self.name == "delete_match_safely":
            return self._delete_match_safely()
        if self.name == "replace_tournament_fixtures":
            return self._replace_tournament_fixtures()
        if self.name == "replace_tournament_fixtures_checked":
            return self._replace_tournament_fixtures_checked()
        if self.name == "update_match_schedule_batch":
            return self._update_match_schedule_batch()
        if self.name == "record_manual_entry_payment":
            return self._record_manual_entry_payment()
        if self.name == "payment_proof_similar_images":
            return self._payment_proof_similar_images()
        if self.name == "payment_proof_reconsideration_ready":
            return Result(True)
        if self.name == "registration_draw_atomicity_ready":
            return Result(self.db.registration_draw_atomicity_ready)
        if self.name == "review_payment_proof_v2":
            return self._review_payment_proof_v2()
        # Unknown RPCs behave like a database without that migration applied.
        raise PostgrestError(
            'Could not find the function public.%s' % self.name, "PGRST202")

    def _payment_proof_similar_images(self):
        fingerprint = self.params.get("p_hash", "")
        distance = self.params.get("p_max_distance", 4)
        if not re.fullmatch(r"[0-9a-f]{16}", fingerprint) or not 0 <= distance <= 8:
            raise PostgrestError("Invalid receipt image fingerprint", "P0001")
        similar = []
        for row in reversed(self.db.rows("payment_proofs")):
            prior = row.get("image_dhash")
            if prior and (int(prior, 16) ^ int(fingerprint, 16)).bit_count() <= distance:
                similar.append({"proof_id": row["id"],
                                "registration_id": row["registration_id"],
                                "transaction_reference": row["transaction_reference"],
                                "distance": (int(prior, 16) ^ int(fingerprint, 16)).bit_count()})
            if len(similar) == 5:
                break
        return Result(similar)

    def _review_payment_proof_v2(self):
        """Model 026's settlement, reversal of a rejection, and rollback."""
        params = self.params
        proof_id = params.get("p_proof_id")
        actor_id = params.get("p_reviewer_id")
        decision = params.get("p_decision")
        note = str(params.get("p_note") or "").strip()
        before = deepcopy(self.db.tables)
        try:
            if decision not in ("approved", "rejected"):
                raise PostgrestError("Decision must be approved or rejected", "P0001")
            proof = next((p for p in self.db.tables.get("payment_proofs", [])
                          if str(p.get("id")) == str(proof_id)), None)
            if not proof:
                raise PostgrestError("Payment proof %s does not exist" % proof_id, "P0001")
            entry = next((r for r in self.db.tables.get("registrations", [])
                          if str(r.get("id")) == str(proof["registration_id"])), None)
            tournament = next((t for t in self.db.rows("tournaments")
                               if str(t.get("id")) == str(proof["tournament_id"])), None)
            if not entry or not tournament or entry.get("tournament_id") != proof.get("tournament_id"):
                raise PostgrestError("Payment proof is not linked to a valid registration", "P0001")
            actor = next((p for p in self.db.rows("profiles")
                          if str(p.get("id")) == str(actor_id)), None)
            manager = any(a.get("tournament_id") == proof["tournament_id"]
                          and a.get("user_id") == actor_id
                          and a.get("status") == "approved"
                          and a.get("access_role") == "manager"
                          for a in self.db.rows("tournament_access"))
            if not actor or actor.get("role") != "admin" or not (
                    params.get("p_allow_any_admin")
                    or tournament.get("owner_id") is None
                    or tournament.get("owner_id") == actor_id or manager):
                raise PostgrestError("Only this tournament owner or an approved manager may review payment proof", "P0001")
            if proof.get("status") != "pending" and not (
                    proof.get("status") == "rejected" and decision == "approved"):
                if proof.get("status") == decision:
                    payment = next((p for p in self.db.rows("payments")
                                    if p.get("id") == proof.get("payment_id")), None)
                    return Result({"proof": deepcopy(proof), "payment": deepcopy(payment),
                                   "registration": deepcopy(entry)})
                raise PostgrestError("This payment proof has already been reviewed", "P0001")
            reconsidered = proof.get("status") == "rejected"
            if reconsidered and len(note) < 15:
                raise PostgrestError("Explain the corrected rejection in at least 15 characters", "P0001")
            previous = deepcopy(proof)
            payment = None
            if decision == "approved":
                if (entry.get("status") == "rejected"
                        or tournament.get("status") in ("cancelled", "completed")
                        or entry.get("payment_status") != "pending"):
                    raise PostgrestError("Entry cannot accept proof approval", "P0001")
                fee = entry.get("fee_paise")
                if fee is None:
                    fee = round(float(tournament.get("entry_fee") or 0) * 100)
                if fee <= 0 or proof.get("amount_paise") != fee:
                    raise PostgrestError("Proof amount does not match the registration fee", "P0001")
                if any(p.get("registration_id") == entry["id"] and p.get("status") == "paid"
                       for p in self.db.rows("payments")):
                    raise PostgrestError("A payment is already recorded for this registration", "P0001")
                if reconsidered and any(p.get("registration_id") == entry["id"]
                                        and p.get("id") != proof_id and p.get("status") == "pending"
                                        for p in self.db.rows("payment_proofs")):
                    raise PostgrestError("Review the newer pending proof before reconsidering this one", "P0001")
                if any(p.get("method") in ("upi", "bank_transfer", "gpay_upi")
                       and p.get("status") in ("paid", "refunded", "refund_due")
                       and re.sub(r"[^A-Z0-9]", "", str((p.get("notes") or {}).get("reference") or "").upper()) == proof["transaction_reference"]
                       for p in self.db.rows("payments")):
                    raise PostgrestError("This transaction reference is already recorded in the payment ledger", "P0001")
                payment = self.db.table("payments").insert({
                    "registration_id": entry["id"], "tournament_id": proof["tournament_id"],
                    "razorpay_order_id": "gpay-proof-" + str(proof_id),
                    "amount_paise": fee, "status": "paid", "method": "gpay_upi",
                    "paid_at": _now(),
                    "notes": {"reference": proof["transaction_reference"],
                              "proof_id": proof_id, "reviewed_by": actor_id,
                              "reconsidered": reconsidered},
                }).execute().data[0]
                entry.update({"payment_status": "paid", "status": "approved"})
                proof.update({"status": "approved", "payment_id": payment["id"]})
            else:
                if len(note) < 5:
                    raise PostgrestError("A rejection needs a reason of at least 5 characters", "P0001")
                proof["status"] = "rejected"
            proof.update({"reviewed_by": actor_id, "reviewed_at": _now(),
                          "review_note": note or None})
            self.db.table("audit_logs").insert({
                "user_id": actor_id,
                "action": "payment.proof_reconsidered_approved" if reconsidered else "payment.proof_" + decision,
                "entity_type": "payment_proof", "entity_id": str(proof_id),
                "previous_state": previous, "new_state": deepcopy(proof),
                "request_context": {"receiving_account_confirmed": decision == "approved",
                                    "reconsidered": reconsidered, "review_note": note},
            }).execute()
            return Result({"proof": deepcopy(proof), "payment": deepcopy(payment),
                           "registration": deepcopy(entry)})
        except Exception:
            self.db.tables = before
            raise

    def _record_manual_entry_payment(self):
        """Model migration 023's all-or-nothing desk settlement."""
        params = self.params
        registration_id = params.get("p_registration_id")
        actor_id = params.get("p_actor_id")
        method = params.get("p_method")
        reference = str(params.get("p_reference") or "").strip()
        if method not in ("cash", "upi", "bank_transfer"):
            raise PostgrestError("Choose cash, UPI or bank transfer", "P0001")
        if method != "cash":
            reference = re.sub(r"[^A-Z0-9]", "", reference.upper())
            if not 6 <= len(reference) <= 80:
                raise PostgrestError("Invalid bank or UPI reference", "P0001")
        elif not 3 <= len(reference) <= 120:
            raise PostgrestError("Invalid cash receipt reference", "P0001")

        before = deepcopy(self.db.tables)
        try:
            registration = next((row for row in self.db.rows("registrations")
                                 if str(row.get("id")) == str(registration_id)), None)
            if not registration:
                raise PostgrestError("Registration does not exist", "P0001")
            tournament = next((row for row in self.db.rows("tournaments")
                               if str(row.get("id")) == str(registration["tournament_id"])), None)
            actor = next((row for row in self.db.rows("profiles")
                          if str(row.get("id")) == str(actor_id)), None)
            access = any(row.get("tournament_id") == registration["tournament_id"]
                         and row.get("user_id") == actor_id
                         and row.get("status") == "approved"
                         and row.get("access_role") == "manager"
                         for row in self.db.rows("tournament_access"))
            if not actor or actor.get("role") != "admin" or not (
                    params.get("p_allow_any_admin")
                    or not tournament.get("owner_id")
                    or tournament.get("owner_id") == actor_id or access):
                raise PostgrestError("Only this organiser may record payment", "P0001")
            paid = next((row for row in self.db.rows("payments")
                         if row.get("registration_id") == registration_id
                         and row.get("status") == "paid"), None)
            if paid:
                if (not str(paid.get("razorpay_order_id") or "").startswith("manual-")
                        or paid.get("method") != method
                        or (paid.get("notes") or {}).get("reference") != reference):
                    raise PostgrestError("A different payment is already recorded for this entry", "P0001")
                if (registration.get("payment_status") == "pending"
                        and registration.get("status") != "rejected"
                        and tournament.get("status") not in ("cancelled", "completed")):
                    registration = self.db.table("registrations").update({
                        "payment_status": "paid", "status": "approved",
                    }).eq("id", registration_id).execute().data[0]
                return Result({"payment": paid, "registration": registration})
            if (registration.get("status") == "rejected"
                    or tournament.get("status") in ("cancelled", "completed")
                    or registration.get("payment_status") != "pending"):
                raise PostgrestError("Entry cannot accept payment", "P0001")
            if any(row.get("registration_id") == registration_id and row.get("status") == "pending"
                   for row in self.db.rows("payment_proofs")):
                raise PostgrestError("A GPay proof is awaiting review", "P0001")
            fee = registration.get("fee_paise")
            if fee is None:
                fee = round(float(tournament.get("entry_fee") or 0) * 100)
            fee = int(fee)
            if fee <= 0:
                raise PostgrestError("This entry has no fee to collect", "P0001")
            if method != "cash":
                if any(row.get("transaction_reference") == reference
                       for row in self.db.rows("payment_proofs")):
                    raise PostgrestError("This reference was already submitted as proof", "P0001")
                if any(row.get("method") in ("upi", "bank_transfer", "gpay_upi")
                       and row.get("status") in ("paid", "refunded", "refund_due")
                       and re.sub(r"[^A-Z0-9]", "", str((row.get("notes") or {}).get("reference") or "").upper()) == reference
                       for row in self.db.rows("payments")):
                    raise PostgrestError("This bank or UPI reference is already recorded", "P0001")
            payment = self.db.table("payments").insert({
                "registration_id": registration_id,
                "tournament_id": registration["tournament_id"],
                "razorpay_order_id": "manual-" + str(uuid.uuid4()),
                "amount_paise": fee, "status": "paid", "method": method,
                "paid_at": _now(),
                "notes": {"reference": reference, "recorded_by": actor_id},
            }).execute().data[0]
            updated = self.db.table("registrations").update({
                "payment_status": "paid", "status": "approved",
            }).eq("id", registration_id).execute().data
            if len(updated) != 1:
                raise PostgrestError("Registration changed during settlement", "P0001")
            self.db.table("audit_logs").insert([
                {"user_id": actor_id, "action": "payment.manual_recorded",
                 "entity_type": "payment", "entity_id": payment["id"],
                 "new_state": payment},
                {"user_id": actor_id, "action": "registration.auto_approved_after_payment",
                 "entity_type": "registration", "entity_id": registration_id,
                 "previous_state": registration, "new_state": updated[0]},
            ]).execute()
            return Result({"payment": payment, "registration": updated[0]})
        except Exception:
            self.db.tables = before
            raise

    def _replace_tournament_fixtures_checked(self):
        tournament_id = self.params["p_tournament_id"]
        tournament = next((t for t in self.db.rows("tournaments")
                           if t.get("id") == tournament_id), None)
        if not tournament or tournament.get("status") not in (
                "registration_closed", "fixture_generation", "fixture_published",
                "scheduled", "in_progress", "ongoing"):
            raise PostgrestError("Close registration before generating fixtures", "P0001")
        settings_snapshot = {
            "status": tournament.get("status"),
            "fixtures_generated": tournament.get("fixtures_generated"),
            "format": tournament.get("format"),
            "category": tournament.get("category"),
            "rules": tournament.get("rules"),
            "number_of_boards": tournament.get("number_of_boards"),
        }
        if settings_snapshot != self.params.get("p_tournament_snapshot"):
            raise PostgrestError("Tournament draw settings changed; reload and retry", "P0001")
        rows = [r for r in self.db.rows("registrations")
                if r.get("tournament_id") == tournament_id]
        if any(r.get("status") == "pending" for r in rows):
            raise PostgrestError("Resolve every pending registration", "P0001")
        current_fee = round(float(tournament.get("entry_fee") or 0) * 100)
        if any(r.get("status") == "approved"
               and int(r.get("fee_paise") if r.get("fee_paise") is not None else current_fee) > 0
               and r.get("payment_status") not in ("paid", "waived") for r in rows):
            raise PostgrestError("Resolve every unpaid approved registration", "P0001")
        roster = [{
            "id": r["id"], "type": r["type"],
            "player_id": r.get("player_id"), "team_id": r.get("team_id"),
            "payment_status": r.get("payment_status"), "fee_paise": r.get("fee_paise"),
        } for r in sorted(rows, key=lambda r: r["id"])
            if r.get("status") == "approved"]
        if roster != self.params.get("p_approved_roster"):
            raise PostgrestError("The approved entry list changed during the draw", "P0001")
        return self._replace_tournament_fixtures()

    def _replace_tournament_fixtures(self):
        """Emulate the database RPC's all-or-nothing draw replacement."""
        tournament_id = self.params["p_tournament_id"]
        matches = self.params.get("p_matches") or []
        boards = self.params.get("p_boards") or []
        links = self.params.get("p_links") or []
        before = deepcopy(self.db.tables)
        try:
            existing = [m for m in self.db.rows("matches")
                        if m.get("tournament_id") == tournament_id]
            if not self.params.get("p_force") and any(
                    m.get("result_confirmed") or m.get("status") in ("live", "completed")
                    for m in existing):
                raise PostgrestError("The existing draw has played results", "P0001")
            self.db.table("matches").delete().eq("tournament_id", tournament_id).execute()
            if matches:
                self.db.table("matches").insert(matches).execute()
            if boards:
                self.db.table("boards").insert(boards).execute()
            for link in links:
                updated = self.db.table("matches").update({
                    "next_match_id": link["next_match_id"],
                    "next_match_slot": link.get("next_match_slot"),
                }).eq("id", link["id"]).eq("tournament_id", tournament_id).execute().data
                if len(updated) != 1:
                    raise PostgrestError("Concurrent draw change", "P0001")
            updated = self.db.table("tournaments").update({
                "fixtures_generated": True,
            }).eq("id", tournament_id).execute().data
            if len(updated) != 1:
                raise PostgrestError("Tournament not found", "P0001")
        except Exception:
            self.db.tables = before
            raise
        return Result(len(matches))

    def _update_match_schedule_batch(self):
        """Update only schedule fields, or leave every row untouched."""
        tournament_id = self.params["p_tournament_id"]
        rows = self.params.get("p_rows") or []
        before = deepcopy(self.db.tables)
        try:
            seen = set()
            for patch in rows:
                match_id = patch["id"]
                if match_id in seen:
                    raise PostgrestError("Duplicate match in schedule", "P0001")
                seen.add(match_id)
                updated = self.db.table("matches").update({
                    "board_number": patch["board_number"],
                    "scheduled_date": patch["scheduled_date"],
                    "scheduled_time": patch["scheduled_time"],
                }).eq("id", match_id).eq("tournament_id", tournament_id).execute().data
                if len(updated) != 1:
                    raise PostgrestError("Concurrent schedule change", "P0001")
        except Exception:
            self.db.tables = before
            raise
        return Result(len(rows))

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

    def _apply_board_result_with_next_set(self):
        p = self.params
        next_set = p.get("p_next_set_number")
        current_set = p.get("p_set_number") or 1
        if next_set != current_set + 1 or p.get("p_next_board_number") is not None:
            raise PostgrestError("Invalid game transition", "P0001")
        next_board = next((board for board in self.db.tables.get("boards", [])
                           if str(board.get("match_id")) == str(p.get("p_match_id"))
                           and board.get("set_number") == next_set
                           and board.get("board_number") == 1), None)
        if next_board is None or next_board.get("status") not in ("pending", "in_progress"):
            raise PostgrestError("The next game has no available first board", "P0001")
        before = deepcopy(self.db.tables)
        try:
            result = self._apply_board_result()
            next_board["status"] = "in_progress"
            return result
        except Exception:
            self.db.tables = before
            raise

    def _delete_match_safely(self):
        match_id = self.params.get("p_match_id")
        force = self.params.get("p_force") is True
        match = next((m for m in self.db.tables.get("matches", [])
                      if str(m.get("id")) == str(match_id)), None)
        if match is None:
            raise PostgrestError("Match does not exist", "P0001")
        tournament_id = match["tournament_id"]
        feeders = [m for m in self.db.tables.get("matches", [])
                   if str(m.get("next_match_id")) == str(match_id)]
        if feeders:
            raise PostgrestError("Delete the feeder matches first or redraw the knockout stage", "P0001")
        boards = [b for b in self.db.tables.get("boards", [])
                  if str(b.get("match_id")) == str(match_id)]
        played = [b for b in boards if b.get("status") == "completed"
                  or b.get("player1_score") or b.get("player2_score")]
        has_play = bool(match.get("result_confirmed") or match.get("winner_id")
                        or match.get("status") in ("live", "paused", "completed")
                        or played)
        if has_play and not force:
            raise PostgrestError("Match has play or a result; force is required to delete it", "P0001")
        parent = next((m for m in self.db.tables.get("matches", [])
                       if str(m.get("id")) == str(match.get("next_match_id"))), None)
        if parent is not None:
            parent_boards = [b for b in self.db.tables.get("boards", [])
                             if str(b.get("match_id")) == str(parent.get("id"))]
            if (parent.get("status") in ("live", "paused", "completed")
                    or parent.get("result_confirmed")
                    or any(b.get("status") == "completed" or b.get("player1_score")
                           or b.get("player2_score") for b in parent_boards)):
                raise PostgrestError("The next-round match already has play", "P0001")
        before = deepcopy(self.db.tables)
        try:
            cleared = None
            if parent is not None and match.get("winner_id"):
                slot = match.get("next_match_slot")
                if slot in ("player1", "player2") and parent.get(slot + "_id") == match["winner_id"]:
                    parent[slot + "_id"] = None
                    parent[slot + "_name"] = "Winner TBD"
                    cleared = {"matchNumber": parent.get("match_number"), "slot": slot}
            self.db.tables["boards"] = [b for b in self.db.tables.get("boards", [])
                                        if str(b.get("match_id")) != str(match_id)]
            self.db.tables["matches"] = [m for m in self.db.tables.get("matches", [])
                                         if str(m.get("id")) != str(match_id)]
            self.db.tables.setdefault("audit_logs", []).append({
                "id": str(uuid.uuid4()), "user_id": self.params.get("p_actor_id"),
                "action": "match.delete", "entity_type": "match", "entity_id": str(match_id),
                "previous_state": dict(match), "new_state": {"deleted": True},
            })
            return Result({"match": dict(match), "tournamentId": tournament_id,
                           "boardsDeleted": len(boards), "boardsWithPlay": len(played),
                           "discardedPlay": has_play, "clearedSlot": cleared})
        except Exception:
            self.db.tables = before
            raise

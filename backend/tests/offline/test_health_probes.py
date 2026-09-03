"""
GET /api/health: the readiness probe a deploy gates on, over HTTP, against the
in-memory database.

The API degrades rather than crashes when a migration is missing. That is right
in the middle of a tournament and wrong at deploy time, so /api/health probes
each migration and .github/workflows/ci.yml refuses a deployment unless it
answers status "ok" with nothing pending. These cases pin what that answer
means: a complete schema is "ok"; a missing column names the migration file to
apply; the migrations PostgREST cannot see are never claimed and are always
listed under unprobeable_migrations, on the cached paths too; and every numbered
file in db/migrations is accounted for one way or the other, so a new migration
without a probe fails here rather than shipping unseen.

Technique: WHITE BOX. fakedb answers any select on any column, so a missing
column is simulated by wrapping the fake's table() to refuse one named column
the way PostgREST does (42703 for a column, PGRST205 for a view or table that
is not in the schema cache). The module-level cache in app.main is reset
between cases because a positive result is kept for the life of the process.

    python tests/offline/test_health_probes.py
"""
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness                       # noqa: E402
from fakedb import PostgrestError                 # noqa: E402
import app.main as app_main                       # noqa: E402

RESULTS = {}

MIGRATIONS_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "db", "migrations"))

# The names the payload must always carry, whatever the schema looks like.
UNPROBEABLE = {"008_drop_city_default", "009_stop_timer_on_finish",
               "013_profiles_trigger_and_rls"}

# 007 replaces a function, so it is probed by rpc rather than by column.
RPC_PROBED = {"007_apply_board_result_sets"}

PAYLOAD_KEYS = {
    "status", "pending_migrations", "migrations", "env", "database_client",
    "database_admin_client", "transactional_writes", "idempotency",
    "tournament_ownership", "unprobeable_migrations",
}


def check(label, cond, example=""):
    slot = RESULTS.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not cond:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(str(example)[:300])
    return bool(cond)


def body(r):
    try:
        return r.json()
    except Exception:
        return r.text


# ---------------------------------------------------------------------------
# Simulating a schema with something missing
# ---------------------------------------------------------------------------

class _ProbedTable:
    """
    Wraps a fakedb Table so that a select on one named column is refused the
    way PostgREST refuses a column, view or table that does not exist. Every
    other call goes straight through to the fake.
    """

    def __init__(self, real, name, seen, missing):
        self._real = real
        self._name = name
        self._seen = seen
        self._missing = missing

    def select(self, columns="*", **kw):
        self._seen.append((self._name, columns))
        if (self._name, "*") in self._missing:
            raise PostgrestError(
                "Could not find the table 'public.%s' in the schema cache" % self._name,
                "PGRST205")
        if (self._name, columns) in self._missing:
            raise PostgrestError(
                "column %s.%s does not exist" % (self._name, columns), "42703")
        return self._real.select(columns, **kw)

    def __getattr__(self, attr):
        return getattr(self._real, attr)


def with_schema(h, missing=()):
    """
    Make the harness's database refuse the given (table, column) probes.
    (table, "*") means the whole table or view is absent. Returns the list the
    wrapper appends every probed (table, column) to.
    """
    seen = []
    # Kept by reference when a set is passed, so a case can "apply" a
    # migration mid-test by clearing it.
    missing = missing if isinstance(missing, set) else set(missing)
    real_table = h.db.table
    h.db.table = lambda name: _ProbedTable(real_table(name), name, seen, missing)
    return seen


def fresh_health(h):
    """GET /api/health with the module-level probe cache cleared first."""
    app_main._pending_cache = None
    app_main._pending_checked_at = 0.0
    return body(h.client.get("/api/health"))


def cached_health(h):
    """GET /api/health leaving whatever the cache holds in place."""
    return body(h.client.get("/api/health"))


# ---------------------------------------------------------------------------
# A complete schema is healthy
# ---------------------------------------------------------------------------

def test_complete_schema_is_ok():
    h = Harness()
    seen = with_schema(h)
    payload = fresh_health(h)

    check("a complete schema answers status ok",
          payload.get("status") == "ok", payload)
    check("a complete schema has nothing pending",
          payload.get("pending_migrations") == [], payload)
    check("a complete schema reports all applied",
          payload.get("migrations") == "all applied", payload)
    check("the payload carries every documented key and nothing else",
          set(payload.keys()) == PAYLOAD_KEYS, sorted(payload.keys()))

    # The probes must actually have been issued; an empty pending list proves
    # nothing if nothing was asked.
    probed = set(seen)
    for table, column in (("tournaments", "champion_id"),
                          ("public_profiles", "id"),
                          ("matches", "walkover_by"),
                          ("idempotency_keys", "key")):
        check("the probe selects %s.%s" % (table, column),
              (table, column) in probed, sorted(probed))
    check("the 007 probe calls apply_board_result by rpc",
          any(name == "apply_board_result" for name, _ in h.db.rpc_calls),
          h.db.rpc_calls)


# ---------------------------------------------------------------------------
# A missing column names the file to apply
# ---------------------------------------------------------------------------

def test_missing_column_is_pending():
    h = Harness()
    with_schema(h, missing={("tournaments", "champion_id")})
    payload = fresh_health(h)
    check("a missing champion_id column reports 012 pending",
          payload.get("pending_migrations") == ["012_lifecycle"], payload)
    check("a missing column degrades the status",
          payload.get("status") == "degraded", payload)
    check("the migrations line names the file to apply",
          "db/migrations/012_lifecycle.sql" in str(payload.get("migrations")),
          payload)

    h = Harness()
    with_schema(h, missing={("public_profiles", "*")})
    payload = fresh_health(h)
    check("a missing public_profiles view reports 011 pending",
          payload.get("pending_migrations") == ["011_profile_privacy"], payload)

    h = Harness()
    with_schema(h, missing={("boards", "board_winner"), ("matches", "toss_choice"),
                            ("tournaments", "champion_id")})
    payload = fresh_health(h)
    check("several missing columns are all reported, in migration order",
          payload.get("pending_migrations") == ["004_match_toss", "005_board_detail",
                                                "012_lifecycle"], payload)
    check("a degraded payload has the same keys as a healthy one",
          set(payload.keys()) == PAYLOAD_KEYS, sorted(payload.keys()))


# ---------------------------------------------------------------------------
# What cannot be seen is never claimed
# ---------------------------------------------------------------------------

def test_unprobeable_migrations_are_listed_not_claimed():
    h = Harness()
    with_schema(h)
    payload = fresh_health(h)
    listed = payload.get("unprobeable_migrations")
    check("unprobeable_migrations is a list",
          isinstance(listed, list), payload)
    names = {m.get("migration") for m in (listed or []) if isinstance(m, dict)}
    check("the unprobeable list names 008, 009 and 013",
          names == UNPROBEABLE, sorted(names))
    for m in listed or []:
        check("each unprobeable migration carries a one-line reason",
              isinstance(m.get("reason"), str) and m["reason"].strip()
              and "\n" not in m["reason"], m)
        check("each unprobeable migration is a file on disk",
              os.path.exists(os.path.join(MIGRATIONS_DIR, "%s.sql" % m.get("migration"))),
              m)
    check("nothing is both pending and unprobeable",
          not (names & set(payload.get("pending_migrations") or [])), payload)

    # The list is a statement about what the probe can see, not about the
    # database, so a degraded schema carries exactly the same one.
    h = Harness()
    with_schema(h, missing={("tournaments", "champion_id")})
    degraded = fresh_health(h)
    check("a degraded payload carries the same unprobeable list",
          degraded.get("unprobeable_migrations") == listed, degraded)
    check("an unprobeable migration is never reported pending",
          not (UNPROBEABLE & set(degraded.get("pending_migrations") or [])),
          degraded)


# ---------------------------------------------------------------------------
# The cached paths return the same shape
# ---------------------------------------------------------------------------

def test_cached_paths_carry_the_list():
    # Positive result: kept for the life of the process, because a schema does
    # not lose a column without a deployment.
    h = Harness()
    seen = with_schema(h)
    first = fresh_health(h)
    probes_after_first = len(seen)
    second = cached_health(h)
    check("a second call after a healthy probe does not re-probe",
          len(seen) == probes_after_first, (probes_after_first, len(seen)))
    check("the positively cached payload is identical to the probed one",
          second == first, (first, second))
    check("the positively cached payload carries unprobeable_migrations",
          second.get("unprobeable_migrations") == first.get("unprobeable_migrations"),
          second)

    # Negative result: re-checked after _PENDING_RECHECK_SECONDS, so applying
    # the migration takes effect without a redeploy.
    h = Harness()
    missing = {("tournaments", "champion_id")}
    seen = with_schema(h, missing=missing)
    first = fresh_health(h)
    check("the degraded probe reports 012 pending",
          first.get("pending_migrations") == ["012_lifecycle"], first)
    probes_after_first = len(seen)
    missing.clear()                       # the operator applies 012
    second = cached_health(h)
    check("within the recheck window the degraded answer is served from cache",
          second.get("pending_migrations") == ["012_lifecycle"]
          and len(seen) == probes_after_first, (second, len(seen)))
    check("the negatively cached payload has every key",
          set(second.keys()) == PAYLOAD_KEYS, sorted(second.keys()))
    check("the negatively cached payload carries unprobeable_migrations",
          second.get("unprobeable_migrations") == first.get("unprobeable_migrations"),
          second)
    app_main._pending_checked_at -= app_main._PENDING_RECHECK_SECONDS + 1
    third = cached_health(h)
    check("after the recheck window the probe runs again and clears",
          third.get("status") == "ok" and third.get("pending_migrations") == []
          and len(seen) > probes_after_first, (third, len(seen)))


# ---------------------------------------------------------------------------
# Every migration on disk is accounted for
# ---------------------------------------------------------------------------

def test_every_migration_is_accounted_for():
    on_disk = set()
    for name in os.listdir(MIGRATIONS_DIR):
        if re.match(r"^\d{3}_.+\.sql$", name):
            on_disk.add(name[:-4])
    check("the migrations directory holds numbered files",
          len(on_disk) >= 11, sorted(on_disk))

    probed = {m for m, _table, _column in app_main._COLUMN_PROBES}
    unprobeable = {m["migration"] for m in app_main.UNPROBEABLE_MIGRATIONS}
    accounted = probed | RPC_PROBED | unprobeable
    for migration in sorted(on_disk):
        check("every numbered migration is probed or declared unprobeable",
              migration in accounted, migration)
    for migration in sorted(accounted):
        check("every probe names a migration that exists on disk",
              migration in on_disk, migration)
    check("no migration is both probed and declared unprobeable",
          not ((probed | RPC_PROBED) & unprobeable),
          sorted((probed | RPC_PROBED) & unprobeable))
    for _migration, table, column in app_main._COLUMN_PROBES:
        check("each column probe names a table and a column",
              isinstance(table, str) and table and isinstance(column, str) and column,
              (table, column))


SUITES = [
    ("complete schema", test_complete_schema_is_ok),
    ("missing column", test_missing_column_is_pending),
    ("unprobeable migrations", test_unprobeable_migrations_are_listed_not_claimed),
    ("cached paths", test_cached_paths_carry_the_list),
    ("every migration accounted for", test_every_migration_is_accounted_for),
]


def main():
    for name, fn in SUITES:
        try:
            fn()
        except Exception:
            check("the %s suite runs to completion" % name, False,
                  traceback.format_exc()[-400:])
        finally:
            # Leave the process-wide cache the way the other suites expect it.
            app_main._pending_cache = None
            app_main._pending_checked_at = 0.0

    total = sum(v[1] for v in RESULTS.values())
    failed = [(k, v) for k, v in sorted(RESULTS.items()) if v[0]]

    print("=" * 78)
    print("health probe suite (real app, in-memory database)")
    print("=" * 78)
    print("assertions executed : %d" % total)
    print("invariants checked  : %d" % len(RESULTS))
    print("invariants violated : %d" % len(failed))
    print()
    if failed:
        print("FAILURES")
        print("-" * 78)
        for label, slot in failed:
            bad, ran, examples = slot
            print("  %s" % label)
            print("     %d of %d cases failed" % (bad, ran))
            for ex in examples:
                print("     e.g. %s" % ex)
            print()
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())

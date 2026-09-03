"""
Count the network round trips each request costs.

Latency here is not CPU. Every Supabase call from a Vercel serverless function
is a network round trip, and on a venue connection each one is tens to hundreds
of milliseconds. So the question that matters for "why does pausing the timer
take five seconds" is not how fast the code is -- it is HOW MANY TIMES the code
goes to the database and back, and how many of those are sequential.

This wraps the in-memory client so every table/auth/rpc call is counted, then
issues the real requests through the real app and reports the tally.

    python tests/offline/probe_latency.py

Offline. Nothing here touches a network or a real database.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness                       # noqa: E402
import fakedb                                     # noqa: E402

CALLS = []


def instrument(db):
    """Record every round trip the app makes, in order."""
    real_table = db.table
    real_rpc = db.rpc
    real_get_user = db.auth.get_user
    real_admin_create = db.auth.admin.create_user
    real_admin_update = db.auth.admin.update_user_by_id

    def table(name):
        t = real_table(name)
        real_select, real_insert = t.select, t.insert
        real_update, real_delete, real_upsert = t.update, t.delete, t.upsert

        def wrap(fn, verb):
            def inner(*a, **kw):
                q = fn(*a, **kw)
                real_execute = q.execute

                def execute():
                    CALLS.append("%s %s" % (verb, name))
                    return real_execute()
                q.execute = execute
                return q
            return inner

        t.select = wrap(real_select, "SELECT")
        t.insert = wrap(real_insert, "INSERT")
        t.update = wrap(real_update, "UPDATE")
        t.delete = wrap(real_delete, "DELETE")
        t.upsert = wrap(real_upsert, "UPSERT")
        return t

    def rpc(name, params=None):
        q = real_rpc(name, params)
        real_execute = q.execute

        def execute():
            CALLS.append("RPC %s" % name)
            return real_execute()
        q.execute = execute
        return q

    def get_user(token=None):
        CALLS.append("AUTH verify token")
        return real_get_user(token)

    def admin_create(attrs):
        CALLS.append("AUTH create user")
        return real_admin_create(attrs)

    def admin_update(uid, attributes=None, **kw):
        CALLS.append("AUTH update user")
        return real_admin_update(uid, attributes, **kw)

    db.table = table
    db.rpc = rpc
    db.auth.get_user = get_user
    db.auth.admin.create_user = admin_create
    db.auth.admin.update_user_by_id = admin_update


def measure(label, fn):
    del CALLS[:]
    response = fn()
    calls = list(CALLS)
    status = getattr(response, "status_code", "-")
    print("\n%s" % label)
    print("  status %s   round trips: %d" % (status, len(calls)))
    seen = {}
    for c in calls:
        seen[c] = seen.get(c, 0) + 1
    for c in calls:
        if seen.get(c):
            print("     %-28s x%d" % (c, seen[c]))
            seen[c] = 0
    return len(calls)


def main():
    h = Harness()
    instrument(h.db)

    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin)
    p1, p2 = h.make_user("Asha"), h.make_user("Bala")
    mid = h.seed_match(tid, p1, p2, boards=8)
    # A realistic field, so the read endpoints have something to serialise.
    for i in range(20):
        h.make_user("Player %d" % i, "player")

    print("=" * 72)
    print("round trips per request (each one is a network hop in production)")
    print("=" * 72)

    totals = {}

    totals["pause"] = measure(
        "POST /matches/{id}/pause      the timer button",
        lambda: h.post("/api/matches/%s/pause" % mid, {}, user_id=admin))

    totals["resume"] = measure(
        "POST /matches/{id}/resume     the timer button",
        lambda: h.post("/api/matches/%s/resume" % mid, {}, user_id=admin))

    totals["submit"] = measure(
        "POST /matches/{id}/boards/1/submit   entering one board score",
        lambda: h.post("/api/matches/%s/boards/1/submit" % mid, {
            "p1Score": 0, "p2Score": 0, "setNumber": 1,
            "boardWinner": "player1", "coinsRemainingWith": "player2",
            "coinsRemaining": 4, "queenPocketedBy": "none",
            "queenCoveredBy": "none"}, user_id=admin))

    totals["list"] = measure(
        "GET  /tournaments            what refreshData() calls after EVERY action",
        lambda: h.get("/api/tournaments", admin))

    totals["one"] = measure(
        "GET  /tournaments/{id}",
        lambda: h.get("/api/tournaments/%s" % tid, admin))

    totals["me"] = measure(
        "GET  /auth/me",
        lambda: h.get("/api/auth/me", admin))

    totals["health"] = measure(
        "GET  /health                 polled by the client",
        lambda: h.client.get("/api/health"))

    print("\n" + "=" * 72)
    print("what one timer tap actually costs the user")
    print("=" * 72)
    tap = totals["pause"] + totals["list"]
    print("  POST /pause                      %2d round trips" % totals["pause"])
    print("  then refreshData() -> GET /tournaments  %2d round trips" % totals["list"])
    print("  ------------------------------------------------")
    print("  total per tap                    %2d round trips" % tap)
    for rtt in (60, 150, 300):
        print("     at %3dms per hop, if serialised: %.1fs" % (rtt, tap * rtt / 1000.0))

    print("\n" + "=" * 72)
    print("what entering one board score costs")
    print("=" * 72)
    score = totals["submit"] + totals["list"]
    print("  POST /boards/n/submit            %2d round trips" % totals["submit"])
    print("  then refreshData()               %2d round trips" % totals["list"])
    print("  ------------------------------------------------")
    print("  total                            %2d round trips" % score)
    for rtt in (60, 150, 300):
        print("     at %3dms per hop, if serialised: %.1fs" % (rtt, score * rtt / 1000.0))

    print("\n  Note: this counts round trips, not milliseconds. It excludes")
    print("  serverless cold start, which the root requirements.txt records as")
    print("  an additional ~867ms for one dependency alone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

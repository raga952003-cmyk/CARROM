"""Queen points: awarded from the tournament rules, only when covered."""
import os, sys
import uuid, requests
sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _session
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
adm = get_admin_db()
created = []
failures = []
H = {}


def ok(label, cond, detail=""):
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + repr(detail)) if (detail not in (None, "") and not cond) else ""))
    if not cond:
        failures.append(label)


def api(m, path, **kw):
    return _session.request(m, path, H, **kw)


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created:
        try: adm.table("tournaments").delete().eq("id", tid).execute()
        except Exception: pass
    for r in (adm.table("profiles").select("id").ilike("email", "%q"+RUN+"%").execute().data or []):
        try: adm.auth.admin.delete_user(r["id"])
        except Exception: pass
    print("  done")


_seq = [0]


def build(queen_points):
    _seq[0] += 1
    tag = "{}{}".format(queen_points, _seq[0])
    """A 2-player knockout with a given queen value; returns the match."""
    r = api("POST", "/tournaments", json={
        "name": "Queen {} {}".format(tag, RUN), "category": "singles",
        "format": "knockout", "registrationStartDate": "2026-09-01",
        "registrationEndDate": "2026-09-05", "tournamentStartDate": "2026-09-10",
        "tournamentEndDate": "2026-09-12", "venue": "V", "city": "Pune",
        "numberOfBoards": 1,
        "rules": {"maxBoardsPerMatch": 3, "targetScore": 29, "queenPoints": queen_points},
        "status": "registration_open"})
    assert r.status_code == 200, r.text[:250]
    tid = r.json()["id"]; created.append(tid)
    for i in range(2):
        p = api("POST", "/players", json={
            "name": "Q{}P{} {}".format(tag, i, RUN),
            "email": "q{}_{}_{}@carromarena.com".format(RUN, tag, i)}).json()
        api("POST", "/tournaments/{}/registrations".format(tid),
            json={"type": "singles", "playerId": p["id"]})
    api("PUT", "/tournaments/" + tid, json={"status": "registration_closed"})
    api("POST", "/tournaments/{}/fixtures".format(tid))
    match = api("GET", "/fixtures/" + tid).json()[0]
    api("POST", "/matches/{}/start".format(match["id"]))
    return tid, match


def board(match_id, n=1):
    rows = adm.table("boards").select("*").eq("match_id", match_id).eq("board_number", n).execute().data
    return rows[0] if rows else {}


try:
    r = requests.post(BASE + "/auth/signup", json={
        "email": "q{}_admin@carromarena.com".format(RUN), "password": "TestPass2345x",
        "name": "Queen Admin", "role": "admin"}, timeout=90)
    assert r.status_code == 200, r.text[:250]
    H["Authorization"] = "Bearer " + r.json()["access_token"]
    _session.remember("q{}_admin@carromarena.com".format(RUN), "TestPass2345x")

    print("=" * 64)
    print("QUEEN = 3 (default), COVERED")
    print("=" * 64)
    tid, m = build(3)
    r = api("POST", "/matches/{}/boards/1/submit".format(m["id"]),
            json={"p1Score": 21, "p2Score": 15, "queenClaimedBy": "player1",
                  "queenCovered": True, "auditReason": "queen test"})
    ok("submit -> {}".format(r.status_code), r.status_code == 200, r.text[:200])
    b = board(m["id"])
    ok("21 coins + queen = 24 stored (got {})".format(b.get("player1_score")),
       b.get("player1_score") == 24)
    ok("opponent unchanged at 15 (got {})".format(b.get("player2_score")),
       b.get("player2_score") == 15)
    audit = api("GET", "/audit/scores/" + m["id"]).json()
    reason = audit[0].get("reason") if audit else ""
    ok("audit records the coins and the award", "coins 21-15" in reason and "+3" in reason, reason)

    print("\n" + "=" * 64)
    print("QUEEN CLAIMED BUT NOT COVERED")
    print("=" * 64)
    tid2, m2 = build(3)
    api("POST", "/matches/{}/boards/1/submit".format(m2["id"]),
        json={"p1Score": 21, "p2Score": 15, "queenClaimedBy": "player1",
              "queenCovered": False, "auditReason": "uncovered"})
    b2 = board(m2["id"])
    ok("uncovered queen scores nothing, stays 21 (got {})".format(b2.get("player1_score")),
       b2.get("player1_score") == 21)

    print("\n" + "=" * 64)
    print("QUEEN VALUE ACTUALLY CHANGES THE RESULT")
    print("=" * 64)
    tid5, m5 = build(5)
    api("POST", "/matches/{}/boards/1/submit".format(m5["id"]),
        json={"p1Score": 21, "p2Score": 15, "queenClaimedBy": "player1",
              "queenCovered": True, "auditReason": "five"})
    b5 = board(m5["id"])
    ok("queenPoints=5 gives 26 (got {}) — the setting was previously inert".format(
        b5.get("player1_score")), b5.get("player1_score") == 26)

    print("\n" + "=" * 64)
    print("QUEEN CAN DECIDE A BOARD")
    print("=" * 64)
    tid3, m3 = build(3)
    # Level on coins; the queen should settle it.
    api("POST", "/matches/{}/boards/1/submit".format(m3["id"]),
        json={"p1Score": 12, "p2Score": 12, "queenClaimedBy": "player2",
              "queenCovered": True, "auditReason": "decider"})
    b3 = board(m3["id"])
    ok("12-12 on coins becomes {}-{}".format(b3.get("player1_score"), b3.get("player2_score")),
       b3.get("player1_score") == 12 and b3.get("player2_score") == 15)
    row = adm.table("matches").select("*").eq("id", m3["id"]).execute().data[0]
    ok("board win awarded to the queen taker ({}-{})".format(
        row["player1_board_wins"], row["player2_board_wins"]),
        row["player2_board_wins"] == 1 and row["player1_board_wins"] == 0)

    print("\n" + "=" * 64)
    print("NOBODY TOOK THE QUEEN")
    print("=" * 64)
    tid4, m4 = build(3)
    api("POST", "/matches/{}/boards/1/submit".format(m4["id"]),
        json={"p1Score": 20, "p2Score": 9, "queenClaimedBy": "none",
              "queenCovered": False, "auditReason": "no queen"})
    b4 = board(m4["id"])
    ok("scores untouched at 20-9 (got {}-{})".format(b4.get("player1_score"), b4.get("player2_score")),
       b4.get("player1_score") == 20 and b4.get("player2_score") == 9)

finally:
    cleanup()

print("\n" + "=" * 64)
print("RESULTS: {} failure(s)".format(len(failures)))
for f in failures:
    print("  - " + f)
print("ALL QUEEN CHECKS PASSED" if not failures else "FAILURES ABOVE")
sys.exit(1 if failures else 0)

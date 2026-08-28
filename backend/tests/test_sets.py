"""Sets: a match of N sets x M boards, won on sets rather than on total points."""
import os
import sys
import uuid

import requests

sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _session
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
adm = get_admin_db()
created, failures, H = [], [], {}
SUPPORTED = True


def ok(label, cond, detail="", needs_006=False):
    if needs_006 and not SUPPORTED:
        print("  SKIP  {} (needs migration 006)".format(label))
        return
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + repr(detail)) if (detail not in (None, "") and not cond) else ""))
    if not cond:
        failures.append(label)


def api(m, path, **kw):
    return _session.request(m, path, H, **kw)


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created:
        try:
            adm.table("tournaments").delete().eq("id", tid).execute()
        except Exception:
            pass
    for r in (adm.table("profiles").select("id").ilike("email", "%st" + RUN + "%").execute().data or []):
        try:
            adm.auth.admin.delete_user(r["id"])
        except Exception:
            pass
    print("  done")


_seq = [0]


def build(sets_n, per_set):
    # Each build needs its own players; two one-set matches would otherwise
    # collide on the same email and silently fail to register anyone.
    _seq[0] += 1
    tag = _seq[0]
    r = api("POST", "/tournaments", json={
        "name": "Sets {} {}".format(tag, RUN), "category": "singles", "format": "knockout",
        "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-12",
        "venue": "V", "city": "Pune", "numberOfBoards": 1,
        "rules": {"maxBoardsPerMatch": per_set, "scoringMode": "remaining_coins",
                  "coinsPerSide": 9, "queenPoints": 3,
                  "numberOfSets": sets_n, "boardsPerSet": per_set},
        "status": "registration_open"})
    assert r.status_code == 200, r.text[:250]
    tid = r.json()["id"]
    created.append(tid)
    for i in range(2):
        p = api("POST", "/players", json={
            "name": "ST{} S{} P{}".format(RUN, tag, i),
            "email": "st{}_{}_{}@carromarena.com".format(RUN, tag, i)}).json()
        api("POST", "/tournaments/{}/registrations".format(tid),
            json={"type": "singles", "playerId": p["id"]})
    api("PUT", "/tournaments/" + tid, json={"status": "registration_closed"})
    api("POST", "/tournaments/{}/fixtures".format(tid))
    m = api("GET", "/fixtures/" + tid).json()[0]
    api("POST", "/matches/{}/start".format(m["id"]))
    return tid, m


def board(match_id, set_no, n, winner, coins):
    loser = "player2" if winner == "player1" else "player1"
    return api("POST", "/matches/{}/boards/{}/submit".format(match_id, n), json={
        "p1Score": 0, "p2Score": 0, "setNumber": set_no,
        "boardWinner": winner, "coinsRemainingWith": loser, "coinsRemaining": coins,
        "queenPocketedBy": "none", "queenCoveredBy": "none", "auditReason": "sets"})


try:
    r = requests.post(BASE + "/auth/signup", json={
        "email": "st{}_admin@carromarena.com".format(RUN), "password": "TestPass2345x",
        "name": "Sets Admin", "role": "admin"}, timeout=90)
    assert r.status_code == 200, r.text[:250]
    H["Authorization"] = "Bearer " + r.json()["access_token"]
    _session.remember("st{}_admin@carromarena.com".format(RUN), "TestPass2345x")

    try:
        adm.table("boards").select("set_number").limit(1).execute()
    except Exception:
        SUPPORTED = False

    print("=" * 70)
    print("A MATCH OF 3 SETS x 4 BOARDS")
    print("=" * 70)
    tid, m = build(3, 4)
    rows = adm.table("boards").select("*").eq("match_id", m["id"]).execute().data or []
    ok("12 boards created (3 sets x 4)", len(rows) == 12, len(rows), needs_006=True)
    if SUPPORTED:
        by_set = {}
        for b in rows:
            by_set.setdefault(b.get("set_number") or 1, []).append(b)
        ok("boards are spread across 3 sets", sorted(by_set) == [1, 2, 3], sorted(by_set))
        ok("board numbers restart each set",
           sorted(b["board_number"] for b in by_set.get(1, [])) == [1, 2, 3, 4],
           sorted(b["board_number"] for b in by_set.get(1, [])))

    print("\n" + "=" * 70)
    print("SETS DECIDE THE MATCH, NOT TOTAL POINTS")
    print("=" * 70)
    # Player 1 takes sets 1 and 3 narrowly; player 2 takes set 2 in a landslide.
    for n in range(1, 5):
        board(m["id"], 1, n, "player1", 2)
    for n in range(1, 5):
        board(m["id"], 2, n, "player2", 9)
    row = adm.table("matches").select("*").eq("id", m["id"]).execute().data[0]
    ok("match stays open with a set still to play",
       row.get("status") != "completed" and not row.get("winner_id"),
       "status={} winner={}".format(row.get("status"), row.get("winner_name")),
       needs_006=True)
    for n in range(1, 5):
        board(m["id"], 3, n, "player1", 2)

    row = adm.table("matches").select("*").eq("id", m["id"]).execute().data[0]
    p1, p2 = row.get("player1_total_points"), row.get("player2_total_points")
    ok("player 2 leads on total points {}-{}".format(p1, p2), p2 > p1, (p1, p2), needs_006=True)
    ok("but player 1 wins the match 2-1 on sets",
       (row.get("player1_sets_won"), row.get("player2_sets_won")) == (2, 1)
       and row.get("winner_id") == m["player1Id"],
       (row.get("player1_sets_won"), row.get("player2_sets_won"), row.get("winner_name")),
       needs_006=True)
    ok("match is completed once every set is in",
       row.get("status") == "completed", row.get("status"), needs_006=True)

    print("\n" + "=" * 70)
    print("THE SETS ENDPOINT")
    print("=" * 70)
    r = api("GET", "/matches/{}/sets".format(m["id"]))
    ok("GET /sets -> {}".format(r.status_code), r.status_code == 200, r.text[:160])
    if r.status_code == 200:
        body = r.json()
        ok("reports 3 sets", body.get("numberOfSets") == 3, body.get("numberOfSets"), needs_006=True)
        if SUPPORTED:
            ok("set 1 went to player 1",
               body["sets"][0]["winnerName"] == m["player1Name"], body["sets"][0])
            ok("set 2 went to player 2",
               body["sets"][1]["winnerName"] == m["player2Name"], body["sets"][1])

    print("\n" + "=" * 70)
    print("COIN COLOURS AND SIDE SWITCHING")
    print("=" * 70)
    tid2, m2 = build(1, 3)
    r = api("POST", "/matches/{}/sides".format(m2["id"]),
            json={"player1Color": "white", "player2Color": "black", "tableNumber": 4})
    ok("sides accepted -> {}".format(r.status_code), r.status_code == 200, r.text[:160],
       needs_006=True)
    if SUPPORTED and r.status_code == 200:
        row = adm.table("matches").select("*").eq("id", m2["id"]).execute().data[0]
        ok("colours stored against the player ids",
           (row.get("player1_color"), row.get("player2_color")) == ("white", "black"),
           (row.get("player1_color"), row.get("player2_color")))
        ok("table number stored", row.get("table_number") == 4, row.get("table_number"))

        before = (row["player1_id"], row["player2_id"], row["player1_color"])
        api("POST", "/matches/{}/sides".format(m2["id"]), json={"sidesSwapped": True})
        row = adm.table("matches").select("*").eq("id", m2["id"]).execute().data[0]
        ok("swapping sides does not move player ids or colours",
           (row["player1_id"], row["player2_id"], row["player1_color"]) == before,
           (row["player1_id"], row["player2_id"], row["player1_color"]))
        ok("the swap flag is recorded", row.get("sides_swapped") is True, row.get("sides_swapped"))

    r = api("POST", "/matches/{}/sides".format(m2["id"]),
            json={"player1Color": "black", "player2Color": "black"})
    ok("both sides cannot play the same colour -> {}".format(r.status_code),
       r.status_code == 422, r.text[:160])

    print("\n" + "=" * 70)
    print("A ONE-SET MATCH IS UNCHANGED")
    print("=" * 70)
    tid3, m3 = build(1, 3)
    rows = adm.table("boards").select("*").eq("match_id", m3["id"]).execute().data or []
    ok("3 boards, no set layer", len(rows) == 3, len(rows))
    for n in (1, 2):
        board(m3["id"], 1, n, "player1", 5)
    row = adm.table("matches").select("*").eq("id", m3["id"]).execute().data[0]
    ok("still open at 2 of 3 boards under remaining-coins",
       row.get("status") != "completed", row.get("status"))
    board(m3["id"], 1, 3, "player1", 5)
    row = adm.table("matches").select("*").eq("id", m3["id"]).execute().data[0]
    ok("completes on the last board", row.get("status") == "completed", row.get("status"))

finally:
    cleanup()

print("\n" + "=" * 70)
if not SUPPORTED:
    print("MIGRATION 006 IS NOT APPLIED - apply backend/db/migrations/APPLY_PENDING.sql")
print("RESULTS: {} failure(s)".format(len(failures)))
for f in failures:
    print("  - " + f)
if not failures:
    print("ALL SET CHECKS PASSED")

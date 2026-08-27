"""Remaining-coins board scoring, end to end. Creates its own fixture, deletes it after."""
import os, sys
import uuid, requests
sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _session
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
adm = get_admin_db()
created, failures, H = [], [], {}
MIGRATED = True


def ok(label, cond, detail="", needs_005=False):
    if needs_005 and not MIGRATED:
        print("  SKIP  {} (needs migration 005)".format(label)); return
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + repr(detail)) if (detail is not None and detail != "" and not cond) else ""))
    if not cond:
        failures.append(label)


def api(m, path, **kw):
    return _session.request(m, path, H, **kw)


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created:
        try: adm.table("tournaments").delete().eq("id", tid).execute()
        except Exception: pass
    for r in (adm.table("profiles").select("id").ilike("email", "%bs" + RUN + "%").execute().data or []):
        try: adm.auth.admin.delete_user(r["id"])
        except Exception: pass
    print("  done")


_seq = [0]


def build(boards=8, rules_extra=None):
    """A 2-player knockout under remaining-coins scoring; returns (tid, match)."""
    _seq[0] += 1
    tag = str(_seq[0])
    rules = {
        "maxBoardsPerMatch": boards, "targetScore": 29, "queenPoints": 3,
        "scoringMode": "remaining_coins", "coinsPerSide": 9,
        "queenMustBeCovered": True, "queenAwardTo": "coverer",
        "tieBreak": "additional_board",
    }
    rules.update(rules_extra or {})
    r = api("POST", "/tournaments", json={
        "name": "BoardScore {} {}".format(tag, RUN), "category": "singles",
        "format": "knockout", "registrationStartDate": "2026-09-01",
        "registrationEndDate": "2026-09-05", "tournamentStartDate": "2026-09-10",
        "tournamentEndDate": "2026-09-12", "venue": "V", "city": "Pune",
        "numberOfBoards": 1, "rules": rules, "status": "registration_open"})
    assert r.status_code == 200, r.text[:250]
    tid = r.json()["id"]; created.append(tid)
    for i in range(2):
        p = api("POST", "/players", json={
            "name": "BS{} P{}".format(tag, i),
            "email": "bs{}_{}_{}@carromarena.com".format(RUN, tag, i)}).json()
        api("POST", "/tournaments/{}/registrations".format(tid),
            json={"type": "singles", "playerId": p["id"]})
    api("PUT", "/tournaments/" + tid, json={"status": "registration_closed"})
    api("POST", "/tournaments/{}/fixtures".format(tid))
    match = api("GET", "/fixtures/" + tid).json()[0]
    api("POST", "/matches/{}/start".format(match["id"]))
    return tid, match


def board_row(match_id, n):
    rows = (adm.table("boards").select("*").eq("match_id", match_id)
            .eq("board_number", n).execute().data)
    return rows[0] if rows else {}


def submit(match_id, n, **kw):
    payload = {"p1Score": 0, "p2Score": 0, "auditReason": "test"}
    payload.update(kw)
    r = api("POST", "/matches/{}/boards/{}/submit".format(match_id, n), json=payload)
    if r.status_code != 200:
        # A rejected write leaves the board at 0-0, which then reads as a wrong
        # SCORE rather than a failed request. Surface it as what it is.
        ok("submit board {} -> {}".format(n, r.status_code), False, r.text[:200])
    return r


try:
    r = requests.post(BASE + "/auth/signup", json={
        "email": "bs{}_admin@carromarena.com".format(RUN), "password": "TestPass2345x",
        "name": "Board Admin", "role": "admin"}, timeout=90)
    assert r.status_code == 200, r.text[:250]
    H["Authorization"] = "Bearer " + r.json()["access_token"]
    _session.remember("bs{}_admin@carromarena.com".format(RUN), "TestPass2345x")

    # ---------------------------------------------------------------- setup
    tid, m = build(boards=8)
    probe = submit(m["id"], 1, boardWinner="player1", coinsRemainingWith="player2",
                   coinsRemaining=4, queenPocketedBy="player1", queenCoveredBy="player1")
    try:
        adm.table("boards").select("board_winner").limit(1).execute()
    except Exception:
        MIGRATED = False

    print("=" * 70)
    print("THE SCREENSHOT CASE — winner, queen and coins are three separate facts")
    print("=" * 70)
    # Player 1 finished the board. Player 2 covered the queen. Player 2 has 2
    # coins left. Under the old modal this could not be entered at all.
    tid2, m2 = build(boards=8)
    r = submit(m2["id"], 1,
               boardWinner="player1", coinsRemainingWith="player2", coinsRemaining=2,
               queenPocketedBy="player1", queenCoveredBy="player2")
    ok("submit -> {}".format(r.status_code), r.status_code == 200, r.text[:220])
    if r.status_code == 200:
        b = board_row(m2["id"], 1)
        ok("winner is player1 (2 coins left, so 2 base)", b.get("player1_score") == 2,
           "got {}".format(b.get("player1_score")))
        ok("player2 loses the board and still scores the queen (3)",
           b.get("player2_score") == 3, "got {}".format(b.get("player2_score")))
        ok("board_winner stored as player1", b.get("board_winner") == "player1",
           b.get("board_winner"), needs_005=True)
        ok("queen_pocketed_by and queen_covered_by differ",
           b.get("queen_pocketed_by") == "player1" and b.get("queen_covered_by") == "player2",
           (b.get("queen_pocketed_by"), b.get("queen_covered_by")), needs_005=True)
        ok("the split is recorded as a warning, not silently resolved",
           bool(b.get("scoring_warnings")), b.get("scoring_warnings"), needs_005=True)

    print("\n" + "=" * 70)
    print("SPEC SECTION 15 — the full 8-board scorecard, through the API")
    print("=" * 70)
    tid3, m3 = build(boards=8)
    SPEC = [(8, 5, "player1"), (6, 8, "player2"), (9, 4, "player1"), (3, 8, "player2"),
            (7, 5, "player1"), (4, 7, "player2"), (8, 5, "player1"), (6, 7, "player2")]
    EXPECT = [(7, 0), (0, 6), (8, 0), (0, 9), (7, 0), (0, 8), (7, 0), (0, 6)]
    for i, ((p1c, p2c, w), (e1, e2)) in enumerate(zip(SPEC, EXPECT), 1):
        loser = "player2" if w == "player1" else "player1"
        left = 9 - (p2c if w == "player1" else p1c)
        rr = submit(m3["id"], i, boardWinner=w, coinsRemainingWith=loser, coinsRemaining=left,
                    queenPocketedBy=w, queenCoveredBy=w,
                    p1CoinsPocketed=p1c, p2CoinsPocketed=p2c)
        b = board_row(m3["id"], i)
        got = (b.get("player1_score"), b.get("player2_score"))
        ok("board {}: {}/{} coins -> {}".format(i, p1c, p2c, got), got == (e1, e2),
           "expected {} ({})".format((e1, e2), rr.text[:120]))

        row = adm.table("matches").select("*").eq("id", m3["id"]).execute().data[0]
        if i < 8:
            ok("  match still open after board {} of 8".format(i),
               row.get("status") != "completed" and not row.get("winner_id"),
               "status={} winner={}".format(row.get("status"), row.get("winner_name")))

    row = adm.table("matches").select("*").eq("id", m3["id"]).execute().data[0]
    ok("final totals are the spec's 29-29",
       (row.get("player1_total_points"), row.get("player2_total_points")) == (29, 29),
       (row.get("player1_total_points"), row.get("player2_total_points")))
    ok("a 29-29 draw asks for a tie-break instead of inventing a winner",
       row.get("tie_break_required") is True and not row.get("winner_id"),
       "tie_break_required={} winner={}".format(row.get("tie_break_required"), row.get("winner_name")), needs_005=True)
    ok("the configured tie-break rule is recorded",
       row.get("tie_break_rule") == "additional_board", row.get("tie_break_rule"), needs_005=True)

    print("\n" + "=" * 70)
    print("SPEC SECTION 26.15 — no early finish on board wins")
    print("=" * 70)
    tid4, m4 = build(boards=8)
    for i in range(1, 6):   # player 1 wins the first five boards outright
        submit(m4["id"], i, boardWinner="player1", coinsRemainingWith="player2",
               coinsRemaining=5, queenPocketedBy="none", queenCoveredBy="none")
    row = adm.table("matches").select("*").eq("id", m4["id"]).execute().data[0]
    ok("5 board wins out of 8 does not end the match",
       row.get("status") != "completed" and not row.get("winner_id"),
       "status={} winner={}".format(row.get("status"), row.get("winner_name")))
    for i in range(6, 9):
        submit(m4["id"], i, boardWinner="player2", coinsRemainingWith="player1",
               coinsRemaining=9, queenPocketedBy="player2", queenCoveredBy="player2")
    row = adm.table("matches").select("*").eq("id", m4["id"]).execute().data[0]
    ok("all 8 played: 25-36 on points, so player 2 takes it despite losing 5 boards",
       (row.get("player1_total_points"), row.get("player2_total_points")) == (25, 36)
       and row.get("winner_id") == m4["player2Id"],
       (row.get("player1_total_points"), row.get("player2_total_points"), row.get("winner_name")))
    ok("match is completed once every board is in", row.get("status") == "completed", row.get("status"))

    print("\n" + "=" * 70)
    print("SPEC SECTION 17 — queen scenarios through the API")
    print("=" * 70)
    tid5, m5 = build(boards=1)
    r = submit(m5["id"], 1, boardWinner="player1", coinsRemainingWith="player2", coinsRemaining=4,
               queenPocketedBy="player1", queenCoveredBy="none")
    b = board_row(m5["id"], 1)
    ok("B: pocketed but not covered scores nothing (4, not 7)",
       b.get("player1_score") == 4, b.get("player1_score"))
    ok("B: the queen is recorded as returned",
       b.get("queen_status") == "returned", b.get("queen_status"), needs_005=True)

    tid6, m6 = build(boards=1)
    submit(m6["id"], 1, boardWinner="player1", coinsRemainingWith="player2", coinsRemaining=4,
           queenPocketedBy="none", queenCoveredBy="none")
    b = board_row(m6["id"], 1)
    ok("finishing the board does not hand over the queen (scores 4, no bonus)",
       b.get("player1_score") == 4, b.get("player1_score"))

    print("\n" + "=" * 70)
    print("SECTION 18 — penalties")
    print("=" * 70)
    tid7, m7 = build(boards=1)
    submit(m7["id"], 1, boardWinner="player1", coinsRemainingWith="player2", coinsRemaining=4,
           queenPocketedBy="player1", queenCoveredBy="player1", p1Penalty=1)
    b = board_row(m7["id"], 1)
    ok("4 base + 3 queen - 1 penalty = 6", b.get("player1_score") == 6, b.get("player1_score"))

    print("\n" + "=" * 70)
    print("SECTION 20 - A CONFIRMED BOARD IS LOCKED")
    print("=" * 70)
    tid8, m8 = build(boards=2)
    submit(m8["id"], 1, boardWinner="player1", coinsRemainingWith="player2", coinsRemaining=5,
           queenPocketedBy="none", queenCoveredBy="none")
    b = board_row(m8["id"], 1)
    ok("a submitted board is marked confirmed", b.get("locked") is True, b.get("locked"), needs_005=True)

    r = api("PUT", "/matches/{}/boards/1?reason=oops".format(m8["id"]),
            json={"boardNumber": 1, "status": "completed",
                  "player1Score": 99, "player2Score": 0})
    ok("changing it without an override is refused -> {}".format(r.status_code),
       r.status_code == 409, r.text[:160], needs_005=True)
    if MIGRATED:
        b = board_row(m8["id"], 1)
        ok("the refused edit changed nothing", b.get("player1_score") == 5, b.get("player1_score"))

    r = api("PUT", "/matches/{}/boards/1?reason=referee+review&override=true".format(m8["id"]),
            json={"boardNumber": 1, "status": "completed",
                  "player1Score": 0, "player2Score": 0})
    ok("an explicit override is accepted -> {}".format(r.status_code),
       r.status_code == 200, r.text[:160], needs_005=True)
    if MIGRATED and r.status_code == 200:
        logs = api("GET", "/audit/scores/" + m8["id"]).json()
        ok("the override is named as such in the audit trail",
           any("OVERRIDE" in (l.get("reason") or "") for l in logs),
           [l.get("reason") for l in logs][:3])
    print("\n" + "=" * 70)
    print("EDITING A SUBMITTED SCORE ACTUALLY CHANGES IT")
    print("=" * 70)
    tid9, m9 = build(boards=2)
    submit(m9["id"], 1, boardWinner="player1", coinsRemainingWith="player2", coinsRemaining=5,
           queenPocketedBy="player1", queenCoveredBy="player1")
    b = board_row(m9["id"], 1)
    ok("submitted as 5 coins + 3 queen = 8", b.get("player1_score") == 8, b.get("player1_score"))

    # The umpire re-counts: it was 3 coins, and the opponent covered the queen.
    r = api("PUT", "/matches/{}/boards/1?reason=recount&override=true".format(m9["id"]),
            json={"boardNumber": 1, "status": "completed",
                  "player1Score": 0, "player2Score": 0,
                  "boardWinner": "player1", "coinsRemainingWith": "player2",
                  "coinsRemaining": 3,
                  "queenPocketedBy": "player1", "queenCoveredBy": "player2"})
    ok("correction accepted -> {}".format(r.status_code), r.status_code == 200, r.text[:200])
    b = board_row(m9["id"], 1)
    ok("the edit took: winner now scores 3, not 8", b.get("player1_score") == 3,
       b.get("player1_score"))
    ok("and the queen moved to the opponent: 3", b.get("player2_score") == 3,
       b.get("player2_score"))

    row = adm.table("matches").select("*").eq("id", m9["id"]).execute().data[0]
    ok("match totals follow the correction", 
       (row.get("player1_total_points"), row.get("player2_total_points")) == (3, 3),
       (row.get("player1_total_points"), row.get("player2_total_points")))

    # A second correction must read the corrected board, not the original.
    r = api("PUT", "/matches/{}/boards/1?reason=again&override=true".format(m9["id"]),
            json={"boardNumber": 1, "status": "completed",
                  "player1Score": 0, "player2Score": 0, "coinsRemaining": 7})
    b = board_row(m9["id"], 1)
    ok("a second edit builds on the first (7 coins -> 7)",
       b.get("player1_score") == 7, b.get("player1_score"), needs_005=True)
    print("\n" + "=" * 70)
    print("CLASSIC TOURNAMENTS ARE UNCHANGED")
    print("=" * 70)
    r = api("POST", "/tournaments", json={
        "name": "Classic " + RUN, "category": "singles", "format": "knockout",
        "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-12",
        "venue": "V", "city": "Pune", "numberOfBoards": 1,
        "rules": {"maxBoardsPerMatch": 3, "targetScore": 29, "queenPoints": 3},
        "status": "registration_open"})
    ctid = r.json()["id"]; created.append(ctid)
    for i in range(2):
        p = api("POST", "/players", json={"name": "BSC{} P{}".format(RUN, i),
                                          "email": "bs{}_c{}@carromarena.com".format(RUN, i)}).json()
        api("POST", "/tournaments/{}/registrations".format(ctid),
            json={"type": "singles", "playerId": p["id"]})
    api("PUT", "/tournaments/" + ctid, json={"status": "registration_closed"})
    api("POST", "/tournaments/{}/fixtures".format(ctid))
    cm = api("GET", "/fixtures/" + ctid).json()[0]
    api("POST", "/matches/{}/start".format(cm["id"]))
    api("POST", "/matches/{}/boards/1/submit".format(cm["id"]),
        json={"p1Score": 21, "p2Score": 15, "queenClaimedBy": "player1",
              "queenCovered": True, "auditReason": "classic"})
    b = board_row(cm["id"], 1)
    ok("a tournament without scoringMode still scores 21+3 = 24 vs 15",
       (b.get("player1_score"), b.get("player2_score")) == (24, 15),
       (b.get("player1_score"), b.get("player2_score")))

finally:
    cleanup()

print("\n" + "=" * 70)
if not MIGRATED:
    print("MIGRATION 005 IS NOT APPLIED — apply backend/db/migrations/005_board_detail.sql")
print("RESULTS: {} failure(s)".format(len(failures)))
for f in failures:
    print("  - " + f)
if not failures:
    print("ALL BOARD-SCORING CHECKS PASSED")

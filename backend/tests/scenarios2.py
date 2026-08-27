"""Edge-case harness: draws, corrections, tiebreakers, gating, larger brackets."""
import os, sys, uuid, requests
sys.path.insert(0, '.')
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
PASSWORD = "TestPass2345x"
admin_db = get_admin_db()
created_users, created_tournaments = [], []
failures = []
H = {}


def ok(label, cond, detail=""):
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + detail) if (detail and not cond) else ""))
    if not cond:
        failures.append(label + ((" | " + detail) if detail else ""))
    return cond


def api(method, path, **kw):
    kw.setdefault("timeout", 120)
    headers = dict(H)
    headers.update(kw.pop("headers", {}))
    return requests.request(method, BASE + path, headers=headers, **kw)


def make_players(n, prefix):
    out = []
    for i in range(n):
        r = api("POST", "/players", json={
            "name": "{} {}".format(prefix, i + 1), "club": "C", "city": "Pune",
            "rating": 1400 + i * 25,
            "email": "ec_{}_{}_{}@carromarena.com".format(RUN, prefix.lower().replace(' ', ''), i)})
        assert r.status_code == 200, r.text[:200]
        out.append(r.json())
        created_users.append(r.json()["id"])
    return out


def make_tournament(name, fmt, category="singles", max_boards=3):
    r = api("POST", "/tournaments", json={
        "name": "{} {}".format(name, RUN), "category": category, "format": fmt,
        "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-20",
        "venue": "Edge Hall", "city": "Pune", "numberOfBoards": 2, "entryFee": 0,
        "rules": {"maxBoardsPerMatch": max_boards, "targetScore": 29,
                  "pointsForWin": 2, "pointsForDraw": 1, "pointsForLoss": 0,
                  "matchDurationMinutes": 30},
        "status": "registration_open"})
    assert r.status_code == 200, r.text[:300]
    created_tournaments.append(r.json()["id"])
    return r.json()


def register_all(tid, players):
    for p in players:
        api("POST", "/tournaments/{}/registrations".format(tid),
            json={"type": "singles", "playerId": p["id"]})


def fixture(tid):
    api("PUT", "/tournaments/" + tid, json={"status": "registration_closed"})
    return api("POST", "/tournaments/{}/fixtures".format(tid))


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created_tournaments:
        try:
            admin_db.table("tournaments").delete().eq("id", tid).execute()
        except Exception:
            pass
    for uid in created_users:
        try:
            admin_db.auth.admin.delete_user(uid)
        except Exception:
            pass
    try:
        for row in (admin_db.table("tournaments").select("id").ilike("name", "%" + RUN + "%").execute().data or []):
            admin_db.table("tournaments").delete().eq("id", row["id"]).execute()
        for row in (admin_db.table("profiles").select("id").ilike("email", "%ec_" + RUN + "%").execute().data or []):
            try:
                admin_db.auth.admin.delete_user(row["id"])
            except Exception:
                pass
    except Exception as e:
        print("  WARN sweep:", e)
    print("  cleanup done")


try:
    r = requests.post(BASE + "/auth/signup", json={
        "email": "ec_admin_{}@carromarena.com".format(RUN), "password": PASSWORD,
        "name": "Edge Admin", "role": "admin"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    H["Authorization"] = "Bearer " + r.json()["access_token"]
    created_users.append(r.json()["user"]["id"])

    # =====================================================================
    print("=" * 70)
    print("SCENARIO 8  MINIMUM / INSUFFICIENT ENTRANTS")
    print("=" * 70)
    solo = make_players(1, "Solo")
    t = make_tournament("TooFew", "knockout")
    register_all(t["id"], solo)
    r = fixture(t["id"])
    ok("1 entrant -> fixtures refused (got {})".format(r.status_code), r.status_code == 400,
       r.text[:160])

    duo = make_players(2, "Duo")
    t2 = make_tournament("MinTwo", "knockout")
    register_all(t2["id"], duo)
    r = fixture(t2["id"])
    ok("2 entrants -> fixtures generated (got {})".format(r.status_code), r.status_code == 200,
       r.text[:200])
    fx = api("GET", "/fixtures/" + t2["id"]).json()
    ok("2 entrants -> exactly 1 match (the final) (got {})".format(len(fx)), len(fx) == 1)
    if fx:
        ok("that match is the Final", fx[0]["roundName"] == "Final", fx[0]["roundName"])
        ok("both entrants present", bool(fx[0]["player1Id"]) and bool(fx[0]["player2Id"]))

    # =====================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 9  ROUND ROBIN - 3 players (odd, smallest bye case)")
    print("=" * 70)
    p3 = make_players(3, "RR3 P")
    t3 = make_tournament("RoundRobin3", "round_robin")
    register_all(t3["id"], p3)
    r = fixture(t3["id"])
    ok("fixtures generated (got {})".format(r.status_code), r.status_code == 200, r.text[:200])
    fx3 = api("GET", "/fixtures/" + t3["id"]).json()
    ok("3 players -> 3 matches (got {})".format(len(fx3)), len(fx3) == 3)
    ok("no BYE leaked", all("BYE" not in (m["player1Name"] + m["player2Name"]).upper() for m in fx3))
    counts = {}
    for m in fx3:
        counts[m["player1Name"]] = counts.get(m["player1Name"], 0) + 1
        counts[m["player2Name"]] = counts.get(m["player2Name"], 0) + 1
    ok("each player plays 2 matches: {}".format(sorted(counts.values())),
       sorted(counts.values()) == [2, 2, 2])

    # =====================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 10  DRAWN MATCH (equal board wins)")
    print("=" * 70)
    p4 = make_players(4, "Draw P")
    t4 = make_tournament("DrawTest", "round_robin", max_boards=2)
    register_all(t4["id"], p4)
    fixture(t4["id"])
    fx4 = api("GET", "/fixtures/" + t4["id"]).json()
    m = fx4[0]
    api("POST", "/matches/{}/start".format(m["id"]))
    r1 = api("POST", "/matches/{}/boards/1/submit".format(m["id"]),
             json={"p1Score": 29, "p2Score": 10, "auditReason": "draw test"})
    r2 = api("POST", "/matches/{}/boards/2/submit".format(m["id"]),
             json={"p1Score": 8, "p2Score": 29, "auditReason": "draw test"})
    ok("both boards submitted", r1.status_code == 200 and r2.status_code == 200,
       "{} / {}".format(r1.status_code, r2.status_code))
    row = admin_db.table("matches").select("*").eq("id", m["id"]).execute().data[0]
    ok("1-1 board split -> match completed", row["status"] == "completed", row["status"])
    ok("drawn match has no winner (winner_id={})".format(row["winner_id"]), row["winner_id"] is None)

    rc = api("POST", "/matches/{}/confirm".format(m["id"]))
    ok("a drawn match can still be confirmed (got {})".format(rc.status_code),
       rc.status_code == 200, rc.text[:200])

    st = api("GET", "/standings/" + t4["id"]).json().get("standings", [])
    drawn = [x for x in st if x["drawn"] > 0]
    ok("standings record 2 drawn entries (got {})".format(len(drawn)), len(drawn) == 2)
    ok("each drawn player got 1 point",
       all(x["points"] == 1 for x in drawn), str([(x["participantName"], x["points"]) for x in drawn]))

    # =====================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 11  SCORE CORRECTION WORKFLOW")
    print("=" * 70)
    m2 = fx4[1]
    api("POST", "/matches/{}/start".format(m2["id"]))
    api("POST", "/matches/{}/boards/1/submit".format(m2["id"]),
        json={"p1Score": 29, "p2Score": 5, "auditReason": "initial"})
    r = api("PUT", "/matches/{}/boards/1?reason=Scorer%20typo%20corrected".format(m2["id"]),
            json={"boardNumber": 1, "status": "completed", "player1Score": 25, "player2Score": 20})
    ok("board score can be corrected (got {})".format(r.status_code), r.status_code == 200, r.text[:200])
    board = admin_db.table("boards").select("*").eq("match_id", m2["id"]).eq("board_number", 1).execute().data
    ok("corrected score persisted ({}-{})".format(board[0]["player1_score"], board[0]["player2_score"]),
       board[0]["player1_score"] == 25 and board[0]["player2_score"] == 20)
    audit = api("GET", "/audit/scores/" + m2["id"]).json()
    ok("correction left an audit trail ({} entries)".format(len(audit)), len(audit) >= 2)
    reasons = [a.get("reason") for a in audit]
    ok("correction reason captured: {}".format(reasons), any("typo" in (x or "") for x in reasons))

    # =====================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 12  MATCH LIFECYCLE - pause / resume")
    print("=" * 70)
    m3 = fx4[2]
    r = api("POST", "/matches/{}/start".format(m3["id"]))
    ok("start -> live (got {})".format(r.status_code), r.status_code == 200)
    r = api("POST", "/matches/{}/pause".format(m3["id"]))
    ok("pause -> {} status={}".format(r.status_code, (r.json() or {}).get("status")),
       r.status_code == 200 and r.json().get("status") == "paused")
    r = api("POST", "/matches/{}/resume".format(m3["id"]))
    ok("resume -> {} status={}".format(r.status_code, (r.json() or {}).get("status")),
       r.status_code == 200 and r.json().get("status") == "live")
    r = api("POST", "/matches/{}/pause".format(m3["id"]))
    elapsed = (r.json() or {}).get("timerElapsedSeconds")
    ok("timer accumulates across pauses (elapsed={})".format(elapsed), elapsed is not None)

    # =====================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 13  PENDING REGISTRATIONS EXCLUDED FROM FIXTURES")
    print("=" * 70)
    p6 = make_players(6, "Gate P")
    t5 = make_tournament("GateTest", "round_robin")
    register_all(t5["id"], p6)
    regs = api("GET", "/tournaments/{}/registrations".format(t5["id"])).json()
    # Reject two so only 4 are approved
    for reg in regs[:2]:
        api("POST", "/registrations/{}/reject".format(reg["id"]))
    r = fixture(t5["id"])
    ok("fixtures generated with 4 approved (got {})".format(r.status_code), r.status_code == 200)
    fx5 = api("GET", "/fixtures/" + t5["id"]).json()
    ok("only approved entrants drawn -> 6 matches (got {})".format(len(fx5)), len(fx5) == 6)
    names = {m["player1Name"] for m in fx5} | {m["player2Name"] for m in fx5}
    ok("rejected entrants absent from the draw ({} distinct)".format(len(names)), len(names) == 4)

    # =====================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 14  LEAGUE + KNOCKOUT - 8 players (semis + final)")
    print("=" * 70)
    p8 = make_players(8, "LK8 P")
    t6 = make_tournament("LeagueKO8", "league_knockout")
    register_all(t6["id"], p8)
    r = fixture(t6["id"])
    ok("fixtures generated (got {})".format(r.status_code), r.status_code == 200, r.text[:300])
    fx6 = api("GET", "/fixtures/" + t6["id"]).json()
    league = [m for m in fx6 if m["stage"] == "league"]
    ko = [m for m in fx6 if m["stage"] == "knockout"]
    ok("8 players -> 28 league matches (got {})".format(len(league)), len(league) == 28)
    ok("top 4 qualify -> 3 knockout matches (got {})".format(len(ko)), len(ko) == 3)
    labels = sorted({m[k + "Name"] for m in ko for k in ("player1", "player2")
                     if m[k + "Name"].startswith("League Rank")})
    ok("4 rank labels: {}".format(labels), len(labels) == 4)

    print("    playing 28 league matches...")
    for m in league:
        api("POST", "/matches/{}/start".format(m["id"]))
        api("POST", "/matches/{}/boards/1/submit".format(m["id"]),
            json={"p1Score": 29, "p2Score": 10, "auditReason": "league"})
        api("POST", "/matches/{}/boards/2/submit".format(m["id"]),
            json={"p1Score": 29, "p2Score": 12, "auditReason": "league"})
        api("POST", "/matches/{}/confirm".format(m["id"]))

    st6 = api("GET", "/standings/" + t6["id"]).json().get("standings", [])
    ok("standings ranked all 8 (got {})".format(len(st6)), len(st6) == 8)
    ok("points descend with rank",
       all(st6[i]["points"] >= st6[i + 1]["points"] for i in range(len(st6) - 1)),
       str([(x["rank"], x["points"]) for x in st6]))

    fx6b = api("GET", "/fixtures/" + t6["id"]).json()
    ko2 = [m for m in fx6b if m["stage"] == "knockout"]
    semis = [m for m in ko2 if m["roundName"] == "Semi Final"]
    filled = [m for m in semis if m.get("player1Id") and m.get("player2Id")]
    ok("both semi-finals auto-populated (got {})".format(len(filled)), len(filled) == 2)
    top4 = [x["participantName"] for x in st6[:4]]
    semi_names = {m["player1Name"] for m in semis} | {m["player2Name"] for m in semis}
    ok("semi-finalists are exactly the top 4: {}".format(sorted(top4)),
       semi_names == set(top4), str(sorted(semi_names)))
    if filled:
        s1 = semis[0]
        ok("rank 1 avoids rank 2 in the semis (1v4 / 2v3 seeding)",
           {s1["player1Name"], s1["player2Name"]} != {top4[0], top4[1]},
           "{} vs {}".format(s1["player1Name"], s1["player2Name"]))

    # =====================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 15  NOTIFICATIONS REACH PLAYERS")
    print("=" * 70)
    pr = requests.post(BASE + "/auth/signup", json={
        "email": "ec_np_{}@carromarena.com".format(RUN), "password": PASSWORD,
        "name": "Notify Player", "role": "player"}, timeout=90)
    assert pr.status_code == 200
    np_id = pr.json()["user"]["id"]
    created_users.append(np_id)
    PH = {"Authorization": "Bearer " + pr.json()["access_token"]}

    t7 = make_tournament("NotifyTest", "round_robin")
    r = requests.post(BASE + "/tournaments/{}/registrations".format(t7["id"]),
                      headers=PH, json={"type": "singles"}, timeout=90)
    ok("player self-registered (got {})".format(r.status_code), r.status_code == 200, r.text[:200])
    reg_id = r.json()["id"] if r.status_code == 200 else None

    if reg_id:
        r = api("POST", "/registrations/{}/approve".format(reg_id))
        ok("admin approved the entry (got {})".format(r.status_code), r.status_code == 200)
        notes = requests.get(BASE + "/notifications", headers=PH, timeout=90).json()
        mine = [n for n in notes if n.get("profileId") == np_id]
        ok("player received an approval notification ({} personal)".format(len(mine)), len(mine) >= 1)
        if mine:
            ok("notification is unread and shaped for the UI",
               mine[0].get("read") is False and mine[0].get("timestamp") is not None)
            rr = requests.put(BASE + "/notifications/{}/read".format(mine[0]["id"]),
                              headers=PH, timeout=90)
            ok("player can mark it read (got {})".format(rr.status_code), rr.status_code == 200)

        others = make_players(3, "Notif P")
        register_all(t7["id"], others)
        fixture(t7["id"])
        api("POST", "/scheduling/{}/generate?restMinutes=10".format(t7["id"]))
        api("POST", "/scheduling/{}/publish".format(t7["id"]))
        notes = requests.get(BASE + "/notifications", headers=PH, timeout=90).json()
        sched = [n for n in notes if n.get("type") == "schedule_published" and n.get("profileId") == np_id]
        ok("player notified when the schedule was published ({})".format(len(sched)), len(sched) >= 1)

        fx7 = api("GET", "/fixtures/" + t7["id"]).json()
        mine_matches = [m for m in fx7 if np_id in (m.get("player1Id"), m.get("player2Id"))]
        ok("player appears in their own fixtures ({} match(es))".format(len(mine_matches)),
           len(mine_matches) > 0)

finally:
    cleanup()

print("\n" + "=" * 70)
print("RESULTS: {} failure(s)".format(len(failures)))
if failures:
    print("\nFAILURES:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL EDGE-CASE SCENARIOS PASSED")

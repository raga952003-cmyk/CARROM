"""End-to-end smoke test. Creates temporary data, then removes all of it."""
import os, sys, uuid, requests
sys.path.insert(0, '.')
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
TAG = uuid.uuid4().hex[:8]
PASSWORD = "TestPass2345x"
admin_db = get_admin_db()
created_users, created_tournaments = [], []
failures = []


def ok(label, cond):
    print("  {}  {}".format("PASS" if cond else "FAIL", label))
    if not cond:
        failures.append(label)
    return cond


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created_tournaments:
        admin_db.table("tournaments").delete().eq("id", tid).execute()
    print("  deleted {} tournament(s) (matches/boards/registrations cascade)".format(len(created_tournaments)))
    n = 0
    for uid in created_users:
        try:
            admin_db.auth.admin.delete_user(uid)
            n += 1
        except Exception as e:
            print("  WARN could not delete user {}: {}".format(uid, e))
    print("  deleted {} auth user(s) (profiles/notifications cascade)".format(n))

    # Safety sweep: catch anything created before a mid-run failure.
    try:
        leftover_t = admin_db.table("tournaments").select("id, name").ilike(
            "name", "%{}%".format(TAG)).execute().data or []
        for row in leftover_t:
            admin_db.table("tournaments").delete().eq("id", row["id"]).execute()
        leftover_p = admin_db.table("profiles").select("id, email").ilike(
            "email", "%{}%".format(TAG)).execute().data or []
        for row in leftover_p:
            try:
                admin_db.auth.admin.delete_user(row["id"])
            except Exception:
                pass
        print("  sweep removed {} stray tournament(s), {} stray profile(s)".format(
            len(leftover_t), len(leftover_p)))
    except Exception as e:
        print("  WARN sweep failed: {}".format(e))


try:
    print("=== AUTH ===")
    admin_email = "e2e_admin_{}@carromarena.com".format(TAG)
    r = requests.post(BASE + "/auth/signup", json={
        "email": admin_email, "password": PASSWORD, "name": "E2E Admin",
        "role": "admin", "club": "QA", "city": "Pune"}, timeout=90)
    assert r.status_code == 200, "signup {} {}".format(r.status_code, r.text[:300])
    admin_tok = r.json()["access_token"]
    admin_id = r.json()["user"]["id"]
    created_users.append(admin_id)
    ok("admin signup + profile trigger", r.json()["user"]["role"] == "admin")

    A = {"Authorization": "Bearer " + admin_tok}

    r = requests.post(BASE + "/auth/login", json={
        "email": admin_email, "password": PASSWORD, "role": "player"}, timeout=90)
    ok("role mismatch returns 403 not 400 (got {})".format(r.status_code), r.status_code == 403)

    print("\n=== PLAYERS ===")
    player_ids = []
    for i in range(4):
        r = requests.post(BASE + "/players", headers=A, json={
            "name": "E2E Player {} {}".format(i + 1, TAG), "club": "QA Club", "city": "Pune",
            "rating": 1500 + i * 10,
            "email": "e2e_p{}_{}@carromarena.com".format(i, TAG)}, timeout=90)
        assert r.status_code == 200, "create player {} {}".format(r.status_code, r.text[:300])
        pid = r.json()["id"]
        player_ids.append(pid)
        created_users.append(pid)
    ok("4 players created", len(player_ids) == 4)

    print("\n=== TOURNAMENT ===")
    r = requests.post(BASE + "/tournaments", headers=A, json={
        "name": "E2E Cup {}".format(TAG), "description": "smoke test", "category": "singles",
        "format": "knockout", "registrationStartDate": "2026-09-01",
        "registrationEndDate": "2026-09-05", "tournamentStartDate": "2026-09-10",
        "tournamentEndDate": "2026-09-12", "venue": "QA Hall", "city": "Pune",
        "numberOfBoards": 2, "entryFee": 100.0, "prizePool": "10k",
        "rules": {"maxBoardsPerMatch": 3, "targetScore": 29, "matchDurationMinutes": 30},
        "status": "registration_open"}, timeout=90)
    assert r.status_code == 200, "create tournament {} {}".format(r.status_code, r.text[:300])
    t = r.json()
    tid = t["id"]
    created_tournaments.append(tid)
    ok("create returns camelCase (numberOfBoards={}, entryFee={})".format(
        t.get("numberOfBoards"), t.get("entryFee")),
        t.get("numberOfBoards") == 2 and t.get("entryFee") == 100.0)

    print("\n=== REGISTRATIONS ===")
    reg_ids = []
    for pid in player_ids:
        r = requests.post(BASE + "/tournaments/{}/registrations".format(tid), headers=A,
                          json={"type": "singles", "playerId": pid}, timeout=90)
        assert r.status_code == 200, "register {} {}".format(r.status_code, r.text[:300])
        reg_ids.append(r.json()["id"])
    ok("4 registrations created", len(reg_ids) == 4)

    admin_db.table("registrations").update({"status": "pending"}).eq("id", reg_ids[0]).execute()
    r = requests.post(BASE + "/registrations/{}/approve".format(reg_ids[0]), headers=A, timeout=90)
    ok("POST /registrations/{id}/approve -> " + str(r.status_code) + " (was 404)",
       r.status_code == 200 and r.json().get("status") == "approved")

    r = requests.post(BASE + "/registrations/{}/reject".format(uuid.uuid4()), headers=A, timeout=90)
    ok("unknown registration -> 404 not 400 (got {})".format(r.status_code), r.status_code == 404)

    print("\n=== FIXTURES & SCHEDULE ===")
    r = requests.post(BASE + "/tournaments/{}/fixtures".format(tid), headers=A, timeout=180)
    assert r.status_code == 200, "fixtures {} {}".format(r.status_code, r.text[:300])
    print("    " + r.json()["message"])
    r = requests.post(BASE + "/tournaments/{}/schedule?restMinutes=10".format(tid), headers=A, timeout=180)
    assert r.status_code == 200, "schedule {} {}".format(r.status_code, r.text[:300])
    r = requests.post(BASE + "/tournaments/{}/publish-schedule".format(tid), headers=A, timeout=180)
    assert r.status_code == 200, "publish {} {}".format(r.status_code, r.text[:300])
    print("    " + r.json()["message"])
    ok("publish-schedule fanned out to recipients", "participant(s)" in r.json()["message"])

    print("\n=== GET /tournaments (list) ===")
    r = requests.get(BASE + "/tournaments", timeout=90)
    assert r.status_code == 200
    lt = next(x for x in r.json() if x["id"] == tid)
    ok("list carries matches ({}) and registrations ({})".format(
        len(lt["matches"]), len(lt["registrations"])),
        len(lt["matches"]) > 0 and len(lt["registrations"]) == 4)

    m0 = lt["matches"][0]
    ok("match camelCase (matchNumber={}, roundName={!r}, maxBoards={})".format(
        m0.get("matchNumber"), m0.get("roundName"), m0.get("maxBoards")),
        m0.get("matchNumber") is not None and m0.get("maxBoards") is not None)
    ok("boards camelCase ({} boards, first player1Score={})".format(
        len(m0["boards"]), m0["boards"][0].get("player1Score") if m0["boards"] else None),
        bool(m0["boards"]) and "player1Score" in m0["boards"][0] and "boardNumber" in m0["boards"][0])
    r0 = lt["registrations"][0]
    ok("registration camelCase (registeredAt set={}, player hydrated={})".format(
        r0.get("registeredAt") is not None, r0.get("player") is not None),
        bool(r0.get("registeredAt")) and bool(r0.get("player")))
    ok("scheduledPublished={} fixturesGenerated={}".format(
        lt.get("scheduledPublished"), lt.get("fixturesGenerated")),
        lt.get("scheduledPublished") is True and lt.get("fixturesGenerated") is True)

    print("\n=== LIVE SCORING -> WINNER ===")
    playable = [m for m in lt["matches"] if m.get("player1Id") and m.get("player2Id")]
    assert playable, "no fully-populated match to score"
    match = playable[0]
    mid = match["id"]
    requests.post(BASE + "/matches/{}/start".format(mid), headers=A, timeout=90)
    for bn, scores in enumerate([(29, 12), (25, 20)], start=1):
        r = requests.post(BASE + "/matches/{}/boards/{}/submit".format(mid, bn), headers=A, json={
            "p1Score": scores[0], "p2Score": scores[1], "queenClaimedBy": "player1",
            "queenCovered": True, "auditReason": "e2e"}, timeout=90)
        assert r.status_code == 200, "submit board {}: {} {}".format(bn, r.status_code, r.text[:300])

    row = admin_db.table("matches").select("*").eq("id", mid).execute().data[0]
    print("    boardWins {}-{}  points {}-{}  status={}".format(
        row["player1_board_wins"], row["player2_board_wins"],
        row["player1_total_points"], row["player2_total_points"], row["status"]))
    print("    winner_id={}  winner_name={}".format(row["winner_id"], row["winner_name"]))
    ok("winner_id persisted (was ALWAYS NULL before the fix)",
       row["winner_id"] == match["player1Id"] and row["winner_name"] == match["player1Name"])

    r = requests.post(BASE + "/matches/{}/confirm".format(mid), headers=A, timeout=90)
    ok("confirm match -> {}".format(r.status_code), r.status_code == 200)

    nxt = row.get("next_match_id")
    if nxt:
        nrow = admin_db.table("matches").select("*").eq("id", nxt).execute().data[0]
        slot = row.get("next_match_slot") or "player2"
        print("    next match slot {}: id={} name={!r}".format(
            slot, nrow.get(slot + "_id"), nrow.get(slot + "_name")))
        ok("knockout progression wrote the winner into the next round",
           nrow.get(slot + "_id") == row["winner_id"])
    else:
        print("    (no next_match_id on this match; progression not applicable)")

    r = requests.get(BASE + "/tournaments/{}".format(tid), timeout=90)
    detail_match = next(m for m in r.json()["matches"] if m["id"] == mid)
    ok("auditHistory exposed ({} entries)".format(len(detail_match.get("auditHistory", []))),
       len(detail_match.get("auditHistory", [])) >= 2)

    print("\n=== NOTIFICATIONS (RLS) ===")
    r = requests.get(BASE + "/notifications", headers=A, timeout=90)
    assert r.status_code == 200, "{} {}".format(r.status_code, r.text[:300])
    notes = r.json()
    mine = [n for n in notes if n.get("profileId") == admin_id]
    print("    {} visible, {} addressed to this user".format(len(notes), len(mine)))
    ok("per-user notification rows delivered", len(mine) > 0)
    ok("notification shape (timestamp + read)",
       bool(notes) and "timestamp" in notes[0] and "read" in notes[0])

    if mine:
        r = requests.put(BASE + "/notifications/{}/read".format(mine[0]["id"]), headers=A, timeout=90)
        ok("mark one read -> {} (was 400 IndexError)".format(r.status_code),
           r.status_code == 200 and r.json().get("read") is True)

    r = requests.put(BASE + "/notifications/read-all", headers=A, timeout=90)
    updated = r.json().get("updated", -1)
    ok("mark-all-read updated {} row(s) (was always 0)".format(updated),
       r.status_code == 200 and updated >= 0)

    r = requests.get(BASE + "/notifications", headers=A, timeout=90)
    unread = [n for n in r.json() if n.get("profileId") == admin_id and not n["read"]]
    ok("unread count for this user now {} (badge can clear)".format(len(unread)), len(unread) == 0)

finally:
    cleanup()

print("\n" + "=" * 52)
if failures:
    print("FAILURES:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL END-TO-END CHECKS PASSED")

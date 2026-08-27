"""Doubles registration paths: pair two players, new partner, existing team."""
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
    for uid in created_users:
        try:
            admin_db.auth.admin.delete_user(uid)
        except Exception:
            pass
    try:
        for row in (admin_db.table("tournaments").select("id").ilike("name", "%" + TAG + "%").execute().data or []):
            admin_db.table("tournaments").delete().eq("id", row["id"]).execute()
        for row in (admin_db.table("profiles").select("id").ilike("email", "%" + TAG + "%").execute().data or []):
            try:
                admin_db.auth.admin.delete_user(row["id"])
            except Exception:
                pass
        # teams created for the throwaway players
        for row in (admin_db.table("teams").select("id").ilike("name", "%" + TAG + "%").execute().data or []):
            admin_db.table("teams").delete().eq("id", row["id"]).execute()
    except Exception as e:
        print("  WARN sweep: {}".format(e))
    print("  cleanup done")


try:
    print("=== SETUP ===")
    r = requests.post(BASE + "/auth/signup", json={
        "email": "dbl_admin_{}@carromarena.com".format(TAG), "password": PASSWORD,
        "name": "Dbl Admin", "role": "admin", "club": "QA", "city": "Pune"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    created_users.append(r.json()["user"]["id"])
    A = {"Authorization": "Bearer " + r.json()["access_token"]}

    players = []
    for i in range(4):
        rr = requests.post(BASE + "/players", headers=A, json={
            "name": "Dbl P{} {}".format(i + 1, TAG), "club": "QA Club", "city": "Pune",
            "rating": 1500 + i, "email": "dbl_p{}_{}@carromarena.com".format(i, TAG)}, timeout=90)
        assert rr.status_code == 200, rr.text[:300]
        players.append(rr.json())
        created_users.append(rr.json()["id"])
    print("    4 roster players created")

    r = requests.post(BASE + "/tournaments", headers=A, json={
        "name": "Doubles Cup {}".format(TAG), "category": "doubles", "format": "knockout",
        "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-12",
        "venue": "QA Hall", "city": "Pune", "numberOfBoards": 2,
        "rules": {"maxBoardsPerMatch": 3, "targetScore": 29},
        "status": "registration_open"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    tid = r.json()["id"]
    created_tournaments.append(tid)

    # ---- 1. Pair two roster players (the "Pair Two Players" mode) ----
    print("\n=== PAIR TWO EXISTING PLAYERS ===")
    r = requests.post(BASE + "/tournaments/{}/registrations".format(tid), headers=A, json={
        "type": "doubles",
        "player_id": players[0]["id"],
        "partner_id": players[1]["id"],
        "team_name": "Strikers {}".format(TAG),
    }, timeout=90)
    ok("register by two profile ids -> {}".format(r.status_code), r.status_code == 200)
    if r.status_code != 200:
        print("      body:", r.text[:300])
    else:
        reg = r.json()
        ok("registration is a doubles team entry", reg.get("type") == "doubles" and reg.get("teamId"))

    # ---- 2. Partner with no account (the "New Partner" mode) ----
    print("\n=== NEW PARTNER (no account yet) ===")
    r = requests.post(BASE + "/tournaments/{}/registrations".format(tid), headers=A, json={
        "type": "doubles",
        "player_id": players[2]["id"],
        "partner_name": "Walk In Partner {}".format(TAG),
        "partner_phone": "9822012345",
        "partner_email": "walkin_{}@carromarena.com".format(TAG),
        "team_name": "Newcomers {}".format(TAG),
    }, timeout=90)
    ok("register with a brand-new partner -> {}".format(r.status_code), r.status_code == 200)
    if r.status_code != 200:
        print("      body:", r.text[:300])

    created = admin_db.table("profiles").select("*").eq(
        "email", "walkin_{}@carromarena.com".format(TAG)).execute().data or []
    ok("partner profile was created ({})".format(created[0]["name"] if created else "none"), len(created) == 1)
    if created:
        created_users.append(created[0]["id"])
        ok("partner phone stored ({})".format(created[0].get("phone")), created[0].get("phone") == "9822012345")

    # ---- 3. Team name is optional ----
    print("\n=== OPTIONAL TEAM NAME ===")
    r = requests.post(BASE + "/tournaments/{}/registrations".format(tid), headers=A, json={
        "type": "doubles",
        "player_id": players[3]["id"],
        "partner_name": "Auto Named {}".format(TAG),
        "partner_email": "auto_{}@carromarena.com".format(TAG),
    }, timeout=90)
    ok("register without a team name -> {}".format(r.status_code), r.status_code == 200)
    auto = admin_db.table("profiles").select("id").eq(
        "email", "auto_{}@carromarena.com".format(TAG)).execute().data or []
    if auto:
        created_users.append(auto[0]["id"])
    if r.status_code == 200:
        team_id = r.json().get("teamId")
        team = admin_db.table("teams").select("name").eq("id", team_id).execute().data
        derived = team[0]["name"] if team else ""
        ok("team name derived from both players: '{}'".format(derived), " & " in derived)

    # ---- 4. Guards ----
    print("\n=== GUARDS ===")
    r = requests.post(BASE + "/tournaments/{}/registrations".format(tid), headers=A, json={
        "type": "doubles", "player_id": players[0]["id"], "partner_id": players[0]["id"],
        "team_name": "Self {}".format(TAG)}, timeout=90)
    ok("self-partnering rejected 422 (got {})".format(r.status_code), r.status_code == 422)

    r = requests.post(BASE + "/tournaments/{}/registrations".format(tid), headers=A, json={
        "type": "doubles", "player_id": players[0]["id"],
        "partner_id": str(uuid.uuid4()), "team_name": "Ghost {}".format(TAG)}, timeout=90)
    ok("unknown partner id rejected 404 (got {})".format(r.status_code), r.status_code == 404)

    r = requests.post(BASE + "/tournaments/{}/registrations".format(tid), headers=A, json={
        "type": "doubles", "player_id": players[0]["id"], "team_name": "Nobody {}".format(TAG)}, timeout=90)
    ok("no partner at all rejected 400 (got {})".format(r.status_code), r.status_code == 400)

    # ---- 5. Partner reuse by email (no duplicate profile) ----
    print("\n=== PARTNER REUSE BY EMAIL ===")
    before = admin_db.table("profiles").select("id", count="exact").eq(
        "email", "walkin_{}@carromarena.com".format(TAG)).execute().count
    r = requests.post(BASE + "/tournaments/{}/registrations".format(tid), headers=A, json={
        "type": "doubles", "player_id": players[1]["id"],
        "partner_name": "Different Name", "partner_email": "walkin_{}@carromarena.com".format(TAG),
        "team_name": "Reuse {}".format(TAG)}, timeout=90)
    after = admin_db.table("profiles").select("id", count="exact").eq(
        "email", "walkin_{}@carromarena.com".format(TAG)).execute().count
    ok("same email reuses the profile (count {} -> {})".format(before, after), before == after == 1)

    # ---- 6. GET /teams now returns something for the dropdown ----
    print("\n=== TEAMS ENDPOINT (was the empty dropdown) ===")
    r = requests.get(BASE + "/teams", timeout=60)
    teams = r.json()
    ok("GET /teams -> {} with {} team(s)".format(r.status_code, len(teams)),
       r.status_code == 200 and len(teams) >= 3)
    if teams:
        t0 = teams[0]
        ok("team carries both hydrated players ({} & {})".format(
            (t0.get("player1") or {}).get("name"), (t0.get("player2") or {}).get("name")),
            t0.get("player1", {}).get("name") and t0.get("player2", {}).get("name"))

    r = requests.get(BASE + "/teams?tournamentId=" + tid, timeout=60)
    ok("GET /teams?tournamentId -> {} team(s) in this tournament".format(len(r.json())),
       r.status_code == 200 and len(r.json()) >= 3)

    r = requests.post(BASE + "/teams", headers=A, json={
        "name": "Explicit {}".format(TAG),
        "player1_id": players[2]["id"], "player2_id": players[3]["id"]}, timeout=90)
    ok("POST /teams creates a pair -> {}".format(r.status_code), r.status_code == 200)
    r2 = requests.post(BASE + "/teams", headers=A, json={
        "name": "Explicit again {}".format(TAG),
        "player1_id": players[3]["id"], "player2_id": players[2]["id"]}, timeout=90)
    ok("re-creating the same pair (reversed) reuses it",
       r2.status_code == 200 and r2.json().get("id") == r.json().get("id"))

    r = requests.post(BASE + "/teams", headers=A, json={
        "player1_id": players[2]["id"], "player2_id": players[2]["id"]}, timeout=90)
    ok("team of one player rejected 422 (got {})".format(r.status_code), r.status_code == 422)

    # ---- 7. The registrations show up on the tournament ----
    print("\n=== TOURNAMENT VIEW ===")
    r = requests.get(BASE + "/tournaments/" + tid, timeout=60)
    regs = r.json().get("registrations", [])
    doubles = [x for x in regs if x.get("type") == "doubles"]
    ok("tournament shows {} doubles registration(s)".format(len(doubles)), len(doubles) >= 4)
    if doubles:
        d = doubles[0]
        team = d.get("team") or {}
        ok("registration team hydrated: '{}' = {} & {}".format(
            team.get("name"), (team.get("player1") or {}).get("name"),
            (team.get("player2") or {}).get("name")),
            bool(team.get("player1") and team.get("player2")))

finally:
    cleanup()

print("\n" + "=" * 52)
if failures:
    print("FAILURES:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL DOUBLES CHECKS PASSED")

"""Import the real roster, then check fixtures and points tables split by category."""
import os, sys
import _session, json, uuid, requests
sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _session
sys.path.insert(0, r"C:/Users/RAGAVE~1/AppData/Local/Temp/claude/C--Users-RAGAVENDRA-Desktop-tournament/88d57351-33e7-4b4c-8c26-2dd37cc67011/scratchpad")
from real_sheet import build
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
admin_db = get_admin_db()
created = []
failures = []
H = {}


def ok(label, cond, detail=""):
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + repr(detail)) if (detail not in (None, "") and not cond) else ""))
    if not cond:
        failures.append(label)
    return cond


def api(method, path, **kw):
    return _session.request(method, path, H, **kw)


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created:
        try: admin_db.table("tournaments").delete().eq("id", tid).execute()
        except Exception: pass
    for row in (admin_db.table("tournaments").select("id").ilike("name", "%"+RUN+"%").execute().data or []):
        admin_db.table("tournaments").delete().eq("id", row["id"]).execute()
    for row in (admin_db.table("profiles").select("id,email").ilike("email", "emp%@carromarena.com").execute().data or []):
        try: admin_db.auth.admin.delete_user(row["id"])
        except Exception: pass
    for row in (admin_db.table("profiles").select("id").ilike("email", "%rl"+RUN+"%").execute().data or []):
        try: admin_db.auth.admin.delete_user(row["id"])
        except Exception: pass
    print("  done")


try:
    r = requests.post(BASE + "/auth/signup", json={
        "email": "rl{}_admin@carromarena.com".format(RUN), "password": "TestPass2345x",
        "name": "Roster Admin", "role": "admin"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    H["Authorization"] = "Bearer " + r.json()["access_token"]
    _session.remember("rl{}_admin@carromarena.com".format(RUN), "TestPass2345x")

    print("=" * 70); print("IMPORT THE REAL ROSTER"); print("=" * 70)
    r = api("POST", "/imports/excel", files={"file": ("roster.xlsx", build(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    ok("parse -> {}".format(r.status_code), r.status_code == 200, r.text[:200])
    d = r.json()
    ok("detected as a roster sheet ({})".format(d.get("detectedFormat")), d.get("detectedFormat") == "roster")
    ok("21 singles + 10 doubles ({}/{})".format(d.get("singlesCount"), d.get("doublesCount")),
       d.get("singlesCount") == 21 and d.get("doublesCount") == 10)
    ok("22 people found (21 rows + partner-only 'sriram')", d.get("peopleFound") == 22)

    r = api("POST", "/tournaments", json={
        "name": "TCS Carrom {}".format(RUN), "category": "both", "format": "round_robin",
        "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-25",
        "venue": "TCS Sholinganallur", "city": "Chennai", "numberOfBoards": 4,
        "rules": {"maxBoardsPerMatch": 3, "targetScore": 29, "pointsForWin": 2,
                  "pointsForDraw": 1, "matchDurationMinutes": 30},
        "status": "registration_open"})
    tid = r.json()["id"]; created.append(tid)

    rc = api("POST", "/imports/confirm", data={
        "tournamentId": tid, "players_json": json.dumps(d["players"]), "autoGenerate": "false"})
    b = rc.json()
    ok("confirm -> {} ({} singles, {} doubles)".format(
        rc.status_code, b.get("singlesImported"), b.get("doublesImported")),
        rc.status_code == 200 and b.get("singlesImported") == 21 and b.get("doublesImported") == 10,
        rc.text[:220])
    ok("nothing skipped", not b.get("skipped"), str(b.get("skipped"))[:200])

    regs = api("GET", "/tournaments/{}/registrations".format(tid)).json()
    singles_regs = [x for x in regs if x["type"] == "singles"]
    doubles_regs = [x for x in regs if x["type"] == "doubles"]
    ok("31 registrations: 21 singles + 10 doubles ({} + {})".format(len(singles_regs), len(doubles_regs)),
       len(singles_regs) == 21 and len(doubles_regs) == 10)

    print("\n" + "=" * 70); print("FIXTURES SPLIT BY CATEGORY"); print("=" * 70)
    api("PUT", "/tournaments/" + tid, json={"status": "registration_closed"})
    rf = api("POST", "/tournaments/{}/fixtures".format(tid))
    ok("fixtures generated -> {}".format(rf.status_code), rf.status_code == 200, rf.text[:250])
    print("    {}".format(rf.json().get("message")))

    fx = api("GET", "/fixtures/" + tid).json()
    s_fx = [m for m in fx if m["type"] == "singles"]
    d_fx = [m for m in fx if m["type"] == "doubles"]
    ok("21 singles -> 210 matches (got {})".format(len(s_fx)), len(s_fx) == 210)
    ok("10 teams -> 45 matches (got {})".format(len(d_fx)), len(d_fx) == 45)

    team_names = {x["team"]["name"] for x in doubles_regs if x.get("team")}
    player_names = {x["player"]["name"] for x in singles_regs if x.get("player")}
    ok("singles fixtures use player names only",
       all(m["player1Name"] in player_names for m in s_fx))
    ok("doubles fixtures use team names only",
       all(m["player1Name"] in team_names for m in d_fx))
    ok("match numbers unique across both categories",
       len({m["matchNumber"] for m in fx}) == len(fx))

    print("\n" + "=" * 70); print("SCHEDULING WITH DUAL-ENTRY PLAYERS"); print("=" * 70)
    rs = api("POST", "/scheduling/{}/generate?restMinutes=10".format(tid))
    ok("schedule generated -> {}".format(rs.status_code), rs.status_code == 200, rs.text[:200])
    conf = api("GET", "/scheduling/{}/conflicts".format(tid)).json()
    ok("no conflicts, counting people inside teams ({} found)".format(conf.get("conflictCount")),
       conf.get("conflictFree") is True,
       str(conf.get("conflicts", [])[:2]))

    print("\n" + "=" * 70); print("POINTS TABLES SPLIT BY CATEGORY"); print("=" * 70)
    for m in s_fx[:6] + d_fx[:6]:
        api("POST", "/matches/{}/start".format(m["id"]))
        api("POST", "/matches/{}/boards/1/submit".format(m["id"]),
            json={"p1Score": 29, "p2Score": 12, "auditReason": "demo"})
        api("POST", "/matches/{}/boards/2/submit".format(m["id"]),
            json={"p1Score": 27, "p2Score": 20, "auditReason": "demo"})
        api("POST", "/matches/{}/confirm".format(m["id"]))

    st = api("GET", "/standings/" + tid).json()
    cats = {c["category"]: c for c in st.get("categories", [])}
    ok("two separate tables returned ({})".format(sorted(cats)), set(cats) == {"singles", "doubles"})
    if "singles" in cats:
        ok("singles table has 21 rows ({})".format(len(cats["singles"]["standings"])),
           len(cats["singles"]["standings"]) == 21)
        ok("singles table counts only singles matches ({})".format(cats["singles"]["matchCount"]),
           cats["singles"]["matchCount"] == 210)
    if "doubles" in cats:
        ok("doubles table has 10 rows ({})".format(len(cats["doubles"]["standings"])),
           len(cats["doubles"]["standings"]) == 10)
        ok("doubles rows are teams",
           all(r["participantType"] == "doubles" for r in cats["doubles"]["standings"]))
        names = {r["participantName"] for r in cats["doubles"]["standings"]}
        ok("no player appears in the doubles table", not (names & player_names),
           str(sorted(names & player_names))[:120])
        print("\n    doubles table top 3:")
        for r in cats["doubles"]["standings"][:3]:
            print("      {}. {:36s} P{} W{} pts{}".format(
                r["rank"], r["participantName"][:36], r["played"], r["won"], r["points"]))
        print("    singles table top 3:")
        for r in cats["singles"]["standings"][:3]:
            print("      {}. {:36s} P{} W{} pts{}".format(
                r["rank"], r["participantName"][:36], r["played"], r["won"], r["points"]))

finally:
    cleanup()

print("\n" + "=" * 70)
print("RESULTS: {} failure(s)".format(len(failures)))
for f in failures:
    print("  - " + f)
print("ALL REAL-ROSTER CHECKS PASSED" if not failures else "FAILURES ABOVE")
sys.exit(1 if failures else 0)

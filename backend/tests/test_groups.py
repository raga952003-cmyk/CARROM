"""Group stage and group+knockout end to end through the API."""
import os, sys
import uuid, requests
sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _session
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
admin_db = get_admin_db()
created = []
failures = []
H = {}


def ok(label, cond, detail=""):
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + detail) if (detail and not cond) else ""))
    if not cond:
        failures.append(label)
    return cond


def api(method, path, **kw):
    return _session.request(method, path, H, **kw)


def players(n):
    out = []
    for i in range(n):
        r = api("POST", "/players", json={
            "name": "GP{:02d} {}".format(i + 1, RUN), "club": "C", "city": "Pune",
            "rating": 1800 - i * 20, "email": "gp{}_{}@carromarena.com".format(RUN, i)})
        assert r.status_code == 200, r.text[:200]
        out.append(r.json())
    return out


def tournament(label, fmt, group_count, qpg=2):
    r = api("POST", "/tournaments", json={
        "name": "{} {}".format(label, RUN), "category": "singles", "format": fmt,
        "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-25",
        "venue": "Group Hall", "city": "Pune", "numberOfBoards": 4,
        "rules": {"maxBoardsPerMatch": 3, "targetScore": 29, "pointsForWin": 2,
                  "pointsForDraw": 1, "matchDurationMinutes": 30,
                  "groupCount": group_count, "qualifiersPerGroup": qpg},
        "status": "registration_open"})
    assert r.status_code == 200, r.text[:300]
    created.append(r.json()["id"])
    return r.json()


def play(m, p1_wins=True):
    api("POST", "/matches/{}/start".format(m["id"]))
    need = ((m.get("maxBoards") or 3) // 2) + 1
    for b in range(1, need + 1):
        hi, lo = (29, 12) if p1_wins else (12, 29)
        api("POST", "/matches/{}/boards/{}/submit".format(m["id"], b),
            json={"p1Score": hi, "p2Score": lo, "auditReason": "group"})
    api("POST", "/matches/{}/confirm".format(m["id"]))


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created:
        try: admin_db.table("tournaments").delete().eq("id", tid).execute()
        except Exception: pass
    for row in (admin_db.table("tournaments").select("id").ilike("name", "%"+RUN+"%").execute().data or []):
        admin_db.table("tournaments").delete().eq("id", row["id"]).execute()
    for row in (admin_db.table("profiles").select("id").ilike("email", "%"+RUN+"%").execute().data or []):
        try: admin_db.auth.admin.delete_user(row["id"])
        except Exception: pass
    print("  done")


try:
    r = requests.post(BASE + "/auth/signup", json={
        "email": "grp{}_admin@carromarena.com".format(RUN), "password": "TestPass2345x",
        "name": "Group Admin", "role": "admin"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    H["Authorization"] = "Bearer " + r.json()["access_token"]
    _session.remember("grp{}_admin@carromarena.com".format(RUN), "TestPass2345x")

    pool = players(16)

    # ================= GROUP STAGE =================
    print("=" * 70); print("GROUP STAGE - 16 players, 4 groups"); print("=" * 70)
    t = tournament("GroupStage", "round_robin", 4)
    for p in pool:
        api("POST", "/tournaments/{}/registrations".format(t["id"]),
            json={"type": "singles", "playerId": p["id"]})
    api("PUT", "/tournaments/" + t["id"], json={"status": "registration_closed"})
    rf = api("POST", "/tournaments/{}/fixtures".format(t["id"]))
    ok("fixtures generated -> {}".format(rf.status_code), rf.status_code == 200, rf.text[:250])

    fx = api("GET", "/fixtures/" + t["id"]).json()
    ok("4 groups of 4 -> 24 matches (got {})".format(len(fx)), len(fx) == 24)
    groups = {}
    for m in fx:
        g = (m.get("bracketPosition") or {}).get("group")
        groups.setdefault(g, []).append(m)
    ok("4 groups persisted ({})".format(sorted(k for k in groups if k)), sorted(k for k in groups if k) == ["A","B","C","D"])
    ok("6 matches per group ({})".format({k: len(v) for k, v in sorted(groups.items())}),
       all(len(v) == 6 for k, v in groups.items() if k))
    ok("round names carry the group", all("Group" in m["roundName"] for m in fx),
       str([m["roundName"] for m in fx][:2]))

    members = {g: {m["player1Name"] for m in ms} | {m["player2Name"] for m in ms}
               for g, ms in groups.items() if g}
    overlap = [(a, b) for a in members for b in members if a < b and (members[a] & members[b])]
    ok("no player appears in two groups", not overlap, str(overlap)[:150])
    ok("every group has exactly 4 players ({})".format({g: len(v) for g, v in sorted(members.items())}),
       all(len(v) == 4 for v in members.values()))

    api("POST", "/scheduling/{}/generate?restMinutes=10".format(t["id"]))
    conf = api("GET", "/scheduling/{}/conflicts".format(t["id"])).json()
    ok("schedule conflict-free ({})".format(conf.get("conflictCount")), conf.get("conflictFree") is True)

    for m in fx:
        play(m, p1_wins=(m["matchNumber"] % 3 != 0))
    st = api("GET", "/standings/" + t["id"]).json()
    cat = st["categories"][0]
    ok("standings split into 4 group tables ({})".format(len(cat.get("groups", []))),
       len(cat.get("groups", [])) == 4)
    ok("each group table has 4 rows",
       all(len(g["standings"]) == 4 for g in cat.get("groups", [])),
       str([len(g["standings"]) for g in cat.get("groups", [])]))
    ok("rows are tagged with their group",
       all(r.get("group") == g["group"] for g in cat["groups"] for r in g["standings"]))
    ok("each group ranks 1..4",
       all(sorted(r["rank"] for r in g["standings"]) == [1,2,3,4] for g in cat["groups"]))
    print("    Group A: " + ", ".join(
        "{}.{}".format(r["rank"], r["participantName"].split()[0]) for r in cat["groups"][0]["standings"]))

    # ================= GROUP + KNOCKOUT =================
    print("\n" + "=" * 70); print("GROUP + KNOCKOUT - 16 players, 4 groups, top 2 qualify"); print("=" * 70)
    t2 = tournament("GroupKO", "league_knockout", 4, qpg=2)
    for p in pool:
        api("POST", "/tournaments/{}/registrations".format(t2["id"]),
            json={"type": "singles", "playerId": p["id"]})
    api("PUT", "/tournaments/" + t2["id"], json={"status": "registration_closed"})
    rf2 = api("POST", "/tournaments/{}/fixtures".format(t2["id"]))
    ok("fixtures generated -> {}".format(rf2.status_code), rf2.status_code == 200, rf2.text[:250])
    print("    {}".format(rf2.json().get("message")))

    fx2 = api("GET", "/fixtures/" + t2["id"]).json()
    gs = [m for m in fx2 if m["stage"] == "league"]
    ko = [m for m in fx2 if m["stage"] == "knockout"]
    ok("24 group matches + 7 knockout ({} + {})".format(len(gs), len(ko)),
       len(gs) == 24 and len(ko) == 7)
    slots = sorted({m[k+"Name"] for m in ko for k in ("player1","player2")
                    if m[k+"Name"].startswith("Group ")})
    ok("8 qualifier slots labelled by group ({})".format(len(slots)), len(slots) == 8, str(slots))
    r1 = [m for m in ko if m["roundIndex"] == 1]
    same_group = [m for m in r1
                  if m["player1Name"].split()[1] == m["player2Name"].split()[1]]
    ok("round 1 never pairs two qualifiers from the same group", not same_group, str(same_group)[:150])

    api("POST", "/scheduling/{}/generate?restMinutes=10".format(t2["id"]))
    print("    playing 24 group matches...")
    for m in gs:
        play(m, p1_wins=(m["matchNumber"] % 3 != 0))

    fx2b = api("GET", "/fixtures/" + t2["id"]).json()
    ko2 = [m for m in fx2b if m["stage"] == "knockout"]
    filled = [m for m in ko2 if m.get("player1Id") and m.get("player2Id")]
    ok("knockout auto-populated from the group tables ({} matches filled)".format(len(filled)),
       len(filled) == 4, str([(m["player1Name"], m["player2Name"]) for m in ko2[:4]]))

    st2 = api("GET", "/standings/" + t2["id"]).json()
    blocks = {g["group"]: g for g in st2["categories"][0]["groups"]}
    expected = set()
    for label, g in blocks.items():
        for r in sorted(g["standings"], key=lambda x: x["rank"])[:2]:
            expected.add(r["participantName"])
    actual = {m["player1Name"] for m in filled} | {m["player2Name"] for m in filled}
    ok("qualifiers are exactly the top 2 of each group", actual == expected,
       "got {} expected {}".format(sorted(actual), sorted(expected)))

    for rnd in sorted({m["roundIndex"] for m in ko2}):
        current = [m for m in api("GET", "/fixtures/" + t2["id"]).json()
                   if m["stage"] == "knockout" and m["roundIndex"] == rnd
                   and m.get("player1Id") and m.get("player2Id") and not m.get("resultConfirmed")]
        for m in current:
            play(m)
    finals = [m for m in api("GET", "/fixtures/" + t2["id"]).json()
              if m["stage"] == "knockout" and m["roundName"] == "Final"]
    champ = finals[0].get("winnerName") if finals else None
    ok("group+knockout produced a champion: {}".format(champ), bool(champ))

finally:
    cleanup()

print("\n" + "=" * 70)
print("RESULTS: {} failure(s)".format(len(failures)))
for f in failures:
    print("  - " + f)
print("ALL GROUP SCENARIOS PASSED" if not failures else "FAILURES ABOVE")
sys.exit(1 if failures else 0)

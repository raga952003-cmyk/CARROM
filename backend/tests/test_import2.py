"""Excel/CSV import: singles, doubles, mixed, category mismatch, CSV, dedupe."""
import os, sys, io, uuid, json, requests
sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _session
from openpyxl import Workbook
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
admin_db = get_admin_db()
created_tournaments = []
failures = []
H = {}


def ok(label, cond, detail=""):
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + repr(detail)) if (detail not in (None, "") and not cond) else ""))
    if not cond:
        failures.append(label)
    return cond


def xlsx(headers, rows):
    wb = Workbook(); ws = wb.active
    ws.append(headers)
    for r in rows: ws.append(r)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf


def csvfile(headers, rows):
    txt = ",".join(headers) + "\n" + "\n".join(",".join(str(c) for c in r) for r in rows)
    return io.BytesIO(txt.encode())


def api(method, path, **kw):
    return _session.request(method, path, H, **kw)


def upload(fileobj, name):
    ct = ("text/csv" if name.endswith(".csv")
          else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return api("POST", "/imports/excel", files={"file": (name, fileobj, ct)})


def tournament(label, fmt, category):
    r = api("POST", "/tournaments", json={
        "name": "{} {}".format(label, RUN), "category": category, "format": fmt,
        "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-20",
        "venue": "Import Hall", "city": "Pune", "numberOfBoards": 2,
        "rules": {"maxBoardsPerMatch": 3, "targetScore": 29}, "status": "registration_open"})
    assert r.status_code == 200, r.text[:300]
    created_tournaments.append(r.json()["id"])
    return r.json()


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created_tournaments:
        try: admin_db.table("tournaments").delete().eq("id", tid).execute()
        except Exception: pass
    for row in (admin_db.table("tournaments").select("id").ilike("name", "%"+RUN+"%").execute().data or []):
        admin_db.table("tournaments").delete().eq("id", row["id"]).execute()
    for row in (admin_db.table("teams").select("id").ilike("name", "%"+RUN+"%").execute().data or []):
        admin_db.table("teams").delete().eq("id", row["id"]).execute()
    for row in (admin_db.table("profiles").select("id").ilike("email", "%imp"+RUN+"%").execute().data or []):
        try: admin_db.auth.admin.delete_user(row["id"])
        except Exception: pass
    print("  done")


try:
    r = requests.post(BASE + "/auth/signup", json={
        "email": "imp{}_admin@carromarena.com".format(RUN), "password": "TestPass2345x",
        "name": "Imp Admin", "role": "admin"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    H["Authorization"] = "Bearer " + r.json()["access_token"]
    _session.remember("imp{}_admin@carromarena.com".format(RUN), "TestPass2345x")

    E = lambda n: "imp{}_{}@carromarena.com".format(RUN, n)

    # ---------------- 1. DOUBLES XLSX ----------------
    print("=" * 70); print("1. DOUBLES SHEET -> doubles tournament"); print("=" * 70)
    rows = [["{} Duo A".format(RUN), "DblA One", "DblA Two", "Club A", "Pune", E("a1"), E("a2")],
            ["{} Duo B".format(RUN), "DblB One", "DblB Two", "Club B", "Pune", E("b1"), E("b2")],
            ["{} Duo C".format(RUN), "DblC One", "DblC Two", "Club C", "Pune", E("c1"), E("c2")],
            ["{} Duo D".format(RUN), "DblD One", "DblD Two", "Club D", "Pune", E("d1"), E("d2")]]
    hdr = ["Team Name", "Player 1 Name", "Player 2 Name", "Club", "City", "Email", "Player 2 Email"]
    r = upload(xlsx(hdr, rows), "d.xlsx")
    ok("parse -> {}".format(r.status_code), r.status_code == 200, r.text[:200])
    d = r.json()
    ok("detected as a doubles sheet ({})".format(d.get("detectedFormat")), d.get("detectedFormat") == "doubles")
    ok("4 doubles / 0 singles", d.get("doublesCount") == 4 and d.get("singlesCount") == 0)
    ok("player name is the person, not the team ({!r})".format(d["players"][0]["name"]),
       d["players"][0]["name"] == "DblA One")
    ok("partner captured ({!r})".format(d["players"][0].get("partnerName")),
       d["players"][0].get("partnerName") == "DblA Two")
    ok("team name captured ({!r})".format(d["players"][0].get("teamName")),
       d["players"][0].get("teamName", "").endswith("Duo A"))
    ok("partner email captured", d["players"][0].get("partnerEmail") == E("a2"))

    td = tournament("ImpDoubles", "round_robin", "doubles")
    rc = api("POST", "/imports/confirm", data={"tournamentId": td["id"],
             "players_json": json.dumps(d["players"])})
    body = rc.json()
    ok("confirm -> {} ({} doubles)".format(rc.status_code, body.get("doublesImported")),
       rc.status_code == 200 and body.get("doublesImported") == 4, rc.text[:200])
    ok("nothing skipped", not body.get("skipped"), str(body.get("skipped")))

    regs = api("GET", "/tournaments/{}/registrations".format(td["id"])).json()
    ok("4 doubles registrations, no singles ({} regs, types={})".format(len(regs), {x["type"] for x in regs}),
       len(regs) == 4 and {x["type"] for x in regs} == {"doubles"})
    ok("each registration carries a hydrated team with both players",
       all(x.get("team") and x["team"].get("player1") and x["team"].get("player2") for x in regs))
    # Row order is not part of the contract, so match on content.
    pairs = {frozenset((x["team"]["player1"]["name"], x["team"]["player2"]["name"])) for x in regs}
    expected = {frozenset(("Dbl{} One".format(c), "Dbl{} Two".format(c))) for c in "ABCD"}
    ok("each team pairs the two players from its sheet row", pairs == expected,
       str(sorted(tuple(sorted(p)) for p in pairs))[:200])

    fx = api("GET", "/fixtures/" + td["id"]).json()
    ok("4 teams -> 6 doubles fixtures ({} of type {})".format(len(fx), {m["type"] for m in fx}),
       len(fx) == 6 and {m["type"] for m in fx} == {"doubles"})
    ok("fixtures show team names, not player names",
       all("Duo" in m["player1Name"] for m in fx), str([m["player1Name"] for m in fx][:2]))

    all_players = {p["name"] for p in api("GET", "/players").json()}
    ok("all 8 humans exist as players",
       all(n in all_players for n in ["DblA One","DblA Two","DblD One","DblD Two"]))

    # ---------------- 2. CATEGORY MISMATCH ----------------
    print("\n" + "=" * 70); print("2. DOUBLES SHEET -> singles-only tournament"); print("=" * 70)
    ts = tournament("ImpSinglesOnly", "round_robin", "singles")
    rc2 = api("POST", "/imports/confirm", data={"tournamentId": ts["id"],
              "players_json": json.dumps(d["players"])})
    b2 = rc2.json()
    ok("import reports partial, not success ({})".format(b2.get("status")), b2.get("status") == "partial")
    ok("0 imported, 4 skipped ({} / {})".format(b2.get("imported"), len(b2.get("skipped", []))),
       b2.get("imported") == 0 and len(b2.get("skipped", [])) == 4)
    ok("skip reason explains the mismatch", "doubles" in (b2.get("skipped") or [""])[0].lower(),
       str((b2.get("skipped") or [""])[0]))

    # ---------------- 3. MIXED SHEET via Type column ----------------
    print("\n" + "=" * 70); print("3. MIXED SHEET (Type column) -> 'both' tournament"); print("=" * 70)
    mixed_hdr = ["Type", "Name", "Partner Name", "Club", "Rating", "Email"]
    mixed_rows = [
        ["Singles", "Mix Solo One", "", "Club S", 1650, E("m1")],
        ["Singles", "Mix Solo Two", "", "Club S", 1600, E("m2")],
        ["Doubles", "Mix Pair A1", "Mix Pair A2", "Club D", 1700, E("m3")],
        ["Doubles", "Mix Pair B1", "Mix Pair B2", "Club D", 1680, E("m4")],
    ]
    r3 = upload(xlsx(mixed_hdr, mixed_rows), "mixed.xlsx")
    ok("parse mixed -> {}".format(r3.status_code), r3.status_code == 200, r3.text[:200])
    d3 = r3.json()
    ok("2 singles + 2 doubles detected ({}/{})".format(d3.get("singlesCount"), d3.get("doublesCount")),
       d3.get("singlesCount") == 2 and d3.get("doublesCount") == 2)

    tb = tournament("ImpBoth", "round_robin", "both")
    rc3 = api("POST", "/imports/confirm", data={"tournamentId": tb["id"],
              "players_json": json.dumps(d3["players"]), "autoGenerate": "false"})
    b3 = rc3.json()
    ok("both types imported ({} singles, {} doubles)".format(
        b3.get("singlesImported"), b3.get("doublesImported")),
        b3.get("singlesImported") == 2 and b3.get("doublesImported") == 2)
    regs3 = api("GET", "/tournaments/{}/registrations".format(tb["id"])).json()
    ok("registrations carry both types ({})".format({x["type"] for x in regs3}),
       {x["type"] for x in regs3} == {"singles", "doubles"})
    ok("autoGenerate=false left fixtures alone", b3.get("fixturesGenerated") is False)

    # ---------------- 4. CSV ----------------
    print("\n" + "=" * 70); print("4. CSV SINGLES SHEET"); print("=" * 70)
    r4 = upload(csvfile(["Name","Club","City","Rating","Seed"],
                        [["Csv One","Club X","Pune",1550,1],["Csv Two","Club Y","Pune",1500,2]]), "s.csv")
    ok("csv parse -> {}".format(r4.status_code), r4.status_code == 200, r4.text[:200])
    if r4.status_code == 200:
        ok("2 singles from csv", r4.json().get("singlesCount") == 2)

    # ---------------- 5. DEDUPE / RE-IMPORT ----------------
    print("\n" + "=" * 70); print("5. RE-IMPORTING THE SAME SHEET"); print("=" * 70)
    before = admin_db.table("registrations").select("id", count="exact").eq(
        "tournament_id", td["id"]).execute().count
    rc5 = api("POST", "/imports/confirm", data={"tournamentId": td["id"],
              "players_json": json.dumps(d["players"]), "autoGenerate": "false"})
    after = admin_db.table("registrations").select("id", count="exact").eq(
        "tournament_id", td["id"]).execute().count
    ok("re-import creates no duplicate registrations ({} -> {})".format(before, after), before == after)
    ok("re-import reports 0 newly imported ({})".format(rc5.json().get("imported")),
       rc5.json().get("imported") == 0)
    dup_players = [p for p in api("GET", "/players").json() if p["name"] == "DblA One"]
    ok("no duplicate player profile created ({} named 'DblA One')".format(len(dup_players)),
       len(dup_players) == 1)

    # ---------------- 6. BAD SHEETS ----------------
    print("\n" + "=" * 70); print("6. MALFORMED SHEETS"); print("=" * 70)
    r6 = upload(xlsx(["Club","City"], [["A","Pune"]]), "nonames.xlsx")
    ok("sheet with no name column -> 400 (got {})".format(r6.status_code), r6.status_code == 400)
    ok("error names the expected columns", "Player 1 Name" in r6.text or "Name" in r6.text)

    r7 = upload(xlsx(["Team Name","Player 1 Name","Player 2 Name"],
                     [["T1","Lonely Player",""]]), "halfteam.xlsx")
    ok("doubles row with no partner -> 400, not a silent drop (got {})".format(r7.status_code),
       r7.status_code == 400, r7.text[:160])

    r8 = upload(io.BytesIO(b"not a spreadsheet"), "notes.txt")
    ok("unsupported extension -> 400 (got {})".format(r8.status_code), r8.status_code == 400)

finally:
    cleanup()

print("\n" + "=" * 70)
print("RESULTS: {} failure(s)".format(len(failures)))
if failures:
    for f in failures: print("  - " + f)
    sys.exit(1)
print("ALL IMPORT SCENARIOS PASSED")

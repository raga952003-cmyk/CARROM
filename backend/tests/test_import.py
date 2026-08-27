"""What actually happens when you upload a singles sheet and a doubles sheet?"""
import os, sys, io, uuid, json, requests
sys.path.insert(0, '.')
from openpyxl import Workbook
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
admin_db = get_admin_db()
created_users, created_tournaments = [], []
H = {}


def sheet(rows, headers):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def api(method, path, **kw):
    kw.setdefault("timeout", 180)
    headers = dict(H)
    headers.update(kw.pop("headers", {}))
    return requests.request(method, BASE + path, headers=headers, **kw)


def cleanup():
    print("\n=== CLEANUP ===")
    for tid in created_tournaments:
        try:
            admin_db.table("tournaments").delete().eq("id", tid).execute()
        except Exception:
            pass
    for pat in ("%imp_" + RUN + "%", "%" + RUN + "%"):
        for row in (admin_db.table("profiles").select("id").ilike("email", pat).execute().data or []):
            try:
                admin_db.auth.admin.delete_user(row["id"])
            except Exception:
                pass
    for row in (admin_db.table("tournaments").select("id").ilike("name", "%" + RUN + "%").execute().data or []):
        admin_db.table("tournaments").delete().eq("id", row["id"]).execute()
    print("  done")


def make_tournament(name, fmt, category):
    r = api("POST", "/tournaments", json={
        "name": "{} {}".format(name, RUN), "category": category, "format": fmt,
        "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-20",
        "venue": "Import Hall", "city": "Pune", "numberOfBoards": 2,
        "rules": {"maxBoardsPerMatch": 3, "targetScore": 29},
        "status": "registration_open"})
    assert r.status_code == 200, r.text[:300]
    created_tournaments.append(r.json()["id"])
    return r.json()


try:
    r = requests.post(BASE + "/auth/signup", json={
        "email": "imp_admin_{}@carromarena.com".format(RUN),
        "password": "TestPass2345x", "name": "Import Admin", "role": "admin"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    H["Authorization"] = "Bearer " + r.json()["access_token"]
    created_users.append(r.json()["user"]["id"])

    # ================= A. SINGLES SHEET =================
    print("=" * 70)
    print("A. SINGLES SHEET  (Name | Club | City | Rating | Seed | Email | Phone)")
    print("=" * 70)
    singles_rows = [
        ["Arun Kale",    "Deccan GC",  "Pune",   1720, 1, "imp_{}_s1@carromarena.com".format(RUN), "9820000001"],
        ["Ravi Menon",   "Shivaji CC", "Pune",   1680, 2, "imp_{}_s2@carromarena.com".format(RUN), "9820000002"],
        ["Sunil Rao",    "Pune United","Pune",   1640, 3, "imp_{}_s3@carromarena.com".format(RUN), "9820000003"],
        ["Kiran Shah",   "Kothrud K",  "Pune",   1600, 4, "imp_{}_s4@carromarena.com".format(RUN), "9820000004"],
    ]
    f = sheet(singles_rows, ["Name", "Club", "City", "Rating", "Seed", "Email", "Phone"])
    r = api("POST", "/imports/excel", files={"file": ("singles.xlsx", f,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    print("  parse -> {}".format(r.status_code))
    if r.status_code == 200:
        d = r.json()
        print("  players parsed: {}".format(len(d.get("players", []))))
        print("  errors: {}".format(d.get("errors")))
        for p in d.get("players", [])[:2]:
            print("    {}".format(json.dumps(p)))
        ts = make_tournament("ImportSingles", "round_robin", "singles")
        rc = api("POST", "/imports/confirm", data={
            "tournamentId": ts["id"], "players_json": json.dumps(d["players"])})
        print("  confirm -> {}  {}".format(rc.status_code, rc.text[:200]))
        regs = api("GET", "/tournaments/{}/registrations".format(ts["id"])).json()
        print("  registrations: {}  types={}".format(len(regs), {x["type"] for x in regs}))
        fx = api("GET", "/fixtures/" + ts["id"]).json()
        print("  fixtures: {}  ({} expected for 4 RR)".format(len(fx), 6))
        if fx:
            print("    e.g. {} vs {}".format(fx[0]["player1Name"], fx[0]["player2Name"]))

    # ================= B. DOUBLES SHEET =================
    print("\n" + "=" * 70)
    print("B. DOUBLES SHEET  (Team Name | Player 1 Name | Player 2 Name | Club | City)")
    print("=" * 70)
    doubles_rows = [
        ["Deccan Duo",   "Vikram Joshi",  "Sneha Patil",   "Deccan GC",   "Pune"],
        ["Shivaji Pair", "Rohan Bhosale", "Ananya Rane",   "Shivaji CC",  "Pune"],
        ["United Twins", "Karthik Iyer",  "Meera Nair",    "Pune United", "Pune"],
        ["Kothrud Kings","Sanjay Gokhale","Divya Shetty",  "Kothrud K",   "Pune"],
    ]
    f2 = sheet(doubles_rows, ["Team Name", "Player 1 Name", "Player 2 Name", "Club", "City"])
    r2 = api("POST", "/imports/excel", files={"file": ("doubles.xlsx", f2,
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    print("  parse -> {}".format(r2.status_code))
    if r2.status_code == 200:
        d2 = r2.json()
        print("  rows parsed: {}".format(len(d2.get("players", []))))
        print("  >>> WHAT NAME DID IT PICK UP? <<<")
        for p in d2.get("players", []):
            print("    name={!r}  club={!r}  (partner fields present: {})".format(
                p.get("name"), p.get("club"),
                [k for k in p if "partner" in k.lower() or "team" in k.lower()] or "NONE"))

        td = make_tournament("ImportDoubles", "round_robin", "doubles")
        rc2 = api("POST", "/imports/confirm", data={
            "tournamentId": td["id"], "players_json": json.dumps(d2["players"])})
        print("\n  confirm into a DOUBLES tournament -> {}  {}".format(rc2.status_code, rc2.text[:200]))
        regs2 = api("GET", "/tournaments/{}/registrations".format(td["id"])).json()
        print("  registrations: {}  types={}".format(len(regs2), {x["type"] for x in regs2}))
        print("  teams created in db: {}".format(
            admin_db.table("teams").select("id", count="exact").limit(1).execute().count))
        fx2 = api("GET", "/fixtures/" + td["id"]).json()
        print("  fixtures: {}  types={}".format(len(fx2), {m["type"] for m in fx2}))
        if fx2:
            for m in fx2[:3]:
                print("    {} vs {}".format(m["player1Name"], m["player2Name"]))

finally:
    cleanup()

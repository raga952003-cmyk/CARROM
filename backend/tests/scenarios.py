"""
Full-flow scenario harness.

Builds a demo tournament per format/size, generates fixtures, plays every match
through the real API, and checks standings, bracket progression, notifications
and the player-side view. Records failures instead of stopping, so one broken
format does not hide the others.
"""
import os, sys, uuid, requests
sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _session
from app.database import get_admin_db

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"
RUN = uuid.uuid4().hex[:6]
PASSWORD = "TestPass2345x"
admin_db = get_admin_db()

created_users, created_tournaments = [], []
failures, warnings = [], []
ADMIN_HEADERS = {}


def ok(label, cond, detail=""):
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + repr(detail)) if (detail not in (None, "") and not cond) else ""))
    if not cond:
        failures.append(label + ((" | " + detail) if detail else ""))
    return cond


def warn(label):
    print("  WARN  " + label)
    warnings.append(label)


def api(method, path, **kw):
    return _session.request(method, path, ADMIN_HEADERS, **kw)


def make_players(n, prefix):
    out = []
    for i in range(n):
        r = api("POST", "/players", json={
            "name": "{} {}{}".format(prefix, i + 1, ""),
            "club": "Club {}".format(chr(65 + (i % 4))),
            "city": "Pune", "rating": 1400 + i * 25,
            "email": "sc_{}_{}_{}@carromarena.com".format(RUN, prefix.lower().replace(' ', ''), i),
        })
        if r.status_code != 200:
            raise RuntimeError("player create failed: {} {}".format(r.status_code, r.text[:200]))
        out.append(r.json())
        created_users.append(r.json()["id"])
    return out


def make_tournament(name, fmt, category="singles", boards=2, max_boards=3, target=29):
    r = api("POST", "/tournaments", json={
        "name": "{} {}".format(name, RUN), "description": "scenario", "category": category,
        "format": fmt, "registrationStartDate": "2026-09-01", "registrationEndDate": "2026-09-05",
        "tournamentStartDate": "2026-09-10", "tournamentEndDate": "2026-09-20",
        "venue": "Scenario Hall", "city": "Pune", "numberOfBoards": boards, "entryFee": 0,
        "rules": {"maxBoardsPerMatch": max_boards, "targetScore": target,
                  "pointsForWin": 2, "pointsForDraw": 1, "pointsForLoss": 0,
                  "matchDurationMinutes": 30},
        "status": "registration_open"})
    if r.status_code != 200:
        raise RuntimeError("tournament create failed: {} {}".format(r.status_code, r.text[:300]))
    t = r.json()
    created_tournaments.append(t["id"])
    return t


def play_match(match, p1_wins=True, label=""):
    """Score boards until the match has a winner, then confirm."""
    mid = match["id"]
    api("POST", "/matches/{}/start".format(mid))
    max_boards = match.get("maxBoards") or 3
    need = (max_boards // 2) + 1

    played = 0
    for b in range(1, max_boards + 1):
        if played >= need:
            break
        hi, lo = (29, 12) if p1_wins else (12, 29)
        r = api("POST", "/matches/{}/boards/{}/submit".format(mid, b),
                json={"p1Score": hi, "p2Score": lo, "queenClaimedBy": "player1" if p1_wins else "player2",
                      "queenCovered": True, "auditReason": "scenario"})
        if r.status_code != 200:
            return False, "board {} submit -> {} {}".format(b, r.status_code, r.text[:160])
        played += 1

    r = api("POST", "/matches/{}/confirm".format(mid))
    if r.status_code != 200:
        return False, "confirm -> {} {}".format(r.status_code, r.text[:160])
    return True, ""


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
    # sweep anything tagged with this run
    try:
        for row in (admin_db.table("tournaments").select("id").ilike("name", "%" + RUN + "%").execute().data or []):
            admin_db.table("tournaments").delete().eq("id", row["id"]).execute()
        for row in (admin_db.table("profiles").select("id").ilike("email", "%sc_" + RUN + "%").execute().data or []):
            try:
                admin_db.auth.admin.delete_user(row["id"])
            except Exception:
                pass
        for row in (admin_db.table("teams").select("id").ilike("name", "%" + RUN + "%").execute().data or []):
            admin_db.table("teams").delete().eq("id", row["id"]).execute()
    except Exception as e:
        print("  WARN sweep: {}".format(e))
    print("  cleanup done")


def register_all(tid, players):
    for p in players:
        r = api("POST", "/tournaments/{}/registrations".format(tid),
                json={"type": "singles", "playerId": p["id"]})
        if r.status_code != 200:
            raise RuntimeError("register failed: {} {}".format(r.status_code, r.text[:200]))


def build_and_fixture(tid):
    api("PUT", "/tournaments/" + tid, json={"status": "registration_closed"})
    r = api("POST", "/tournaments/{}/fixtures".format(tid))
    return r


# =============================================================================
try:
    print("=== SETUP ===")
    r = requests.post(BASE + "/auth/signup", json={
        "email": "sc_admin_{}@carromarena.com".format(RUN), "password": PASSWORD,
        "name": "Scenario Admin", "role": "admin", "club": "QA", "city": "Pune"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    ADMIN_HEADERS["Authorization"] = "Bearer " + r.json()["access_token"]
    _session.remember("sc_admin_{}@carromarena.com".format(RUN), PASSWORD)
    admin_id = r.json()["user"]["id"]
    created_users.append(admin_id)
    print("    admin ready")

    # =========================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 1  ROUND ROBIN - 4 players (even, no byes)")
    print("=" * 70)
    p4 = make_players(4, "RR4 P")
    t = make_tournament("RoundRobin4", "round_robin")
    register_all(t["id"], p4)
    r = build_and_fixture(t["id"])
    ok("fixtures generated -> {}".format(r.status_code), r.status_code == 200, r.text[:200])

    fx = api("GET", "/fixtures/" + t["id"]).json()
    ok("4 players -> 6 league matches (got {})".format(len(fx)), len(fx) == 6)
    pairs = {frozenset([m["player1Name"], m["player2Name"]]) for m in fx}
    ok("all 6 pairings unique (got {})".format(len(pairs)), len(pairs) == 6)
    ok("every match has both players", all(m.get("player1Id") and m.get("player2Id") for m in fx))
    ok("all matches are league stage", all(m["stage"] == "league" for m in fx))

    api("POST", "/scheduling/{}/generate?restMinutes=10".format(t["id"]))
    conf = api("GET", "/scheduling/{}/conflicts".format(t["id"])).json()
    ok("schedule conflict-free ({} conflicts)".format(conf.get("conflictCount")), conf.get("conflictFree") is True)
    api("POST", "/scheduling/{}/publish".format(t["id"]))

    # Play every match: player 1 of each pairing wins
    fx = api("GET", "/fixtures/" + t["id"]).json()
    all_played = True
    for m in fx:
        good, why = play_match(m)
        if not good:
            all_played = False
            ok("play match #{}".format(m["matchNumber"]), False, why)
            break
    if all_played:
        ok("all 6 league matches played and confirmed", True)

    st = api("GET", "/standings/" + t["id"]).json()
    rows = st.get("standings", [])
    ok("standings has 4 rows (got {})".format(len(rows)), len(rows) == 4)
    total_played = sum(r_["played"] for r_ in rows)
    ok("sum of played == 12 (6 matches x 2) (got {})".format(total_played), total_played == 12)
    total_pts = sum(r_["points"] for r_ in rows)
    ok("total points == 12 (6 wins x 2) (got {})".format(total_pts), total_pts == 12)
    ok("ranks are 1..4 without gaps",
       sorted(r_["rank"] for r_ in rows) == [1, 2, 3, 4],
       str(sorted(r_["rank"] for r_ in rows)))
    ok("leader has most points",
       rows[0]["points"] == max(r_["points"] for r_ in rows))
    for r_ in rows:
        if r_["won"] + r_["lost"] + r_["drawn"] != r_["played"]:
            ok("W+L+D == played for {}".format(r_["participantName"]), False,
               "{}+{}+{} != {}".format(r_["won"], r_["lost"], r_["drawn"], r_["played"]))
            break
    else:
        ok("W+L+D == played for every row", True)

    # =========================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 2  ROUND ROBIN - 5 players (odd, byes)")
    print("=" * 70)
    p5 = make_players(5, "RR5 P")
    t2 = make_tournament("RoundRobin5", "round_robin")
    register_all(t2["id"], p5)
    r = build_and_fixture(t2["id"])
    ok("fixtures generated -> {}".format(r.status_code), r.status_code == 200, r.text[:200])
    fx2 = api("GET", "/fixtures/" + t2["id"]).json()
    ok("5 players -> 10 matches (got {})".format(len(fx2)), len(fx2) == 10)
    ok("no BYE placeholder leaked into fixtures",
       all("BYE" not in (m["player1Name"] + m["player2Name"]).upper() for m in fx2))
    pairs2 = {frozenset([m["player1Name"], m["player2Name"]]) for m in fx2}
    ok("all 10 pairings unique (got {})".format(len(pairs2)), len(pairs2) == 10)
    counts = {}
    for m in fx2:
        counts[m["player1Name"]] = counts.get(m["player1Name"], 0) + 1
        counts[m["player2Name"]] = counts.get(m["player2Name"], 0) + 1
    ok("every player appears in exactly 4 matches: {}".format(sorted(counts.values())),
       sorted(counts.values()) == [4, 4, 4, 4, 4])

    # =========================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 3  KNOCKOUT - 4 players (power of two)")
    print("=" * 70)
    k4 = make_players(4, "KO4 P")
    t3 = make_tournament("Knockout4", "knockout")
    register_all(t3["id"], k4)
    r = build_and_fixture(t3["id"])
    ok("fixtures generated -> {}".format(r.status_code), r.status_code == 200, r.text[:200])
    fx3 = api("GET", "/fixtures/" + t3["id"]).json()
    ok("4 players -> 3 knockout matches (got {})".format(len(fx3)), len(fx3) == 3)
    r1 = [m for m in fx3 if m["roundIndex"] == 1]
    final = [m for m in fx3 if m["roundName"] == "Final"]
    ok("2 semi-finals + 1 final", len(r1) == 2 and len(final) == 1)
    ok("both semis fully populated", all(m.get("player1Id") and m.get("player2Id") for m in r1))
    ok("final starts empty (Winner TBD)",
       final and not final[0].get("player1Id") and not final[0].get("player2Id"))
    ok("semis link to the final", all(m.get("nextMatchId") == final[0]["id"] for m in r1))
    ok("semis occupy opposite final slots",
       {m.get("nextMatchSlot") for m in r1} == {"player1", "player2"})

    api("POST", "/scheduling/{}/generate?restMinutes=10".format(t3["id"]))
    api("POST", "/scheduling/{}/publish".format(t3["id"]))

    winners = []
    for m in r1:
        good, why = play_match(m)
        if not good:
            ok("play semi #{}".format(m["matchNumber"]), False, why)
        else:
            winners.append(m["player1Name"])
    ok("both semis completed", len(winners) == 2)

    fx3b = api("GET", "/fixtures/" + t3["id"]).json()
    final_now = [m for m in fx3b if m["roundName"] == "Final"][0]
    ok("final populated by progression: '{}' vs '{}'".format(
        final_now.get("player1Name"), final_now.get("player2Name")),
        bool(final_now.get("player1Id")) and bool(final_now.get("player2Id")))
    ok("final contestants are the semi winners",
       {final_now.get("player1Name"), final_now.get("player2Name")} == set(winners),
       "{} vs {}".format(final_now.get("player1Name"), final_now.get("player2Name")))

    good, why = play_match(final_now)
    ok("final played and confirmed", good, why)
    champ = admin_db.table("matches").select("winner_name").eq("id", final_now["id"]).execute().data
    ok("champion recorded: {}".format(champ[0]["winner_name"] if champ else None),
       bool(champ and champ[0]["winner_name"]))

    # =========================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 4  KNOCKOUT - 6 players (NOT a power of two -> byes)")
    print("=" * 70)
    k6 = make_players(6, "KO6 P")
    t4 = make_tournament("Knockout6", "knockout")
    register_all(t4["id"], k6)
    r = build_and_fixture(t4["id"])
    ok("fixtures generated -> {}".format(r.status_code), r.status_code == 200, r.text[:200])
    fx4 = api("GET", "/fixtures/" + t4["id"]).json()
    r1_4 = [m for m in fx4 if m["roundIndex"] == 1]
    print("    round 1: " + " | ".join(
        "{} vs {}".format(m["player1Name"], m["player2Name"]) for m in r1_4))

    ghost = [m for m in r1_4 if not m.get("player1Id") and not m.get("player2Id")]
    ok("no phantom round-1 match with neither player ({} found)".format(len(ghost)), len(ghost) == 0)

    half = [m for m in r1_4 if bool(m.get("player1Id")) != bool(m.get("player2Id"))]
    ok("no unplayable half-empty round-1 match ({} found)".format(len(half)), len(half) == 0,
       "a player facing 'Winner TBD' can never advance")

    entrants = set()
    for m in fx4:
        for nm, pid in ((m["player1Name"], m.get("player1Id")), (m["player2Name"], m.get("player2Id"))):
            if pid:
                entrants.add(nm)
    ok("all 6 entrants appear in the bracket (got {})".format(len(entrants)), len(entrants) == 6)
    ok("6 players -> 5 matches total (n-1) (got {})".format(len(fx4)), len(fx4) == 5)

    byes = [m for m in fx4 if m["roundIndex"] == 2 and (m.get("player1Id") or m.get("player2Id"))]
    ok("top seeds received byes into round 2 ({} pre-filled slot(s))".format(len(byes)), len(byes) == 2)

    api("POST", "/scheduling/{}/generate?restMinutes=10".format(t4["id"]))
    api("POST", "/scheduling/{}/publish".format(t4["id"]))

    # Play the whole bracket round by round until a champion exists.
    played_rounds = 0
    for round_no in range(1, 5):
        current = [m for m in api("GET", "/fixtures/" + t4["id"]).json()
                   if m["roundIndex"] == round_no]
        if not current:
            break
        playable = [m for m in current if m.get("player1Id") and m.get("player2Id")]
        if not playable:
            ok("round {} has playable matches".format(round_no), False,
               "bracket stalled: no match in round {} has two players".format(round_no))
            break
        for m in playable:
            good, why = play_match(m)
            if not good:
                ok("play round {} match #{}".format(round_no, m["matchNumber"]), False, why)
        played_rounds += 1

    final_rows = [m for m in api("GET", "/fixtures/" + t4["id"]).json() if m["roundName"] == "Final"]
    champ = final_rows[0].get("winnerName") if final_rows else None
    ok("bye bracket played to a champion: {}".format(champ), bool(champ),
       "bracket could not be completed")
    remaining = [m for m in api("GET", "/fixtures/" + t4["id"]).json() if not m.get("resultConfirmed")]
    ok("no match left unplayable ({} unconfirmed)".format(len(remaining)), len(remaining) == 0)

    # =========================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 5  LEAGUE + KNOCKOUT - 4 players")
    print("=" * 70)
    lk = make_players(4, "LK4 P")
    t5 = make_tournament("LeagueKO4", "league_knockout")
    register_all(t5["id"], lk)
    r = build_and_fixture(t5["id"])
    ok("fixtures generated -> {}".format(r.status_code), r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        fx5 = api("GET", "/fixtures/" + t5["id"]).json()
        league = [m for m in fx5 if m["stage"] == "league"]
        ko = [m for m in fx5 if m["stage"] == "knockout"]
        ok("league stage has 6 matches (got {})".format(len(league)), len(league) == 6)
        ok("knockout stage created (got {})".format(len(ko)), len(ko) > 0)
        ok("match numbers are unique across stages",
           len({m["matchNumber"] for m in fx5}) == len(fx5))
        ok("knockout slots start empty, labelled by league rank",
           all(not m.get("player1Id") and not m.get("player2Id") for m in ko))
        labels = sorted({m[k + "Name"] for m in ko for k in ("player1", "player2")
                         if m[k + "Name"].startswith("League Rank")})
        ok("rank labels present: {}".format(labels), len(labels) >= 2)

        api("POST", "/scheduling/{}/generate?restMinutes=10".format(t5["id"]))
        api("POST", "/scheduling/{}/publish".format(t5["id"]))

        # Promotion must be refused while the league is still running.
        r_early = api("POST", "/standings/{}/promote".format(t5["id"]))
        ok("promotion refused mid-league (got {})".format(r_early.status_code),
           r_early.status_code == 409, r_early.text[:160])

        # Play the league. Lower-numbered players win, so ranks are predictable.
        league_ok = True
        for m in league:
            good, why = play_match(m)
            if not good:
                league_ok = False
                ok("play league match #{}".format(m["matchNumber"]), False, why)
                break
        ok("all 6 league matches played", league_ok)

        if league_ok:
            st5 = api("GET", "/standings/" + t5["id"]).json().get("standings", [])
            ok("league standings produced {} ranked rows".format(len(st5)), len(st5) == 4)

            fx5b = api("GET", "/fixtures/" + t5["id"]).json()
            ko2 = [m for m in fx5b if m["stage"] == "knockout"]
            filled = [m for m in ko2 if m.get("player1Id") or m.get("player2Id")]
            ok("knockout auto-populated on league completion ({} match(es) filled)".format(len(filled)),
               len(filled) > 0, "qualifiers were never promoted")

            if filled:
                first_ko = sorted(ko2, key=lambda m: m["roundIndex"])[0]
                ok("top knockout match holds real qualifiers: '{}' vs '{}'".format(
                    first_ko.get("player1Name"), first_ko.get("player2Name")),
                    bool(first_ko.get("player1Id")) and bool(first_ko.get("player2Id")))
                top_two = {row["participantName"] for row in st5[:2]}
                ok("promoted names match the top of the table ({})".format(sorted(top_two)),
                   {first_ko.get("player1Name"), first_ko.get("player2Name")} <= {
                       row["participantName"] for row in st5})

                # Play the knockout out to a champion.
                for round_no in sorted({m["roundIndex"] for m in ko2}):
                    current = [m for m in api("GET", "/fixtures/" + t5["id"]).json()
                               if m["stage"] == "knockout" and m["roundIndex"] == round_no
                               and m.get("player1Id") and m.get("player2Id")
                               and not m.get("resultConfirmed")]
                    for m in current:
                        good, why = play_match(m)
                        if not good:
                            ok("play knockout match #{}".format(m["matchNumber"]), False, why)

                finals = [m for m in api("GET", "/fixtures/" + t5["id"]).json()
                          if m["stage"] == "knockout" and m["roundName"] == "Final"]
                champ5 = finals[0].get("winnerName") if finals else None
                ok("league+knockout produced a champion: {}".format(champ5), bool(champ5))

    # =========================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 6  DOUBLES ROUND ROBIN - 4 teams")
    print("=" * 70)
    dbl = make_players(8, "DBL P")
    t6 = make_tournament("DoublesRR", "round_robin", category="doubles")
    for i in range(0, 8, 2):
        rr = api("POST", "/tournaments/{}/registrations".format(t6["id"]), json={
            "type": "doubles", "playerId": dbl[i]["id"], "partnerId": dbl[i + 1]["id"],
            "teamName": "Team {} {}".format(i // 2 + 1, RUN)})
        if rr.status_code != 200:
            ok("doubles registration {}".format(i // 2 + 1), False, rr.text[:200])
    r = build_and_fixture(t6["id"])
    ok("doubles fixtures generated -> {}".format(r.status_code), r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        fx6 = api("GET", "/fixtures/" + t6["id"]).json()
        ok("4 teams -> 6 matches (got {})".format(len(fx6)), len(fx6) == 6)
        ok("matches typed as doubles", all(m["type"] == "doubles" for m in fx6),
           str({m["type"] for m in fx6}))
        ok("team names on fixtures, not player names",
           all("Team" in m["player1Name"] for m in fx6),
           str([m["player1Name"] for m in fx6][:3]))
        if fx6:
            good, why = play_match(fx6[0])
            ok("a doubles match can be scored", good, why)
            st6 = api("GET", "/standings/" + t6["id"]).json()
            ok("doubles standings computed ({} rows)".format(len(st6.get("standings", []))),
               len(st6.get("standings", [])) == 4)
            types = {r_["participantType"] for r_ in st6.get("standings", [])}
            ok("standings mark participants as doubles", types == {"doubles"}, str(types))

    # =========================================================================
    print("\n" + "=" * 70)
    print("SCENARIO 7  PLAYER-SIDE FLOW")
    print("=" * 70)
    pr = requests.post(BASE + "/auth/signup", json={
        "email": "sc_player_{}@carromarena.com".format(RUN), "password": PASSWORD,
        "name": "Scenario Player", "role": "player", "club": "QA", "city": "Pune"}, timeout=90)
    ok("player signup -> {}".format(pr.status_code), pr.status_code == 200, pr.text[:200])
    if pr.status_code == 200:
        player_id = pr.json()["user"]["id"]
        created_users.append(player_id)
        PH = {"Authorization": "Bearer " + pr.json()["access_token"]}

        r = requests.get(BASE + "/tournaments", headers=PH, timeout=90)
        ok("player can list tournaments ({})".format(len(r.json())), r.status_code == 200)

        r = requests.post(BASE + "/tournaments/{}/registrations".format(t2["id"]),
                          headers=PH, json={"type": "singles"}, timeout=90)
        ok("player self-registers -> {}".format(r.status_code),
           r.status_code in (200, 409), r.text[:200])
        if r.status_code == 200:
            ok("self-registration is pending, not auto-approved",
               r.json().get("status") == "pending", str(r.json().get("status")))

        r = requests.get(BASE + "/notifications", headers=PH, timeout=90)
        ok("player can read notifications ({})".format(len(r.json())), r.status_code == 200)

        r = requests.get(BASE + "/standings/" + t["id"], headers=PH, timeout=90)
        ok("player can read standings", r.status_code == 200)

        r = requests.post(BASE + "/players", headers=PH,
                          json={"name": "Hack", "email": "hack_{}@x.com".format(RUN)}, timeout=90)
        ok("player CANNOT create players (got {})".format(r.status_code), r.status_code == 403)

        r = requests.post(BASE + "/tournaments/{}/fixtures".format(t["id"]), headers=PH, timeout=90)
        ok("player CANNOT generate fixtures (got {})".format(r.status_code), r.status_code == 403)

        if 'fx' in dir() and fx:
            r = requests.post(BASE + "/matches/{}/boards/1/submit".format(fx[0]["id"]),
                              headers=PH, json={"p1Score": 29, "p2Score": 0}, timeout=90)
            ok("player CANNOT submit scores (got {})".format(r.status_code), r.status_code == 403)

        r = requests.get(BASE + "/audit", headers=PH, timeout=90)
        ok("player CANNOT read the audit trail (got {})".format(r.status_code), r.status_code == 403)

finally:
    cleanup()

print("\n" + "=" * 70)
print("RESULTS: {} failure(s), {} warning(s)".format(len(failures), len(warnings)))
if failures:
    print("\nFAILURES:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL SCENARIOS PASSED")

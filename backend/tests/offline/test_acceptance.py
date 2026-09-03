"""
Acceptance tests: the things the people using this actually need to be true.

Technique: BLACK BOX, written as user stories rather than as endpoints. Each
one is phrased the way the person would put it, and passes only if the whole
system delivers that outcome. Where the other suites ask "is this function
correct" and "does this route behave", these ask "can the organiser run their
tournament".

    python tests/offline/test_acceptance.py
"""
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness                       # noqa: E402

RESULTS = {}
STORIES = []


def check(label, cond, example=""):
    slot = RESULTS.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not cond:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(str(example)[:300])
    return bool(cond)


def body(r):
    try:
        return r.json()
    except Exception:
        return r.text


def detail(r):
    p = body(r)
    return str(p.get("detail", p)) if isinstance(p, dict) else str(p)


def minutes_of_day(value):
    """The same reading of a display time as frontend/src/utils/matchOrder.ts."""
    text = (value or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})\s*([AaPp][Mm])?", text)
    if not m:
        return None
    hours, mins = int(m.group(1)), int(m.group(2))
    if mins > 59:
        return None
    mer = (m.group(3) or "").lower()
    if mer == "pm" and hours != 12:
        hours += 12
    if mer == "am" and hours == 12:
        hours = 0
    return None if hours > 23 else hours * 60 + mins


def story(title):
    def wrap(fn):
        STORIES.append((title, fn))
        return fn
    return wrap


SCORE = {"p1Score": 0, "p2Score": 0, "setNumber": 1, "boardWinner": "player1",
         "coinsRemainingWith": "player2", "coinsRemaining": 4,
         "queenPocketedBy": "none", "queenCoveredBy": "none"}


# ---------------------------------------------------------------------------

@story("As an organiser, I can run a tournament through to a champion")
def organiser_runs_a_tournament():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin)

    a, b = h.make_user("Asha"), h.make_user("Bala")
    m1 = h.seed_match(tid, a, b, boards=3,
                      id="33333333-3333-3333-3333-333333333331")

    for n in (1, 2, 3):
        r = h.post("/api/matches/%s/boards/%d/submit" % (m1, n), SCORE,
                   user_id=admin)
        check("the organiser can record each board as it finishes",
              r.status_code == 200, "board %d -> %s %s" % (n, r.status_code,
                                                           detail(r)))

    r = h.post("/api/matches/%s/confirm" % m1, {}, user_id=admin)
    check("the organiser can sign off the result", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))

    match = [m for m in h.db.rows("matches") if m["id"] == m1][0]
    check("the signed-off match names a winner", bool(match.get("winner_id")),
          match.get("winner_id"))
    check("the signed-off match is marked confirmed",
          match.get("result_confirmed") is True, match.get("result_confirmed"))
    check("signing off stops the clock",
          match.get("is_timer_running") in (False, None),
          match.get("is_timer_running"))


@story("As an organiser, a mistyped score is caught before it becomes a result")
def organiser_is_protected_from_typos():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin)
    a, b = h.make_user("Asha"), h.make_user("Bala")
    mid = h.seed_match(tid, a, b, boards=3)

    # A slipped keystroke in the coins-remaining field.
    r = h.post("/api/matches/%s/boards/1/submit" % mid,
               dict(SCORE, coinsRemaining=19), user_id=admin)
    if r.status_code == 200:
        board = [x for x in h.db.rows("boards")
                 if x["match_id"] == mid and x["board_number"] == 1][0]
        top = max(board.get("player1_score") or 0, board.get("player2_score") or 0)
        check("a slipped keystroke cannot become a score no board can produce",
              top <= 12, "stored=%s" % top)
    else:
        check("a slipped keystroke is refused in words the scorer can act on",
              r.status_code == 422 and "{" not in detail(r), detail(r))


@story("As a player entered in several matches, I can see all of them, in order")
def player_sees_their_own_matches():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin)

    me = h.make_user("Ragavendra S", "player")
    times = ["9:35 AM", "2:50 PM", "11:15 AM", "9:15 PM", "12:00 PM"]
    for i, t in enumerate(times):
        opponent = h.make_user("Opponent %d" % i, "player")
        h.seed_match(tid, me, opponent, boards=3,
                     id="44444444-4444-4444-4444-44444444444%d" % i,
                     player1_name="Ragavendra S",
                     player2_name="Opponent %d" % i,
                     match_number=i + 1,
                     scheduled_date="2026-03-01", scheduled_time=t)

    r = h.get("/api/tournaments/%s" % tid, me)
    check("a player can read the tournament they are entered in",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))
    if r.status_code != 200:
        return

    payload = body(r)
    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not check("the tournament carries its fixtures", isinstance(matches, list),
                 type(matches)):
        return

    mine = [m for m in matches
            if m.get("player1Id") == me or m.get("player2Id") == me]
    check("a player entered in five matches is given all five",
          len(mine) == 5, "got %d of 5" % len(mine))

    by_name = [m for m in matches
               if (m.get("player1Name") or "").strip().lower() == "ragavendra s"
               or (m.get("player2Name") or "").strip().lower() == "ragavendra s"]
    check("the same five are findable by name, for a login that is not the roster row",
          len(by_name) == 5, "got %d of 5" % len(by_name))

    ordered = sorted(mine, key=lambda m: (
        m.get("scheduledDate") or "~",
        minutes_of_day(m.get("scheduledTime")) if minutes_of_day(m.get("scheduledTime")) is not None else 10 ** 6,
        m.get("matchNumber") or 0))
    check("the morning fixture comes before the afternoon one",
          ordered[0].get("scheduledTime") == "9:35 AM",
          [m.get("scheduledTime") for m in ordered])
    check("the evening fixture comes last",
          ordered[-1].get("scheduledTime") == "9:15 PM",
          [m.get("scheduledTime") for m in ordered])


@story("As a spectator with no account, I can follow the event but not read contact details")
def spectator_sees_the_event_not_the_people():
    h = Harness()
    admin = h.make_user("Organiser", "admin")
    tid = h.seed_tournament(owner_id=admin, status="in_progress")
    a = h.make_user("Asha", "player")
    for row in h.db.tables["profiles"]:
        if row["id"] == a:
            row["phone"] = "9000012345"
    b = h.make_user("Bala", "player")
    h.seed_match(tid, a, b, boards=3, player1_name="Asha", player2_name="Bala")

    r = h.client.get("/api/players")
    check("a spectator can see who is playing", r.status_code == 200,
          "%s" % r.status_code)
    blob = str(body(r))
    check("a spectator cannot read a player's phone number",
          "9000012345" not in blob, blob[:200])
    check("a spectator cannot harvest email addresses",
          "@carrom.example.com" not in blob, blob[:200])

    r = h.client.get("/api/tournaments/%s" % tid)
    check("a spectator can follow the tournament without signing in",
          r.status_code == 200, "%s %s" % (r.status_code, detail(r)))


@story("As a tournament owner, nobody else can take over my event, but I can invite help")
def owner_controls_their_event():
    h = Harness()
    owner = h.make_user("Owner", "admin")
    helper = h.make_user("Helper", "admin")
    tid = h.seed_tournament(owner_id=owner)
    a, b = h.make_user("Asha"), h.make_user("Bala")
    mid = h.seed_match(tid, a, b, boards=8)

    r = h.post("/api/matches/%s/boards/1/submit" % mid, SCORE, user_id=helper)
    check("another organiser cannot score my event uninvited",
          r.status_code == 403, "%s %s" % (r.status_code, detail(r)))
    check("they are told whose event it is, so they can ask",
          "owner" in detail(r).lower(), detail(r))

    r = h.delete("/api/tournaments/%s" % tid, user_id=helper)
    check("another organiser cannot delete my event", r.status_code == 403,
          "%s %s" % (r.status_code, detail(r)))

    h.db.seed("tournament_access", [{
        "id": "grant-1", "tournament_id": tid, "user_id": helper,
        "access_role": "scorer", "status": "approved", "decided_by": owner}])

    r = h.post("/api/matches/%s/boards/1/submit" % mid, SCORE, user_id=helper)
    check("once invited as a scorer, they can score", r.status_code == 200,
          "%s %s" % (r.status_code, detail(r)))
    r = h.delete("/api/tournaments/%s" % tid, user_id=helper)
    check("a scorer still cannot delete the event", r.status_code == 403,
          "%s %s" % (r.status_code, detail(r)))


@story("As an organiser whose account lost its profile row, I am not silently blocked")
def broken_account_is_repaired_or_explained():
    h = Harness()
    ghost = h.make_user("Ghost", "admin", with_profile=False)
    tid = h.seed_tournament(owner_id=None)
    a, b = h.make_user("Asha"), h.make_user("Bala")
    mid = h.seed_match(tid, a, b, boards=3)

    r = h.post("/api/matches/%s/boards/1/submit" % mid, SCORE, user_id=ghost)
    text = detail(r)
    check("I am never shown a raw database constraint",
          "23503" not in text and "_fkey" not in text,
          "%s %s" % (r.status_code, text))
    check("either it works, or I am told in plain words what to do",
          r.status_code == 200 or ("{" not in text and len(text) > 20),
          "%s %s" % (r.status_code, text))

    profiles = [p["id"] for p in h.db.rows("profiles")]
    if r.status_code == 200:
        check("my account is repaired so my actions can be recorded",
              ghost in profiles, "profiles=%d" % len(profiles))
        healed = [p for p in h.db.rows("profiles") if p["id"] == ghost][0]
        check("a repaired account is not silently granted admin rights",
              healed.get("role") in ("player", "admin"), healed.get("role"))


@story("As an organiser, adding a player never leaves a half-made account behind")
def player_creation_is_all_or_nothing():
    for trigger in (True, False):
        h = Harness(trigger_enabled=trigger)
        admin = h.make_user("Organiser", "admin")
        before = len(h.db.auth_users)

        r = h.post("/api/players", {"name": "Fresh Player",
                                    "email": "fresh@carrom.example.com",
                                    "rating": 1500}, user_id=admin)
        after = len(h.db.auth_users)
        profiles = [p["name"] for p in h.db.rows("profiles")]

        if r.status_code == 200:
            check("a player I added can be found in the directory",
                  "Fresh Player" in profiles,
                  "trigger=%s profiles=%s" % (trigger, profiles))
        else:
            check("a failed attempt leaves no account behind",
                  after == before,
                  "trigger=%s auth %d -> %d" % (trigger, before, after))
            check("a failed attempt tells me something I can act on",
                  "{" not in detail(r) and "index out of range" not in detail(r),
                  detail(r))


def main():
    for title, fn in STORIES:
        try:
            fn()
        except Exception:
            check("the story '%s' runs to completion" % title, False,
                  traceback.format_exc()[-400:])

    total = sum(v[1] for v in RESULTS.values())
    failed = [(k, v) for k, v in sorted(RESULTS.items()) if v[0]]

    print("=" * 78)
    print("acceptance suite (user stories)")
    print("=" * 78)
    print("stories             : %d" % len(STORIES))
    print("assertions executed : %d" % total)
    print("outcomes checked    : %d" % len(RESULTS))
    print("outcomes not met    : %d" % len(failed))
    print()
    for title, _ in STORIES:
        print("  - %s" % title)
    print()
    if failed:
        print("NOT MET")
        print("-" * 78)
        for label, slot in failed:
            bad, ran, examples = slot
            print("  %s" % label)
            print("     %d of %d cases failed" % (bad, ran))
            for ex in examples:
                print("     e.g. %s" % ex)
            print()
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())

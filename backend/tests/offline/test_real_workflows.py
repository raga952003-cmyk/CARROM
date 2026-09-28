"""180 HTTP workflow scenarios across roles, event phases, and write actions.

The production API and permission dependencies run against the in-memory
Supabase replacement. No live accounts, bank payments, or database rows change.
These cases focus on cross-flow boundaries; the payment, scoring, and draw
suites exercise their detailed state machines separately.
"""
import os
import sys
import uuid
import logging
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import Harness  # noqa: E402
logging.getLogger("httpx").setLevel(logging.WARNING)


PHASES = ("draft", "registration_open", "registration_closed", "in_progress", "completed")
ROLES = ("owner", "manager", "scorer", "outsider_admin", "player", "anonymous")
ACTIONS = ("rename", "patch_rest", "set_status", "set_published", "add_unapproved_match", "publish_empty_draw")
RESULTS = []


def _detail(response):
    try:
        return response.json().get("detail", "")
    except Exception:
        return response.text[:160]


def run_matrix():
    for phase in PHASES:
        h = Harness()
        owner = h.make_user("Matrix Owner", "admin")
        manager = h.make_user("Matrix Manager", "admin")
        scorer = h.make_user("Matrix Scorer", "admin")
        outsider = h.make_user("Matrix Outsider", "admin")
        player = h.make_user("Matrix Player", "player")
        tid = h.seed_tournament(owner, status=phase, schedule_published=False)
        for role, actor in (("manager", manager), ("scorer", scorer)):
            h.db.seed("tournament_access", [{
                "id": str(uuid.uuid4()), "tournament_id": tid,
                "user_id": actor, "access_role": role, "status": "approved",
                "decided_by": owner,
            }])
        identities = {
            "owner": owner, "manager": manager, "scorer": scorer,
            "outsider_admin": outsider, "player": player, "anonymous": None,
        }
        path = "/api/tournaments/%s" % tid
        original_rules = dict(next(row for row in h.db.rows("tournaments") if row["id"] == tid)["rules"])

        for role in ROLES:
            actor = identities[role]
            privileged = role in ("owner", "manager")
            for action in ACTIONS:
                row = next(row for row in h.db.rows("tournaments") if row["id"] == tid)
                before = (row.get("status"), row.get("schedule_published"),
                          row.get("name"), len(h.db.rows("matches")))
                if action == "rename":
                    response = h.put(path, {"name": "Matrix Event"}, user_id=actor)
                elif action == "patch_rest":
                    response = h.put(path, {"rules": {"restTimeMinutes": 20}}, user_id=actor)
                elif action == "set_status":
                    response = h.put(path, {"status": "registration_open"}, user_id=actor)
                elif action == "set_published":
                    response = h.put(path, {"schedulePublished": True}, user_id=actor)
                elif action == "add_unapproved_match":
                    response = h.post(path + "/matches", {
                        "stage": "league", "player1Id": str(uuid.uuid4()),
                        "player2Id": str(uuid.uuid4()),
                    }, user_id=actor)
                else:
                    response = h.post(path + "/publish-schedule", {}, user_id=actor)
                row = next(row for row in h.db.rows("tournaments") if row["id"] == tid)

                if not privileged:
                    accepted = response.status_code in (401, 403)
                elif phase == "completed":
                    accepted = response.status_code == 409
                elif action in ("rename", "patch_rest"):
                    accepted = response.status_code == 200
                elif action == "add_unapproved_match":
                    accepted = response.status_code == 422
                else:
                    accepted = response.status_code == 409

                # Every refusal must be side-effect free. Allowed edits must
                # preserve the unrelated lifecycle state and the full ruleset.
                if action not in ("rename", "patch_rest") or not privileged or phase == "completed":
                    accepted = accepted and before == (
                        row.get("status"), row.get("schedule_published"),
                        row.get("name"), len(h.db.rows("matches")))
                if action == "patch_rest" and privileged and phase != "completed":
                    accepted = accepted and row["rules"].get("restTimeMinutes") == 20 \
                        and row["rules"].get("scoringMode") == original_rules["scoringMode"] \
                        and row["rules"].get("coinsPerSide") == original_rules["coinsPerSide"]
                RESULTS.append({
                    "scenario": "%s / %s / %s" % (phase, role, action),
                    "ok": accepted, "status": response.status_code,
                    "detail": str(_detail(response))[:160],
                })


def run_fixture_edges():
    """Late group entries and edits to a schedule players have already seen."""
    h = Harness()
    owner = h.make_user("Group Owner", "admin")
    players = [h.make_user("Group P%d" % n) for n in range(1, 8)]
    tid = h.seed_tournament(owner, format="group_stage", type="singles",
                            rules={"scoringMode": "classic", "groupCount": 2,
                                   "numberOfSets": 1, "boardsPerSet": 1,
                                   "maxBoardsPerMatch": 1})
    h.db.seed("registrations", [{
        "id": str(uuid.uuid4()), "tournament_id": tid, "type": "singles",
        "player_id": player, "status": "approved", "payment_status": "waived",
    } for player in players])
    for number, (p1, p2, group) in enumerate((
            (players[0], players[1], "A"), (players[2], players[3], "B")), 1):
        h.db.seed("matches", [{
            "id": str(uuid.uuid4()), "tournament_id": tid,
            "match_number": number, "round_index": 0, "round_name": "Group " + group,
            "stage": "league", "type": "singles", "player1_id": p1,
            "player2_id": p2, "player1_name": "P1", "player2_name": "P2",
            "status": "scheduled", "board_number": number,
            "bracket_position": {"group": group},
        }])
    path = "/api/tournaments/%s/matches" % tid

    def add(label, p1, p2, expected, **extra):
        response = h.post(path, {"stage": "league", "player1Id": p1,
                                 "player2Id": p2, **extra}, user_id=owner)
        match = response.json() if response.status_code == 200 else {}
        group = (match.get("bracketPosition") or {}).get("group")
        good = response.status_code == expected
        if expected == 200:
            good = good and group == extra.get("group", "A")
        RESULTS.append({"scenario": label, "ok": good,
                        "status": response.status_code, "detail": _detail(response)})

    add("group rematch inherits A", players[0], players[1], 200)
    add("cross-group league fixture refused", players[0], players[2], 422)
    add("late entrant joins existing A", players[0], players[4], 200)
    add("two unassigned entrants need a group", players[5], players[6], 422)
    add("two unassigned entrants explicitly join B", players[5], players[6], 200, group="B")

    h = Harness()
    owner = h.make_user("Published Owner", "admin")
    players = [h.make_user("Published P%d" % n) for n in range(1, 5)]
    tid = h.seed_tournament(owner, format="round_robin", type="singles",
                            schedule_published=True, number_of_boards=2,
                            rules={"scoringMode": "classic", "numberOfSets": 1,
                                   "boardsPerSet": 1, "maxBoardsPerMatch": 1,
                                   "matchDurationMinutes": 30, "restTimeMinutes": 10})
    h.db.seed("registrations", [{
        "id": str(uuid.uuid4()), "tournament_id": tid, "type": "singles",
        "player_id": player, "status": "approved", "payment_status": "waived",
    } for player in players])
    start = (date.today() + timedelta(days=20)).isoformat()
    after_end = (date.today() + timedelta(days=23)).isoformat()
    h.db.seed("matches", [{
        "id": str(uuid.uuid4()), "tournament_id": tid, "match_number": 1,
        "round_index": 0, "stage": "league", "type": "singles",
        "player1_id": players[0], "player2_id": players[1],
        "status": "scheduled", "board_number": 1,
        "scheduled_date": start, "scheduled_time": "09:00",
    }])
    path = "/api/tournaments/%s/matches" % tid

    def scheduled(label, p1, p2, expected, **extra):
        count = len(h.db.rows("matches"))
        notice_count = len(h.db.rows("notifications"))
        response = h.post(path, {"stage": "league", "player1Id": p1,
                                 "player2Id": p2, **extra}, user_id=owner)
        good = response.status_code == expected
        if expected == 200:
            good = good and len(h.db.rows("matches")) == count + 1 \
                and len(h.db.rows("notifications")) == notice_count + 2
        else:
            good = good and len(h.db.rows("matches")) == count \
                and len(h.db.rows("notifications")) == notice_count
        RESULTS.append({"scenario": label, "ok": good,
                        "status": response.status_code, "detail": _detail(response)})

    scheduled("published match needs time", players[2], players[3], 422)
    scheduled("published board cannot double-book", players[2], players[3], 409,
              scheduledDate=start, scheduledTime="09:00", boardNumber=1)
    scheduled("published participant needs rest", players[0], players[2], 409,
              scheduledDate=start, scheduledTime="09:10", boardNumber=2)
    scheduled("published board must exist", players[2], players[3], 409,
              scheduledDate=start, scheduledTime="09:00", boardNumber=3)
    scheduled("published match fits event dates", players[2], players[3], 409,
              scheduledDate=after_end, scheduledTime="09:00", boardNumber=2)
    scheduled("published late match notifies its players", players[2], players[3], 200,
              scheduledDate=start, scheduledTime="09:00", boardNumber=2)


def main():
    RESULTS.clear()
    run_matrix()
    run_fixture_edges()
    failures = [result for result in RESULTS if not result["ok"]]
    print("workflow scenarios: %d; passed: %d; failed: %d" % (
        len(RESULTS), len(RESULTS) - len(failures), len(failures)))
    for failure in failures[:20]:
        print("FAIL %s: HTTP %s %s" % (failure["scenario"], failure["status"], failure["detail"]))
    if failures:
        raise AssertionError("%d workflow scenarios failed" % len(failures))
    return 0


if __name__ == "__main__":
    main()

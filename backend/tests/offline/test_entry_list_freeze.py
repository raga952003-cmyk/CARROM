"""HTTP checks that a published draw never gains an unfixtured entrant."""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness  # noqa: E402

RESULTS = {}


def check(label, condition, example=""):
    slot = RESULTS.setdefault(label, [0, 0, []])
    slot[1] += 1
    if not condition:
        slot[0] += 1
        if len(slot[2]) < 3:
            slot[2].append(str(example)[:300])


def detail(response):
    try:
        return str(response.json().get("detail", response.json()))
    except Exception:
        return response.text


def test_existing_draw_blocks_reopen_and_forced_entries():
    # The flag and the rows are checked independently. A legacy draw or a
    # successful fixture RPC followed by a failed lifecycle status write can
    # leave one present while the tournament still says registration_closed.
    for signal in ("fixtures_generated", "match_row"):
        h = Harness()
        admin = h.make_user("Draw Owner", "admin")
        entrant = h.make_user("Late Entrant")
        first = h.make_user("First Entrant")
        second = h.make_user("Second Entrant")
        tid = h.seed_tournament(
            owner_id=admin, status="registration_closed",
            fixtures_generated=(signal == "fixtures_generated"),
            category="singles",
        )
        if signal == "match_row":
            h.seed_match(tid, first, second, boards=1)

        registration_count = len(h.db.rows("registrations"))
        profile_count = len(h.db.rows("profiles"))
        reopen = h.post("/api/tournaments/%s/open-registration" % tid, {}, user_id=admin)
        check("%s stops reopening registration" % signal,
              reopen.status_code == 409 and "fixtures" in detail(reopen).lower(),
              "%s %s" % (reopen.status_code, detail(reopen)))
        check("%s leaves registration closed" % signal,
              h.db.rows("tournaments")[0]["status"] == "registration_closed")

        add = h.post("/api/tournaments/%s/registrations?force=true" % tid,
                     {"type": "singles", "playerId": entrant}, user_id=admin)
        check("%s stops a forced late entry" % signal,
              add.status_code == 409 and "fixtures" in detail(add).lower(),
              "%s %s" % (add.status_code, detail(add)))

        imported = h.client.post("/api/imports/confirm", data={
            "tournamentId": tid,
            "players_json": json.dumps([{
                "name": "Late Sheet Entrant",
                "email": "late.sheet@carrom.example.com",
            }]),
            "force": "true",
        }, headers=h.auth(admin))
        check("%s stops a forced late import" % signal,
              imported.status_code == 409 and "fixtures" in detail(imported).lower(),
              "%s %s" % (imported.status_code, detail(imported)))
        check("%s refusal makes no participants" % signal,
              len(h.db.rows("registrations")) == registration_count
              and len(h.db.rows("profiles")) == profile_count)


def test_pending_entries_block_the_draw_without_side_effects():
    h = Harness()
    admin = h.make_user("Pending Owner", "admin")
    first = h.make_user("Ready First")
    second = h.make_user("Ready Second")
    waiting = h.make_user("Waiting Player")
    tid = h.seed_tournament(
        owner_id=admin, status="registration_closed", category="singles",
        format="round_robin", entry_fee=0, fixtures_generated=False,
        number_of_boards=2,
        rules={"numberOfSets": 1, "boardsPerSet": 3, "maxBoardsPerMatch": 3,
               "targetScore": 25, "scoringMode": "remaining_coins"},
    )
    for index, (player, status) in enumerate(((first, "approved"),
                                               (second, "approved"),
                                               (waiting, "pending"))):
        h.db.seed("registrations", [{
            "id": "55555555-5555-5555-5555-5555555555%02d" % index,
            "tournament_id": tid, "type": "singles", "player_id": player,
            "status": status, "payment_status": "waived", "fee_paise": 0,
        }])

    draw = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    check("pending entrant refuses the draw",
          draw.status_code == 409 and "pending" in detail(draw).lower(),
          "%s %s" % (draw.status_code, detail(draw)))
    check("refused draw creates no matches or boards",
          not h.db.rows("matches") and not h.db.rows("boards"))
    check("refused draw leaves lifecycle and draw flag unchanged",
          h.db.rows("tournaments")[0]["status"] == "registration_closed"
          and not h.db.rows("tournaments")[0]["fixtures_generated"])

    h.db.table("registrations").update({"status": "rejected"}).eq(
        "player_id", waiting).execute()
    draw = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    check("resolving the pending entrant permits the final draw",
          draw.status_code == 200 and bool(h.db.rows("matches")),
          "%s %s" % (draw.status_code, detail(draw)))


def _closed_draw_harness():
    h = Harness()
    admin = h.make_user("Roster Owner", "admin")
    players = [h.make_user("Roster Player %d" % i) for i in range(3)]
    tid = h.seed_tournament(
        owner_id=admin, status="registration_closed", type="both",
        format="round_robin", entry_fee=0, fixtures_generated=False,
        rules={"numberOfSets": 1, "boardsPerSet": 3,
               "maxBoardsPerMatch": 3, "targetScore": 25},
    )
    for i, player in enumerate(players[:2]):
        h.db.seed("registrations", [{
            "id": "55555555-5555-5555-5555-5555555556%02d" % i,
            "tournament_id": tid, "type": "singles", "player_id": player,
            "status": "approved", "payment_status": "waived", "fee_paise": 0,
        }])
    return h, admin, players, tid


def test_singleton_category_and_missing_player_block_entire_draw():
    h, admin, players, tid = _closed_draw_harness()
    team_id = "66666666-6666-6666-6666-666666666666"
    h.db.seed("teams", [{"id": team_id, "name": "Solo Team"}])
    h.db.seed("registrations", [{
        "id": "55555555-5555-5555-5555-555555555603",
        "tournament_id": tid, "type": "doubles", "team_id": team_id,
        "status": "approved", "payment_status": "waived", "fee_paise": 0,
    }])
    draw = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    check("a single doubles entrant blocks the whole mixed draw",
          draw.status_code == 409 and "doubles" in detail(draw).lower(),
          "%s %s" % (draw.status_code, detail(draw)))
    check("singleton category creates no singles fixtures",
          not h.db.rows("matches") and not h.db.rows("boards"))

    # The approved registration still points at a player ID, but the player
    # profile is gone. PostgREST's joined player becomes null; no entry may be
    # silently omitted from the bracket.
    h.db.tables["registrations"] = [r for r in h.db.rows("registrations")
                                    if r.get("type") == "singles"]
    h.db.tables["profiles"] = [p for p in h.db.rows("profiles")
                               if p["id"] != players[0]]
    draw = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
    check("an approved entry missing its player refuses the draw",
          draw.status_code == 409 and "valid singles player" in detail(draw).lower(),
          "%s %s" % (draw.status_code, detail(draw)))
    check("missing player creates no fixtures", not h.db.rows("matches"))


def test_database_roster_snapshot_catches_concurrent_change():
    for drift in ("new entrant", "changed player", "changed draw settings"):
        h, admin, players, tid = _closed_draw_harness()
        original_rpc = h.db.rpc

        def race_at_draw(name, params=None):
            if name == "replace_tournament_fixtures_checked":
                if drift == "new entrant":
                    h.db.seed("registrations", [{
                        "id": "55555555-5555-5555-5555-555555555699",
                        "tournament_id": tid, "type": "singles",
                        "player_id": players[2], "status": "approved",
                        "payment_status": "waived", "fee_paise": 0,
                    }])
                elif drift == "changed player":
                    h.db.tables["registrations"][0]["player_id"] = players[2]
                else:
                    h.db.tables["tournaments"][0]["format"] = "knockout"
            return original_rpc(name, params)

        h.db.rpc = race_at_draw
        draw = h.post("/api/tournaments/%s/fixtures" % tid, {}, user_id=admin)
        check("%s at the database lock aborts the draw" % drift,
              draw.status_code == 409 and
              ("entry list" in detail(draw).lower()
               or "draw changed" in detail(draw).lower()),
              "%s %s" % (draw.status_code, detail(draw)))
        check("%s creates no matches or boards" % drift,
              not h.db.rows("matches") and not h.db.rows("boards"))


SUITES = [
    ("draw freezes the entry list", test_existing_draw_blocks_reopen_and_forced_entries),
    ("pending entries must be resolved", test_pending_entries_block_the_draw_without_side_effects),
    ("mixed category and joined entrant validation", test_singleton_category_and_missing_player_block_entire_draw),
    ("database roster snapshot", test_database_roster_snapshot_catches_concurrent_change),
]


def main():
    for label, fn in SUITES:
        try:
            fn()
        except Exception:
            check("%s suite completes" % label, False, traceback.format_exc()[-400:])
    failed = [(label, slot) for label, slot in RESULTS.items() if slot[0]]
    print("entry-list freeze: %d assertions, %d failures" % (
        sum(slot[1] for slot in RESULTS.values()), len(failed)))
    for label, slot in failed:
        print("FAIL %s: %s" % (label, slot[2]))
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())

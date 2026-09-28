"""Official 25-point / eight-board games and best-of-three match flow."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness  # noqa: E402
from app.services.scoring_engine import apply_set_results, summarise_sets, board_result  # noqa: E402


RULES = {
    "scoringMode": "classic", "setWinnerRule": "target_points",
    "numberOfSets": 3, "boardsPerSet": 8, "maxBoardsPerMatch": 8,
    "targetScore": 25, "queenPoints": 3,
}


def run():
    match = {
        "player1_id": "one", "player2_id": "two",
        "player1_name": "One", "player2_name": "Two",
        "number_of_sets": 3, "max_boards": 8, "target_points": 25,
        "status": "live", "stage": "knockout",
    }
    boards = [
        {"set_number": set_number, "board_number": number,
         "status": "pending", "player1_score": 0, "player2_score": 0}
        for set_number in (1, 2, 3) for number in range(1, 9)
    ]
    for board in boards:
        if board["set_number"] in (1, 2) and board["board_number"] <= 3:
            board.update(status="completed", player1_score=(9, 8, 8)[board["board_number"] - 1])
    games = summarise_sets(match, boards, RULES)
    assert games[0]["status"] == "completed" and games[0]["boardsCompleted"] == 3
    assert games[1]["status"] == "completed" and games[2]["status"] == "pending"
    decided = apply_set_results(match, boards, RULES)
    assert decided["winnerId"] == "one" and decided["status"] == "completed"
    print("PASS official games end at 25 points and match ends after two games")

    tied = [
        {"set_number": 1, "board_number": number, "status": "completed",
         "player1_score": 1 if number % 2 else 0,
         "player2_score": 0 if number % 2 else 1}
        for number in range(1, 9)
    ]
    row = summarise_sets(match, tied, RULES)[0]
    assert row["status"] == "in_progress" and row["needsExtraBoard"]
    tied.append({"set_number": 1, "board_number": 9, "status": "completed",
                 "player1_score": 1, "player2_score": 0})
    row = summarise_sets(match, tied, RULES)[0]
    assert row["status"] == "completed" and row["winnerId"] == "one"
    print("PASS a tied eighth board requires an additional deciding board")

    h = Harness()
    owner = h.make_user("Official Organiser", role="admin")
    one = h.make_user("Official One")
    two = h.make_user("Official Two")
    tournament = h.seed_tournament(owner, rules=RULES)
    match_id = h.seed_match(tournament, one, two, boards=8, sets=3,
                            target_points=25)
    for set_number in (1, 2):
        for number, score in enumerate((9, 8, 8), 1):
            response = h.post(f"/api/matches/{match_id}/boards/{number}/submit", {
                "p1Score": score, "p2Score": 0, "setNumber": set_number,
                "queenClaimedBy": "none", "queenCovered": False,
            }, user_id=owner)
            assert response.status_code == 200, response.text
        current = next(m for m in h.db.rows("matches") if m["id"] == match_id)
        if set_number == 1:
            assert current["status"] != "completed"
            next_game = next(b for b in h.db.rows("boards") if b["match_id"] == match_id
                             and b["set_number"] == 2 and b["board_number"] == 1)
            assert next_game["status"] == "in_progress"
    current = next(m for m in h.db.rows("matches") if m["id"] == match_id)
    assert current["status"] == "completed" and current["winner_id"] == one
    unplayed = next(b for b in h.db.rows("boards") if b["match_id"] == match_id
                    and b["set_number"] == 1 and b["board_number"] == 4)
    assert unplayed["status"] == "pending"
    confirm = h.post(f"/api/matches/{match_id}/confirm", {}, user_id=owner)
    assert confirm.status_code == 200, confirm.text
    print("PASS scorer advances games, leaves unused boards alone, and confirms winner")

    h = Harness()
    owner = h.make_user("Tie Organiser", role="admin")
    one = h.make_user("Tie One")
    two = h.make_user("Tie Two")
    tournament = h.seed_tournament(owner, rules=RULES)
    match_id = h.seed_match(tournament, one, two, boards=8, sets=3,
                            target_points=25)
    for number in range(1, 9):
        response = h.post(f"/api/matches/{match_id}/boards/{number}/submit", {
            "p1Score": 1 if number % 2 else 0,
            "p2Score": 0 if number % 2 else 1,
            "setNumber": 1, "queenClaimedBy": "none", "queenCovered": False,
        }, user_id=owner)
        assert response.status_code == 200, response.text
    current = next(m for m in h.db.rows("matches") if m["id"] == match_id)
    assert current["tie_break_required"] is True
    extra = h.post(f"/api/matches/{match_id}/boards", {}, user_id=owner)
    assert extra.status_code == 200 and extra.json()["boardNumber"] == 9, extra.text
    response = h.post(f"/api/matches/{match_id}/boards/9/submit", {
        "p1Score": 1, "p2Score": 0, "setNumber": 1,
        "queenClaimedBy": "none", "queenCovered": False,
    }, user_id=owner)
    assert response.status_code == 200, response.text
    next_game = next(b for b in h.db.rows("boards") if b["match_id"] == match_id
                     and b["set_number"] == 2 and b["board_number"] == 1)
    assert next_game["status"] == "in_progress"
    print("PASS tied eighth board opens and scores a ninth deciding board")

    # The ICF rules credit the queen only to the person who covered it AND
    # won the board. Seniors lose that bonus after entering a board on 22
    # points; other age groups play 21-point games and retain it throughout.
    official = {**RULES, "scoringMode": "official_icf", "boardEntryMode": "detailed"}
    def scored(*, before=0, target=25, queen="player1", remaining=9, penalty=0):
        return board_result(
            winner="player1", coins_remaining_with="player2",
            coins_remaining=remaining, queen_pocketed_by=queen,
            queen_covered_by=queen, p1_penalty=penalty,
            game_points_before=before,
            rules={**official, "targetScore": target},
        )

    assert scored(before=21)["player1_score"] == 12
    assert scored(before=22)["player1_score"] == 9
    assert scored(before=22, target=21)["player1_score"] == 12
    assert scored(queen="player2")["player1_score"] == 9
    assert scored(queen="player2")["player2_score"] == 0
    assert scored(penalty=2)["player1_score"] == 10
    assert scored(remaining=9)["player1_score"] <= 12
    print("PASS ICF queen ownership, senior cutoff, age-group variant, penalty and board cap")

    h = Harness()
    owner = h.make_user("ICF Organiser", role="admin")
    one, two = h.make_user("ICF One"), h.make_user("ICF Two")
    tournament = h.seed_tournament(owner, rules=official)
    match_id = h.seed_match(tournament, one, two, boards=8, sets=3,
                            target_points=25)

    def enter(number, coins, queen="none"):
        return h.post(f"/api/matches/{match_id}/boards/{number}/submit", {
            "p1Score": 0, "p2Score": 0, "setNumber": 1,
            "boardWinner": "player1", "coinsRemainingWith": "player2",
            "coinsRemaining": coins, "queenPocketedBy": queen,
            "queenCoveredBy": queen,
        }, user_id=owner)

    assert enter(1, 9, "player1").status_code == 200
    assert enter(2, 9).status_code == 200
    assert enter(3, 1, "player1").status_code == 200
    by_number = {b["board_number"]: b for b in h.db.rows("boards")
                 if b["match_id"] == match_id and b["set_number"] == 1}
    assert [by_number[n]["player1_score"] for n in (1, 2, 3)] == [12, 9, 4]
    assert h.post(f"/api/matches/{match_id}/boards/1/submit", {
        "p1Score": 0, "p2Score": 0, "setNumber": 1,
        "boardWinner": "player2", "coinsRemainingWith": "player1",
        "coinsRemaining": 9,
    }, user_id=owner).status_code == 409
    assert enter(4, 1).status_code == 409  # game one already reached 25
    print("PASS ICF game counts earlier boards, stops at 25 and refuses duplicate scores")

    h = Harness()
    owner = h.make_user("Late Queen Organiser", role="admin")
    one, two = h.make_user("Late Queen One"), h.make_user("Late Queen Two")
    tournament = h.seed_tournament(owner, rules=official)
    match_id = h.seed_match(tournament, one, two, boards=8, sets=3,
                            target_points=25)
    assert enter(1, 9, "player1").status_code == 200  # 12
    assert enter(2, 9).status_code == 200              # 21
    assert enter(3, 2).status_code == 200              # 23
    assert enter(4, 1, "player1").status_code == 200   # 24, no queen bonus
    board4 = next(b for b in h.db.rows("boards") if b["match_id"] == match_id
                  and b["set_number"] == 1 and b["board_number"] == 4)
    assert board4["player1_score"] == 1 and board4["queen_bonus"] == 0
    assert h.post(f"/api/matches/{match_id}/boards", {}, user_id=owner).status_code == 409
    assert h.post(f"/api/matches/{match_id}/boards/resize?boards=4", {},
                  user_id=owner).status_code == 409
    assert h.delete(f"/api/matches/{match_id}/boards/unplayed",
                    user_id=owner).status_code == 409
    print("PASS ICF senior queen bonus stops after 21 game points")

    # Correcting board 2 can change queen eligibility on board 4. The server
    # therefore makes the scorer roll later boards back in reverse order,
    # recording each change, then replay them with the corrected game score.
    def correct(number, payload):
        return h.put(f"/api/matches/{match_id}/boards/{number}?override=true&reason=Review",
                     payload, user_id=owner)

    assert correct(2, {"boardNumber": 2, "setNumber": 1, "status": "completed",
                       "player1Score": 0, "player2Score": 0,
                       "coinsRemaining": 8}).status_code == 409
    for number in (4, 3):
        response = correct(number, {"boardNumber": number, "setNumber": 1,
                                    "status": "pending", "player1Score": 0,
                                    "player2Score": 0})
        assert response.status_code == 200, response.text
    assert correct(2, {"boardNumber": 2, "setNumber": 1, "status": "completed",
                       "player1Score": 0, "player2Score": 0,
                       "coinsRemaining": 8}).status_code == 200
    assert enter(3, 2, "player1").status_code == 200  # prior 20; queen counts
    board3 = next(b for b in h.db.rows("boards") if b["match_id"] == match_id
                  and b["set_number"] == 1 and b["board_number"] == 3)
    assert board3["player1_score"] == 5 and board3["queen_bonus"] == 3
    print("PASS ICF correction preserves queen eligibility by replaying later boards")

    h = Harness()
    owner = h.make_user("Classic Organiser", role="admin")
    one, two = h.make_user("Classic One"), h.make_user("Classic Two")
    classic = {"scoringMode": "classic", "numberOfSets": 1,
               "setWinnerRule": "total_points", "queenPoints": 3}
    tournament = h.seed_tournament(owner, rules=classic)
    match_id = h.seed_match(tournament, one, two, boards=1, sets=1)
    response = h.post(f"/api/matches/{match_id}/boards/1/submit", {
        "p1Score": 2, "p2Score": 0, "setNumber": 1,
        "queenClaimedBy": "player2", "queenCovered": True,
    }, user_id=owner)
    assert response.status_code == 200, response.text
    match_row = next(m for m in h.db.rows("matches") if m["id"] == match_id)
    board_row = next(b for b in h.db.rows("boards") if b["match_id"] == match_id)
    assert board_row["player1_score"] == 2 and board_row["player2_score"] == 3
    assert board_row["board_winner"] == "player1" and match_row["winner_id"] == one
    print("PASS custom classic board winner is inferred before the losing queen bonus")

    h = Harness()
    owner = h.make_user("Short Match Organiser", role="admin")
    one, two = h.make_user("Short Match One"), h.make_user("Short Match Two")
    rules = {"scoringMode": "remaining_coins", "numberOfSets": 1,
             "setWinnerRule": "total_points", "coinsPerSide": 9}
    tournament = h.seed_tournament(owner, rules=rules)
    match_id = h.seed_match(tournament, one, two, boards=2, sets=1)
    response = h.post(f"/api/matches/{match_id}/boards/1/submit", {
        "p1Score": 0, "p2Score": 0, "boardWinner": "player1",
        "coinsRemainingWith": "player2", "coinsRemaining": 5,
        "queenPocketedBy": "none", "queenCoveredBy": "none",
    }, user_id=owner)
    assert response.status_code == 200, response.text
    response = h.delete(f"/api/matches/{match_id}/boards/unplayed", user_id=owner)
    assert response.status_code == 200, response.text
    match_row = next(m for m in h.db.rows("matches") if m["id"] == match_id)
    assert match_row["max_boards"] == 1 and match_row["status"] == "completed"
    assert match_row["winner_id"] == one and match_row["player1_total_points"] == 5
    print("PASS removing an unplayed tail immediately settles the played result")
    return 0


def main():
    return run()


if __name__ == "__main__":
    sys.exit(main())

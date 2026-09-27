"""Official 25-point / eight-board games and best-of-three match flow."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness  # noqa: E402
from app.services.scoring_engine import apply_set_results, summarise_sets  # noqa: E402


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
    return 0


def main():
    return run()


if __name__ == "__main__":
    sys.exit(main())

"""
Emit the board-scoring parity corpus.

frontend/src/utils/boardScoring.ts says in its own header that it mirrors
board_result() in scoring_engine.py, and that the one thing which must never
happen is the umpire's preview disagreeing with the score the server stores.
Nothing checked that. This writes every case and the answer the SERVER gives;
frontend/tests/offline/test_pure.ts replays them through previewBoard and
compares.

    python tests/offline/gen_parity_cases.py

Offline: imports the module directly, writes one JSON file, touches nothing else.
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

from app.services.scoring_engine import board_result  # noqa: E402

OUT = os.path.join(ROOT, "frontend", "tests", "offline", "parity-cases.json")

SIDES = ("none", "player1", "player2")
RULE_SETS = [
    {},
    {"queenPoints": 3, "coinValue": 1},
    {"queenPoints": 5},
    {"coinValue": 2},
    {"queenMustBeCovered": False},
    {"queenAwardTo": "pocketer"},
]


def main():
    cases = []
    for winner, qp, qc, rw, rc, pen, rules in itertools.product(
        SIDES, SIDES, SIDES, SIDES, (0, 1, 5, 9, 12), ((0, 0), (2, 0), (0, 4)),
        RULE_SETS,
    ):
        # previewBoard has no notion of pocketed coin counts, so the parity
        # corpus stays inside the subset both implementations model: the
        # umpire names who still holds coins, and how many.
        out = board_result(
            winner=winner,
            p1_coins_pocketed=None, p2_coins_pocketed=None,
            coins_remaining_with=rw, coins_remaining=rc,
            queen_pocketed_by=qp, queen_covered_by=qc,
            p1_penalty=pen[0], p2_penalty=pen[1],
            rules=rules,
        )
        cases.append({
            "obs": {
                "winner": winner,
                "queenPocketedBy": qp,
                "queenCoveredBy": qc,
                "coinsRemainingWith": rw,
                "coinsRemaining": rc,
                "p1Penalty": pen[0],
                "p2Penalty": pen[1],
            },
            "rules": rules,
            "server": {
                "p1": out["player1_score"],
                "p2": out["player2_score"],
                "base": out["base_points"],
                "queenBonus": out["queen_bonus"],
                "queenSide": out["queen_awarded_to"],
                "queenStatus": out["queen_status"],
            },
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(cases, fh, indent=0)
    print("wrote %d parity cases to %s" % (len(cases), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())

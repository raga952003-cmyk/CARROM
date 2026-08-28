"""
The umpire's live preview must never disagree with the stored score.

The browser previews a board score before it is saved (frontend previewBoard)
and the server computes the value it actually stores (board_result). They are
two implementations of one rule, in two languages, and the failure mode is the
worst one a scoring app has: the umpire reads 7, the record says 4, and nobody
notices until the standings are wrong.

This bundles the real frontend module with esbuild, runs it in node over an
exhaustive grid of observations, and compares every result against Python.
No reimplementation on either side — if the two ever drift, this fails.
"""
import itertools
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.scoring_engine import board_result

FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "..", "frontend")
MODULE = os.path.join(FRONTEND, "src", "utils", "boardScoring.ts")

SIDES = ("player1", "player2", "none")
RULE_SETS = [
    {"coinsPerSide": 9, "queenPoints": 3},
    {"coinsPerSide": 9, "queenPoints": 5, "queenAwardTo": "pocketer"},
    {"coinsPerSide": 12, "queenPoints": 1, "queenMustBeCovered": False},
    # A coin worth more than one point: the preview and the server must agree
    # on the multiplier as well as on the count.
    {"coinsPerSide": 9, "queenPoints": 3, "coinValue": 2},
]

ENTRY = """
import { previewBoard } from %(module)s;
import { readFileSync } from 'fs';
// Thousands of cases exceed the Windows command-line limit, so they travel by file.
const cases = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out = cases.map(([obs, rules]) => {
  const r = previewBoard(obs, rules);
  return [r.p1, r.p2, r.queenStatus, r.queenSide, r.warnings.length];
});
console.log(JSON.stringify(out));
"""


def build_cases():
    cases = []
    for winner, pocketed, covered, left_with in itertools.product(SIDES, SIDES, SIDES, SIDES):
        for coins in (0, 2, 4, 9):
            for pen in ((0, 0), (1, 0), (0, 2), (50, 0)):
                for rules in RULE_SETS:
                    cases.append(({
                        "winner": winner,
                        "queenPocketedBy": pocketed,
                        "queenCoveredBy": covered,
                        "coinsRemainingWith": left_with,
                        "coinsRemaining": coins,
                        "p1Penalty": pen[0],
                        "p2Penalty": pen[1],
                    }, rules))
    return cases


def run_frontend(cases):
    if not os.path.exists(MODULE):
        raise SystemExit("frontend module not found: {}".format(MODULE))
    tmp = tempfile.mkdtemp()
    entry = os.path.join(tmp, "entry.ts")
    with open(entry, "w", encoding="utf-8") as f:
        f.write(ENTRY % {"module": json.dumps(os.path.abspath(MODULE).replace("\\", "/"))})

    bundle = os.path.join(tmp, "bundle.cjs")
    npx = "npx.cmd" if os.name == "nt" else "npx"
    build = subprocess.run(
        [npx, "esbuild", entry, "--bundle", "--platform=node", "--format=cjs",
         "--outfile=" + bundle, "--log-level=error"],
        cwd=FRONTEND, capture_output=True, text=True)
    if build.returncode != 0:
        raise SystemExit("esbuild failed:\n" + build.stdout + build.stderr)

    payload_path = os.path.join(tmp, "cases.json")
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(cases, f)
    proc = subprocess.run(["node", bundle, payload_path],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("node failed:\n" + proc.stdout + proc.stderr)
    return json.loads(proc.stdout)


def main():
    cases = build_cases()
    print("comparing {} observation/rule combinations".format(len(cases)))
    front = run_frontend(cases)

    mismatches = []
    for (obs, rules), got in zip(cases, front):
        back = board_result(
            winner=obs["winner"],
            coins_remaining_with=obs["coinsRemainingWith"],
            coins_remaining=obs["coinsRemaining"],
            queen_pocketed_by=obs["queenPocketedBy"],
            queen_covered_by=obs["queenCoveredBy"],
            p1_penalty=obs["p1Penalty"],
            p2_penalty=obs["p2Penalty"],
            rules=rules,
        )
        mine = [back["player1_score"], back["player2_score"],
                back["queen_status"], back["queen_awarded_to"],
                len(back["warnings"])]
        # The scores and the queen outcome must match exactly. Warning wording
        # is allowed to differ; whether there IS a warning is not.
        if (mine[0], mine[1], mine[2]) != (got[0], got[1], got[2]):
            mismatches.append((obs, rules, mine, got))
        elif (mine[3] or "none") != (got[3] or "none"):
            mismatches.append((obs, rules, mine, got))
        elif bool(mine[4]) != bool(got[4]):
            mismatches.append((obs, rules, mine, got))

    print("\n" + "=" * 70)
    if mismatches:
        print("RESULTS: {} mismatch(es) out of {}".format(len(mismatches), len(cases)))
        for obs, rules, mine, got in mismatches[:12]:
            print("\n  obs   : {}".format(obs))
            print("  rules : {}".format(rules))
            print("  python: {}".format(mine))
            print("  browser: {}".format(got))
        return 1
    print("RESULTS: 0 failure(s)")
    print("ALL PREVIEW-PARITY CHECKS PASSED ({} combinations agree)".format(len(cases)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

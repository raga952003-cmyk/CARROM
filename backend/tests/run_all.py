"""
Run every harness against a live API and fail loudly if any of them does.

    python tests/run_all.py                 # against http://127.0.0.1:8000
    CARROM_API=https://... python tests/run_all.py

Each harness creates the tournaments, players and matches it needs and deletes
them again, so this is safe to point at a staging database. Do not point it at
a database holding a live tournament: the harnesses sign up throwaway admin
accounts and write real rows while they run.

Exit code is the number of failing suites, so CI can gate a deploy on it.
"""
import os
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = os.getenv("CARROM_API", "http://127.0.0.1:8000")

# Ordered cheapest-first so an obvious break is reported in seconds, not minutes.
SUITES = [
    "test_access",
    "test_preview_parity",
    "test_queen",
    "test_boardscoring",
    "test_toss",
    "test_groups",
    "test_import",
    "test_import2",
    "test_real",
    "e2e",
    "e2e2",
    "e2e_doubles",
    "scenarios",
    "scenarios2",
]

PASS_MARKERS = ("RESULTS: 0 failure(s)",
                "ALL PREVIEW-PARITY CHECKS PASSED", "ALL END-TO-END CHECKS PASSED",
                "ALL ARCHITECTURE CHECKS PASSED", "ALL DOUBLES CHECKS PASSED",
                "ALL EDGE-CASE SCENARIOS PASSED")


def wait_for_api(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(API + "/api/health", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1)
    return None


def main():
    health = wait_for_api()
    if health is None:
        print("No API at {}. Start the server first:\n"
              "    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000".format(API))
        return 1

    pending = health.get("pending_migrations") or []
    if pending:
        # Not fatal: every migration-dependent feature degrades on purpose. But
        # a suite that skips half its assertions is not the green light it looks
        # like, so say so before the results scroll past.
        print("WARNING: migrations not applied: {}".format(", ".join(pending)))
        print("         Assertions covering those features will be skipped.\n")

    failed = []
    for name in SUITES:
        path = os.path.join(HERE, name + ".py")
        if not os.path.exists(path):
            continue
        sys.stdout.write("{:<20}".format(name))
        sys.stdout.flush()
        started = time.time()
        proc = subprocess.run([sys.executable, path], cwd=ROOT,
                              capture_output=True, text=True, timeout=1800)
        out = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0 and any(m in out for m in PASS_MARKERS)
        print("{}  ({:.0f}s)".format("PASS" if ok else "FAIL", time.time() - started))
        if not ok:
            failed.append(name)
            for line in out.splitlines():
                if "FAIL" in line or "Traceback" in line or "Error" in line:
                    print("      " + line.strip())

    print("\n" + "=" * 60)
    if failed:
        print("{} suite(s) failed: {}".format(len(failed), ", ".join(failed)))
    else:
        print("ALL SUITES PASSED" + (" (with migrations pending)" if pending else ""))
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())

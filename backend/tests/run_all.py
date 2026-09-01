"""
Run every harness against a live API and fail loudly if any of them does.

    python tests/run_all.py                 # against http://127.0.0.1:8000
    CARROM_API=https://... python tests/run_all.py

THESE SUITES WRITE REAL ROWS. They sign up throwaway admin accounts and create
tournaments through the live API, against whatever database that API is backed
by. They clean up after themselves only when they pass; a suite that dies
partway through leaves its accounts and tournaments behind.

This has already gone wrong once. A run pointed at production left "Sets Admin"
and "Board Admin" accounts and a "Sets 1 6b3a11" tournament in the live
database, and the tournament could not be deleted through the UI because a
throwaway account owned it.

So the runner now refuses twice over: once unless CARROM_ALLOW_DB_WRITES=1 says
the database is expendable, and again if the target already holds tournaments
the harnesses did not create.

Exit code is the number of failing suites, so CI can gate a deploy on it.
"""
import os
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _session

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
API = os.getenv("CARROM_API", "http://127.0.0.1:8000")

# Ordered cheapest-first so an obvious break is reported in seconds, not minutes.
SUITES = [
    "test_access",
    "test_preview_parity",
    "test_privacy",
    "test_queen",
    "test_boardscoring",
    "test_sets",
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
                "ALL PRIVACY CHECKS PASSED",
                "ALL SET CHECKS PASSED",
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


def looks_like_real_data(api: str) -> str:
    """
    A one-line reason to stop, or "" if the target looks like a scratch database.

    The opt-in variable proves intent; this proves the target. A database
    already holding tournaments that the harnesses did not create is somebody's
    real work, and no environment variable makes it safe to write test accounts
    into it.
    """
    try:
        r = requests.get(api + "/api/tournaments", timeout=10)
        if r.status_code != 200:
            return ""
        names = [(t.get("name") or "") for t in (r.json() or [])]
    except Exception:
        return ""
    # Harness tournaments are all named "<Prefix> <n> <6-hex>", e.g. "Sets 1 6b3a11".
    foreign = [n for n in names if not re.search(r"\s[0-9a-f]{6}$", n)]
    if foreign:
        return ("target holds {} tournament(s) the harnesses did not create, "
                "including {!r}".format(len(foreign), foreign[0]))
    return ""


def main():
    _session.require_write_opt_in()

    health = wait_for_api()
    if health is None:
        print("No API at {}. Start the server first:\n"
              "    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000".format(API))
        return 1

    reason = looks_like_real_data(API)
    if reason and os.getenv("CARROM_I_KNOW_THIS_IS_NOT_PRODUCTION") != "1":
        print("Refusing to run: {}.\n".format(reason))
        print("These suites create and delete real rows. Point CARROM_API at a scratch\n"
              "database, or if you are certain this one is expendable, set\n"
              "    CARROM_I_KNOW_THIS_IS_NOT_PRODUCTION=1")
        return 2

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

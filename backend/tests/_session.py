"""
Shared auth handling for the harnesses.

A long suite occasionally gets a 401 partway through — the token is refused
even though the run is only minutes old. Before this existed, that 401 left the
board at 0-0 and the failure surfaced as "the score is wrong", which sent us
looking for a scoring bug that was not there. Now the request re-authenticates
once and retries, and anything still failing is a real defect.
"""
import os
import sys
import time

import requests

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"

# Filled in by remember(); used to recover a refused token.
_creds = {}


def require_write_opt_in() -> None:
    """
    Refuse to run unless the operator has said, explicitly, that this database
    is expendable.

    These harnesses do not use fixtures or a scratch schema: they sign up real
    accounts and write real tournaments through the live API, against whatever
    database that API is pointed at. Pointed at production -- which is the
    default, because CARROM_API defaults to a local server usually configured
    with production credentials -- they leave permanent residue.

    That is not hypothetical. This is how "Sets Admin", "Board Admin" and a
    "Sets 1 6b3a11" tournament ended up in the live database, where the tournament
    could not be deleted because a throwaway account owned it.

    An environment variable is a low bar, but it is a deliberate one, and it
    turns an accident into a decision.
    """
    if os.getenv("CARROM_ALLOW_DB_WRITES") == "1":
        return
    sys.stderr.write(
        "\nRefusing to run: these harnesses create real accounts and tournaments\n"
        "in whatever database {} is backed by.\n\n"
        "If that database is expendable, opt in explicitly:\n"
        "    CARROM_ALLOW_DB_WRITES=1 python tests/run_all.py\n\n"
        "Never set it against production. Residue from a previous run had to be\n"
        "cleaned out of the live database by hand.\n".format(BASE)
    )
    raise SystemExit(2)


def remember(email, password, role="admin"):
    """Record the credentials a harness signed up with, so a 401 is recoverable."""
    _creds.update({"email": email, "password": password, "role": role})


def signup(headers, email, password, name, role="admin", timeout=90):
    """Sign up, store the token on `headers`, and remember the credentials."""
    r = requests.post(BASE + "/auth/signup",
                      json={"email": email, "password": password, "name": name, "role": role},
                      timeout=timeout)
    r.raise_for_status()
    headers["Authorization"] = "Bearer " + r.json()["access_token"]
    remember(email, password, role)
    return r.json()


def relogin(headers):
    """Swap in a fresh token. False when there is nothing to log back in with."""
    if not _creds:
        return False
    try:
        r = requests.post(BASE + "/auth/login", json=_creds, timeout=60)
        if r.status_code != 200:
            return False
        headers["Authorization"] = "Bearer " + r.json()["access_token"]
        return True
    except Exception:
        return False


# A dropped Supabase connection surfaces as a 400 carrying this text. It is not
# a bad request, and treating it as one made a lost write look like a wrong
# score in whichever assertion happened to read the row next.
_TRANSIENT_TEXT = ("server disconnected", "connection reset", "read timeout",
                   "timed out", "remote end closed", "temporarily unavailable")


def _is_transient(response) -> bool:
    if response.status_code in (502, 503, 504):
        return True
    if response.status_code != 400:
        return False
    return any(m in (response.text or "").lower() for m in _TRANSIENT_TEXT)


def request(method, path, headers, **kw):
    """One API call, retried against a refused token or a dropped connection."""
    kw.setdefault("timeout", 120)
    extra = kw.pop("headers", {}) or {}

    def send():
        h = dict(headers)
        h.update(extra)
        return requests.request(method, BASE + path, headers=h, **kw)

    r = send()
    if r.status_code == 401 and relogin(headers):
        r = send()
    for attempt in range(2):
        if not _is_transient(r):
            break
        time.sleep(0.5 * (attempt + 1))
        r = send()
    return r

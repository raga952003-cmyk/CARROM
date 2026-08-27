"""
Shared auth handling for the harnesses.

A long suite occasionally gets a 401 partway through — the token is refused
even though the run is only minutes old. Before this existed, that 401 left the
board at 0-0 and the failure surfaced as "the score is wrong", which sent us
looking for a scoring bug that was not there. Now the request re-authenticates
once and retries, and anything still failing is a real defect.
"""
import os

import requests

BASE = os.getenv("CARROM_API", "http://127.0.0.1:8000") + "/api"

# Filled in by remember(); used to recover a refused token.
_creds = {}


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


def request(method, path, headers, **kw):
    """One API call, retried once against a refused token."""
    kw.setdefault("timeout", 120)
    extra = kw.pop("headers", {}) or {}

    def send():
        h = dict(headers)
        h.update(extra)
        return requests.request(method, BASE + path, headers=h, **kw)

    r = send()
    if r.status_code == 401 and relogin(headers):
        r = send()
    return r

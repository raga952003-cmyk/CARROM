"""Auth requests must not share a mutable Supabase session.

This suite uses only the offline fake database. No configured Supabase client
or network request is created.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import Harness  # noqa: E402 (blanks Supabase env before imports)
import app.database as database  # noqa: E402
from app.config import settings  # noqa: E402


RESULTS = {}


def check(label, condition):
    RESULTS[label] = bool(condition)


def main():
    original_create = database.create_client
    original_url = settings.SUPABASE_URL
    original_key = settings.SUPABASE_ANON_KEY
    try:
        made = []

        def make_client(url, key):
            client = object()
            made.append((url, key, client))
            return client

        database.create_client = make_client
        settings.SUPABASE_URL = "https://isolated.example.invalid"
        settings.SUPABASE_ANON_KEY = "test-publishable-key"
        first, second = database.get_db(), database.get_db()
        check("fresh client per configured auth request",
              first is not second and len(made) == 2)
        check("same anon configuration on both requests",
              all(row[:2] == (settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
                  for row in made))
    finally:
        database.create_client = original_create
        settings.SUPABASE_URL = original_url
        settings.SUPABASE_ANON_KEY = original_key

    h = Harness()
    alice = h.make_user("Alice")
    bob = h.make_user("Bob")
    first_login = h.post("/api/auth/login", json={
        "email": "alice@carrom.example.com", "password": "test", "role": "player",
    })
    second_login = h.post("/api/auth/login", json={
        "email": "bob@carrom.example.com", "password": "test", "role": "player",
    })
    check("Alice login resolves Alice profile",
          first_login.status_code == 200 and first_login.json().get("user", {}).get("id") == alice)
    check("Bob login resolves Bob profile",
          second_login.status_code == 200 and second_login.json().get("user", {}).get("id") == bob)

    for label, passed in RESULTS.items():
        print(("PASS" if passed else "FAIL") + " auth isolation: " + label)
    return 0 if all(RESULTS.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

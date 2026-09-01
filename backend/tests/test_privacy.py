"""
Contact details must never reach a caller who is not an admin.

This has now gone wrong twice, in two different layers. First the RLS policy on
`profiles` was `FOR SELECT TO public USING (true)`, so the anon key embedded in
the browser bundle could read every row; migration 011 fixed that. Then it
turned out the API never depended on RLS at all -- the tournament and
registration routes read through the service-role client, which bypasses it --
and `serialize_player` defaulted to `include_contact=True`, so twenty email
addresses and a mobile number were still being served to anonymous callers out
of /api/tournaments and /api/tournaments/{id}/registrations. The public
spectator board was itself downloading them.

Both fixes are invisible: nothing about the app looks different when they
regress. So they are asserted here.

Pure -- no database, no network, no API server.

    python tests/test_privacy.py
"""
import sys

sys.path.insert(0, ".")

from app.utils.serializers import (
    serialize_player, serialize_team, serialize_registration,
)

PROFILE = {
    "id": "p1", "name": "Santhoshraj R", "email": "santhoshraj519@gmail.com",
    "phone": "9566909877", "club": "TCS", "city": "Chennai", "rating": 1500,
    "role": "player",
}

failures = []


def check(label, obj, *, expect_contact):
    """Assert whether phone and email survive serialization."""
    import json
    blob = json.dumps(obj)
    has_email = '"email"' in blob
    has_phone = '"phone"' in blob
    ok = (has_email == expect_contact) and (has_phone == expect_contact)
    print("  {:54} email={!s:5} phone={!s:5} {}".format(
        label, has_email, has_phone, "ok" if ok else "FAIL"))
    if not ok:
        failures.append(label)


print("serialize_player")
check("default (anonymous or player)", serialize_player(PROFILE), expect_contact=False)
check("include_contact=True (admin, or your own profile)",
      serialize_player(PROFILE, include_contact=True), expect_contact=True)

print("\nserialize_team")
team = {"id": "t1", "name": "Pair A", "player1": PROFILE, "player2": PROFILE}
check("default", serialize_team(team), expect_contact=False)
check("include_contact=True", serialize_team(team, include_contact=True), expect_contact=True)

print("\nserialize_registration")
reg = {"id": "r1", "tournament_id": "T", "status": "approved", "player": PROFILE}
check("default", serialize_registration(reg), expect_contact=False)
check("include_contact=True", serialize_registration(reg, include_contact=True), expect_contact=True)

print("\nserialize_registration for a doubles entry")
dbl = {"id": "r2", "tournament_id": "T", "status": "approved", "team": team}
check("default -- both partners", serialize_registration(dbl), expect_contact=False)
check("include_contact=True -- both partners",
      serialize_registration(dbl, include_contact=True), expect_contact=True)

print("\n" + "=" * 66)
if failures:
    print("RESULTS: {} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("RESULTS: 0 failure(s)")
print("ALL PRIVACY CHECKS PASSED (contact details withheld unless asked for)")

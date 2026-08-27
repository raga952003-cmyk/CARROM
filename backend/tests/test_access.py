"""Ownership and delegated-access decisions, against a stubbed database."""
import sys
sys.path.insert(0, '.')
from fastapi import HTTPException
from app.services import access_control as ac

failures = []


def ok(label, cond, detail=""):
    print("  {}  {}{}".format("PASS" if cond else "FAIL", label,
                              ("  <- " + repr(detail)) if (detail not in (None, "") and not cond) else ""))
    if not cond:
        failures.append(label)


class FakeTable:
    def __init__(self, name, data, missing):
        self.name, self.data, self.missing, self.filters = name, data, missing, {}

    def select(self, *a, **k): return self
    def eq(self, col, val): self.filters[col] = val; return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self

    def execute(self):
        if self.name in self.missing:
            raise Exception(
                "Could not find the table 'public.{}' in the schema cache".format(self.name))
        rows = [r for r in self.data.get(self.name, [])
                if all(str(r.get(k)) == str(v) for k, v in self.filters.items())]
        return type("R", (), {"data": rows})()


class FakeDB:
    def __init__(self, data, missing=()):
        self.data, self.missing = data, set(missing)

    def table(self, name):
        return FakeTable(name, self.data, self.missing)


OWNER = {"id": "u-owner", "name": "Owner", "role": "admin"}
OTHER = {"id": "u-other", "name": "Other Admin", "role": "admin"}
PLAYER = {"id": "u-play", "name": "Player", "role": "player"}
T = {"id": "t1", "name": "Cup", "owner_id": "u-owner"}


def db(access_rows=None, missing=()):
    return FakeDB({
        "tournaments": [T],
        "profiles": [OWNER, OTHER, PLAYER],
        "tournament_access": access_rows or [],
    }, missing=missing)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except HTTPException as e:
        return e


print("=" * 66)
print("OWNERSHIP")
print("=" * 66)
ac._ownership_available = None
ok("owner can manage", ac.describe_access(db(), T, OWNER)["canManage"] is True)
ok("another admin cannot manage", ac.describe_access(db(), T, OTHER)["canManage"] is False)
ok("owner is flagged as owner", ac.describe_access(db(), T, OWNER)["isOwner"] is True)

e = refused(ac.require_tournament_access, db(), "t1", OTHER, "tournament.delete")
ok("another admin deleting -> 403", e is not None and e.status_code == 403)
ok("refusal names the owner and the way forward",
   e is not None and "owned by Owner" in e.detail and "request access" in e.detail.lower(),
   e.detail if e else "")
ok("owner deleting is allowed",
   refused(ac.require_tournament_access, db(), "t1", OWNER, "tournament.delete") is None)

print("\n" + "=" * 66)
print("APPROVED MANAGER")
print("=" * 66)
approved = [{"id": "a1", "tournament_id": "t1", "user_id": "u-other",
             "access_role": "manager", "status": "approved"}]
acc = ac.describe_access(db(approved), T, OTHER)
ok("approved manager can manage", acc["canManage"] is True and acc["role"] == "manager")
ok("approved manager may re-draw fixtures",
   refused(ac.require_tournament_access, db(approved), "t1", OTHER, "tournament.fixtures") is None)

print("\n" + "=" * 66)
print("SCORER IS LIMITED")
print("=" * 66)
scorer = [{"id": "a1", "tournament_id": "t1", "user_id": "u-other",
           "access_role": "scorer", "status": "approved"}]
ok("scorer may enter scores",
   refused(ac.require_tournament_access, db(scorer), "t1", OTHER, "match.score") is None)
ok("scorer may confirm a result",
   refused(ac.require_tournament_access, db(scorer), "t1", OTHER, "match.confirm") is None)
e = refused(ac.require_tournament_access, db(scorer), "t1", OTHER, "tournament.fixtures")
ok("scorer cannot re-draw fixtures -> 403", e is not None and e.status_code == 403)
e = refused(ac.require_tournament_access, db(scorer), "t1", OTHER, "tournament.delete")
ok("scorer cannot delete the tournament -> 403", e is not None and e.status_code == 403)

print("\n" + "=" * 66)
print("PENDING / REJECTED / REVOKED EXPLAIN THEMSELVES")
print("=" * 66)
for status, expect in (("pending", "awaiting"), ("rejected", "declined"), ("revoked", "revoked")):
    rows = [{"id": "a1", "tournament_id": "t1", "user_id": "u-other",
             "access_role": "manager", "status": status}]
    e = refused(ac.require_tournament_access, db(rows), "t1", OTHER, "tournament.update")
    ok("{:8s} -> 403 mentioning '{}'".format(status, expect),
       e is not None and e.status_code == 403 and expect in e.detail.lower(),
       e.detail if e else "")

print("\n" + "=" * 66)
print("NON-ADMINS")
print("=" * 66)
e = refused(ac.require_tournament_access, db(), "t1", PLAYER, "match.score")
ok("a player cannot score -> 403", e is not None and e.status_code == 403)
e = refused(ac.owned_by, db(), "t1", OTHER)
ok("only the owner decides access requests -> 403",
   e is not None and e.status_code == 403, e.detail if e else "")

print("\n" + "=" * 66)
print("UN-MIGRATED DATABASE DEGRADES HONESTLY")
print("=" * 66)
ac._ownership_available = None
legacy = FakeDB({"tournaments": [{"id": "t1", "name": "Cup", "owner_id": None}],
                 "profiles": [OWNER, OTHER]}, missing=("tournament_access",))
acc = ac.describe_access(legacy, {"id": "t1", "name": "Cup", "owner_id": None}, OTHER)
ok("previous behaviour preserved (admin can still manage)", acc["canManage"] is True)
ok("but it reports enforced=False rather than pretending", acc["enforced"] is False,
   "enforced={}".format(acc["enforced"]))
ok("ownership_enforced() reports False", ac.ownership_enforced() is False)
ok("owner is not stamped on create when the column is absent",
   "owner_id" not in ac.set_owner_on_create({"name": "x"}, OWNER))

print("\n" + "=" * 66)
print("RESULTS: {} failure(s)".format(len(failures)))
for f in failures:
    print("  - " + f)
print("ALL ACCESS-CONTROL CHECKS PASSED" if not failures else "FAILURES ABOVE")
sys.exit(1 if failures else 0)

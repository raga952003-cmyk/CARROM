"""
Remove accounts and tournaments left behind by the test harnesses.

The suites in backend/tests sign up throwaway admins and create tournaments
through the live API. Run against production once, they left "Sets Admin",
"Board Admin", sixteen "BS<n> P<n>" players and a "Sets 1 6b3a11" tournament in
the real database — and that tournament could not be deleted through the UI,
because the account that owned it was a throwaway nobody could sign in as.

This removes that residue and nothing else.

Two independent tests must BOTH agree before anything is deleted:

  1. The email matches a harness naming pattern.
  2. The profile is referenced by no tournament other than harness ones.

Test 2 is the one that matters. Names can coincide; participation cannot. A
player who appears in a real tournament is kept whatever they are called.

Anything ambiguous is listed and kept, because leaving a stray test account
costs a line in a list, and deleting a real player costs their history.

    python db/purge_test_residue.py            # show the plan, change nothing
    python db/purge_test_residue.py --apply    # carry it out
"""
import re
import sys

sys.path.insert(0, ".")

from app.database import get_admin_db

# Logins that must never be touched, whatever else is true of them.
PROTECTED_EMAILS = {
    "ragavenragavendra4@gmail.com",
    "admin@carromarena.com",
}

# The harnesses mint accounts as "<prefix><6-hex>_<suffix>@carromarena.com"
# and tournaments as "<Prefix> <n> <6-hex>".
HARNESS_EMAIL = re.compile(r"^[a-z]{2,3}[0-9a-f]{6}_", re.I)
HARNESS_TOURNAMENT = re.compile(r"\s[0-9a-f]{6}$")


def main(apply: bool) -> int:
    adm = get_admin_db()

    tournaments = adm.table("tournaments").select("id,name,owner_id").execute().data or []
    harness_t = [t for t in tournaments if HARNESS_TOURNAMENT.search(t.get("name") or "")]
    real_t = [t for t in tournaments if t not in harness_t]
    real_ids = {t["id"] for t in real_t}

    # Every profile any REAL tournament depends on. These are untouchable.
    protected_ids = set()
    for r in (adm.table("registrations").select("tournament_id,player_id").execute().data or []):
        if r.get("tournament_id") in real_ids and r.get("player_id"):
            protected_ids.add(r["player_id"])
    for m in (adm.table("matches").select("tournament_id,player1_id,player2_id").execute().data or []):
        if m.get("tournament_id") in real_ids:
            for key in ("player1_id", "player2_id"):
                if m.get(key):
                    protected_ids.add(m[key])
    # Teams carry no tournament id, so any team member is treated as in use.
    for t in (adm.table("teams").select("player1_id,player2_id").execute().data or []):
        for key in ("player1_id", "player2_id"):
            if t.get(key):
                protected_ids.add(t[key])

    profiles = adm.table("profiles").select("id,name,email,role").execute().data or []
    for p in profiles:
        if (p.get("email") or "") in PROTECTED_EMAILS:
            protected_ids.add(p["id"])

    removable, ambiguous = [], []
    for p in profiles:
        email = p.get("email") or ""
        if p["id"] in protected_ids:
            continue
        if HARNESS_EMAIL.match(email):
            removable.append(p)
        else:
            # Unreferenced, but not named like harness output. Someone's
            # abandoned test player, most likely. Not ours to judge.
            ambiguous.append(p)

    print("tournaments            : {}".format(len(tournaments)))
    print("  harness-named        : {}".format(len(harness_t)))
    print("  real (kept)          : {}".format(len(real_t)))
    for t in real_t:
        print("      keep {!r}".format(t.get("name")))
    print("profiles               : {}".format(len(profiles)))
    print("  in use / protected   : {}".format(len(protected_ids)))
    print("  removable            : {}".format(len(removable)))
    print("  unreferenced, kept   : {}".format(len(ambiguous)))

    if ambiguous:
        print("\nUnreferenced but not named like harness output - KEPT, review by hand:")
        for p in sorted(ambiguous, key=lambda x: x.get("email") or ""):
            print("      {:44} {}".format(p.get("email") or "-", (p.get("name") or "")[:24]))

    if harness_t:
        print("\nTournaments to delete (registrations, matches and boards go by cascade):")
        for t in harness_t:
            n = len(adm.table("matches").select("id").eq("tournament_id", t["id"]).execute().data or [])
            print("      {!r}  ({} matches)".format(t.get("name"), n))

    if removable:
        print("\nAccounts to delete:")
        for p in sorted(removable, key=lambda x: x.get("email") or ""):
            print("      {:44} {:24} {}".format(
                p.get("email") or "-", (p.get("name") or "")[:24], p.get("role")))

    if not harness_t and not removable:
        print("\nNothing to remove.")
        return 0

    if not apply:
        print("\nDry run. Re-run with --apply to delete "
              "{} tournament(s) and {} account(s).".format(len(harness_t), len(removable)))
        return 0

    deleted_t = 0
    for t in harness_t:
        try:
            adm.table("tournaments").delete().eq("id", t["id"]).execute()
            deleted_t += 1
        except Exception as e:
            print("   could not delete tournament {!r}: {}".format(t.get("name"), str(e)[:100]))

    deleted_p, deleted_a, failures = 0, 0, []
    for p in removable:
        try:
            adm.table("profiles").delete().eq("id", p["id"]).execute()
            deleted_p += 1
        except Exception as e:
            failures.append(("profile", p.get("email"), str(e)[:90]))
        try:
            # The auth user outlives the profile row, and would still occupy the
            # email address, so it has to go too.
            adm.auth.admin.delete_user(p["id"])
            deleted_a += 1
        except Exception as e:
            failures.append(("auth", p.get("email"), str(e)[:90]))

    print("\ndeleted {} tournament(s), {} profile row(s), {} auth user(s)".format(
        deleted_t, deleted_p, deleted_a))
    for kind, email, err in failures:
        print("   FAILED {} {} -> {}".format(kind, email, err))

    after_t = adm.table("tournaments").select("id").execute().data or []
    after_p = adm.table("profiles").select("id").execute().data or []
    print("remaining: {} tournament(s), {} profile(s)".format(len(after_t), len(after_p)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))

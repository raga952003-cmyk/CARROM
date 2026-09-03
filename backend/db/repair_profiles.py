"""
Rebuild public.profiles from Supabase Auth.

Every login in this app is an auth user plus a matching row in public.profiles.
The row carries the name, role and rating; auth carries the credentials. If the
rows are lost while the auth users survive, the result is an app that looks
signed-in and refuses every action: the API reads `profile.role`, finds nothing,
and returns 403. Re-registering does not help either, because Supabase still has
the email.

Worse than a 403, an orphaned identity breaks WRITES. Several columns reference
profiles(id) -- boards.confirmed_by, matches.toss_recorded_by,
tournaments.owner_id, tournament_access.user_id -- so the first action that
stores who did it fails with a foreign key violation (SQLSTATE 23503) instead of
anything a scorer can act on.

This restores one row per auth user from the metadata already stored against it.
It only inserts what is missing and never overwrites an existing row.

    python db/repair_profiles.py                    # show what is missing
    python db/repair_profiles.py --apply            # insert the missing rows
    python db/repair_profiles.py --id=<uuid>        # narrow to one account
    python db/repair_profiles.py --id=<uuid> --apply

Narrow with --id when only one identity is blocking. A blanket --apply
resurrects EVERY orphan, including players someone deleted on purpose.
"""
import sys

sys.path.insert(0, ".")

from app.database import get_admin_db

# GoTrue caps a page at 1000; 200 keeps each round trip small.
PAGE_SIZE = 200
MAX_PAGES = 100


def list_all_users(adm):
    """
    Every auth user, not just the first page.

    `list_users()` returns GoTrue's default of 50 per page. Calling it bare
    reported the first fifty accounts as if they were the whole population, so
    an orphan on any later page was invisible here while the app went on
    failing on it -- and the count printed as a suspiciously round "50".
    """
    users, page = [], 1
    while page <= MAX_PAGES:
        batch = adm.auth.admin.list_users(page=page, per_page=PAGE_SIZE)
        # Older client versions wrap the list in an object.
        batch = list(getattr(batch, "users", batch) or [])
        users.extend(batch)
        if len(batch) < PAGE_SIZE:
            return users
        page += 1
    print("WARNING: stopped after {} pages; there may be more.".format(MAX_PAGES))
    return users


def main(apply: bool, only_id: str) -> int:
    adm = get_admin_db()

    users = list_all_users(adm)
    existing = {p["id"] for p in (adm.table("profiles").select("id").execute().data or [])}
    missing = [u for u in users if u.id not in existing]

    print("auth users        : {}".format(len(users)))
    print("profile rows      : {}".format(len(existing)))
    print("missing profiles  : {}".format(len(missing)))

    if only_id:
        wanted = [u for u in missing if str(u.id) == only_id]
        if not wanted:
            known = any(str(u.id) == only_id for u in users)
            print("\n{} is {}.".format(
                only_id,
                "an auth user that ALREADY has a profile row" if known
                else "not an auth user in this project at all"))
            return 0
        missing = wanted
        print("narrowed to       : 1 (--id)")

    if not missing:
        print("\nNothing to repair.")
        return 0

    rows = []
    for u in missing:
        md = u.user_metadata or {}
        rows.append({
            "id": u.id,
            "name": md.get("name") or (u.email or "unknown").split("@")[0],
            "email": u.email,
            # Only 'admin' and 'player' are valid; anything else is a player.
            "role": "admin" if md.get("role") == "admin" else "player",
            "club": md.get("club") or "Independent",
            "city": md.get("city"),
            "rating": md.get("rating") or 1500,
            "phone": md.get("phone"),
        })

    print()
    for r in sorted(rows, key=lambda x: (x["role"] != "admin", x["email"] or "")):
        # The id is printed because a foreign key violation names the id and
        # nothing else -- matching it to a person was guesswork without this.
        print("   {}  {:36} {:22} {}".format(
            r["id"], r["email"] or "", (r["name"] or "")[:22], r["role"]))

    if not apply:
        print("\nDry run. Re-run with --apply to insert these rows.")
        return 0

    inserted, failed = 0, []
    for r in rows:
        try:
            adm.table("profiles").insert(r).execute()
            inserted += 1
        except Exception as e:
            failed.append((r["email"], str(e)[:120]))

    print("\ninserted {} profile row(s)".format(inserted))
    for email, err in failed:
        print("   FAILED {} -> {}".format(email, err))

    admins = [p["email"] for p in
              (adm.table("profiles").select("email,role").execute().data or [])
              if p["role"] == "admin"]
    print("\nadmin logins now: {}".format(admins or "NONE"))
    if not admins:
        print("No admin exists. Promote one with:")
        print("   UPDATE public.profiles SET role='admin' WHERE email='<your email>';")
    return 0 if not failed else 1


if __name__ == "__main__":
    only = next((a.split("=", 1)[1] for a in sys.argv[1:]
                 if a.startswith("--id=")), "")
    sys.exit(main(apply="--apply" in sys.argv, only_id=only.strip()))

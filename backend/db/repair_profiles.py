"""
Rebuild public.profiles from Supabase Auth.

Every login in this app is an auth user plus a matching row in public.profiles.
The row carries the name, role and rating; auth carries the credentials. If the
rows are lost while the auth users survive, the result is an app that looks
signed-in and refuses every action: the API reads `profile.role`, finds nothing,
and returns 403. Re-registering does not help either, because Supabase still has
the email.

This restores one row per auth user from the metadata already stored against it.
It only inserts what is missing and never overwrites an existing row.

    python db/repair_profiles.py            # show what is missing
    python db/repair_profiles.py --apply    # insert the missing rows
"""
import sys

sys.path.insert(0, ".")

from app.database import get_admin_db


def main(apply: bool) -> int:
    adm = get_admin_db()

    users = adm.auth.admin.list_users()
    existing = {p["id"] for p in (adm.table("profiles").select("id").execute().data or [])}
    missing = [u for u in users if u.id not in existing]

    print("auth users        : {}".format(len(users)))
    print("profile rows      : {}".format(len(existing)))
    print("missing profiles  : {}".format(len(missing)))

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
            "city": md.get("city") or "Pune",
            "rating": md.get("rating") or 1500,
            "phone": md.get("phone"),
        })

    print()
    for r in sorted(rows, key=lambda x: (x["role"] != "admin", x["email"] or "")):
        print("   {:36} {:22} {}".format(r["email"], r["name"][:22], r["role"]))

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
    sys.exit(main(apply="--apply" in sys.argv))

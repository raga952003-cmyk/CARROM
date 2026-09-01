"""
Grant or remove admin rights on an account.

Signing up no longer makes anyone an admin. `role` used to be an ordinary field
on the sign-up request, written straight into Supabase app_metadata, and the
form offered it as a visible choice — so anyone who could reach the page could
give themselves full rights, and at least one did.

Admin is now granted deliberately, here.

Both places must agree: `profiles.role` is what the API reads for most checks,
and `app_metadata.role` is what the JWT carries. Setting one and not the other
produces an account that half works, which is harder to diagnose than one that
does not work at all.

    python db/promote_admin.py                          # list who is what
    python db/promote_admin.py someone@example.com       # show the plan
    python db/promote_admin.py someone@example.com --apply
    python db/promote_admin.py someone@example.com --demote --apply
"""
import sys

sys.path.insert(0, ".")

from app.database import get_admin_db


def main(email: str, apply: bool, demote: bool) -> int:
    adm = get_admin_db()
    target_role = "player" if demote else "admin"

    profiles = adm.table("profiles").select("id,name,email,role").execute().data or []

    if not email:
        print("{:38} {:22} {}".format("EMAIL", "NAME", "ROLE"))
        for p in sorted(profiles, key=lambda x: (x.get("role") != "admin", x.get("email") or "")):
            print("{:38} {:22} {}".format(
                (p.get("email") or "-")[:38], (p.get("name") or "")[:22], p.get("role")))
        admins = [p for p in profiles if p.get("role") == "admin"]
        print("\n{} admin(s), {} player(s)".format(len(admins), len(profiles) - len(admins)))
        return 0

    hits = [p for p in profiles if (p.get("email") or "").lower() == email.lower()]
    if not hits:
        print("No account for {!r}. They must register first.".format(email))
        return 1
    person = hits[0]

    if person.get("role") == target_role:
        print("{} is already {}.".format(person.get("email"), target_role))
        return 0

    print("account : {}  ({})".format(person.get("email"), person.get("name")))
    print("role    : {} -> {}".format(person.get("role"), target_role))

    if demote:
        # Refusing to leave the instance with nobody who can run it.
        remaining = [p for p in profiles
                     if p.get("role") == "admin" and p["id"] != person["id"]]
        if not remaining:
            print("\nRefusing: this is the only admin, and demoting them would "
                  "leave nobody able to administer the instance.")
            return 1

    if not apply:
        print("\nDry run. Re-run with --apply to make the change.")
        return 0

    failures = []
    try:
        adm.table("profiles").update({"role": target_role}).eq("id", person["id"]).execute()
    except Exception as e:
        failures.append(("profiles", str(e)[:120]))

    try:
        # The JWT reads app_metadata, so a profile row alone is not enough.
        adm.auth.admin.update_user_by_id(
            person["id"], attributes={"app_metadata": {"role": target_role}}
        )
    except Exception as e:
        failures.append(("app_metadata", str(e)[:120]))

    if failures:
        for where, err in failures:
            print("   FAILED {} -> {}".format(where, err))
        print("\nThe account may now be inconsistent between profiles and auth. "
              "Re-run to retry.")
        return 1

    print("\n{} is now {}. They must sign out and back in for the new token to "
          "carry it.".format(person.get("email"), target_role))
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(
        args[0] if args else "",
        apply="--apply" in sys.argv,
        demote="--demote" in sys.argv,
    ))

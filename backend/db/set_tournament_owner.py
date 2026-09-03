"""
Say who owns a tournament.

A tournament's owner is the admin who runs it: only they and the managers they
approve may delete it, redraw the fixtures, change the settings or reopen a
result. Everyone else asks, through the access request flow.

`owner_id` is stamped on creation, but only from migration 003 onwards — and
`describe_access` deliberately treats an UNOWNED tournament as manageable by
any admin, because the alternative is a tournament nobody can run. So anything
created before 003 was applied is still wide open, and looks no different on
screen. This is how those get adopted.

    python db/set_tournament_owner.py                                  # who owns what
    python db/set_tournament_owner.py "September Month" owner@mail.com  # show the plan
    python db/set_tournament_owner.py "September Month" owner@mail.com --apply
    python db/set_tournament_owner.py "September Month" --disown --apply

The tournament is matched on a case-insensitive substring of its name, and the
match must be unique -- naming two tournaments at once is how the wrong one
gets handed over.
"""
import sys

sys.path.insert(0, ".")

from app.database import get_admin_db


def main(name: str, email: str, apply: bool, disown: bool) -> int:
    adm = get_admin_db()

    tournaments = adm.table("tournaments").select(
        "id,name,owner_id,status").execute().data or []
    profiles = adm.table("profiles").select("id,name,email,role").execute().data or []
    by_id = {p["id"]: p for p in profiles}

    def describe(owner_id):
        person = by_id.get(owner_id)
        if not person:
            return "unowned - ANY admin may delete and redraw it"
        return "{} <{}>".format(person.get("name"), person.get("email"))

    if not name:
        print("{:44} {:20} {}".format("TOURNAMENT", "STATUS", "OWNER"))
        for t in tournaments:
            print("{:44} {:20} {}".format(
                (t.get("name") or "")[:44], (t.get("status") or "")[:20],
                describe(t.get("owner_id"))))
        unowned = [t for t in tournaments if not t.get("owner_id")]
        if unowned:
            print("\n{} unowned. Until one is adopted, every admin can delete "
                  "it.".format(len(unowned)))
        return 0

    hits = [t for t in tournaments if name.lower() in (t.get("name") or "").lower()]
    if not hits:
        print("No tournament matching {!r}.".format(name))
        return 1
    if len(hits) > 1:
        print("{!r} matches {} tournaments. Be more specific:".format(name, len(hits)))
        for t in hits:
            print("   {}".format(t.get("name")))
        return 1
    tournament = hits[0]

    if disown:
        if not tournament.get("owner_id"):
            print("{!r} is already unowned.".format(tournament.get("name")))
            return 0
        new_owner = None
        print("tournament : {}".format(tournament.get("name")))
        print("owner      : {} -> unowned (ANY admin may then delete it)".format(
            describe(tournament.get("owner_id"))))
    else:
        people = [p for p in profiles if (p.get("email") or "").lower() == email.lower()]
        if not people:
            print("No account for {!r}. They must register first.".format(email))
            return 1
        person = people[0]
        if person.get("role") != "admin":
            # A player as owner is an owner who cannot use anything they own:
            # every route behind ownership also requires the admin role.
            print("{} is a {}, not an admin. Promote them first with "
                  "db/promote_admin.py.".format(person.get("email"), person.get("role")))
            return 1
        if tournament.get("owner_id") == person["id"]:
            print("{} already owns {!r}.".format(person.get("email"), tournament.get("name")))
            return 0
        new_owner = person["id"]
        print("tournament : {}".format(tournament.get("name")))
        print("owner      : {} -> {} <{}>".format(
            describe(tournament.get("owner_id")), person.get("name"), person.get("email")))

    if not apply:
        print("\nDry run. Re-run with --apply to make the change.")
        return 0

    try:
        adm.table("tournaments").update(
            {"owner_id": new_owner}).eq("id", tournament["id"]).execute()
    except Exception as e:
        print("\nFAILED: {}".format(str(e)[:200]))
        if "owner_id" in str(e):
            print("Apply db/migrations/003_ownership_and_access.sql first.")
        return 1

    if new_owner:
        print("\n{!r} now belongs to {}. Other admins must request access to "
              "score it.".format(tournament.get("name"), email))
    else:
        print("\n{!r} is now unowned, and any admin can manage it.".format(
            tournament.get("name")))
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    disown = "--disown" in flags
    tournament_name = args[0] if args else ""
    owner_email = args[1] if len(args) > 1 else ""
    if tournament_name and not owner_email and not disown:
        print("Give the owner's email, or --disown to remove the current one.\n")
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(tournament_name, owner_email, "--apply" in flags, disown))

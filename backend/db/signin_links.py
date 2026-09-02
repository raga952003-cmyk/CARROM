"""
One-time sign-in links for players who cannot receive email.

Every account created from a sheet import or the Add Player form was given a
random 32-byte password that was generated, handed to Supabase and discarded.
Nobody knows it -- not the organiser, not the player, not the database, which
holds only a bcrypt hash. That is the right way to create an account somebody
else will claim, but it leaves one question: how do they get in?

Not by resetting: those accounts were given synthetic addresses like
player_c34f7de9@carromarena.com, and nothing delivers mail there.

So the link is generated here and handed over directly -- read out, messaged,
or printed on the entry slip. Clicking it signs that player in, and they set
their own password from Settings once they are there.

    python db/signin_links.py                       # every player, as a table
    python db/signin_links.py "Ragavendra S"        # one person
    python db/signin_links.py --format=csv > links.csv

Each link is single-use and expires, so re-run this when one goes stale.

Treat the output the way you would treat a door key: a link is a way into that
person's account until it is used. Send each one to its own player, and do not
post the whole list anywhere shared.
"""
import sys

sys.path.insert(0, ".")

from app.config import settings
from app.database import get_admin_db


def redirect_target() -> str:
    """Where a clicked link should land. The deployed site, or localhost."""
    origins = settings.cors_origin_list()
    return origins[0] if origins else "http://localhost:5173"


def main(who: str, as_csv: bool) -> int:
    adm = get_admin_db()

    profiles = adm.table("profiles").select("id, name, email, role").eq(
        "role", "player").order("name").execute().data or []
    if who:
        needle = who.strip().lower()
        profiles = [p for p in profiles
                    if needle in (p.get("name") or "").lower()
                    or needle == (p.get("email") or "").lower()]
        if not profiles:
            print("Nobody matches {!r}.".format(who))
            return 1

    if not profiles:
        print("No player accounts found.")
        return 1

    if as_csv:
        print("name,email,link")

    failures = []
    for p in profiles:
        email = p.get("email")
        if not email:
            failures.append((p.get("name"), "no email address on the account"))
            continue
        try:
            res = adm.auth.admin.generate_link({
                # magiclink signs them in; they set a password afterwards from
                # Settings. 'recovery' would send them to a set-password screen
                # instead, which is a worse first experience for someone who
                # has never seen the app.
                "type": "magiclink",
                "email": email,
                "options": {"redirect_to": redirect_target()},
            })
            # GenerateLinkResponse.properties.action_link, per the client's
            # own types. The dict branch is for older client versions.
            props = getattr(res, "properties", None)
            link = getattr(props, "action_link", None)
            if link is None and isinstance(res, dict):
                link = (res.get("properties") or {}).get("action_link")
        except Exception as e:
            failures.append((p.get("name"), str(e)[:90]))
            continue

        if not link:
            failures.append((p.get("name"), "no link returned"))
            continue

        if as_csv:
            print('"{}","{}","{}"'.format(
                (p.get("name") or "").replace('"', "'"), email, link))
        else:
            print("\n{}".format(p.get("name") or "(unnamed)"))
            print("   {}".format(link))

    if failures:
        print("\n{} could not be generated:".format(len(failures)))
        for name, why in failures:
            print("   {:24} {}".format((name or "?")[:24], why))

    if not as_csv:
        print("\nEach link signs that one player in and can be used once. Send each")
        print("to its own player; the whole list is a way into every account.")
    return 0 if not failures else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else "", as_csv="--format=csv" in sys.argv))

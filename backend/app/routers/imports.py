from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin
from app.services.access_control import require_tournament_access
from app.services.state_machine import (
    assert_participants_can_be_added, assert_entry_list_not_drawn,
)
from app.services.sheet_parser import read_sheet, parse_participants
from app.services.audit_service import record_audit
from app.services.razorpay_client import rupees_to_paise
from app.routers.tournaments import (
    _entrants_already_in, generate_fixtures, generate_schedule, publish_schedule,
)
from typing import Any, Dict, List, Optional, Tuple
import io
import json
import uuid
import secrets
import logging

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/excel")
async def import_excel_file(
    file: UploadFile = File(...),
    admin = Depends(verify_admin)
):
    """
    Parse an uploaded Excel/CSV participant sheet and return it for review.

    Nothing is written here: the admin confirms the parsed rows separately
    (spec 67), so a misread sheet cannot create accounts.
    """
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Upload a .xlsx, .xls or .csv file.",
        )

    try:
        content = await file.read()
        df = read_sheet(content, filename)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read the file: {str(e)}")

    # `not df.rows`, not `df.empty`.
    #
    # `.empty` is the pandas DataFrame API. pandas was removed from this
    # project -- it cost 60 MB plus numpy plus pyarrow and broke the 225 MB
    # serverless ceiling -- and replaced by services/sheet_parser.Sheet, which
    # defines __slots__ and therefore raises AttributeError here rather than
    # returning a falsy value. The line sits outside the try/except above, so
    # every single upload answered 500: the bulk import was dead for every
    # .csv, .xls and .xlsx file, however clean, and the only remaining way in
    # was to add participants one at a time.
    if not df.rows:
        raise HTTPException(status_code=400, detail="The sheet has no rows.")

    try:
        entries, errors, meta = parse_participants(df)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse the sheet: {str(e)}")

    if not entries:
        raise HTTPException(
            status_code=400,
            detail="No usable participant rows were found. " + (" ".join(errors[:3]) if errors else ""),
        )

    return {
        "fileName": filename,
        "players": entries,
        "errors": errors,
        "status": "success",
        **meta,
    }


def _find_profile(admin_db, name: str, email: Optional[str],
                  by_email: Dict[str, Any], by_name: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Match an existing player by email, falling back to name ONLY when the
    sheet gives no email.

    Email is the reliable key. Name was a fallback for every row, including
    rows that carried an email which simply did not match -- and then any
    existing profile with the same name was registered instead. The sheet's
    club, city and rating were discarded, nothing was added to `skipped`, and
    the organiser was told "success" with no warning. The wrong human ended
    up in the entry list, the draw and the standings, owing the fee, while the
    person on the sheet had no entry at all.

    An email on the sheet is an explicit claim about WHO this is. If it
    matches nobody, this is somebody new; guessing from the name would
    contradict the only identifying detail the organiser supplied.
    """
    if email:
        return by_email.get(email.lower())
    if name and name.lower() in by_name:
        return by_name[name.lower()]
    return None


def _create_profile(admin_db, name: str, email: Optional[str], club: str, city: str,
                    rating: int, phone: Optional[str]) -> str:
    """Create an auth user + profile for an imported participant."""
    address = email or f"player_{uuid.uuid4().hex[:8]}@carromarena.com"
    auth_user = admin_db.auth.admin.create_user({
        "email": address,
        "password": secrets.token_urlsafe(32),
        "email_confirm": True,
        "user_metadata": {
            "name": name, "role": "player",
            "club": club, "city": city, "rating": rating,
        },
    })
    user_id = auth_user.user.id
    admin_db.auth.admin.update_user_by_id(
        user_id, attributes={"app_metadata": {"role": "player"}}
    )

    patch = {"club": club, "city": city, "rating": rating}
    if phone:
        patch["phone"] = phone
    admin_db.table("profiles").update(patch).eq("id", user_id).execute()
    return user_id


@router.post("/confirm")
async def confirm_bulk_import(
    tournamentId: str = Form(...),
    players_json: str = Form(...),
    # Defaulted OFF. It used to default to True while the browser never sent a
    # value, so importing one late entrant silently regenerated the whole draw
    # -- deleting every board already played -- and republished the schedule to
    # every participant. Rebuilding a draw is not a side effect of adding a
    # player to it.
    autoGenerate: bool = Form(False),
    # Same lifecycle escape as single-entry; the browser does not send it and
    # it never bypasses the registration closing date.
    force: bool = Form(False),
    admin = Depends(verify_admin),
):
    """
    Create the participants and register them, honouring singles vs doubles.

    Doubles rows create both players and a team, and register as a doubles
    entry. Previously every row was registered as `singles` regardless, so a
    doubles sheet produced singles fixtures between team names.
    """
    # Importing writes players and registrations into one tournament, so it is
    # that tournament's owner's call.
    require_tournament_access(get_admin_db(), tournamentId, admin)

    try:
        entries = json.loads(players_json)
        if not isinstance(entries, list):
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid participants payload.")

    admin_db = get_admin_db()

    tournament = admin_db.table("tournaments").select("*").eq("id", tournamentId).execute().data
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found.")
    assert_entry_list_not_drawn(admin_db, tournament[0])
    # A bulk import is the same act as entering people one at a time, and was
    # the unguarded way in: the single-entry route checked the tournament's
    # state and this one never did, so a whole sheet could be imported into a
    # tournament that was already being played.
    assert_participants_can_be_added(tournament[0], force=force)
    category = (tournament[0].get("category") or "both").lower()
    fee_paise = rupees_to_paise(tournament[0].get("entry_fee"))
    # Confirming a spreadsheet is not evidence of payment. Free entries can be
    # admitted by this organiser action; paid entries join the draw only after
    # their payment is recorded or their fee is explicitly waived.
    new_status = "pending" if fee_paise > 0 else "approved"
    new_payment_status = "pending" if fee_paise > 0 else "waived"

    try:
        existing = admin_db.table("profiles").select(
            "id, name, email, role").execute().data or []
        by_email = {(p["email"] or "").lower(): p for p in existing if p.get("email")}
        # Names are matched against PLAYERS only. This select was unfiltered,
        # so a sheet row naming an organiser matched that admin's profile and
        # entered them as a competitor -- "Imported 1 singles entry." with the
        # registration's player_id pointing at another admin's account. An
        # email still matches whoever owns it, admin included, because an
        # organiser entering their own event is ordinary and the email says so
        # explicitly.
        by_name = {(p["name"] or "").lower(): p for p in existing
                   if p.get("name") and (p.get("role") or "player") == "player"}
        # Include members of existing doubles teams, not just player_id on a
        # singles registration. Keep this map current as rows in this sheet are
        # inserted so a later row cannot give somebody a second entry.
        entered = _entrants_already_in(admin_db, tournamentId)

        singles_added = 0
        doubles_added = 0
        skipped: List[str] = []

        for entry in entries:
            if not entry.get("selected", True):
                continue

            entry_type = (entry.get("type") or "singles").lower()
            name = (entry.get("name") or "").strip()
            if not name:
                skipped.append("A row had no player name.")
                continue

            # A doubles entry cannot go into a singles-only event, and vice versa.
            if entry_type == "doubles" and category == "singles":
                skipped.append(f"'{name}' is a doubles entry but this tournament is singles only.")
                continue
            if entry_type == "singles" and category == "doubles":
                skipped.append(f"'{name}' is a singles entry but this tournament is doubles only.")
                continue

            partner_name = (entry.get("partnerName") or "").strip() if entry_type == "doubles" else ""
            if entry_type == "doubles" and not partner_name:
                skipped.append(f"'{name}' has no partner name, so no team was formed.")
                continue

            matched = _find_profile(admin_db, name, entry.get("email"), by_email, by_name)
            partner_match = (
                _find_profile(admin_db, partner_name, entry.get("partnerEmail"), by_email, by_name)
                if entry_type == "doubles" else None
            )
            # Refuse known conflicts before creating any new auth profiles or
            # teams. An existing pending entry also counts; a rejected one does
            # not occupy a place in the draw.
            conflict = next((
                entered[str(profile["id"])]
                for profile in (matched, partner_match)
                if profile and str(profile["id"]) in entered
            ), None)
            if conflict:
                skipped.append(
                    f"'{name}' was skipped: {conflict} is already entered in this "
                    "tournament. A player can only hold one entry."
                )
                continue

            club = entry.get("club") or "Independent"
            city = entry.get("city")
            rating = int(entry.get("rating") or 1500)

            if matched:
                player_id = matched["id"]
            else:
                player_id = _create_profile(
                    admin_db, name, entry.get("email"), club, city, rating, entry.get("phone")
                )
                by_name[name.lower()] = {"id": player_id, "name": name}
                if entry.get("email"):
                    by_email[entry["email"].lower()] = {"id": player_id, "name": name}

            if entry_type == "doubles":
                # Repeat the lookup after adding the primary player to the
                # local cache, so the same person in both slots is caught.
                partner_match = _find_profile(
                    admin_db, partner_name, entry.get("partnerEmail"), by_email, by_name
                )
                if partner_match:
                    partner_id = partner_match["id"]
                else:
                    partner_id = _create_profile(
                        admin_db, partner_name, entry.get("partnerEmail"),
                        club, city, rating, entry.get("partnerPhone")
                    )
                    by_name[partner_name.lower()] = {"id": partner_id, "name": partner_name}
                    if entry.get("partnerEmail"):
                        by_email[entry["partnerEmail"].lower()] = {"id": partner_id, "name": partner_name}

                if partner_id == player_id:
                    skipped.append(f"'{name}' was paired with themselves.")
                    continue

                conflict = entered.get(str(player_id)) or entered.get(str(partner_id))
                if conflict:
                    skipped.append(
                        f"'{name}' was skipped: {conflict} is already entered in this "
                        "tournament. A player can only hold one entry."
                    )
                    continue

                team_name = entry.get("teamName") or f"{name} & {partner_name}"
                existing_team = admin_db.table("teams").select("id").or_(
                    f"and(player1_id.eq.{player_id},player2_id.eq.{partner_id}),"
                    f"and(player1_id.eq.{partner_id},player2_id.eq.{player_id})"
                ).execute().data
                if existing_team:
                    team_id = existing_team[0]["id"]
                else:
                    team_id = admin_db.table("teams").insert({
                        "name": team_name, "player1_id": player_id, "player2_id": partner_id,
                        "club": club, "city": city, "rating": rating,
                        "seed": entry.get("seed"),
                    }).execute().data[0]["id"]

                already = admin_db.table("registrations").select("id").eq(
                    "tournament_id", tournamentId).eq("team_id", team_id).execute().data
                if not already:
                    admin_db.table("registrations").insert({
                        "tournament_id": tournamentId, "type": "doubles",
                        "team_id": team_id, "status": new_status,
                        "payment_status": new_payment_status, "fee_paise": fee_paise,
                    }).execute()
                    doubles_added += 1
                    entered[str(player_id)] = name
                    entered[str(partner_id)] = partner_name
                else:
                    skipped.append(f"'{name}' is already registered as a team in this tournament.")
            else:
                if str(player_id) in entered:
                    skipped.append(
                        f"'{name}' was skipped: {entered[str(player_id)]} is already "
                        "entered in this tournament. A player can only hold one entry."
                    )
                    continue
                already = admin_db.table("registrations").select("id").eq(
                    "tournament_id", tournamentId).eq("player_id", player_id).execute().data
                if not already:
                    admin_db.table("registrations").insert({
                        "tournament_id": tournamentId, "type": "singles",
                        "player_id": player_id, "status": new_status,
                        "payment_status": new_payment_status, "fee_paise": fee_paise,
                    }).execute()
                    singles_added += 1
                    entered[str(player_id)] = name
                else:
                    skipped.append(f"'{name}' is already registered in this tournament.")

        imported = singles_added + doubles_added
        record_audit(
            admin_db, actor=admin, action="tournament.import_participants",
            entity_type="tournament", entity_id=tournamentId,
            new_state={"singles": singles_added, "doubles": doubles_added},
            request_context={"skipped": len(skipped), "autoGenerate": autoGenerate},
        )

        fixtures_built = False
        fixture_error = None
        if autoGenerate and imported > 0 and fee_paise > 0:
            fixture_error = (
                "Imported entries await payment or an explicit fee waiver. "
                "Record or waive their entry fees before generating fixtures."
            )
        elif autoGenerate and imported > 0:
            try:
                # Keyword arguments, deliberately. generate_fixtures is
                # (id, force, admin) and this used to call it as (id, admin):
                # the admin profile slid into `force` and `admin` kept
                # FastAPI's Depends marker, so every auto-generated import
                # ended in "Fixtures were not generated" with a reason that
                # named nothing. force stays False here on purpose -- an
                # import must never be the thing that discards recorded
                # results; if there are any, generate_fixtures answers 409 and
                # the except below reports it as fixtureError. The schedule
                # and publish calls take the same shape so a later change to
                # either signature fails loudly instead of shifting.
                await generate_fixtures(id=tournamentId, force=False, admin=admin)
                await generate_schedule(id=tournamentId, restMinutes=10, admin=admin)
                await publish_schedule(id=tournamentId, admin=admin)
                fixtures_built = True
            except HTTPException as e:
                fixture_error = e.detail
            except Exception as e:
                fixture_error = str(e)

        parts = []
        if singles_added:
            parts.append(f"{singles_added} singles entr{'y' if singles_added == 1 else 'ies'}")
        if doubles_added:
            parts.append(f"{doubles_added} doubles team{'' if doubles_added == 1 else 's'}")
        summary = " and ".join(parts) if parts else "no new entries"

        message = f"Imported {summary}."
        if skipped:
            message += f" {len(skipped)} row(s) were skipped."
        if fee_paise > 0 and imported:
            message += " These entries await recorded payment or an explicit fee waiver."
        if fixtures_built:
            message += " Fixtures generated and the schedule published."
        elif fixture_error:
            message += f" Fixtures were not generated: {fixture_error}"

        return {
            # Only a clean run reports success, so a partial import is visible
            # instead of being reported as a complete one.
            "status": "success" if not skipped else "partial",
            "message": message,
            "singlesImported": singles_added,
            "doublesImported": doubles_added,
            "imported": imported,
            "skipped": skipped,
            "fixturesGenerated": fixtures_built,
            # Why the draw was not built, when it was asked for. It was only
            # ever folded into the message, which is how the mis-call above
            # went unnoticed: nothing machine-readable said the draw failed.
            "fixtureError": fixture_error,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk import failed for {tournamentId}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

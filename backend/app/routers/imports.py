from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin
from app.services.access_control import require_tournament_access
from app.services.sheet_parser import parse_participants
from app.services.audit_service import record_audit
from app.routers.tournaments import generate_fixtures, generate_schedule, publish_schedule
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
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
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read the file: {str(e)}")

    if df.empty:
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
    Match an existing player by email first, falling back to name.

    Email is the reliable key; name matching alone would merge two different
    people who happen to share a name, so it is only a fallback.
    """
    if email and email.lower() in by_email:
        return by_email[email.lower()]
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
    autoGenerate: bool = Form(True),
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
    category = (tournament[0].get("category") or "both").lower()

    try:
        existing = admin_db.table("profiles").select("id, name, email").execute().data or []
        by_email = {(p["email"] or "").lower(): p for p in existing if p.get("email")}
        by_name = {(p["name"] or "").lower(): p for p in existing if p.get("name")}

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

            club = entry.get("club") or "Independent"
            city = entry.get("city")
            rating = int(entry.get("rating") or 1500)

            matched = _find_profile(admin_db, name, entry.get("email"), by_email, by_name)
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
                partner_name = (entry.get("partnerName") or "").strip()
                if not partner_name:
                    skipped.append(f"'{name}' has no partner name, so no team was formed.")
                    continue

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
                        "team_id": team_id, "status": "approved", "payment_status": "pending",
                    }).execute()
                    doubles_added += 1
            else:
                already = admin_db.table("registrations").select("id").eq(
                    "tournament_id", tournamentId).eq("player_id", player_id).execute().data
                if not already:
                    admin_db.table("registrations").insert({
                        "tournament_id": tournamentId, "type": "singles",
                        "player_id": player_id, "status": "approved", "payment_status": "pending",
                    }).execute()
                    singles_added += 1

        imported = singles_added + doubles_added
        record_audit(
            admin_db, actor=admin, action="tournament.import_participants",
            entity_type="tournament", entity_id=tournamentId,
            new_state={"singles": singles_added, "doubles": doubles_added},
            request_context={"skipped": len(skipped), "autoGenerate": autoGenerate},
        )

        fixtures_built = False
        fixture_error = None
        if autoGenerate and imported > 0:
            try:
                await generate_fixtures(tournamentId, admin)
                await generate_schedule(tournamentId, restMinutes=10, admin=admin)
                await publish_schedule(tournamentId, admin)
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
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk import failed for {tournamentId}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

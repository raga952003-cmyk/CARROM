from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from app.database import get_db, get_admin_db
from app.utils.security import verify_admin
from app.routers.tournaments import generate_fixtures, generate_schedule, publish_schedule
import pandas as pd
import io
import uuid
import secrets
from typing import List, Dict, Any

router = APIRouter(prefix="/imports", tags=["imports"])

@router.post("/excel")
async def import_excel_file(
    file: UploadFile = File(...),
    admin = Depends(verify_admin)
):
    """
    Parses uploaded Excel/CSV file and returns validation results.
    """
    filename = file.filename
    if not (filename.endswith(".xlsx") or filename.endswith(".xls") or filename.endswith(".csv")):
        raise HTTPException(status_code=400, detail="Unsupported file format. Please upload an Excel or CSV file.")
    
    try:
        content = await file.read()
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content))

        # Check required columns
        # Normalize column headers to lowercase/no space
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        name_col = next((c for c in df.columns if "name" in c), None)
        if not name_col:
            raise HTTPException(status_code=400, detail="Sheet must contain a 'Name' column.")
            
        club_col = next((c for c in df.columns if "club" in c), None)
        city_col = next((c for c in df.columns if "city" in c), None)
        rating_col = next((c for c in df.columns if "rating" in c or "rate" in c), None)
        seed_col = next((c for c in df.columns if "seed" in c), None)
        email_col = next((c for c in df.columns if "email" in c or "mail" in c), None)
        phone_col = next((c for c in df.columns if "phone" in c or "contact" in c), None)

        parsed_players = []
        validation_errors = []

        for idx, row in df.iterrows():
            name = str(row[name_col]).strip() if pd.notna(row[name_col]) else ""
            if not name or name.lower() == "nan":
                validation_errors.append(f"Row {idx + 1}: Name is missing.")
                continue

            club = str(row[club_col]).strip() if (club_col and pd.notna(row[club_col])) else "Independent"
            city = str(row[city_col]).strip() if (city_col and pd.notna(row[city_col])) else "Pune"
            
            try:
                rating = int(row[rating_col]) if (rating_col and pd.notna(row[rating_col])) else 1500
            except ValueError:
                rating = 1500
                validation_errors.append(f"Row {idx + 1} ({name}): Invalid rating format. Defaulted to 1500.")

            try:
                seed = int(row[seed_col]) if (seed_col and pd.notna(row[seed_col])) else None
            except ValueError:
                seed = None
                validation_errors.append(f"Row {idx + 1} ({name}): Invalid seed format. Ignored.")
                
            email = str(row[email_col]).strip() if (email_col and pd.notna(row[email_col])) else None
            phone = str(row[phone_col]).strip() if (phone_col and pd.notna(row[phone_col])) else None

            parsed_players.append({
                "name": name,
                "club": club,
                "city": city,
                "rating": rating,
                "seed": seed,
                "email": email,
                "phone": phone,
                "selected": True
            })

        return {
            "fileName": filename,
            "players": parsed_players,
            "errors": validation_errors,
            "status": "success"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {str(e)}")

@router.post("/confirm")
async def confirm_bulk_import(
    tournamentId: str = Form(...),
    players_json: str = Form(...),
    admin = Depends(verify_admin)
):
    """
    Saves players, registers them to the tournament, and auto-schedules fixtures.
    """
    import json
    try:
        players = json.loads(players_json)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid players JSON payload.")
    
    admin_db = get_admin_db()
    imported_count = 0
    
    try:
        # Load all existing profiles
        existing_profiles_res = admin_db.table("profiles").select("id, name, email").execute()
        existing_profiles = {p["name"].lower(): p for p in existing_profiles_res.data}
        
        for p in players:
            if not p.get("selected", True):
                continue
                
            name = p["name"]
            email = p.get("email") or f"player_{uuid.uuid4().hex[:8]}@carromarena.com"
            matched_profile = existing_profiles.get(name.lower())
            
            if not matched_profile:
                # Create companion auth user
                auth_user = admin_db.auth.admin.create_user({
                    "email": email,
                    "password": secrets.token_urlsafe(32),
                    "email_confirm": True,
                    "user_metadata": {
                        "name": name,
                        "role": "player",
                        "club": p.get("club", "Independent"),
                        "city": p.get("city", "Pune"),
                        "rating": p.get("rating", 1500)
                    }
                })
                
                # Set metadata role
                admin_db.auth.admin.update_user_by_id(
                    auth_user.user.id,
                    attributes={"app_metadata": {"role": "player"}}
                )
                
                # Fetch profiles just in case
                profile_id = auth_user.user.id
            else:
                profile_id = matched_profile["id"]

            # Register player to tournament (singles)
            reg_payload = {
                "tournament_id": tournamentId,
                "type": "singles",
                "player_id": profile_id,
                "status": "approved",
                "payment_status": "pending"
            }
            # Avoid duplicate registrations
            check_reg = admin_db.table("registrations").select("id").eq("tournament_id", tournamentId).eq("player_id", profile_id).execute()
            if not check_reg.data:
                admin_db.table("registrations").insert(reg_payload).execute()
                
            imported_count += 1
            
        # Trigger automatic fixture and schedule generation
        await generate_fixtures(tournamentId, admin)
        await generate_schedule(tournamentId, restMinutes=10, admin=admin)
        await publish_schedule(tournamentId, admin)

        return {
            "status": "success",
            "message": f"Imported {imported_count} players, created fixtures, and published boards conflict-free schedule successfully."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

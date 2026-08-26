import os
import sys
import uuid
import logging

# Add backend app directory to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.config import settings
from app.database import get_admin_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seeder")

def seed():
    admin_db = get_admin_db()
    if not admin_db:
        logger.error("Supabase Admin client not configured.")
        return

    logger.info("Starting database seeding...")

    # 1. Create Admin User
    admin_email = "admin@carrom.com"
    admin_password = "admin123"  # Standard secure admin password
    
    # Check if admin already exists in profiles
    try:
        existing_admin = admin_db.table("profiles").select("*").eq("email", admin_email).execute()
    except Exception as e:
        logger.error(f"Failed to query profiles. Did you run migrations/triggers? Error: {e}")
        return

    if not existing_admin.data:
        logger.info(f"Creating Admin user: {admin_email}")
        try:
            auth_user = admin_db.auth.admin.create_user({
                "email": admin_email,
                "password": admin_password,
                "email_confirm": True,
                "user_metadata": {
                    "name": "Tournament Director",
                    "role": "admin",
                    "club": "All India Carrom Federation",
                    "city": "New Delhi",
                    "rating": 2000
                }
            })
            
            # Set metadata role to admin
            admin_db.auth.admin.update_user_by_id(
                auth_user.user.id,
                attributes={"app_metadata": {"role": "admin"}}
            )
            logger.info("Admin user created successfully.")
        except Exception as e:
            logger.error(f"Error creating admin user: {e}")
    else:
        logger.info("Admin user already exists.")

    # 2. Create Sample Players
    sample_players = [
        {"email": "rohit.deshmukh@sports.in", "name": "Rohit Deshmukh", "club": "Pune Carrom Club", "city": "Pune", "rating": 1720},
        {"email": "aditya.joshi@sports.in", "name": "Aditya Joshi", "club": "Deccan Gymkhana", "city": "Pune", "rating": 1680},
        {"email": "amit.sharma@sports.in", "name": "Amit Sharma", "club": "Independent", "city": "Mumbai", "rating": 1540},
        {"email": "siddharth.roy@sports.in", "name": "Siddharth Roy", "club": "Mumbai Carrom Association", "city": "Mumbai", "rating": 1610},
        {"email": "vikram.singh@sports.in", "name": "Vikram Singh", "club": "Independent", "city": "Pune", "rating": 1480},
        {"email": "rahul.verma@sports.in", "name": "Rahul Verma", "club": "Delhi Club", "city": "Delhi", "rating": 1590},
        {"email": "sanjay.patil@sports.in", "name": "Sanjay Patil", "club": "Pune Carrom Club", "city": "Pune", "rating": 1550},
        {"email": "pranav.shah@sports.in", "name": "Pranav Shah", "club": "Independent", "city": "Ahmedabad", "rating": 1620},
    ]

    player_ids = []
    for p in sample_players:
        # Check if player already exists
        existing_p = admin_db.table("profiles").select("*").eq("email", p["email"]).execute()
        if not existing_p.data:
            logger.info(f"Creating player user: {p['email']}")
            try:
                auth_user = admin_db.auth.admin.create_user({
                    "email": p["email"],
                    "password": "player123",
                    "email_confirm": True,
                    "user_metadata": {
                        "name": p["name"],
                        "role": "player",
                        "club": p["club"],
                        "city": p["city"],
                        "rating": p["rating"]
                    }
                })
                
                admin_db.auth.admin.update_user_by_id(
                    auth_user.user.id,
                    attributes={"app_metadata": {"role": "player"}}
                )
                player_ids.append(auth_user.user.id)
            except Exception as e:
                logger.error(f"Error creating player {p['email']}: {e}")
        else:
            player_ids.append(existing_p.data[0]["id"])
            logger.info(f"Player {p['email']} already exists.")

    # 3. Create Sample Tournament
    tournament_name = "Carrom Premier League 2026"
    existing_t = admin_db.table("tournaments").select("*").eq("name", tournament_name).execute()
    
    if not existing_t.data:
        logger.info(f"Creating tournament: {tournament_name}")
        t_payload = {
            "name": tournament_name,
            "description": "The premier annual carrom championship gathering grandmasters from all over India.",
            "category": "singles",
            "format": "knockout",
            "status": "registration_open",
            "registration_start_date": "2026-08-01",
            "registration_end_date": "2026-08-30",
            "tournament_start_date": "2026-09-01",
            "tournament_end_date": "2026-09-05",
            "venue": "Shivaji Nagar Sports Complex",
            "city": "Pune",
            "number_of_boards": 4,
            "entry_fee": 500.0,
            "prize_pool": "₹50,000 + Trophy",
            "rules": {
                "maxBoardsPerMatch": 3,
                "targetScore": 25,
                "pointsForWin": 2,
                "pointsForDraw": 1,
                "pointsForLoss": 0
            },
            "poster_config": {
                "themeStyle": "emerald_gold",
                "tagline": "Strike with Precision. Reign Supreme on the Board.",
                "highlights": [
                    "Championship Grade Synco & Siscaa Boards",
                    "Official Carrom Federation Standard Rules",
                    "Live Digital Scoreboards & Stream Highlights"
                ],
                "badgeText": "OFFICIAL 2026 INVITATIONAL",
                "announcement": f"Join top carrom masters at Shivaji Nagar Sports Complex for the {tournament_name}!"
            }
        }
        try:
            res = admin_db.table("tournaments").insert(t_payload).execute()
            t_id = res.data[0]["id"]
            logger.info(f"Tournament created with ID: {t_id}")
        except Exception as e:
            logger.error(f"Error creating tournament: {e}")
            return
    else:
        t_id = existing_t.data[0]["id"]
        logger.info(f"Tournament already exists with ID: {t_id}")

    # 4. Register players to the tournament
    logger.info("Registering players to tournament...")
    for pid in player_ids:
        # Check if registration already exists
        existing_reg = admin_db.table("registrations").select("*").eq("tournament_id", t_id).eq("player_id", pid).execute()
        if not existing_reg.data:
            reg_payload = {
                "tournament_id": t_id,
                "type": "singles",
                "player_id": pid,
                "status": "approved",
                "payment_status": "paid"
            }
            try:
                admin_db.table("registrations").insert(reg_payload).execute()
                logger.info(f"Registered player ID {pid} successfully.")
            except Exception as e:
                logger.error(f"Error registering player ID {pid}: {e}")
        else:
            logger.info(f"Player ID {pid} already registered.")

    logger.info("Database seeding completed successfully.")

if __name__ == "__main__":
    seed()

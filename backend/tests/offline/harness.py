"""
Boots the real FastAPI application against the in-memory database.

Every route, dependency, service and serializer is the production one. Only the
Supabase client is replaced, so what these tests exercise is the app itself.

The Supabase environment variables are blanked BEFORE app.config is imported.
python-dotenv does not override variables that already exist, so this keeps the
run hermetic even on a machine with a populated backend/.env -- no client is
ever constructed against a real project.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
for path in (BACKEND, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

# Must happen before app.config is imported.
os.environ.setdefault("SUPABASE_URL", "")
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""
# Blank by default so the fake "tok:<id>" tokens take the remote
# verification path; the JWT tests set it deliberately.
os.environ["SUPABASE_JWT_SECRET"] = ""
os.environ["ENV"] = "test"
os.environ["CORS_ORIGINS"] = "https://carrom.example.com"

from fastapi.testclient import TestClient          # noqa: E402
from fakedb import FakeSupabase                    # noqa: E402

import app.database as database                    # noqa: E402
import app.utils.security as security              # noqa: E402
import app.services.access_control as access_control  # noqa: E402
import app.routers.matches as matches_router       # noqa: E402
from app.main import app                           # noqa: E402


class Harness:
    def __init__(self, trigger_enabled=True, enforce_ownership=True):
        self.db = FakeSupabase(trigger_enabled=trigger_enabled)

        database.supabase_client = self.db
        database.supabase_admin = self.db
        database.get_user_client = lambda token: self.db
        security.get_user_client = lambda token: self.db

        # Probe caches are module-level and would otherwise leak between the
        # scenarios that deliberately vary the schema.
        access_control._ownership_available = None
        matches_router._board_detail_available.clear()
        from app.config import settings
        settings.ENFORCE_TOURNAMENT_OWNERSHIP = enforce_ownership

        self.client = TestClient(app, raise_server_exceptions=False)

    # -- identities --------------------------------------------------------
    def make_user(self, name, role="player", with_profile=True, **meta):
        """An auth user, and (unless suppressed) its profiles row."""
        created = self.db.auth.admin.create_user({
            "email": "%s@carrom.example.com" % name.lower().replace(" ", "."),
            "user_metadata": dict({"name": name, "role": role}, **meta),
            "app_metadata": {"role": role},
        })
        user_id = created.user.id
        if with_profile:
            rows = [p for p in self.db.tables.get("profiles", [])
                    if p["id"] == user_id]
            if rows:
                rows[0]["role"] = role
            else:
                self.db.seed("profiles", [{
                    "id": user_id, "name": name, "role": role,
                    "email": "%s@carrom.example.com" % name.lower().replace(" ", "."),
                    "rating": 1500,
                }])
        else:
            self.db.orphan_profile(user_id)
        return user_id

    @staticmethod
    def auth(user_id):
        return {"Authorization": "Bearer tok:%s" % user_id}

    # -- shortcuts ---------------------------------------------------------
    def get(self, path, user_id=None, **kw):
        return self.client.get(path, headers=self.auth(user_id) if user_id else {}, **kw)

    def post(self, path, json=None, user_id=None, **kw):
        return self.client.post(path, json=json,
                                headers=self.auth(user_id) if user_id else {}, **kw)

    def put(self, path, json=None, user_id=None, **kw):
        return self.client.put(path, json=json,
                               headers=self.auth(user_id) if user_id else {}, **kw)

    def delete(self, path, user_id=None, **kw):
        return self.client.delete(path,
                                  headers=self.auth(user_id) if user_id else {}, **kw)

    # -- fixtures ----------------------------------------------------------
    def seed_tournament(self, owner_id, name="Test Open", **over):
        row = {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": name, "owner_id": owner_id, "status": "in_progress",
            "format": "knockout", "type": "singles", "max_boards": 8,
            "target_points": 29, "rules": {"scoringMode": "remaining_coins",
                                           "queenPoints": 3, "coinsPerSide": 9},
        }
        row.update(over)
        self.db.seed("tournaments", [row])
        return row["id"]

    def seed_match(self, tournament_id, p1, p2, boards=8, sets=1, **over):
        """
        `boards` is boards per SET, matching how a draw is generated: board
        numbers restart at 1 in each set, so the match holds boards * sets rows.
        """
        match_id = over.pop("id", "22222222-2222-2222-2222-222222222222")
        row = {
            "id": match_id, "tournament_id": tournament_id,
            "player1_id": p1, "player2_id": p2,
            "player1_name": "P1", "player2_name": "P2",
            # Fixture generation stamps this from rules.targetScore; the
            # validator reads it off the match, so a seeded match needs it too.
            "target_points": 29,
            # Matches are scheduled|live|paused|completed. "in_progress" is a
            # BOARD status; seeding it here made every timer transition 422.
            "status": "live", "stage": "knockout",
            "max_boards": boards, "number_of_sets": sets,
            "match_number": 1, "result_confirmed": False,
            "player1_board_wins": 0, "player2_board_wins": 0,
            "player1_total_points": 0, "player2_total_points": 0,
        }
        row.update(over)
        self.db.seed("matches", [row])
        self.db.seed("boards", [{
            "id": "b%d-%d" % (s, i), "match_id": match_id, "board_number": i,
            "set_number": s,
            "status": "in_progress" if (s == 1 and i == 1) else "pending",
            "player1_score": 0, "player2_score": 0,
        } for s in range(1, sets + 1) for i in range(1, boards + 1)])
        return match_id

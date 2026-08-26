from pydantic import BaseModel
from typing import Optional
from app.models.tournament import BaseCamelModel

class NotificationCreateSchema(BaseCamelModel):
    title: str
    message: str
    type: str  # e.g., 'tournament_published', 'registration_confirmed', etc.
    profile_id: Optional[str] = None
    tournament_id: Optional[str] = None

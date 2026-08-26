from pydantic import BaseModel, Field
from typing import Optional

class PlayerSchema(BaseModel):
    id: Optional[str] = None
    name: str
    avatar: Optional[str] = None
    club: Optional[str] = "Independent"
    city: Optional[str] = "Pune"
    rating: Optional[int] = 1500
    seed: Optional[int] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = "player"

class TeamSchema(BaseModel):
    id: Optional[str] = None
    name: str
    player1_id: str
    player2_id: str
    club: Optional[str] = None
    city: Optional[str] = None
    rating: Optional[int] = 1500
    seed: Optional[int] = None

from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class SignUpSchema(BaseModel):
    """
    Public registration. Everyone who signs up is a player.

    `role` used to be an ordinary field here, taken from the request and written
    straight into Supabase app_metadata -- so anyone who could reach the sign-up
    form could make themselves a full admin, and the form offered it as a
    visible choice. Admin rights are granted deliberately now, by an existing
    admin or with db/promote_admin.py, never claimed on the way in.
    """
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str
    club: Optional[str] = "Independent"
    city: Optional[str] = None
    phone: Optional[str] = None
    rating: Optional[int] = 1500

class LoginSchema(BaseModel):
    email: EmailStr
    password: str
    role: str = "player"  # "player" or "admin"


class RefreshSchema(BaseModel):
    refresh_token: str

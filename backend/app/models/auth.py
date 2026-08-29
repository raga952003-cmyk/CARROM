from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class SignUpSchema(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str
    role: str = "player"  # "player" or "admin"
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

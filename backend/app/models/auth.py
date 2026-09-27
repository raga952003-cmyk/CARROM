from pydantic import BaseModel, EmailStr, Field
from app.models.tournament import BaseCamelModel
from typing import Optional

class SignUpSchema(BaseCamelModel):
    """
    Public registration creates a player account. Admins are provisioned by
    invitation or a trusted operator.
    """
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str
    club: Optional[str] = "Independent"
    city: Optional[str] = None
    phone: Optional[str] = None
    rating: Optional[int] = 1500
    # Kept for older clients; the public endpoint accepts only player.
    role: Optional[str] = "player"

class LoginSchema(BaseModel):
    email: EmailStr
    password: str
    role: str = "player"  # "player" or "admin"


class RefreshSchema(BaseModel):
    refresh_token: str

class ProfileUpdateSchema(BaseCamelModel):
    """What a person may change about their own profile."""
    name: Optional[str] = None
    club: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    # A data URI. Kept small by the browser before it is sent -- see the size
    # check in the endpoint, which is the real guard.
    avatar: Optional[str] = None


class PasswordChangeSchema(BaseCamelModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


class EmailChangeSchema(BaseCamelModel):
    """Changing the address you sign in with, so the current password is required."""
    current_password: str
    new_email: EmailStr


class ForgotPasswordSchema(BaseCamelModel):
    """Ask for a reset link. Answered identically whether the account exists or not."""
    email: EmailStr


class ResetPasswordSchema(BaseCamelModel):
    """
    Finish a reset.

    The recovery token hash from the email is verified as a one-time recovery
    proof by Supabase Auth before any password change.
    """
    token_hash: str
    new_password: str = Field(..., min_length=6)

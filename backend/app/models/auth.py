from pydantic import BaseModel, EmailStr, Field
from app.models.tournament import BaseCamelModel
from typing import Optional

class SignUpSchema(BaseCamelModel):
    """
    Public registration, in whichever role the form asked for.

    `role` is taken from the request. Registration is therefore OPEN: anyone who
    can reach the sign-up page can create an administrator account, which is a
    deliberate choice for this deployment and not an oversight. There is no key
    and no invitation; the only thing standing between a visitor and admin
    rights is the sign-up form itself.

    Anything narrower has to be done elsewhere -- put the app behind something,
    or take the Administrator tab off the form and promote accounts with
    db/promote_admin.py instead.
    """
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str
    club: Optional[str] = "Independent"
    city: Optional[str] = None
    phone: Optional[str] = None
    rating: Optional[int] = 1500
    # "player" or "admin". Anything else is refused rather than guessed at,
    # so a typo cannot quietly create the wrong kind of account.
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

    The recovery token arrives in the URL fragment of the emailed link. The
    browser hands it back here rather than talking to Supabase directly,
    because the frontend Supabase client is not configured in this deployment
    -- so the standard client-side recovery flow has nothing to run on.
    """
    access_token: str
    new_password: str = Field(..., min_length=6)

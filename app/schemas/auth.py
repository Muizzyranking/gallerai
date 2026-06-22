from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def password_strength(password: str) -> str:
    if not any(c.isupper() for c in password):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not any(c.isdigit() for c in password):
        raise ValueError("Password must contain at least one digit.")
    return password


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return password_strength(v)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class LogoutUser(BaseModel):
    refresh_token: str | None = None
    all_sessions: bool = False


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str | None
    is_email_confirmed: bool
    pending_email: str | None = None
    auth_provider: str
    is_admin: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return password_strength(v)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return password_strength(v)


class EmailConfirmRequest(BaseModel):
    token: str


class ResendConfirmationRequest(BaseModel):
    email: EmailStr


class DeleteAccountRequest(BaseModel):
    current_password: str | None = None


class GoogleAuthUrlResponse(BaseModel):
    auth_url: str

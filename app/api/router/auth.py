from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request, status
from fastlimit import rate_limit

from app.api.dependencies import AsyncDB, CurrentUser
from app.core.schemas import ApiResponse
from app.schemas.auth import (
    ChangePasswordRequest,
    ConfirmEmailRequest,
    DeleteAccountRequest,
    EmailConfirmRequest,
    ForgotPasswordRequest,
    GoogleAuthUrlResponse,
    LogoutUser,
    RefreshTokenRequest,
    ResendConfirmationRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
)
from app.services.auth import auth_service, google_oauth

router = APIRouter()


@router.post(
    "/register",
    response_model=ApiResponse[None],
    status_code=201,
    dependencies=[rate_limit("5/min", user="10/min")],
)
async def register(payload: UserCreate, db: AsyncDB, bg_task: BackgroundTasks):
    await auth_service.register_user(payload, db, bg_task)
    return ApiResponse[None](
        message="User registered successfully, check email for confirmation link"
    )


@router.post(
    "/register/confirm",
    response_model=ApiResponse[None],
    status_code=200,
)
async def email_confirmation(payload: ConfirmEmailRequest, db: AsyncDB):
    await auth_service.confirm_email(payload.token, db)
    return ApiResponse[None](message="Email confirmed successfully")


@router.post(
    "/verify-email",
    response_model=ApiResponse[None],
    status_code=status.HTTP_200_OK,
)
async def verify_email(payload: EmailConfirmRequest, db: AsyncDB):
    await auth_service.confirm_email(payload.token, db)
    return ApiResponse[None](message="Email confirmed successfully")


@router.post(
    "/resend_email_confirmation", response_model=ApiResponse[None], status_code=201
)
async def resend_email_confirmation(
    email: Annotated[str, Query()], db: AsyncDB, bg_task: BackgroundTasks
):
    await auth_service.resend_confirmation(email, db, bg_task)
    return ApiResponse[None](message="Confirmation email resent successfully")


@router.post(
    "/resend-email-verification",
    response_model=ApiResponse[None],
    status_code=status.HTTP_201_CREATED,
)
async def resend_email_verification(
    payload: ResendConfirmationRequest, db: AsyncDB, bg_task: BackgroundTasks
):
    await auth_service.resend_confirmation(payload.email, db, bg_task)
    return ApiResponse[None](message="Confirmation email resent successfully")


@router.post("/login", response_model=ApiResponse[TokenResponse])
def login(request: Request, payload: UserLogin, db: AsyncDB):
    data = auth_service.login_user(payload, db, request)
    return ApiResponse[TokenResponse](message="Login successful", data=data)


@router.post("/refresh", response_model=ApiResponse[TokenResponse])
def refresh_token(request: Request, payload: RefreshTokenRequest, db: AsyncDB):
    data = auth_service.refresh_access_token(db, payload.refresh_token, request)
    return ApiResponse[TokenResponse](message="Token refreshed successfully", data=data)


@router.post("/logout", response_model=ApiResponse[None])
async def logout(current_user: CurrentUser, db: AsyncDB, payload: LogoutUser):
    await auth_service.logout_user(
        db,
        current_user,
        payload.refresh_token,
        all_sessions=payload.all_sessions,
    )
    return ApiResponse[None](message="Logout successful")


@router.post("/forgot-password", response_model=ApiResponse[None])
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncDB,
    bg_task: BackgroundTasks,
):
    await auth_service.forgot_password(payload, db, bg_task)
    return ApiResponse[None](
        message="If the email exists, a password reset link has been sent"
    )


@router.post("/reset-password", response_model=ApiResponse[None])
async def reset_password(payload: ResetPasswordRequest, db: AsyncDB):
    await auth_service.reset_password(payload, db)
    return ApiResponse[None](message="Password reset successfully")


@router.post("/change-password", response_model=ApiResponse[None])
async def change_password(
    payload: ChangePasswordRequest, current_user: CurrentUser, db: AsyncDB
):
    await auth_service.change_password(payload, current_user, db)
    return ApiResponse[None](message="Password changed successfully")


@router.get("/me", response_model=ApiResponse[UserResponse])
def me(current_user: CurrentUser):
    return ApiResponse[UserResponse](
        message="Current user retrieved successfully",
        data=UserResponse.model_validate(current_user),
    )


@router.patch("/me", response_model=ApiResponse[UserResponse])
def update_me(
    payload: UserUpdate,
    current_user: CurrentUser,
    db: AsyncDB,
    bg_task: BackgroundTasks,
):
    data = auth_service.update_me(payload, current_user, db, bg_task)
    return ApiResponse[UserResponse](
        message="Account updated successfully",
        data=data,
    )


@router.delete("/me", response_model=ApiResponse[None])
async def delete_account(
    payload: DeleteAccountRequest,
    current_user: CurrentUser,
    db: AsyncDB,
):
    await auth_service.delete_account(
        current_user,
        db,
        current_password=payload.current_password,
    )
    return ApiResponse[None](message="Account deleted successfully")


@router.get("/google", response_model=ApiResponse[GoogleAuthUrlResponse])
def google_auth_url():
    return ApiResponse[GoogleAuthUrlResponse](
        message="Google authorization URL generated successfully",
        data=GoogleAuthUrlResponse(auth_url=google_oauth.get_google_auth_url()),
    )


@router.get("/google/callback", response_model=ApiResponse[TokenResponse])
async def google_callback(
    request: Request,
    db: AsyncDB,
    code: Annotated[str, Query()],
    state: Annotated[str | None, Query()] = None,
):
    data = await google_oauth.google_callback(code, db, request, state=state)
    return ApiResponse[TokenResponse](message="Google login successful", data=data)

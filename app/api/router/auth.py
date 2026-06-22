from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastlimit import rate_limit

from app.api.dependencies import DB, CurrentUser
from app.core.schemas import ApiResponse
from app.schemas.auth import LogoutUser, TokenResponse, UserCreate, UserLogin
from app.schemas.user import UserResponse
from app.services.auth import auth_service

router = APIRouter()


@router.post(
    "/register",
    response_model=ApiResponse[None],
    status_code=201,
    dependencies=[rate_limit("5/min", user="10/min")],
)
def register(payload: UserCreate, db: DB, bg_task: BackgroundTasks):
    auth_service.register_user(payload, db, bg_task)
    return ApiResponse[None](
        message="User registered successfully, check email for confirmation link"
    )


@router.post(
    "/register/confirm",
    response_model=ApiResponse[None],
    status_code=200,
)
def email_confirmation(token: Annotated[str, Query()], db: DB):
    auth_service.confirm_email(token, db)
    return ApiResponse[None](message="Email confirmed successfully")


@router.post(
    "/resend_email_confirmation", response_model=ApiResponse[None], status_code=201
)
def resend_email_confirmation(
    email: Annotated[str, Query()], db: DB, bg_task: BackgroundTasks
):
    auth_service.resend_confirmation(email, db, bg_task)
    return ApiResponse[None](message="Confirmation email resent successfully")


@router.post("/login", response_model=ApiResponse[TokenResponse])
def login(request: Request, payload: UserLogin, db: DB):
    data = auth_service.login_user(payload, db, request)
    return ApiResponse[TokenResponse](message="Login successful", data=data)


@router.post("/logout", response_model=ApiResponse[None])
def logout(current_user: CurrentUser, db: DB, payload: LogoutUser):
    auth_service.logout_user(db, current_user, payload.refresh_token)
    return ApiResponse[None](message="Logout successful")


@router.get("/me", response_model=ApiResponse[UserResponse])
def me(current_user: CurrentUser):
    return ApiResponse[UserResponse](
        message="Current user retrieved successfully",
        data=UserResponse.model_validate(current_user),
    )


@router.get("/me", response_model=ApiResponse[UserResponse])
def update_me(current_user: CurrentUser):
    return ApiResponse[UserResponse](
        message="Current user retrieved successfully",
        data=UserResponse.model_validate(current_user),
    )

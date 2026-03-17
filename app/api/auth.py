from fastapi import APIRouter

from app.api.dependencies import DB, CurrentUser
from app.core.schemas import ApiResponse
from app.schemas.user import TokenResponse, UserCreate, UserLogin, UserResponse
from app.services.auth_service import login_user, register_user

router = APIRouter()


@router.post("/register", response_model=ApiResponse[TokenResponse], status_code=201)
def register(payload: UserCreate, db: DB):
    data = register_user(payload, db)
    return ApiResponse[TokenResponse](message="User registered successfully", data=data)


@router.post("/login", response_model=ApiResponse[TokenResponse])
def login(payload: UserLogin, db: DB):
    data = login_user(payload.email, payload.password, db)
    return ApiResponse[TokenResponse](message="Login successful", data=data)


@router.get("/me", response_model=ApiResponse[UserResponse])
def me(current_user: CurrentUser):
    return ApiResponse[UserResponse](
        message="Current user retrieved successfully",
        data=UserResponse.model_validate(current_user),
    )

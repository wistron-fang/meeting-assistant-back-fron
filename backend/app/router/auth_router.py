# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""用户认证路由"""
import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_token,
)
from models.user import User
from schemas.user import (
    UserCreate,
    UserLogin,
    UserResponse,
    TokenResponse,
    PasswordChange,
    StarUpdate,
)

router = APIRouter(prefix="/auth", tags=["认证"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)

# 自助注册开关：默认【关闭】，用户由管理员用脚本/SQL 创建（见运维文档）。
# 临时开放：在 backend/.env 设 ALLOW_REGISTRATION=true 后重启 uvicorn。
REGISTRATION_ENABLED = os.getenv("ALLOW_REGISTRATION", "false").strip().lower() in ("1", "true", "yes", "on")


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """根据用户名获取用户"""
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """根据邮箱获取用户"""
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    """根据 ID 获取用户"""
    return db.query(User).filter(User.id == user_id).first()


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """验证用户"""
    # 支持用户名或邮箱登录
    user = get_user_by_username(db, username)
    if not user:
        user = get_user_by_email(db, username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """获取当前用户（可选认证）"""
    if not token:
        return None

    token_data = decode_token(token)
    if token_data is None:
        return None

    user = get_user_by_id(db, token_data.user_id)
    if user is None:
        return None

    return user


async def get_current_user_required(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """获取当前用户（必须认证）"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    token_data = decode_token(token)
    if token_data is None:
        raise credentials_exception

    user = get_user_by_id(db, token_data.user_id)
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用"
        )

    return user


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: Session = Depends(get_db)
):
    """用户注册（默认已关闭，由管理员建号；ALLOW_REGISTRATION=true 可临时开放）"""
    if not REGISTRATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="注册已关闭，请联系管理员开通账号",
        )
    # 检查用户名是否已存在
    if get_user_by_username(db, user_data.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已被注册"
        )

    # 检查邮箱是否已存在
    if get_user_by_email(db, user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱已被注册"
        )

    # 创建用户
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password)
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 生成 Token
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username}
    )

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.model_validate(user)
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    user_data: UserLogin,
    db: Session = Depends(get_db)
):
    """用户登录"""
    user = authenticate_user(db, user_data.username, user_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用"
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username}
    )

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.model_validate(user)
    )


@router.post("/token", response_model=TokenResponse)
async def login_for_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """OAuth2 兼容的登录接口"""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username}
    )

    return TokenResponse(
        access_token=access_token,
        user=UserResponse.model_validate(user)
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user_required)):
    """获取当前用户信息"""
    return UserResponse.model_validate(current_user)


@router.post("/change-password")
async def change_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    """修改密码"""
    if not verify_password(password_data.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="旧密码错误"
        )

    current_user.hashed_password = get_password_hash(password_data.new_password)
    db.commit()

    return {"message": "密码修改成功"}


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user_required)):
    """用户登出（前端清除 Token 即可）"""
    return {"message": "登出成功"}


@router.patch("/users/{user_id}/star", response_model=UserResponse)
async def update_user_star(
    user_id: str,
    body: StarUpdate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """管理员调整用户星级 / 配额覆盖（仅 superuser）。

    星级驱动队列优先级与配额默认；max_concurrent / monthly_task_quota / monthly_token_quota
    为可选覆盖列，传了才改，不传保持原值（None = 取星级默认）。
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")

    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    user.star_level = body.star_level
    if body.max_concurrent is not None:
        user.max_concurrent = body.max_concurrent
    if body.monthly_task_quota is not None:
        user.monthly_task_quota = body.monthly_task_quota
    if body.monthly_token_quota is not None:
        user.monthly_token_quota = body.monthly_token_quota
    db.commit()
    db.refresh(user)

    return UserResponse.model_validate(user)

# Copyright © 2026 广州金元信息科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""用户相关 Schema"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from uuid import UUID


class UserBase(BaseModel):
    """用户基础 Schema"""
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: EmailStr = Field(..., description="邮箱")


class UserCreate(UserBase):
    """用户注册 Schema"""
    password: str = Field(..., min_length=6, max_length=100, description="密码")


class UserLogin(BaseModel):
    """用户登录 Schema"""
    username: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码")


class UserResponse(UserBase):
    """用户响应 Schema"""
    id: UUID
    is_active: bool
    created_at: datetime
    star_level: int = 1

    class Config:
        from_attributes = True


class StarUpdate(BaseModel):
    """管理员调整用户星级 / 配额覆盖 Schema（仅传的字段会被改）"""
    star_level: int = Field(..., ge=1, le=5, description="星级 1~5")
    max_concurrent: Optional[int] = Field(None, ge=1, description="并发上限覆盖；不传则保持原值")
    monthly_task_quota: Optional[int] = Field(None, ge=0, description="月任务数覆盖；不传则保持原值")
    monthly_token_quota: Optional[int] = Field(None, description="月 token 覆盖；≤0 表示不限；不传则保持原值")


class UserInDB(UserResponse):
    """数据库中的用户"""
    hashed_password: str
    is_superuser: bool
    updated_at: datetime


class TokenResponse(BaseModel):
    """Token 响应"""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PasswordChange(BaseModel):
    """修改密码 Schema"""
    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., min_length=6, max_length=100, description="新密码")

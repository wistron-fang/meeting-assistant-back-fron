/**
 * Copyright © 2026 广州金元信息科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 */

import { request } from './request'

export interface LoginParams {
  username: string
  password: string
}

export interface RegisterParams {
  username: string
  email: string
  password: string
}

export interface UserInfo {
  id: string
  username: string
  email: string
  is_active: boolean
  is_superuser?: boolean   // 后端 user 表有此字段，UserResponse 可能未默认返回；按 false 处理
  created_at: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: UserInfo
}

/**
 * 用户登录
 */
export function login(params: LoginParams) {
  return request.post<AuthResponse>('/auth/login', params)
}

/**
 * 用户注册
 */
export function register(params: RegisterParams) {
  return request.post<AuthResponse>('/auth/register', params)
}

/**
 * 获取当前用户信息
 */
export function getCurrentUser() {
  return request.get<UserInfo>('/auth/me')
}

/**
 * 用户登出
 */
export function logout() {
  return request.post('/auth/logout')
}

/**
 * 修改密码
 */
export function changePassword(params: { old_password: string; new_password: string }) {
  return request.post('/auth/change-password', params)
}

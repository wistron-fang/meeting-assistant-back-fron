/**
 * Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 */

/**
 * 认证插件：
 * 1. 请求时自动添加 Token 到请求头
 * 2. 响应 401 时清空登录状态并跳回登录页（保留 from 以便登录后回跳）
 */
import { IRequestPlugin } from './plugin'

const AUTH_STORAGE_KEY = 'auth'

let isRedirecting = false

function getToken(): string | null {
  try {
    const authData = localStorage.getItem(AUTH_STORAGE_KEY)
    if (authData) {
      const parsed = JSON.parse(authData)
      return parsed?.token || null
    }
  } catch {
    // ignore
  }
  return null
}

function handleUnauthorized() {
  if (isRedirecting) return
  isRedirecting = true

  localStorage.removeItem(AUTH_STORAGE_KEY)

  const inLogin = window.location.pathname.startsWith('/login')
  if (inLogin) {
    isRedirecting = false
    return
  }

  try {
    window.$app?.message.warning('登录已过期，请重新登录')
  } catch {
    // ignore
  }

  const from = window.location.pathname + window.location.search
  setTimeout(() => {
    window.location.href = `/login?from=${encodeURIComponent(from)}`
  }, 600)
}

export const authPlugin: IRequestPlugin = {
  preinstall(instance) {
    instance.interceptors.request.use(
      (config) => {
        const token = getToken()
        if (token) {
          config.headers.Authorization = `Bearer ${token}`
        }
        return config
      },
      (error) => Promise.reject(error)
    )
  },
  postinstall(instance) {
    instance.interceptors.response.use(
      (response) => response,
      (error) => {
        if (error?.response?.status === 401) {
          if (error.config) {
            (error.config as any).errorToast = false
          }
          handleUnauthorized()
        }
        return Promise.reject(error)
      }
    )
  },
}

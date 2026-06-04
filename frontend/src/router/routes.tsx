/**
 * Copyright © 2026 广州金元信息科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 */
import MeetingPage from '@/pages/meeting'
import { AuthGuard } from '@/components/auth-guard'
import { BaseLayout } from '@/layout/base'
import NotFound from '@/pages/404'
import LoginPage from '@/pages/auth/login'
import Index from '@/pages/index'
import {
  Navigate,
  Outlet,
  RouteObject,
  createBrowserRouter,
} from 'react-router-dom'

export type IRouteObject = {
  children?: IRouteObject[]
  name?: string
  auth?: boolean
  pure?: boolean
  meta?: any
} & Omit<RouteObject, 'children'>

export const routes: IRouteObject[] = [
  { path: '/', Component: Index },
  { path: '/meeting', Component: MeetingPage },
  { path: '/404', Component: NotFound, pure: true },
]

export const router = createBrowserRouter(
  [
    { path: '/login', element: <LoginPage /> },
    {
      path: '/',
      element: (
        <AuthGuard>
          <BaseLayout>
            <Outlet />
          </BaseLayout>
        </AuthGuard>
      ),
      children: routes,
    },
    { path: '*', element: <Navigate to="/404" /> },
  ] as RouteObject[],
  {
    basename: import.meta.env.BASE_URL,
    future: {
      v7_startTransition: true,
      v7_relativeSplatPath: true,
    },
  },
)

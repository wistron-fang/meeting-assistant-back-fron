/**
 * Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 */

import { useLocation } from 'react-router-dom'
import GbaLogo from '@/assets/gba-logo.png'
import { Footer } from './footer'
import './index.scss'
import { Nav } from './nav'

export function BaseLayout({ children }: { children?: React.ReactNode }) {
  const { pathname } = useLocation()
  const showTopBar = pathname === '/' || pathname.startsWith('/meeting')
  // 首页和会议纪要页让内容直接坐在浅灰底上，去掉白卡片包裹，更像扁平卡片布局
  const flushContent = pathname === '/' || pathname.startsWith('/meeting')

  const layoutClass = [
    'base-layout',
    showTopBar ? 'base-layout--with-topbar' : '',
    flushContent ? 'base-layout--flush' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={layoutClass}>
      {showTopBar && (
        <div className="base-layout__topbar">
          <img src={GbaLogo} alt="广州粤港澳大湾区研究院" />
        </div>
      )}

      <div className="base-layout__sidebar">
        <div className="base-layout__sidebar-main scrollbar-style">
          <Nav />

          <Footer />
        </div>
      </div>

      <div className="base-layout__content">{children}</div>
    </div>
  )
}

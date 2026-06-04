/**
 * Copyright © 2026 广州金元信息科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 */
import IconHome from '@/assets/layout/home.svg'
import IconMeeting from '@/assets/layout/news.svg'
import { useMemo } from 'react'
import { useLocation } from 'react-router-dom'
import { NavItem } from './nav-item'
import './nav.scss'

export function Nav() {
  const { pathname } = useLocation()

  const items = useMemo(
    () => [
      { key: 'home', label: '首页', icon: IconHome, href: '/' },
      { key: 'meeting', label: '会议纪要', icon: IconMeeting, href: '/meeting' },
    ],
    [],
  )

  return (
    <div className="base-layout-nav">
      {items.map((item) => (
        <NavItem
          key={item.key}
          {...item}
          active={pathname === item.href}
        />
      ))}
    </div>
  )
}

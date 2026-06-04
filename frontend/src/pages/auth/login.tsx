/**
 * Copyright © 2026 广州金元信息科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 */

import * as api from '@/api'
import { authActions } from '@/store/auth'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { App, Button, Form, Input } from 'antd'
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import styles from './login.module.scss'

export default function LoginPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const location = useLocation()
  const [loading, setLoading] = useState(false)

  const fromQuery = new URLSearchParams(location.search).get('from')
  const from = (location.state as any)?.from?.pathname || fromQuery || '/'

  const onLogin = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      const { data } = await api.auth.login(values)
      authActions.login(data.access_token, data.user)
      message.success('登录成功')
      navigate(from, { replace: true })
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={styles['login-page']}>
      <div className={styles['login-container']}>
        {/* 左侧品牌区域 */}
        <div className={styles['brand-section']}>
          <div className={styles['brand-content']}>
            <div className={styles['brand-icon']}>
              <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M24 4L4 14V34L24 44L44 34V14L24 4Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                <path d="M24 4V24M24 24L4 14M24 24L44 14M24 24V44" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
              </svg>
            </div>
            <h1 className={styles['brand-title']}>
              广州粤港澳大湾区研究院
              <br />
              会议纪要助手
            </h1>
            <p className={styles['brand-subtitle']}>Meeting Minutes Assistant</p>
            <div className={styles['brand-features']}>
              <div className={styles['feature-item']}>
                <span className={styles['feature-icon']} />
                <span>AI 自动整理会议纪要</span>
              </div>
              <div className={styles['feature-item']}>
                <span className={styles['feature-icon']} />
                <span>Markdown + PDF + Word 一键导出</span>
              </div>
              <div className={styles['feature-item']}>
                <span className={styles['feature-icon']} />
                <span>结构化输出会议要点</span>
              </div>
            </div>
          </div>
        </div>

        {/* 右侧表单区域 */}
        <div className={styles['form-section']}>
          <div className={styles['form-container']}>
            <div className={styles['form-header']}>
              <h2>欢迎回来</h2>
              <p>登录您的账户以继续</p>
            </div>

            <Form
              name="login"
              onFinish={onLogin}
              autoComplete="off"
              layout="vertical"
              requiredMark={false}
            >
              <Form.Item
                name="username"
                rules={[{ required: true, message: '请输入用户名' }]}
              >
                <Input
                  prefix={<UserOutlined className={styles['input-icon']} />}
                  placeholder="用户名或邮箱"
                  size="large"
                  className={styles['form-input']}
                />
              </Form.Item>

              <Form.Item
                name="password"
                rules={[{ required: true, message: '请输入密码' }]}
              >
                <Input.Password
                  prefix={<LockOutlined className={styles['input-icon']} />}
                  placeholder="密码"
                  size="large"
                  className={styles['form-input']}
                />
              </Form.Item>

              <Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={loading}
                  block
                  size="large"
                  className={styles['submit-btn']}
                >
                  登录
                </Button>
              </Form.Item>
            </Form>
          </div>
        </div>
      </div>
    </div>
  )
}

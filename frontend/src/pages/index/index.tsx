/**
 * Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 *
 * 首页中间内容区
 * 顶部 logo 栏和左侧导航栏由 BaseLayout 提供，此处仅渲染 Content 部分。
 */

import { Button, Card, Row, Col, Tag } from 'antd'
import HeroAvatar from '@/assets/index/female.png'
import {
  ArrowRightOutlined,
  FileTextOutlined,
  FileMarkdownOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  EditOutlined,
  UploadOutlined,
  AudioOutlined,
  ClockCircleOutlined,
  SafetyCertificateOutlined,
  CalendarOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import styles from './index.module.scss'

interface FeatureCard {
  key: string
  icon: React.ReactNode
  color: string
  title: string
  desc: string
  tags: { icon: React.ReactNode; label: string }[]
}

const FEATURES: FeatureCard[] = [
  {
    key: 'input',
    icon: <EditOutlined />,
    color: '#1677ff',
    title: '支持多种输入',
    desc: '粘贴会议原文、上传 .txt / .Word 文件、上传录音（自动转写文稿）',
    tags: [
      { icon: <EditOutlined />, label: '粘贴文本' },
      { icon: <UploadOutlined />, label: '上传文件' },
      { icon: <AudioOutlined />, label: '上传录音' },
    ],
  },
  {
    key: 'output',
    icon: <FileTextOutlined />,
    color: '#52c41a',
    title: '支持多种输出',
    desc: '同时生成 Markdown / PDF / Word 等格式，支持下载',
    tags: [
      { icon: <FileMarkdownOutlined />, label: 'Markdown' },
      { icon: <FilePdfOutlined />, label: 'PDF' },
      { icon: <FileWordOutlined />, label: 'Word' },
    ],
  },
  {
    key: 'retain',
    icon: <ClockCircleOutlined />,
    color: '#faad14',
    title: '自动保留 7 天',
    desc: '历史会议纪要保留 7 天后自动清理，期间随时下载',
    tags: [
      { icon: <SafetyCertificateOutlined />, label: '隐私安全' },
      { icon: <CalendarOutlined />, label: '7 天保留' },
    ],
  },
]

export default function Index() {
  const navigate = useNavigate()

  return (
    <div className={styles.indexPage}>
      {/* 问候 */}
      <div className={styles.greet}>
        <h2 className={styles.greetTitle}>欢迎使用会议纪要助手 👋</h2>
        <div className={styles.greetSub}>让每一次会议都变成可检索的资产。</div>
      </div>

      {/* Hero 欢迎卡 */}
      <Card className={styles.heroCard} bordered={false}>
        <div className={styles.heroInner}>
          {/* 左侧人物插画 */}
          <div className={styles.heroAvatar} aria-hidden>
            <img src={HeroAvatar} alt="" className={styles.heroAvatarImg} />
          </div>

          <div className={styles.heroLeft}>
            <h1 className={styles.heroTitle}>
              我是广州粤港澳大湾区研究院研究助理小纪，我擅长整理会议报告。
            </h1>
            <p className={styles.heroDesc}>
              粘贴或上传会议原文，自动生成结构化会议纪要（Markdown + PDF + Word）。
            </p>
            <Button
              type="primary"
              size="large"
              icon={<ArrowRightOutlined />}
              className={styles.heroPrimary}
              onClick={() => navigate('/meeting')}
            >
              开始整理会议纪要
            </Button>
          </div>
        </div>
      </Card>

      {/* 功能介绍 */}
      <div className={styles.featureHead}>
        <h3 className={styles.featureTitle}>主要功能</h3>
        <p className={styles.featureSub}>三步走，把零散的会议讨论沉淀成规范的会议纪要。</p>
      </div>

      <Row gutter={[20, 20]} className={styles.featureRow}>
        {FEATURES.map((f) => (
          <Col xs={24} md={8} key={f.key}>
            <Card bordered={false} className={styles.featureCard}>
              <div
                className={styles.featureIcon}
                style={{ color: f.color, background: `${f.color}1a` }}
              >
                {f.icon}
              </div>
              <div className={styles.featureCardTitle}>{f.title}</div>
              <div className={styles.featureCardDesc}>{f.desc}</div>
              <div className={styles.featureCardTags}>
                {f.tags.map((t) => (
                  <Tag
                    key={t.label}
                    bordered={false}
                    icon={t.icon}
                    className={styles.featureTag}
                  >
                    {t.label}
                  </Tag>
                ))}
              </div>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  )
}

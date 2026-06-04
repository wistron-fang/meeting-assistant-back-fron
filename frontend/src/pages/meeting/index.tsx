/**
 * Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 *
 * 会议纪要整理页（卡片化扁平布局）
 * 业务逻辑（API / 轮询 / 校验 / 分页 / 搜索）完全沿用旧版，仅做排版重构。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Row,
  Col,
  Card,
  Segmented,
  Input,
  Upload,
  Button,
  Form,
  Tag,
  message,
  Popconfirm,
  Popover,
  Pagination,
  Tooltip,
  Empty,
} from 'antd'
import {
  InboxOutlined,
  ReloadOutlined,
  FileMarkdownOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  LoadingOutlined,
  AudioOutlined,
  SoundOutlined,
  InfoCircleOutlined,
  SearchOutlined,
  EditOutlined,
  UploadOutlined,
  AppstoreOutlined,
  ClockCircleOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
} from '@ant-design/icons'
import type { UploadFile, RcFile } from 'antd/es/upload/interface'
import {
  MeetingTask,
  createMeetingTasks,
  listMeetingTasks,
  deleteMeetingTask,
  downloadMeetingTask,
  batchZipDownload,
} from '@/api/meeting'
import styles from './index.module.scss'

const { Dragger } = Upload
const { TextArea } = Input
const POLL_INTERVAL_MS = 3000

type StatusFilter = 'all' | 'pending,running' | 'done' | 'failed'

const STATUS_TAG: Record<MeetingTask['status'], { color: string; text: string; icon: React.ReactNode }> = {
  pending: { color: 'default', text: '排队中', icon: <LoadingOutlined /> },
  running: { color: 'processing', text: '处理中', icon: <LoadingOutlined /> },
  done: { color: 'success', text: '已完成', icon: <CheckCircleFilled /> },
  failed: { color: 'error', text: '失败', icon: <CloseCircleFilled /> },
}

// 秒数 → "HH:MM:SS"
function formatElapsed(secs: number): string {
  if (!Number.isFinite(secs) || secs < 0) secs = 0
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = Math.floor(secs % 60)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${pad(h)}:${pad(m)}:${pad(s)}`
}

// 计算任务已用时（秒）
function getElapsedSec(t: MeetingTask, nowMs: number): number | null {
  if (!t.started_at) return null
  const startMs = new Date(t.started_at).getTime()
  if (t.status === 'done' || t.status === 'failed') {
    if (!t.finished_at) return null
    return (new Date(t.finished_at).getTime() - startMs) / 1000
  }
  return (nowMs - startMs) / 1000
}

// 计算任务距离过期还剩几天（后端 7 天后自动清理）
const RETENTION_DAYS = 7
function formatExpireHint(createdAt: string): string {
  const createdMs = new Date(createdAt).getTime()
  const expireMs = createdMs + RETENTION_DAYS * 86400000
  const diffDays = Math.ceil((expireMs - Date.now()) / 86400000)
  if (diffDays < 0) return '已过期'
  if (diffDays === 0) return '今天过期'
  return `${diffDays} 天后过期`
}

const SAMPLE_TEXT = `今天召开本周例会，参会人员包括项目经理、前端组、后端组、测试组共 12 人，会议时长 1 小时。

第一项议程，项目进度回顾。本周后端完成了用户中心模块的接口开发，覆盖率 85%；前端完成首页改版与登录页接入；测试组发现了 3 个高优先级 bug，已全部修复。整体进度符合预期，按计划下周可进入联调阶段。

第二项议程，下周客户对齐会议筹备。客户希望演示完整的用户注册到下单的流程，需要由产品负责输出演示脚本，前后端各派一人配合彩排。会议定在下周三下午 2 点，地点客户公司总部。

第三项议程，团队人员补充。前端组目前缺一名工程师，已收到 8 份简历，HR 和组长本周内完成初筛，下周开始安排技术面。希望本月内能到岗以缓解前端压力。

会议最后强调质量第一，避免赶工。会议于上午 11 点结束。`

const AUDIO_ACCEPT = '.m4a,.mp4,.wav,.aac,.mp3'
const AUDIO_MAX_MB = 500
const DOC_ALLOWED_EXTS = ['.txt', '.docx']
const DOC_MAX_MB = 50

export default function MeetingPage() {
  const [mode, setMode] = useState<'paste' | 'upload' | 'audio'>('paste')
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm()
  const [fileList, setFileList] = useState<UploadFile[]>([])
  const [audioList, setAudioList] = useState<UploadFile[]>([])

  const [tasks, setTasks] = useState<MeetingTask[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState({ pending: 0, running: 0, done: 0, failed: 0 })
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<number[]>([])
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 10

  const [searchTitle, setSearchTitle] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [nowMs, setNowMs] = useState(() => Date.now())
  const pollRef = useRef<number | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const r = await listMeetingTasks({
        page,
        page_size: PAGE_SIZE,
        title: searchTitle || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
      })
      setTasks(r.items)
      setTotal(r.total)
      setStats(r.stats)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const hasPending = tasks.some((t) => t.status === 'pending' || t.status === 'running')
    if (hasPending && !pollRef.current) {
      pollRef.current = window.setInterval(load, POLL_INTERVAL_MS)
    } else if (!hasPending && pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [tasks])

  // 本地秒级 tick：只在有运行中任务时启动
  useEffect(() => {
    const hasRunning = tasks.some(
      (t) => (t.status === 'pending' || t.status === 'running') && t.started_at,
    )
    if (!hasRunning) return
    const id = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [tasks])

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, searchTitle, statusFilter])

  const onSubmit = async () => {
    const values = await form.validateFields().catch(() => null)
    if (!values) return
    setSubmitting(true)
    try {
      if (mode === 'paste') {
        if (!values.text?.trim()) {
          message.warning('请粘贴会议原文')
          return
        }
        await createMeetingTasks({ title: values.title, text: values.text })
      } else if (mode === 'upload') {
        if (fileList.length === 0) {
          message.warning('请选择 .txt 或 .docx 文件')
          return
        }
        if (fileList.length > 2) {
          message.warning('最多同时上传 2 个文件')
          return
        }
        const files = fileList.map((f) => f.originFileObj as File).filter(Boolean)
        await createMeetingTasks({ title: values.title, files })
      } else {
        if (audioList.length === 0) {
          message.warning('请选择录音文件')
          return
        }
        const audio = audioList[0].originFileObj as File | undefined
        if (!audio) {
          message.warning('音频文件无效')
          return
        }
        await createMeetingTasks({ title: values.title, audio })
      }
      message.success('任务已创建，正在排队处理')
      form.resetFields()
      setFileList([])
      setAudioList([])
      if (page !== 1) {
        setPage(1)
      } else {
        await load()
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      message.error(typeof detail === 'string' ? detail : e?.message || '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  const fillSample = () => {
    form.setFieldsValue({ title: '本周项目例会', text: SAMPLE_TEXT })
    message.info('已填入示例文本，可直接提交体验')
  }

  const beforeUpload = (file: RcFile, list: RcFile[]) => {
    if (list.length > 2 || fileList.length + list.length > 2) {
      message.warning('最多同时上传 2 个文件')
      return Upload.LIST_IGNORE
    }
    const ext = '.' + (file.name.split('.').pop() || '').toLowerCase()
    if (!DOC_ALLOWED_EXTS.includes(ext)) {
      message.warning(`仅支持 ${DOC_ALLOWED_EXTS.join(' / ')}（不支持 .doc）`)
      return Upload.LIST_IGNORE
    }
    if (file.size > DOC_MAX_MB * 1024 * 1024) {
      message.warning(`文件 ${file.name} 超过 ${DOC_MAX_MB}MB 上限`)
      return Upload.LIST_IGNORE
    }
    return false
  }

  const beforeUploadAudio = (file: RcFile) => {
    const allowed = ['.m4a', '.mp4', '.wav', '.aac', '.mp3']
    const ext = '.' + (file.name.split('.').pop() || '').toLowerCase()
    if (!allowed.includes(ext)) {
      message.warning(`仅支持 ${allowed.join(' / ')}`)
      return Upload.LIST_IGNORE
    }
    if (file.size > AUDIO_MAX_MB * 1024 * 1024) {
      message.warning(`音频文件不能超过 ${AUDIO_MAX_MB}MB`)
      return Upload.LIST_IGNORE
    }
    return false
  }

  const onDelete = async (id: number) => {
    await deleteMeetingTask(id)
    message.success('已删除')
    setSelected((s) => s.filter((x) => x !== id))
    await load()
  }

  const toggleSelect = (id: number) => {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  }

  const onBatchZip = async () => {
    if (selected.length === 0) {
      message.warning('请勾选任务')
      return
    }
    const valid = tasks.filter((t) => selected.includes(t.id) && t.status === 'done')
    if (valid.length === 0) {
      message.warning('选中任务里没有已完成的')
      return
    }
    await batchZipDownload(valid.map((t) => t.id), 'all')
  }

  // 输入方式 Segmented 配置
  const inputSegments = [
    { label: (<span><EditOutlined /> 粘贴文本</span>), value: 'paste' },
    { label: (<span><UploadOutlined /> 上传文档</span>), value: 'upload' },
    { label: (<span><AudioOutlined /> 上传录音</span>), value: 'audio' },
  ]

  // 状态筛选 chip 配置
  const filterChips = useMemo(
    () => [
      { key: 'all' as StatusFilter, label: '全部任务', count: total, color: '#1d2129' },
      { key: 'pending,running' as StatusFilter, label: '进行中', count: stats.pending + stats.running, color: '#1677ff' },
      { key: 'done' as StatusFilter, label: '已完成', count: stats.done, color: '#52c41a' },
      { key: 'failed' as StatusFilter, label: '失败', count: stats.failed, color: '#ff4d4f' },
    ],
    [total, stats],
  )

  return (
    <div className={styles.meetingPage}>
      {/* 页头 */}
      <div className={styles.header}>
        <h2>📋 会议纪要整理</h2>
        <div className={styles.subtitle}>
          上传会议录音转写或粘贴原文，AI 自动生成结构化的 Markdown / PDF / Word 报告
        </div>
      </div>

      <Row gutter={[20, 20]} className={styles.mainRow}>
        {/* 左列：新建任务（sticky） */}
        <Col xs={24} lg={8}>
          <div className={styles.stickyWrap}>
            <Card
              bordered={false}
              className={styles.newTaskCard}
              title={
                <div className={styles.cardHead}>
                  <span className={styles.cardTitle}>
                    <AppstoreOutlined className={styles.cardTitleIcon} /> 新建任务
                  </span>
                </div>
              }
            >
              <Segmented
                block
                value={mode}
                onChange={(v) => setMode(v as any)}
                options={inputSegments}
                className={styles.modeSwitch}
              />

              <Form form={form} layout="vertical">
                {mode === 'paste' && (
                  <div className={styles.inputArea}>
                    <Button
                      type="link"
                      size="small"
                      className={styles.sampleBtn}
                      icon={<ExperimentOutlined />}
                      onClick={fillSample}
                    >
                      使用示例文本
                    </Button>
                    <Form.Item label="会议原文" name="text" style={{ marginBottom: 0 }}>
                      <TextArea
                        autoSize={{ minRows: 10, maxRows: 16 }}
                        placeholder="将会议原文粘贴到此处（建议 500 字以上）"
                        showCount
                        className={styles.pasteArea}
                      />
                    </Form.Item>
                  </div>
                )}

                {mode === 'upload' && (
                  <div className={styles.inputArea}>
                    <Form.Item label="选择文件（Word 或 txt）" style={{ marginBottom: 0 }}>
                      <Dragger
                        className={styles.dragger}
                        beforeUpload={beforeUpload}
                        fileList={fileList}
                        onChange={({ fileList }) => setFileList(fileList.slice(0, 2))}
                        multiple
                        maxCount={2}
                        accept=".txt,.docx"
                      >
                        <p>
                          <InboxOutlined className={styles.draggerIcon} />
                        </p>
                        <p>点击或拖拽 .txt / .docx 文件到此处</p>
                        <p className={styles.draggerHint}>
                          支持单个或批量上传，最多 2 个，单文件 ≤ {DOC_MAX_MB}MB（不支持 .doc）
                        </p>
                      </Dragger>
                    </Form.Item>
                  </div>
                )}

                {mode === 'audio' && (
                  <div className={styles.inputArea}>
                    <Form.Item label="选择录音文件" style={{ marginBottom: 0 }}>
                      <Dragger
                        className={styles.dragger}
                        beforeUpload={beforeUploadAudio}
                        fileList={audioList}
                        onChange={({ fileList }) => setAudioList(fileList.slice(-1))}
                        maxCount={1}
                        accept={AUDIO_ACCEPT}
                      >
                        <p>
                          <AudioOutlined className={styles.draggerIcon} />
                        </p>
                        <p>点击或拖拽录音文件到此处</p>
                        <p className={styles.draggerHint}>
                          支持 m4a / mp4 / wav / aac / mp3，单文件 ≤ {AUDIO_MAX_MB}MB
                        </p>
                        <p className={styles.draggerHint}>
                          系统会自动语音转写，再生成会议纪要（转写文本可单独下载）
                        </p>
                      </Dragger>
                    </Form.Item>
                  </div>
                )}

                <Form.Item label="会议标题" name="title" className={styles.formField} style={{ marginBottom: 0 }}>
                  <Input placeholder="可选，默认用文件名或会议日期" maxLength={120} />
                </Form.Item>

                <Button
                  type="primary"
                  block
                  size="large"
                  loading={submitting}
                  onClick={onSubmit}
                  className={styles.submitBtn}
                >
                  开始整理
                </Button>
              </Form>
            </Card>
          </div>
        </Col>

        {/* 右列：任务列表 + 内嵌状态筛选 */}
        <Col xs={24} lg={16}>
          <Card
            bordered={false}
            className={styles.tasksCard}
            title={
              <div className={styles.tasksHead}>
                <div className={styles.tasksTitleRow}>
                  <span className={styles.cardTitle}>
                    我的纪要任务
                    <Popover
                      trigger="click"
                      placement="bottomLeft"
                      content={
                        <div style={{ maxWidth: 280, fontSize: 12, lineHeight: 1.6 }}>
                          会议纪要任务（含 Markdown / PDF / Word 等产物）会在创建满 7 天后由系统自动清理，
                          届时记录将从列表中移除且不可恢复。请在有效期内下载所需文件。
                        </div>
                      }
                    >
                      <InfoCircleOutlined className={styles.titleIcon} />
                    </Popover>
                  </span>
                  <div className={styles.cardActions}>
                    <Input
                      allowClear
                      prefix={<SearchOutlined className={styles.searchIcon} />}
                      placeholder="按标题搜索"
                      value={searchInput}
                      onChange={(e) => setSearchInput(e.target.value)}
                      onPressEnter={() => {
                        setPage(1)
                        setSearchTitle(searchInput.trim())
                      }}
                      onBlur={() => {
                        // 失焦时若内容被清空，自动同步触发搜索
                        if (!searchInput.trim() && searchTitle) {
                          setPage(1)
                          setSearchTitle('')
                        }
                      }}
                      className={styles.searchInput}
                    />
                    {selected.length > 0 && (
                      <span style={{ fontSize: 12, color: '#8c8c8c' }}>已选 {selected.length}</span>
                    )}
                    <Button icon={<DownloadOutlined />} disabled={selected.length === 0} onClick={onBatchZip}>
                      批量打包
                    </Button>
                    <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>
                      刷新
                    </Button>
                  </div>
                </div>

                {/* 状态筛选 chips（兼任顶部统计） */}
                <div className={styles.filterChips}>
                  {filterChips.map((chip) => {
                    const active = statusFilter === chip.key
                    return (
                      <button
                        type="button"
                        key={chip.key}
                        onClick={() => {
                          setStatusFilter(chip.key)
                          setPage(1)
                        }}
                        className={`${styles.chip} ${active ? styles.chipActive : ''}`}
                      >
                        <span className={styles.chipDot} style={{ background: chip.color }} />
                        <span className={styles.chipLabel}>{chip.label}</span>
                        <span className={styles.chipCount}>{chip.count}</span>
                      </button>
                    )
                  })}
                </div>
              </div>
            }
          >
            {tasks.length === 0 && !loading ? (
              <Empty description="暂无任务" className={styles.empty} />
            ) : (
              <Row gutter={[16, 16]}>
                {tasks.map((t) => {
                  const tag = STATUS_TAG[t.status]
                  const isSelected = selected.includes(t.id)
                  const cardClass = [
                    styles.taskCard,
                    styles[`taskCard_${t.status}`],
                    isSelected ? styles.taskCard_selected : '',
                  ]
                    .filter(Boolean)
                    .join(' ')
                  const elapsed = getElapsedSec(t, nowMs)

                  return (
                    <Col xs={24} md={12} xxl={8} key={t.id}>
                      <div className={cardClass} onClick={() => toggleSelect(t.id)}>
                        <div className={styles.taskHead}>
                          <span className={styles.taskTitle}>{t.title || `任务 #${t.id}`}</span>
                          <Tag
                            bordered={false}
                            color={tag.color}
                            icon={tag.icon}
                            className={styles.statusTag}
                          >
                            {tag.text}
                          </Tag>
                        </div>

                        <div className={styles.taskMeta}>
                          <span className={styles.metaItem}>
                            {t.source_type === 'upload' ? (
                              <><UploadOutlined /> 上传</>
                            ) : t.source_type === 'audio' ? (
                              <><AudioOutlined /> 录音</>
                            ) : (
                              <><EditOutlined /> 粘贴</>
                            )}
                          </span>
                          <span className={styles.metaItem}>
                            {new Date(t.created_at).toLocaleString('zh-CN')}
                          </span>
                          {(t.status === 'done' || t.status === 'failed') && (
                            <span className={`${styles.metaItem} ${styles.expireHint}`}>
                              {formatExpireHint(t.created_at)}
                            </span>
                          )}
                        </div>

                        {(t.status === 'pending' || t.status === 'running') && (
                          <div className={styles.stageText}>
                            <LoadingOutlined spin /> {t.stage || '准备中'}
                          </div>
                        )}

                        {t.status === 'failed' && t.error && (
                          <div className={styles.cardError}>{t.error.split('\n')[0]}</div>
                        )}

                        {elapsed !== null &&
                          (t.status === 'pending' || t.status === 'running') && (
                          <div className={styles.taskDuration}>
                            <span className={styles.durationLabel}>
                              <ClockCircleOutlined /> 用时
                            </span>
                            <span
                              className={styles.durationValue}
                              style={{
                                color:
                                  t.status === 'done'
                                    ? '#52c41a'
                                    : t.status === 'failed'
                                    ? '#ff4d4f'
                                    : '#1677ff',
                              }}
                            >
                              {formatElapsed(elapsed)}
                            </span>
                          </div>
                        )}

                        <div className={styles.taskFooter} onClick={(e) => e.stopPropagation()}>
                          <div className={styles.formatList}>
                            {t.source_type === 'audio' && (
                              <Tooltip title="下载语音转写文本">
                                <Button
                                  type="text"
                                  size="small"
                                  icon={<SoundOutlined />}
                                  disabled={!t.has_transcript}
                                  className={styles.formatBtn}
                                  onClick={() => downloadMeetingTask(t.id, 'transcript')}
                                >
                                  转写
                                </Button>
                              </Tooltip>
                            )}
                            <Tooltip title="下载 Markdown">
                              <Button
                                type="text"
                                size="small"
                                icon={<FileMarkdownOutlined />}
                                disabled={!t.has_md}
                                className={styles.formatBtn}
                                onClick={() => downloadMeetingTask(t.id, 'md')}
                              >
                                md
                              </Button>
                            </Tooltip>
                            <Tooltip title="下载 PDF">
                              <Button
                                type="text"
                                size="small"
                                icon={<FilePdfOutlined />}
                                disabled={!t.has_pdf}
                                className={styles.formatBtn}
                                onClick={() => downloadMeetingTask(t.id, 'pdf')}
                              >
                                pdf
                              </Button>
                            </Tooltip>
                            <Tooltip title="下载 Word">
                              <Button
                                type="text"
                                size="small"
                                icon={<FileWordOutlined />}
                                disabled={!t.has_docx}
                                className={styles.formatBtn}
                                onClick={() => downloadMeetingTask(t.id, 'docx')}
                              >
                                word
                              </Button>
                            </Tooltip>
                          </div>
                          <Popconfirm
                            title="确认删除该任务及产物？"
                            onConfirm={() => onDelete(t.id)}
                            okText="删除"
                            cancelText="取消"
                            okButtonProps={{ danger: true }}
                          >
                            <Button type="text" size="small" icon={<DeleteOutlined />} className={styles.deleteBtn} />
                          </Popconfirm>
                        </div>
                      </div>
                    </Col>
                  )
                })}
              </Row>
            )}

            {total > 0 && (
              <div className={styles.pagination}>
                <Pagination
                  current={page}
                  pageSize={PAGE_SIZE}
                  total={total}
                  showSizeChanger={false}
                  showTotal={(t) => `共 ${t} 条`}
                  onChange={(p) => setPage(p)}
                />
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}

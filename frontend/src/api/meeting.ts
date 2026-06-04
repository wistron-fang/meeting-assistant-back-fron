

import { request } from './request'

export interface MeetingTask {
  id: number
  title: string | null
  source_type: 'paste' | 'upload' | 'audio' | null
  status: 'pending' | 'running' | 'done' | 'failed'
  progress: number
  stage: string | null
  has_md: boolean
  has_pdf: boolean
  has_docx: boolean
  has_transcript: boolean
  error: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export interface MeetingStats {
  pending: number
  running: number
  done: number
  failed: number
}

export interface MeetingTaskListResp {
  items: MeetingTask[]
  total: number
  stats: MeetingStats
}

// 创建任务（粘贴 / 上传 txt / 上传录音,三选一）
export const createMeetingTasks = async (data: {
  title?: string
  text?: string
  files?: File[]
  audio?: File
}): Promise<MeetingTask[]> => {
  const form = new FormData()
  if (data.title) form.append('title', data.title)
  if (data.text) form.append('text', data.text)
  ;(data.files || []).forEach((f) => form.append('files', f))
  if (data.audio) form.append('audio', data.audio)
  const res = await request.post<MeetingTask[]>('/meeting/tasks', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    loading: false,
    timeout: 600000, // 录音文件可能较大,放宽到 10 分钟
  })
  return res.data
}

// 列表
export const listMeetingTasks = async (params?: {
  status?: string
  title?: string
  page?: number
  page_size?: number
}): Promise<MeetingTaskListResp> => {
  const res = await request.get<MeetingTaskListResp>('/meeting/tasks', {
    params,
    loading: false,
  })
  return res.data
}

// 单条详情（轮询用）
export const getMeetingTask = async (id: number): Promise<MeetingTask> => {
  const res = await request.get<MeetingTask>(`/meeting/tasks/${id}`, {
    loading: false,
  })
  return res.data
}

// 删除任务
export const deleteMeetingTask = async (id: number): Promise<void> => {
  await request.delete(`/meeting/tasks/${id}`, { loading: false })
}

// 单个下载（触发浏览器下载）
export const downloadMeetingTask = async (
  id: number,
  format: 'md' | 'pdf' | 'docx' | 'transcript',
) => {
  const res = await request.get(`/meeting/tasks/${id}/download`, {
    params: { format },
    responseType: 'blob',
    loading: false,
  })
  const ext = format === 'transcript' ? 'txt' : format
  triggerBlobDownload(res.data as Blob, suggestFilename(res), `task_${id}.${ext}`)
}

// 批量打包下载
export const batchZipDownload = async (
  ids: number[],
  format: 'md' | 'pdf' | 'docx' | 'all' = 'all',
) => {
  const res = await request.post('/meeting/tasks/batch-zip', { ids, format }, {
    responseType: 'blob',
    loading: false,
  })
  triggerBlobDownload(res.data as Blob, suggestFilename(res), `meeting_minutes.zip`)
}

function suggestFilename(res: any): string | null {
  const cd: string | undefined = res?.headers?.['content-disposition']
  if (!cd) return null
  // 优先解析 RFC 5987 形式（含中文等非 ASCII 字符时 Starlette 只设这一份）:
  //   Content-Disposition: attachment; filename*=UTF-8''%E4%BC%9A%E8%AE%AE.md
  const mExt = /filename\*\s*=\s*([^']+)''([^;]+)/i.exec(cd)
  if (mExt) {
    try {
      return decodeURIComponent(mExt[2].trim())
    } catch {
      // 落到下面的普通形式
    }
  }
  // 退化解析普通形式 filename="xxx" 或 filename=xxx
  const m = /filename\s*=\s*"?([^";]+)"?/i.exec(cd)
  return m ? decodeURIComponent(m[1].trim()) : null
}

function triggerBlobDownload(blob: Blob, suggested: string | null, fallback: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = suggested || fallback
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
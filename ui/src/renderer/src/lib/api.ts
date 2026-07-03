import type {
  CaptionLine,
  CaptionStyle,
  Clip,
  FilterName,
  Job,
  ModelsInfo,
  RenderOpts,
  Settings,
  SystemStats,
  Video
} from './types'

export const API_BASE = 'http://127.0.0.1:8765'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${path}: ${body.slice(0, 200)}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<{ ok: boolean }>('/health'),
  systemStats: () => request<SystemStats>('/system/stats'),

  createJob: (
    url: string,
    opts?: {
      force?: boolean
      captionStyle?: CaptionStyle
      captions?: boolean
      longClips?: boolean
      filter?: FilterName
    }
  ) =>
    request<{ job_id: number | null; already_processed?: boolean; video_id?: string }>('/jobs', {
      method: 'POST',
      body: JSON.stringify({
        url,
        force: opts?.force ?? false,
        caption_style: opts?.captionStyle ?? null,
        captions: opts?.captions ?? null,
        long_clips: opts?.longClips ?? null,
        filter: opts?.filter && opts.filter !== 'none' ? opts.filter : null
      })
    }),
  jobs: () => request<Job[]>('/jobs'),

  videos: () => request<Video[]>('/videos'),
  clips: (videoId: string) => request<Clip[]>(`/videos/${videoId}/clips`),
  patchClip: (id: number, patch: { title?: string; description?: string; hashtags?: string[] }) =>
    request<Clip>(`/clips/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  captions: (id: number) => request<{ lines: CaptionLine[] }>(`/clips/${id}/captions`),
  saveCaptions: (id: number, lines: CaptionLine[]) =>
    request<{ job_id: number }>(`/clips/${id}/captions`, {
      method: 'PUT',
      body: JSON.stringify({ lines })
    }),
  aiEdit: (id: number, message: string) =>
    request<{ reply: string; job_id: number | null }>(`/clips/${id}/ai-edit`, {
      method: 'POST',
      body: JSON.stringify({ message })
    }),
  rerenderClip: (id: number, range?: { start?: number; end?: number }, renderOpts?: RenderOpts) =>
    request<{ job_id: number }>(`/clips/${id}/render`, {
      method: 'POST',
      body: JSON.stringify({ ...(range ?? {}), render_opts: renderOpts ?? null })
    }),
  exportClip: (id: number, folder: string) =>
    request<{ exported: string[] }>(`/clips/${id}/export`, {
      method: 'POST',
      body: JSON.stringify({ folder })
    }),
  exportBatch: (clipIds: number[], folder: string) =>
    request<{ exported: string[] }>('/export/batch', {
      method: 'POST',
      body: JSON.stringify({ clip_ids: clipIds, folder })
    }),
  mediaUrl: (clipId: number) => `${API_BASE}/media/${clipId}`,

  models: () => request<ModelsInfo>('/models'),
  activateModel: (tag: string) =>
    request<{ active: string }>('/models/activate', { method: 'POST', body: JSON.stringify({ tag }) }),
  pullModel: (tag: string) =>
    request<{ started: string }>('/models/pull', { method: 'POST', body: JSON.stringify({ tag }) }),
  deleteModel: (tag: string) => request<{ deleted: string }>(`/models/${tag}`, { method: 'DELETE' }),

  settings: () => request<Settings>('/settings'),
  patchSettings: (patch: Partial<Settings>) =>
    request<{ ok: boolean }>('/settings', { method: 'PATCH', body: JSON.stringify(patch) })
}

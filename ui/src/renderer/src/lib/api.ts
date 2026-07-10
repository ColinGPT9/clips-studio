import type {
  CaptionLine,
  CaptionStyle,
  Clip,
  CreatorDetail,
  CreatorSuggestion,
  CreatorSummary,
  FilterName,
  Job,
  ModelsInfo,
  RenderOpts,
  Settings,
  SystemStats,
  Video,
  Word
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
      longform?: { mode: string } | null
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
        filter: opts?.filter && opts.filter !== 'none' ? opts.filter : null,
        longform: opts?.longform ?? null
      })
    }),
  addLocalVideo: (opts: {
    path: string
    title?: string
    channel?: string
    platform?: string
    captions?: boolean
    captionStyle?: CaptionStyle
    longClips?: boolean
  }) =>
    request<{ job_id: number; video_id: string }>('/videos/local', {
      method: 'POST',
      body: JSON.stringify({
        path: opts.path,
        title: opts.title ?? '',
        channel: opts.channel ?? '',
        platform: opts.platform ?? 'youtube',
        captions: opts.captions ?? null,
        caption_style: opts.captionStyle ?? null,
        long_clips: opts.longClips ?? null
      })
    }),
  jobs: () => request<Job[]>('/jobs'),
  cancelProcessing: (videoId: string) =>
    request<{ cancelling: string }>('/cancel', {
      method: 'POST',
      body: JSON.stringify({ video_id: videoId })
    }),
  deleteVideo: (videoId: string) =>
    request<{ deleted: string }>(`/videos/${videoId}`, { method: 'DELETE' }),

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
  clipWords: (clipId: number) => request<{ words: Word[] }>(`/clips/${clipId}/words`),
  previewClip: (
    clipId: number,
    edit: unknown,
    captionLines?: unknown,
    crop?: string | null,
    captionStyle?: CaptionStyle | null
  ) =>
    request<{ url: string }>(`/clips/${clipId}/preview`, {
      method: 'POST',
      body: JSON.stringify({
        edit,
        caption_lines: captionLines ?? null,
        crop: crop ?? null,
        caption_style: captionStyle ?? null
      })
    }),

  models: () => request<ModelsInfo>('/models'),
  activateModel: (tag: string) =>
    request<{ active: string }>('/models/activate', { method: 'POST', body: JSON.stringify({ tag }) }),
  pullModel: (tag: string) =>
    request<{ started: string }>('/models/pull', { method: 'POST', body: JSON.stringify({ tag }) }),
  deleteModel: (tag: string) => request<{ deleted: string }>(`/models/${tag}`, { method: 'DELETE' }),

  creators: () =>
    request<{ creators: CreatorSummary[]; suggestions: CreatorSuggestion[] }>('/creators'),
  creatorDetail: (id: number) => request<CreatorDetail>(`/creators/${id}`),
  mergeCreators: (fromId: number, intoId: number) =>
    request<{ merged: number; into: number }>('/creators/merge', {
      method: 'POST',
      body: JSON.stringify({ from_id: fromId, into_id: intoId })
    }),
  splitCreatorAccount: (accountId: number) =>
    request<{ new_creator_id: number }>(`/creators/split/${accountId}`, { method: 'POST' }),
  addCreatorAccount: (creatorId: number, platform: string, channel: string) =>
    request<{ account_id: number }>(`/creators/${creatorId}/accounts`, {
      method: 'POST',
      body: JSON.stringify({ platform, channel })
    }),
  deleteCreatorKnowledge: (creatorId: number, knowledgeId: number) =>
    request<{ deleted: number }>(`/creators/${creatorId}/knowledge/${knowledgeId}`, {
      method: 'DELETE'
    }),
  setCreatorLearning: (creatorId: number, enabled: boolean) =>
    request<{ learning_enabled: boolean }>(`/creators/${creatorId}/learning`, {
      method: 'POST',
      body: JSON.stringify({ enabled })
    }),
  wipeCreatorMemory: (creatorId: number) =>
    request<{ wiped: number }>(`/creators/${creatorId}/memory`, { method: 'DELETE' }),

  settings: () => request<Settings>('/settings'),
  patchSettings: (patch: Partial<Settings>) =>
    request<{ ok: boolean }>('/settings', { method: 'PATCH', body: JSON.stringify(patch) })
}

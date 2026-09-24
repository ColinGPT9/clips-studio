import type {
  AutomationActivity,
  AutomationStatus,
  BrandingProfile,
  CaptionLine,
  CaptionStyle,
  Clip,
  CreatorDetail,
  CreatorSuggestion,
  CreatorSummary,
  FilterName,
  Job,
  JobOptions,
  ModelsInfo,
  Preflight,
  PublishPlanItem,
  QueueSnapshot,
  RenderOpts,
  Settings,
  SystemStats,
  Translation,
  Video,
  Watch,
  WatchItem,
  WatchPlatform,
  WatchPublish,
  WatermarkConfig,
  Word
} from './types'
import type {
  Playlist,
  PublishJobRow,
  PublishRecord,
  VideoCategory,
  YouTubeChannel,
  YouTubeSettings,
  YouTubeStatus
} from './youtube'
import type { Capabilities, FanOut, PlatformRow, UploadPostStatus } from './uploadpost'

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
  /** Can this install actually make a clip? FFmpeg, Ollama, model, GPU, disk. */
  preflight: () => request<Preflight>('/health/preflight'),
  systemStats: () => request<SystemStats>('/system/stats'),

  feedbackDiagnostics: (videoId?: string) =>
    request<Record<string, unknown>>(
      `/feedback/diagnostics${videoId ? `?video_id=${encodeURIComponent(videoId)}` : ''}`
    ),
  feedbackSubmit: (payload: {
    kind: 'bug' | 'feature' | 'improvement'
    title: string
    answers: Record<string, string>
    areas: string[]
    severity: string
    include_diagnostics: boolean
    images: { path: string }[]
  }) =>
    request<{ ok: boolean; url?: string; markdown: string; error?: string }>('/feedback/submit', {
      method: 'POST',
      body: JSON.stringify(payload)
    }),

  createJob: (
    url: string,
    opts?: {
      force?: boolean
      captionStyle?: CaptionStyle
      captions?: boolean
      longClips?: boolean
      filter?: FilterName
      longform?: { mode: string } | null
      watermarkProfileId?: number | null
      podcast?: boolean
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
        longform: opts?.longform ?? null,
        watermark_profile_id: opts?.watermarkProfileId ?? null,
        podcast: opts?.podcast ?? null
      })
    }),
  /** Import a file from this computer and queue it. Takes the same options as
   *  a pasted link, so an upload can be set up exactly like a download. */
  addLocalVideo: (opts: { path: string; title?: string; channel?: string; platform?: string } & JobOptions) =>
    request<{ job_id: number; video_id: string }>('/videos/local', {
      method: 'POST',
      body: JSON.stringify({
        path: opts.path,
        title: opts.title ?? '',
        channel: opts.channel ?? '',
        platform: opts.platform ?? 'youtube',
        captions: opts.captions ?? null,
        caption_style: opts.caption_style ?? null,
        long_clips: opts.long_clips ?? null,
        podcast: opts.podcast ?? null,
        longform: opts.longform ?? null,
        watermark_profile_id: opts.watermark_profile_id ?? null,
        filter: opts.filter ?? null,
        min_score: opts.min_score ?? null,
        max_clips: opts.max_clips ?? null,
        force: opts.force ?? false
      })
    }),
  jobs: () => request<Job[]>('/jobs'),

  // ---- processing queue ----
  queue: () => request<QueueSnapshot>('/queue'),
  pauseQueue: () => request<{ paused: boolean }>('/queue/pause', { method: 'POST' }),
  resumeQueue: () => request<{ paused: boolean }>('/queue/resume', { method: 'POST' }),
  /** delta -1/+1 steps one place; `to` jumps to either end. */
  moveJob: (jobId: number, move: { delta?: number; to?: 'top' | 'bottom' }) =>
    request<{ moved: boolean }>(`/jobs/${jobId}/move`, {
      method: 'POST',
      body: JSON.stringify({ delta: move.delta ?? 0, to: move.to ?? null })
    }),
  retryJob: (jobId: number) =>
    request<{ job_id: number }>(`/jobs/${jobId}/retry`, { method: 'POST' }),
  deleteJob: (jobId: number) =>
    request<{ deleted: number }>(`/jobs/${jobId}`, { method: 'DELETE' }),
  clearQueue: (what: 'completed' | 'failed' | 'queued' | 'all') =>
    request<{ deleted: number }>('/queue/clear', {
      method: 'POST',
      body: JSON.stringify({ what })
    }),
  /** Change ONE queued video's settings. Only fields present are changed;
   *  `clear` puts an option back to the app-wide default. */
  patchJob: (jobId: number, patch: Partial<JobOptions> & { clear?: string[] }) =>
    request<{ ok: boolean }>(`/jobs/${jobId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch)
    }),
  /** Queue a staged list. Each item carries its OWN settings — that is the
   *  point of staging, so there is no batch-wide option set here. */
  createJobsBatch: (items: ({ url: string } & JobOptions)[]) =>
    request<{
      created: { url: string; job_id: number; video_id: string }[]
      skipped: { url: string; reason: string; detail?: string; video_id?: string }[]
    }>('/jobs/batch', {
      method: 'POST',
      body: JSON.stringify({ items })
    }),
  jobLog: (jobId: number, tail = 300) =>
    request<{ log: string; missing: boolean }>(`/jobs/${jobId}/log?tail=${tail}`),

  cancelProcessing: (videoId: string) =>
    request<{ cancelling: string }>('/cancel', {
      method: 'POST',
      body: JSON.stringify({ video_id: videoId })
    }),
  storage: () =>
    request<{
      reclaimable: Record<string, { files: number; bytes: number }>
      reclaimable_bytes: number
      sources: { files: number; bytes: number }
    }>('/storage'),
  storageVideos: () =>
    request<{
      videos: {
        video_id: string
        title: string
        channel: string
        clips: number
        source_bytes: number
        transcript_bytes: number
        clip_bytes: number
        total_bytes: number
      }[]
      total_bytes: number
    }>('/storage/videos'),

  storageCleanup: () =>
    request<{ files_removed: number; bytes_freed: number }>('/storage/cleanup', {
      method: 'POST'
    }),
  deleteVideo: (videoId: string) =>
    request<{ deleted: string }>(`/videos/${videoId}`, { method: 'DELETE' }),
  deleteClip: (clipId: number) =>
    request<{ deleted: number; bytes_freed: number }>(`/clips/${clipId}`, { method: 'DELETE' }),

  videos: () => request<Video[]>('/videos'),
  clips: (videoId: string) => request<Clip[]>(`/videos/${videoId}/clips`),
  patchClip: (
    id: number,
    patch: {
      title?: string
      description?: string
      hashtags?: string[]
      exported?: boolean
    }
  ) =>
    request<Clip>(`/clips/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  captions: (id: number) => request<{ lines: CaptionLine[] }>(`/clips/${id}/captions`),
  saveCaptions: (id: number, lines: CaptionLine[]) =>
    request<{ job_id: number }>(`/clips/${id}/captions`, {
      method: 'PUT',
      body: JSON.stringify({ lines })
    }),
  tightenClip: (id: number, opts?: { silence?: boolean; fillers?: boolean }) =>
    request<{
      keep: [number, number][]
      removed_seconds: number
      cuts: number
      new_duration: number
    }>(`/clips/${id}/tighten`, {
      method: 'POST',
      body: JSON.stringify({ silence: opts?.silence ?? true, fillers: opts?.fillers ?? true })
    }),
  /** Whether the assistant can run, and which model it would use. The app's
   *  default scoring model cannot call tools, so this is a real question. */
  agentStatus: () =>
    request<{ ready: boolean; model: string; configured: string; reason: string }>(
      '/agent/status'
    ),
  /** One exchange with the assistant. `steps` is what it actually did, `plan`
   *  is a proposed batch of uploads that NOTHING has acted on yet. */
  agentChat: (
    message: string,
    history: { role: string; content: string }[],
    /** The Generate bar's current toggles, so a job the assistant queues
     *  behaves like one started by hand. */
    defaults?: JobOptions
  ) =>
    request<{
      reply: string
      model: string
      steps: { tool: string; arguments: Record<string, unknown>; result: string }[]
      plan: { items: PublishPlanItem[]; warnings: string[] } | null
    }>('/agent/chat', { method: 'POST', body: JSON.stringify({ message, history, defaults }) }),
  /** Carry out a plan the person has agreed to. Only a click reaches this. */
  executePublishPlan: (items: PublishPlanItem[]) =>
    request<{
      started: { clip_id: number; publish_job_id: number }[]
      skipped: { clip_id: number; reason: string }[]
    }>('/publish/plan/execute', { method: 'POST', body: JSON.stringify({ items }) }),
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
    captionStyle?: CaptionStyle | null,
    watermark?: WatermarkConfig | Record<string, never>
  ) =>
    request<{ url: string }>(`/clips/${clipId}/preview`, {
      method: 'POST',
      body: JSON.stringify({
        edit,
        caption_lines: captionLines ?? null,
        crop: crop ?? null,
        caption_style: captionStyle ?? null,
        watermark: watermark === undefined ? null : watermark
      })
    }),

  languages: () =>
    request<{
      languages: {
        code: string
        name: string
        native: string
        can_dub: boolean
        caption_font: string | null
      }[]
      dubbing_available: boolean
    }>('/languages'),
  translateClips: (body: {
    clip_ids: number[]
    languages: string[]
    stage?: 'translate' | 'export'
    folder?: string
    include_video?: boolean
    burn?: boolean
    dub?: boolean
    subtitles?: boolean
    post_text?: boolean
    voices?: Record<string, string>
    style?: CaptionStyle
  }) =>
    request<{ job_id: number; languages: string[]; clips: number }>('/translate', {
      method: 'POST',
      body: JSON.stringify(body)
    }),
  translations: (clipId: number) =>
    request<{ source: CaptionLine[]; translations: Translation[] }>(
      `/clips/${clipId}/translations`
    ),
  saveTranslation: (clipId: number, language: string, lines: CaptionLine[]) =>
    request<{ saved: string; lines: number }>(`/clips/${clipId}/translations/${language}`, {
      method: 'PUT',
      body: JSON.stringify({ lines })
    }),
  glossary: (clipId: number) =>
    request<{ protected: string[]; ignored: string[]; mine: string[] }>(
      `/clips/${clipId}/glossary`
    ),
  ruleTerm: (clipId: number, term: string, rule: 'protect' | 'ignore' | 'auto') =>
    request<{ term: string; rule: string }>(`/clips/${clipId}/glossary`, {
      method: 'POST',
      body: JSON.stringify({ term, rule })
    }),
  discardTranslation: (clipId: number, language: string) =>
    request<{ discarded: string }>(`/clips/${clipId}/translations/${language}`, {
      method: 'DELETE'
    }),

  voicesFor: (language: string) =>
    request<{
      voices: { id: string; name: string; country: string; quality: string }[]
      default: string | null
    }>(`/voices?language=${encodeURIComponent(language)}`),
  // A plain URL, not a blob: the app's CSP allows media from the API only.
  voicePreviewUrl: (language: string, voice?: string) =>
    `${API_BASE}/voices/preview?language=${encodeURIComponent(language)}` +
    (voice ? `&voice=${encodeURIComponent(voice)}` : ''),

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
  /** Forget the unconfirmed catchphrases and dormant facts in one go. */
  clearUnusedKnowledge: (creatorId: number) =>
    request<{ deleted: number }>(`/creators/${creatorId}/knowledge`, { method: 'DELETE' }),
  setCreatorLearning: (creatorId: number, enabled: boolean) =>
    request<{ learning_enabled: boolean }>(`/creators/${creatorId}/learning`, {
      method: 'POST',
      body: JSON.stringify({ enabled })
    }),
  setCreatorBranding: (creatorId: number, brandingId: number | null) =>
    request<{ default_branding_id: number | null }>(`/creators/${creatorId}/branding`, {
      method: 'POST',
      body: JSON.stringify({ branding_id: brandingId })
    }),
  deleteCreator: (creatorId: number) =>
    request<{ deleted: number; name: string; videos_unlinked: number }>(
      `/creators/${creatorId}`,
      { method: 'DELETE' }
    ),
  wipeCreatorMemory: (creatorId: number) =>
    request<{ wiped: number }>(`/creators/${creatorId}/memory`, { method: 'DELETE' }),

  branding: () => request<BrandingProfile[]>('/branding'),
  createBranding: (name: string, config: WatermarkConfig) =>
    request<{ id: number }>('/branding', {
      method: 'POST',
      body: JSON.stringify({ name, config })
    }),
  updateBranding: (id: number, name: string, config: WatermarkConfig) =>
    request<{ id: number }>(`/branding/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name, config })
    }),
  deleteBranding: (id: number) =>
    request<{ deleted: number }>(`/branding/${id}`, { method: 'DELETE' }),
  uploadBrandingAsset: (path: string) =>
    request<{ asset: string }>('/branding/asset', {
      method: 'POST',
      body: JSON.stringify({ path })
    }),
  brandingAssetUrl: (name: string) => `${API_BASE}/branding/asset/${name}`,

  settings: () => request<Settings>('/settings'),
  patchSettings: (patch: Partial<Settings>) =>
    request<{ ok: boolean }>('/settings', { method: 'PATCH', body: JSON.stringify(patch) }),

  // ---- YouTube publishing ----
  // Every route here 404s while the feature is disabled, which is why callers
  // check youtubeStatus() first rather than treating an error as a fault.

  youtubeStatus: () => request<YouTubeStatus>('/youtube/status'),
  patchYoutubeSettings: (patch: Partial<YouTubeSettings>) =>
    request<{ settings: YouTubeSettings; status: YouTubeStatus }>('/youtube/settings', {
      method: 'PATCH',
      body: JSON.stringify(patch)
    }),
  /** Bring your own Google Cloud project. The secret goes straight into the
   *  encrypted store and is never returned by any route. */
  putYoutubeCredentials: (clientId: string, clientSecret: string) =>
    request<{ ok: boolean; client_id_tail: string }>('/youtube/credentials', {
      method: 'PUT',
      body: JSON.stringify({ client_id: clientId, client_secret: clientSecret })
    }),
  deleteYoutubeCredentials: () =>
    request<{ cleared: boolean }>('/youtube/credentials', { method: 'DELETE' }),
  /** Starts the browser consent on a background thread; poll pollYoutubeConnect. */
  /** Each consent ADDS a channel. To publish to a second channel, run this
   *  again and pick the other one on Google's channel chooser. */
  startYoutubeConnect: (playlists: boolean, add = false) =>
    request<{ state: string }>('/youtube/connect', {
      method: 'POST',
      body: JSON.stringify({ playlists, add })
    }),
  pollYoutubeConnect: () =>
    request<{
      state: string
      channel?: YouTubeChannel | null
      error?: string
      status?: YouTubeStatus
    }>('/youtube/connect'),
  /** Omit channelId to disconnect every channel. */
  youtubeDisconnect: (channelId?: string) =>
    request<{ disconnected: boolean; status?: YouTubeStatus }>('/youtube/disconnect', {
      method: 'POST',
      body: JSON.stringify({ channel_id: channelId ?? null })
    }),
  setDefaultYoutubeAccount: (channelId: string) =>
    request<{ status: YouTubeStatus }>('/youtube/default-account', {
      method: 'PATCH',
      body: JSON.stringify({ channel_id: channelId })
    }),

  youtubeCategories: (region: string) =>
    request<{ categories: VideoCategory[] }>(`/youtube/categories?region=${region}`),
  youtubePlaylists: () => request<{ playlists: Playlist[] }>('/youtube/playlists'),
  youtubeUploads: (limit = 20) =>
    request<{ uploads: PublishRecord[] }>(`/youtube/uploads?limit=${limit}`),

  clipPublishStatus: (clipId: number) =>
    request<{ upload: PublishRecord | null; job: PublishJobRow | null }>(
      `/clips/${clipId}/publish`
    ),
  publishClip: (clipId: number, body: Record<string, unknown>) =>
    request<{ publish_job_id: number; render_job_id: number | null }>(
      `/clips/${clipId}/publish`,
      { method: 'POST', body: JSON.stringify(body) }
    ),
  cancelPublish: (jobId: number) =>
    request<{ cancelled?: boolean; cancelling?: boolean }>(`/publish/${jobId}/cancel`, {
      method: 'POST'
    }),

  /** A frame from the clip, for the thumbnail picker. Needs img-src in the CSP. */
  clipFrameUrl: (clipId: number, t: number) =>
    `${API_BASE}/clips/${clipId}/frame?t=${t.toFixed(3)}`,
  /** `image` is base64 data, not a path: the backend never receives a filename
   *  for the thumbnail, so it never has to trust one. */
  chooseThumbnail: (clipId: number, body: { image?: string; t?: number; generated?: number }) =>
    request<{ thumbnail: string }>(`/clips/${clipId}/thumbnail`, {
      method: 'POST',
      body: JSON.stringify(body)
    }),
  /** Make thumbnail candidates from the clip itself, on this machine: a frame
   *  with a face in it, cropped to 16:9, with the hook burned across it.
   *  Returns how many were made; zero is a normal answer for a clip with no
   *  readable frames, and the fixed suggestions are still there. */
  generateThumbnails: (clipId: number, count = 3) =>
    request<{ generated: number }>(`/clips/${clipId}/thumbnail/generate?count=${count}`, {
      method: 'POST'
    }),
  generatedThumbnailUrl: (clipId: number, index: number) =>
    `${API_BASE}/clips/${clipId}/thumbnail/generated/${index}`,

  // ---- Upload-Post: publishing to several platforms at once ----
  // Same rule as the YouTube routes: everything except /status 404s while the
  // feature is off, so callers check uploadPostStatus() first rather than
  // treating an error as a fault. The API key is never returned by any of
  // these — the status carries has_key and a four-character tail instead.

  uploadPostStatus: () => request<UploadPostStatus>('/uploadpost/status'),
  patchUploadPostSettings: (patch: Record<string, unknown>) =>
    request<UploadPostStatus>('/uploadpost/settings', {
      method: 'PATCH',
      body: JSON.stringify(patch)
    }),
  /** Stores the key, then proves it works before reporting success. A key
   *  that fails validation is discarded rather than left looking connected. */
  putUploadPostKey: (apiKey: string) =>
    request<UploadPostStatus & { plan: string; email: string }>('/uploadpost/key', {
      method: 'PUT',
      body: JSON.stringify({ api_key: apiKey })
    }),
  deleteUploadPostKey: () =>
    request<UploadPostStatus & { removed: boolean }>('/uploadpost/key', { method: 'DELETE' }),
  /** A hosted Upload-Post page for linking social accounts, good for 48 hours.
   *  Opened in the real browser — Clips Kitty never sees a social password. */
  uploadPostConnect: (username?: string) =>
    request<{ url: string; expires_hours: number; profile: string }>('/uploadpost/connect', {
      method: 'POST',
      body: JSON.stringify({ username: username ?? '' })
    }),
  uploadPostProfiles: () =>
    request<{ profiles: { username: string }[] }>('/uploadpost/profiles'),
  /** Which platforms are linked. Polled after sending someone to the
   *  connection page, so setup finishes on its own. */
  uploadPostConnections: () =>
    request<{ profile: string; connected: string[] }>('/uploadpost/connections'),
  /** One upload, several platforms. Returns straight away with a request_id;
   *  poll refreshUploadPost until `done`. */
  uploadPostPublish: (
    clipId: number,
    body: {
      platforms: string[]
      title: string
      description?: string
      tags?: string[]
      first_comment?: string
      thumbnail?: boolean
      overrides?: Record<string, Record<string, string>>
      scheduled_date?: string
      timezone?: string
      add_to_queue?: boolean
    }
  ) =>
    request<FanOut>(`/uploadpost/clips/${clipId}/publish`, {
      method: 'POST',
      body: JSON.stringify(body)
    }),
  refreshUploadPost: (requestId: string) =>
    request<FanOut>(`/uploadpost/refresh/${encodeURIComponent(requestId)}`, { method: 'POST' }),
  /** Re-runs only the platforms that failed, reusing the media already
   *  uploaded — never a second upload, which would duplicate the successes. */
  retryUploadPost: (requestId: string) =>
    request<FanOut>(`/uploadpost/retry/${encodeURIComponent(requestId)}`, { method: 'POST' }),
  uploadPostCapabilities: () =>
    request<{ platforms: Capabilities }>('/uploadpost/capabilities'),
  /** Several clips, each uploaded once and fanned out. `every_hours` spaces
   *  them through Upload-Post's scheduler rather than firing a burst. */
  uploadPostBatch: (body: {
    clip_ids: number[]
    platforms: string[]
    every_hours?: number
    start_at?: string
    timezone?: string
    add_to_queue?: boolean
  }) =>
    request<{
      started: { clip_id: number; request_id: string }[]
      skipped: { clip_id: number; reason: string }[]
    }>('/uploadpost/batch', { method: 'POST', body: JSON.stringify(body) }),
  clipUploadPostRows: (clipId: number) =>
    request<{ platforms: PlatformRow[] }>(`/uploadpost/clips/${clipId}`),

  // ---- WoopSocial: the second provider ----
  // Same job as Upload-Post, different account. Routes mirror each other so
  // the panel can drive either without special-casing.

  woopSocialStatus: () => request<UploadPostStatus>('/woopsocial/status'),
  patchWoopSocialSettings: (patch: Record<string, unknown>) =>
    request<UploadPostStatus>('/woopsocial/settings', {
      method: 'PATCH',
      body: JSON.stringify(patch)
    }),
  putWoopSocialKey: (apiKey: string) =>
    request<UploadPostStatus & { projects: number }>('/woopsocial/key', {
      method: 'PUT',
      body: JSON.stringify({ api_key: apiKey })
    }),
  deleteWoopSocialKey: () =>
    request<UploadPostStatus & { removed: boolean }>('/woopsocial/key', { method: 'DELETE' }),
  woopSocialConnections: () => request<{ connected: string[] }>('/woopsocial/connections'),
  /** WoopSocial authorises one platform at a time, so the caller names it. */
  woopSocialConnect: (platform: string) =>
    request<{ url: string; platform: string }>('/woopsocial/connect', {
      method: 'POST',
      body: JSON.stringify({ platform })
    }),
  woopSocialPublish: (
    clipId: number,
    body: {
      platforms: string[]
      title: string
      description?: string
      tags?: string[]
      overrides?: Record<string, Record<string, string>>
      scheduled_date?: string
    }
  ) =>
    request<FanOut>(`/woopsocial/clips/${clipId}/publish`, {
      method: 'POST',
      body: JSON.stringify(body)
    }),
  refreshWoopSocial: (postId: string) =>
    request<FanOut>(`/woopsocial/refresh/${encodeURIComponent(postId)}`, { method: 'POST' }),
  /** Several clips through WoopSocial, spaced by their own scheduler so a
   *  run lasting days survives the app being closed. */
  woopSocialBatch: (body: {
    clip_ids: number[]
    platforms: string[]
    every_hours?: number
    start_at?: string
    /** A daily budget, which is the shape posting limits actually take:
     *  WoopSocial allows five YouTube posts a day. Sending 37 at once
     *  failed 32 of them. */
    per_day?: number
    gap_hours?: number
    /** Clip id to the platforms it should skip, for the handful a stricter
     *  platform should not get. */
    exclude?: Record<string, string[]>
    /** Added to every clip in the run, on top of each clip's own. */
    hashtags?: string[]
  }) =>
    request<{
      started: { clip_id: number; request_id: string; scheduled_for: string }[]
      skipped: { clip_id: number; reason: string }[]
    }>('/woopsocial/batch', { method: 'POST', body: JSON.stringify(body) }),

  /** What is due, soonest first. Only rows that were given a time. */
  woopSocialSchedule: () =>
    request<{
      posts: {
        clip_id: number
        platform: string
        state: string
        scheduled_for: string
        post_url: string
        error: string
        title: string
      }[]
    }>('/woopsocial/schedule'),

  /** Ask WoopSocial what became of everything still in the air. Without it
   *  rows sit at "processing" forever and a slow queue is indistinguishable
   *  from a batch that failed. */
  woopSocialRefresh: () =>
    request<{ checked: number; updated: number; still_waiting: number; failed: number }>(
      '/woopsocial/refresh',
      { method: 'POST' }
    ),

  // ---- watched channels ----
  // A channel posts, its video is queued once, and its clips are published,
  // asked about, or left alone. Off until switched on.

  automation: () => request<AutomationStatus>('/automation'),
  setAutomation: (patch: { enabled?: boolean; delete_sources?: boolean }) =>
    request<AutomationStatus>('/automation', {
      method: 'PATCH',
      body: JSON.stringify(patch)
    }),
  watches: () => request<Watch[]>('/automation/watches'),
  /** What the watcher is doing right now, and its last few steps. */
  automationActivity: () => request<AutomationActivity>('/automation/activity'),
  /** When the next posts would go out with these settings. Reserves nothing. */
  automationSlots: (perDay: number, gapHours: number, dayStart: string, count = 3) =>
    request<{ times: string[]; already_scheduled: number }>(
      `/automation/slots?per_day=${perDay}&gap_hours=${gapHours}&day_start=${encodeURIComponent(
        dayStart
      )}&count=${count}`
    ),
  /** `publish` sets what happens to its clips in the same step, so a
   *  hands-off channel is set up once. */
  addWatch: (platform: WatchPlatform, channel: string, publish?: Partial<WatchPublish>) =>
    request<Watch & { created: boolean }>('/automation/watches', {
      method: 'POST',
      body: JSON.stringify({ platform, channel, ...(publish ? { publish } : {}) })
    }),
  patchWatch: (
    id: number,
    patch: {
      enabled?: boolean
      preset?: string
      options?: Partial<JobOptions> & { clear?: string[] }
      publish?: WatchPublish
      backlog?: Watch['backlog']
      min_minutes?: number
    }
  ) =>
    request<Watch>(`/automation/watches/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch)
    }),
  deleteWatch: (id: number) =>
    request<{ deleted: boolean }>(`/automation/watches/${id}`, { method: 'DELETE' }),
  checkWatch: (id: number) =>
    request<{ checking: boolean }>(`/automation/watches/${id}/check`, { method: 'POST' }),
  watchItems: (watchId: number, limit = 100) =>
    request<WatchItem[]>(`/automation/items?watch_id=${watchId}&limit=${limit}`),
  /** Clip a video the watch set aside (back catalogue, missed, too short). */
  clipWatchItem: (id: number) =>
    request<WatchItem>(`/automation/items/${id}/queue`, { method: 'POST' }),
  /** Publish with the watch's settings. Also the retry: only what is not
   *  already sent, or on its way, goes out. */
  publishWatchItem: (id: number) =>
    request<WatchItem>(`/automation/items/${id}/publish`, { method: 'POST' }),
  skipWatchItem: (id: number) =>
    request<WatchItem>(`/automation/items/${id}/skip`, { method: 'POST' })
}

/** The server's own words from a failed request, for showing to a person.
 *  request() throws "400 /path: {"detail":"..."}"; this returns the detail. */
export function errorText(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  const body = raw.slice(raw.indexOf(': ') + 2)
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
  } catch {
    // not JSON: fall through to the raw text
  }
  return raw
}

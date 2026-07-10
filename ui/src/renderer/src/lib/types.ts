export interface Video {
  video_id: string
  channel_id: string
  channel_name: string
  title: string
  status: string
  created_at: string
  updated_at: string
  clip_count: number
  process_seconds: number
  creator_id: number | null
  creator_name: string | null
}

export interface SubScores {
  text?: number
  audio?: number
  visual?: number
  reaction?: number
  engagement?: number
  source?: string
  rerank_position?: number
}

export interface CaptionLine {
  start: number
  end: number
  text: string
}

export interface CaptionStyle {
  font?: string
  font_size?: number
  color?: string
  position?: 'bottom' | 'middle' | 'top'
  words_per_caption?: number
  uppercase?: boolean
}

export type FilterName =
  | 'none'
  | 'vibrant'
  | 'warm'
  | 'cool'
  | 'cinematic'
  | 'vintage'
  | 'bw'
  | 'fade'

export interface Adjust {
  brightness?: number // -0.5..0.5, 0 = unchanged
  saturation?: number // 0..3, 1 = unchanged
  contrast?: number // 0.5..2, 1 = unchanged
}

export interface MutedWord {
  start: number
  end: number
  word: string
}

/** Non-destructive manual edits (Shorts editor). All times are seconds
 *  relative to the clip start, on the clip's ORIGINAL timeline. */
export interface EditData {
  keep?: [number, number][]
  mutes: [number, number][]
  muted_words: MutedWord[]
  volume: number
  mute_all: boolean
  fade_in: number
  fade_out: number
  speed: number
  hook: { text: string; seconds: number } | null
  music: { path: string; volume: number; duck: boolean } | null
}

export interface Word {
  start: number
  end: number
  word: string
}

export interface RenderOpts {
  crop?: 'track' | 'center' | 'bias_left' | 'bias_right'
  captions?: boolean
  caption_style?: CaptionStyle
  caption_lines?: CaptionLine[]
  filter?: FilterName
  adjust?: Adjust
  edit?: EditData | null
  profile?: string // longform rendering profile (16:9); absent = vertical Short
}

export interface Clip {
  id: number
  video_id: string
  start_s: number
  end_s: number
  score: number
  hook: string
  path: string
  status: string
  scheduled_for: string | null
  title: string
  description: string
  hashtags: string[]
  scores: SubScores
  render_opts: RenderOpts
  created_at: string
}

export interface Job {
  id: number
  type: 'process' | 'render'
  payload: string
  status: 'queued' | 'running' | 'done' | 'failed'
  error: string
  created_at: string
  updated_at: string
}

export interface InstalledModel {
  name: string
  size_gb: number
}

export interface ModelsInfo {
  active: string
  installed: InstalledModel[]
  recommendations: { hardware: string; model: string; note: string }[]
}

export interface GpuStats {
  name: string
  vram_used: number
  vram_total: number
  gpu_percent: number
}

export interface SystemStats {
  cpu_percent: number
  ram_percent: number
  data_dir_bytes: number
  disk_free_bytes: number
  gpu: GpuStats | null
}

export interface Settings {
  model: string
  channel: string
  auto_upload: boolean
  privacy: string
}

/** Events arriving over the WebSocket. */
export interface StudioEvent {
  type: 'progress' | 'job' | 'model_pull'
  job_id?: number
  status?: string
  stage?: string
  message?: string
  video_id?: string
  title?: string
  clip?: number
  total?: number
  clips?: number
  current?: number
  fraction?: number
  duration?: number
  seconds?: number
  downloaded?: number
  error?: string
  tag?: string
  completed?: number
}

/** Creator intelligence */
export interface CreatorAccount {
  account_id: number
  platform: string
  username: string
}

export interface CreatorSummary {
  creator_id: number
  display_name: string
  aliases: string[]
  learning_enabled: number
  videos: number
  clips: number
  avg_score: number | null
  accounts: CreatorAccount[]
}

export interface CreatorSuggestion {
  creator_a: { id: number; name: string; platform: string }
  creator_b: { id: number; name: string; platform: string }
  reason: string
}

export interface CreatorKnowledgeItem {
  knowledge_id: number
  knowledge_type: string
  information: string
  confidence: string
  source_video: string | null
  created_at: string
}

export interface CreatorEvent {
  event_id: number
  event_name: string
  description: string
  status: string
  detected_date: string
}

export interface CreatorDetail {
  creator_id: number
  display_name: string
  aliases: string[]
  learning_enabled: number
  accounts: CreatorAccount[]
  knowledge: CreatorKnowledgeItem[]
  events: CreatorEvent[]
  feedback: Record<string, number>
  preferences: { weight_bias: Record<string, number>; preferred_duration: number | null; signals: number } | null
}

export interface Video {
  video_id: string
  channel_id: string
  channel_name: string
  title: string
  status: string
  created_at: string
  updated_at: string
  clip_count: number
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

export interface RenderOpts {
  crop?: 'track' | 'center' | 'bias_left' | 'bias_right'
  captions?: boolean
  caption_style?: CaptionStyle
  caption_lines?: CaptionLine[]
  filter?: FilterName
  adjust?: Adjust
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
  error?: string
  tag?: string
  completed?: number
}

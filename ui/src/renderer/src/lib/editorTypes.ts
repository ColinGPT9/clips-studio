/** The new editor's shapes, as the engine sends them (docs/editor-suite/api/
 *  step8-editor.md, with the document of step5-project-core.md). Times are
 *  whole microseconds (`*_us`), exact in JavaScript. */

export interface Fps {
  num: number
  den: number
}

export interface Canvas {
  width: number
  height: number
  fps: Fps
}

export interface Framing {
  /** An AI item's kept framing ("AI framing"). */
  crop?: string
  gaming?: unknown
  /** A manual framing. */
  fit?: 'fit' | 'fill'
  align?: 'center' | 'top' | 'bottom' | 'left' | 'right'
  /** "blur" or "#RRGGBB"; V1 only. */
  pad?: string
}

export interface Transform {
  x?: number
  y?: number
  scale?: number
  rotation?: number
  opacity?: number
}

export interface Item {
  id: string
  media_id: string | null
  source_in_us: number
  source_out_us: number
  start_us: number
  duration_us: number
  speed: number
  volume: number
  fades: { in_us: number; out_us: number }
  framing: Framing
  transform: Transform
  color: { filter?: string; adjust?: Record<string, number> }
  text: { content: string; role?: string; font?: string; size?: number; color?: string; position?: unknown } | null
  link: string | null
  extra: Record<string, unknown>
  role?: 'music' | 'sfx' | 'voice'
  loop?: boolean
  duck?: boolean
  loop_offset_us?: number
}

export interface Track {
  id: string
  kind: 'video' | 'text' | 'audio'
  name: string
  mute: boolean
  volume?: number
  solo?: boolean
  items: Item[]
  extra: Record<string, unknown>
}

export interface MediaEntry {
  id: string
  kind: 'video' | 'audio' | 'image'
  source: { type: 'video'; video_id: string } | { type: 'asset'; sha256: string } | { type: 'library'; id: string } | { type: 'file'; path: string }
  duration_us: number
  width: number
  height: number
  fps: Fps | null
  has_audio: boolean
  name: string
  extra: Record<string, unknown>
}

export interface ProjectDoc {
  schema: number
  name: string
  seq: number
  canvas: Canvas
  media: Record<string, MediaEntry>
  tracks: Track[]
  audio: { master_volume: number; normalize: boolean }
  captions: Record<string, unknown>
  branding: Record<string, unknown>
  export: { preset: unknown }
  context: Record<string, unknown>
  extra: Record<string, unknown>
}

/** GET /projects/{id}, and what creating or opening one answers. */
export interface Project {
  id: string
  name: string
  clip_id: number | null
  video_id: string | null
  revision: number
  can_undo: boolean
  can_redo: boolean
  read_only: boolean
  created_at: string
  updated_at: string
  document: ProjectDoc
  created?: boolean
  /** POST /clips/{id}/project (8-D6): the clip was changed in today's
   *  editor after this project was made or last exported, and the project
   *  has changes of its own, so the person is asked. */
  clip_changed_since?: boolean
  /** The project had no changes of its own and followed the clip by itself. */
  started_again?: boolean
}

/** GET /projects: one row, no document. */
export interface ProjectSummary {
  id: string
  name: string
  clip_id: number | null
  video_id: string | null
  revision: number
  created_at: string
  updated_at: string
  read_only: boolean
  damaged: boolean
  origin: 'clip' | 'manual'
  clip_deleted: boolean
  clip_file_is_export: boolean
  canvas: Canvas | null
  duration_us: number
  items: number
}

/** POST /projects/{id}/ops, undo and redo. */
export interface OpsAnswer {
  id: string
  revision: number
  changed: boolean
  document: ProjectDoc
  warnings?: string[]
  created?: { kind: 'item' | 'media' | 'track'; id: string }[]
  can_undo: boolean
  can_redo: boolean
  label?: string
}

export type Op = { op: string } & Record<string, unknown>

/** A product's state in the media info: proxy, peaks, thumbnails. */
export interface ProductState {
  state: 'ready' | 'queued' | 'running' | 'failed' | 'cancelled' | 'none'
  job_id?: number
  fraction?: number
  error?: string
}

/** GET /media/{id}/info. */
export interface MediaInfo {
  id: string
  name: string
  kind: 'video' | 'audio' | 'image'
  duration_us: number
  start_us: number
  width: number
  height: number
  display_width: number
  display_height: number
  rotation: number
  fps: Fps | null
  has_audio: boolean
  has_video: boolean
  needs_proxy: boolean
  proxy_reasons: string[]
  proxy: ProductState
  peaks: ProductState
  thumbs: ProductState
  urls: { source: string; proxy: string; peaks: string; thumbs: string }
  token: string
}

/** POST /media/import: the asset. */
export interface ImportedAsset {
  id: string
  sha256: string
  kind: 'video' | 'audio' | 'image'
  name: string
  ext: string
  size: number
  duration_us: number
  width: number
  height: number
  has_audio: boolean
}

/** A lane task (proxy, waveform, thumbnails, import, draft), as the
 *  `media_job` events and the draft and import routes give it. */
export interface MediaJob {
  job_id: number
  kind: string
  key?: string
  name?: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  cancelling?: boolean
  fraction?: number
  result?: unknown
  error?: string | null
  stage?: string
  project_id?: string
  revision?: number
}

/** POST /projects/{id}/draft. */
export interface DraftAnswer {
  job: MediaJob | null
  revision: number
  draft: { revision: number; bytes: number; url: string } | null
  urls: { still: string; draft: string }
}

/** The editor's own events on the shared socket. */
export interface EditorEvent {
  type: 'media_job' | 'clip_file_replaced' | 'export' | string
  job?: MediaJob
  clip_id?: number
  project_id?: string
}

/** The canvases a new project can take (POST /projects `canvas`). */
export const CANVAS_CHOICES = ['9:16', '16:9', '1:1', '4:5'] as const
export type CanvasChoice = (typeof CANVAS_CHOICES)[number]

/** GET /projects/{id}/exports: one export of the project. `url` (with the
 *  project's token) plays it, only for a finished export whose file is there. */
export interface ProjectExportRow {
  id: number
  project_id: string
  head: number
  clip_id: number | null
  path: string | null
  preset: unknown
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted' | 'replaced'
  error: string | null
  duration_us: number | null
  bytes: number | null
  job_id: number | null
  created_at: string
  updated_at: string
  file_exists: boolean
  url: string | null
}

/** A project's export on the editor lane (POST /projects/{id}/export and
 *  the `export` events). */
export interface ProjectExportJob {
  export_id: number
  kind: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  cancelling?: boolean
  fraction?: number
  smaller?: boolean
  attempt_fraction?: number | null
  result?: { path: string; file: string; folder?: string; bytes: number; duration_us?: number; clip_id?: number } | null
  error?: string | null
  project_id?: string
  revision?: number
  project_export_id?: number
  created_at?: string
}

/** POST /projects/{id}/export. */
export interface ProjectExportAnswer {
  export: ProjectExportJob
  project_export_id: number
  revision: number
}

/** GET /media/{id}/thumbs: the thumbnail index (step 6). */
export interface ThumbIndex {
  interval_us: number
  tile: { width: number; height: number }
  columns: number
  rows: number
  count: number
  times_us: number[]
  sheets: { index: number; url: string; first: number; count: number; width: number; height: number }[]
}

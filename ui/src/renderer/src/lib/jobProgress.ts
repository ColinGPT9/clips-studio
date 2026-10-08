import type { StudioEvent } from './types'

/** Folds WebSocket progress events into one overall job progress value
 *  (0..1) using stage weights, plus a label and timing for the ETA. */

export interface JobProgress {
  active: boolean
  title: string
  label: string
  fraction: number
  startedAt: number
  videoId: string
  /** Remote rendering only: where the current clip is ("on Gaming PC · rendering"). */
  remote?: string
}

const STAGES: Record<string, { base: number; weight: number; label: string }> = {
  download: { base: 0.0, weight: 0.15, label: 'Downloading video' },
  downloaded: { base: 0.15, weight: 0.0, label: 'Downloaded' },
  transcribe: { base: 0.15, weight: 0.25, label: 'Transcribing speech' },
  signals: { base: 0.4, weight: 0.05, label: 'Analyzing audio & visuals' },
  analyze: { base: 0.45, weight: 0.2, label: 'Finding the best moments' },
  // Ranking used to be inside `analyze`, and reported nothing. The bar
  // reached the end of analyze and sat at 70% through one LLM call per batch
  // of finalists, which on a CPU-only machine looks exactly like a crash.
  ranking: { base: 0.65, weight: 0.05, label: 'Ranking the best moments' },
  // A Marketplace plugin saying what happens in the moments (Rate & understand).
  understand: { base: 0.65, weight: 0.05, label: 'Understanding the moments' },
  reactions: { base: 0.7, weight: 0.08, label: 'Scoring on-screen reactions' },
  // A Marketplace plugin suggesting edits for the clips (Suggest edits).
  edit: { base: 0.7, weight: 0.08, label: 'Suggesting edits' },
  render: { base: 0.78, weight: 0.22, label: 'Rendering clips' }
}

/** A stage's label while a Marketplace plugin works in it, before its name. */
const PLUGIN_LABELS: Record<'ranking' | 'understand' | 'edit', string> = {
  ranking: 'Rating moments with',
  understand: 'Understanding moments with',
  edit: 'Suggesting edits with'
}

export const emptyProgress: JobProgress = {
  active: false,
  title: '',
  label: '',
  fraction: 0,
  startedAt: 0,
  videoId: ''
}

/** Survives page switches: whichever page is mounted keeps it updated. */
export const progressStore: { current: JobProgress } = { current: { ...emptyProgress } }

export function applyEvent(p: JobProgress, e: StudioEvent): JobProgress {
  if (e.type === 'job') {
    if (e.status === 'running')
      return { ...emptyProgress, active: true, label: 'Starting…', startedAt: Date.now() }
    if (e.status === 'done' || e.status === 'failed' || e.status === 'cancelled')
      return { ...emptyProgress }
  }
  if (e.type !== 'progress') return p
  // The Shorts pass of a both-formats job ending isn't the job ending: the
  // 16:9 pass carries on from the middle of the bar.
  if (e.stage === 'done') return e.span && e.span[1] < 1 ? p : { ...emptyProgress }
  // A YouTube upload is not the video pipeline. Without this the unknown-stage
  // branch below would set active:true and light up the global processing bar
  // with a stale label for the duration of every publish.
  if (e.stage === 'publish') return p

  const startedAt = p.active && p.startedAt ? p.startedAt : Date.now()
  const title = e.title || p.title
  const videoId = e.video_id || p.videoId
  const stage = STAGES[e.stage ?? '']
  if (!stage) return { ...p, active: true, startedAt, title, videoId }

  let within = 0.5
  if (typeof e.fraction === 'number') within = e.fraction
  else if (typeof e.clip === 'number' && e.total) within = (e.clip - 1) / e.total
  else if (typeof e.current === 'number' && e.total) within = Math.max(0, e.current - 1) / e.total

  const [lo, hi] = e.span ?? [0, 1]
  const fraction = Math.min(0.99, lo + (hi - lo) * (stage.base + stage.weight * Math.min(1, Math.max(0, within))))
  // Remote rendering adds where the clip is; a later local event keeps it
  // until the stage moves on.
  const remote = e.stage === 'render' ? (e.remote ?? p.remote) : undefined
  // A Marketplace plugin rating or understanding the moments, or suggesting
  // edits for the clips, names itself: the bar may not move then, because it
  // never goes backwards.
  const label =
    e.stage === 'render' && e.clip && e.total
      ? `Rendering clip ${e.clip}/${e.total}${remote ? ` · ${remote}` : ''}`
      : e.plugin && (e.stage === 'ranking' || e.stage === 'understand' || e.stage === 'edit')
        ? `${PLUGIN_LABELS[e.stage]} ${e.plugin}`
        : stage.label

  return {
    active: true,
    title,
    label,
    fraction: Math.max(fraction, p.fraction), // progress never moves backwards
    startedAt,
    videoId,
    ...(remote ? { remote } : {})
  }
}

/** Remaining seconds, extrapolated from elapsed time vs fraction complete.
 *  Recomputed against a live clock, so it counts down between events. */
export function etaSeconds(p: JobProgress, now: number): number | null {
  if (!p.active || p.fraction < 0.06) return null // too early to estimate honestly
  const elapsed = (now - p.startedAt) / 1000
  return Math.max(0, (elapsed * (1 - p.fraction)) / p.fraction)
}

export function formatEta(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m > 0 ? `${m}m ${s.toString().padStart(2, '0')}s` : `${s}s`
}

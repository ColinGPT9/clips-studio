import type { StudioEvent } from './types'

/** Folds WebSocket progress events into one overall job progress value
 *  (0..1) using stage weights, plus a label and timing for the ETA. */

export interface JobProgress {
  active: boolean
  title: string
  label: string
  fraction: number
  startedAt: number
}

const STAGES: Record<string, { base: number; weight: number; label: string }> = {
  download: { base: 0.0, weight: 0.05, label: 'Downloading video' },
  downloaded: { base: 0.05, weight: 0.0, label: 'Downloaded' },
  transcribe: { base: 0.05, weight: 0.25, label: 'Transcribing speech' },
  signals: { base: 0.3, weight: 0.05, label: 'Analyzing audio & visuals' },
  analyze: { base: 0.35, weight: 0.3, label: 'Finding the best moments' },
  reactions: { base: 0.65, weight: 0.1, label: 'Scoring on-screen reactions' },
  render: { base: 0.75, weight: 0.25, label: 'Rendering clips' }
}

export const emptyProgress: JobProgress = {
  active: false,
  title: '',
  label: '',
  fraction: 0,
  startedAt: 0
}

/** Survives page switches: whichever page is mounted keeps it updated. */
export const progressStore: { current: JobProgress } = { current: { ...emptyProgress } }

export function applyEvent(p: JobProgress, e: StudioEvent): JobProgress {
  if (e.type === 'job') {
    if (e.status === 'running')
      return { active: true, title: '', label: 'Starting…', fraction: 0, startedAt: Date.now() }
    if (e.status === 'done' || e.status === 'failed') return { ...emptyProgress }
  }
  if (e.type !== 'progress') return p
  if (e.stage === 'done') return { ...emptyProgress }

  const startedAt = p.active && p.startedAt ? p.startedAt : Date.now()
  const title = e.title || p.title
  const stage = STAGES[e.stage ?? '']
  if (!stage) return { ...p, active: true, startedAt, title }

  let within = 0.5
  if (typeof e.fraction === 'number') within = e.fraction
  else if (typeof e.clip === 'number' && e.total) within = (e.clip - 1) / e.total
  else if (typeof e.current === 'number' && e.total) within = Math.max(0, e.current - 1) / e.total

  const fraction = Math.min(0.99, stage.base + stage.weight * Math.min(1, Math.max(0, within)))
  const label =
    e.stage === 'render' && e.clip && e.total ? `Rendering clip ${e.clip}/${e.total}` : stage.label

  return {
    active: true,
    title,
    label,
    fraction: Math.max(fraction, p.fraction), // progress never moves backwards
    startedAt
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

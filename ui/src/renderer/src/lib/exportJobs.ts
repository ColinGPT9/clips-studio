import type { ExportEstimate, ExportJob } from './types'

/** Exports on the editor lane (server/editor_lane.py), as this window knows
 *  them. Any preset but Original (Settings → Export) makes one per clip, and
 *  its progress arrives as 'export' events (docs/editor-suite/api/step1-export.md).
 *
 *  The Export button, Export all, Settings → Export and the Dashboard's
 *  Activity feed share these helpers. No React and no engine calls in here,
 *  so tests/test_ui_export_settings.py runs this file under Node;
 *  lib/useExportJobs.ts is the hook around it. Words that are shown go
 *  through `tr`, the screen's t(); without it they are English. */

type Words = (s: string) => string
const english: Words = (s) => s

/** A job's key. The engine numbers exports from 1 again at each start, so the
 *  number alone could name an export from before a restart. */
export function jobKey(job: Pick<ExportJob, 'export_id' | 'created_at'>): string {
  return `${job.export_id}@${job.created_at}`
}

export function isActive(job: ExportJob): boolean {
  return job.status === 'queued' || job.status === 'running'
}

const STAGE: Record<string, number> = { queued: 0, running: 1, done: 2, failed: 2, cancelled: 2 }

/** Whether `incoming` is as far along as `known`, or further. A job only moves
 *  forward (waiting, running, finished) and its fraction never goes back, so
 *  an answer that arrives after a newer event never undoes it. A finished job
 *  stays as it finished. */
export function isNewer(known: ExportJob | undefined, incoming: ExportJob): boolean {
  if (!known) return true
  const was = STAGE[known.status] ?? 0
  const now = STAGE[incoming.status] ?? 0
  if (now !== was) return now > was
  if (now === STAGE.done) return false
  return incoming.fraction >= known.fraction
}

/** `map` with `jobs` taken in wherever they are newer. The same map comes
 *  back when nothing changed, so a render can be skipped. */
export function mergeJobs(map: Map<string, ExportJob>, jobs: ExportJob[]): Map<string, ExportJob> {
  let next: Map<string, ExportJob> | null = null
  for (const job of jobs) {
    if (!job || typeof job.export_id !== 'number') continue
    const key = jobKey(job)
    if (!isNewer((next ?? map).get(key), job)) continue
    next ??= new Map(map)
    next.set(key, job)
  }
  return next ?? map
}

/** GET /export/jobs' answer, taken in. The engine keeps every waiting and
 *  running export, so one this window had as waiting or running when it asked
 *  (`asked`: their keys, taken just before the request) that the answer
 *  doesn't list was lost when the engine restarted. It is marked failed with
 *  `lost`, rather than left running forever. An export queued after the
 *  question was sent is not in `asked`, so a late answer can't lose it. */
export function reconcileJobs(
  map: Map<string, ExportJob>,
  listed: ExportJob[],
  asked: Set<string>,
  lost: string
): Map<string, ExportJob> {
  let next = mergeJobs(map, listed)
  const present = new Set(listed.map(jobKey))
  for (const key of asked) {
    const job = next.get(key)
    if (!job || !isActive(job) || present.has(key)) continue
    if (next === map) next = new Map(map)
    next.set(key, { ...job, status: 'failed', cancelling: false, error: lost })
  }
  return next
}

/** How far along a set of exports is, 0-1. A finished one counts as whole. */
export function overallFraction(jobs: ExportJob[]): number {
  if (jobs.length === 0) return 0
  const sum = jobs.reduce((s, j) => s + (isActive(j) ? Math.min(1, Math.max(0, j.fraction || 0)) : 1), 0)
  return sum / jobs.length
}

/** What the bar shows for a set of exports: how far along, 0-1, and whether
 *  the one running came out over its size limit and is being made again,
 *  smaller (`smaller`). One export being made smaller shows that try from 0
 *  (`attempt_fraction`): its first try has used up `fraction` by then. Several
 *  keep the bar that never goes back, which waits there meanwhile. */
export function exportProgress(jobs: ExportJob[]): { fraction: number; smaller: boolean } {
  const running = jobs.find((j) => j.status === 'running')
  const smaller = Boolean(running?.smaller)
  if (smaller && jobs.length === 1 && running)
    return { fraction: Math.min(1, Math.max(0, running.attempt_fraction ?? 0)), smaller }
  return { fraction: overallFraction(jobs), smaller }
}

/** Newest first: by when the engine made them, then by number. */
export function newestFirst(a: ExportJob, b: ExportJob): number {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? 1 : -1
  return b.export_id - a.export_id
}

/** What to show for one clip, oldest first, each export on its own line:
 *
 *  - the newest export this window started (`started`: keys) that has
 *    finished, from the moment it finishes, whatever else is exporting the
 *    clip meanwhile: another window's export, one through the local API,
 *    Export all's;
 *  - every export of the clip that is waiting or running, whoever started
 *    it, so it can be seen and cancelled (and Export waits for it).
 *
 *  The result gives way only to an export this window starts after it
 *  finished: exporting the clip again shows the new export, not the old
 *  file. */
export function clipExports(jobs: Iterable<ExportJob>, clipId: number, started: Set<string>): ExportJob[] {
  const mine = [...jobs].filter((j) => j.clip_id === clipId).sort(newestFirst)
  const own = (j: ExportJob): boolean => started.has(jobKey(j))
  const last = mine.find((j) => own(j) && !isActive(j))
  const active = mine.filter(isActive).reverse()
  // Times are the engine's, to the second; a tie counts as started after.
  const again = last && active.some((j) => own(j) && j.created_at >= (last.finished_at ?? last.created_at))
  return last && !again ? [last, ...active] : active
}

export interface ExportTally {
  done: number
  failed: number
  cancelled: number
  active: number
  /** The finished files' bytes, all together. */
  bytes: number
  /** Finished files over their size limit even after the retry. */
  over: number
  /** Why the first failed one failed, in the engine's words. */
  firstError: string
}

export function tally(jobs: ExportJob[]): ExportTally {
  const out: ExportTally = { done: 0, failed: 0, cancelled: 0, active: 0, bytes: 0, over: 0, firstError: '' }
  for (const job of jobs) {
    if (isActive(job)) out.active += 1
    else if (job.status === 'done') {
      out.done += 1
      out.bytes += job.result?.bytes ?? 0
      if (job.result?.over_target) out.over += 1
    } else if (job.status === 'cancelled') out.cancelled += 1
    else {
      out.failed += 1
      if (!out.firstError) out.firstError = job.error ?? ''
    }
  }
  return out
}

/** "23 MB", "4.5 MB", "1.2 GB", "640 KB": decimal units, the ones "Keep
 *  under N MB" counts (1 MB = 1,000,000 bytes). */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 MB'
  const mb = bytes / 1_000_000
  if (mb >= 999.5) return `${(mb / 1000).toFixed(1)} GB`
  if (mb >= 9.95) return `${Math.round(mb)} MB`
  if (mb >= 1) return `${mb.toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1000))} KB`
}

/** The longest clips Clips Kitty makes for Shorts: 3 minutes (the 60s+
 *  option's 61-180 s clips; YouTube Shorts stop there too). Settings →
 *  Export warns when a size limit can't keep a clip this long looking good.
 *  Longform's highlights run longer, but a warning set for them would show
 *  for nearly every limit; each such export says so when it is done. */
export const LONGEST_CLIP_S = 180

/** How long a clip can be and still look good under the size limit
 *  (POST /export/estimate's longest_watchable_s), when that is shorter than
 *  the clips Clips Kitty makes (LONGEST_CLIP_S); otherwise null, as it is
 *  with no limit. */
export function watchableUnder(estimate: Pick<ExportEstimate, 'longest_watchable_s'> | null): number | null {
  const seconds = estimate?.longest_watchable_s
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds < LONGEST_CLIP_S ? seconds : null
}

/** "45 seconds", "1 minute 31 seconds", "2 minutes": a clip's length in
 *  words, rounded down to the second. */
export function clipLength(seconds: number, tr: Words = english): string {
  const whole = Math.max(1, Math.floor(Number.isFinite(seconds) ? seconds : 1))
  const minutes = Math.floor(whole / 60)
  const rest = whole % 60
  const parts: string[] = []
  if (minutes > 0) parts.push(`${minutes} ${minutes === 1 ? tr('minute') : tr('minutes')}`)
  if (rest > 0) parts.push(`${rest} ${rest === 1 ? tr('second') : tr('seconds')}`)
  return parts.join(' ')
}

/** How many exports the Activity feed remembers what it said about. */
const ACTIVITY_REMEMBERS = 200

/** `said` with `key` moved to the end, so the exports heard from longest ago
 *  are the ones forgotten first, never a running one (heard from twice a second). */
function remember(said: Map<string, ExportJob['status']>, key: string, status: ExportJob['status']): void {
  said.delete(key)
  said.set(key, status)
  for (const old of said.keys()) {
    if (said.size <= ACTIVITY_REMEMBERS) break
    said.delete(old)
  }
}

/** The Dashboard's Activity feed line for an 'export' event, or null when the
 *  event is no news there. While an export runs, the lane sends about two
 *  events a second, each a little further along, so the feed says only when an
 *  export starts and how it ended, in Clip Studio's words. Waiting in line is
 *  no news either: Export all queues every clip at once.
 *
 *  `said` is the feed's memory, by jobKey: how far along each export was when
 *  the feed last heard of it. An event no further along says nothing, so
 *  neither progress nor a 'queued' that lost the race with 'running' adds a
 *  line; a finished export stays as it finished, as in isNewer. */
export function exportActivityLine(
  said: Map<string, ExportJob['status']>,
  job: ExportJob,
  tr: Words = english
): string | null {
  const key = jobKey(job)
  const known = said.get(key)
  if (known !== undefined && (STAGE[job.status] ?? 0) <= (STAGE[known] ?? 0)) {
    remember(said, key, known)
    return null
  }
  remember(said, key, job.status)
  if (job.status === 'running') return `${tr('Exporting')} ${job.name}…`
  if (job.status === 'done') {
    const r = job.result
    if (!r) return `${tr('Exported')} ${job.name}`
    const over = r.over_target ? ` · ⚠ ${tr('over the size limit')}` : ''
    return `${tr('Exported')} ${r.file} · ${formatBytes(r.bytes)} · ${r.width}×${r.height}${over}`
  }
  if (job.status === 'failed')
    return `${tr('Export failed:')} ${job.name} · ${job.error || tr('the engine gave no reason.')}`
  if (job.status === 'cancelled') return `${tr('Export cancelled:')} ${job.name}`
  return null
}

/** Export all's exports: which jobs, where they went, and what it left out
 *  (clips already starred as exported, clips with no file). */
export interface ExportBatch {
  keys: string[]
  folder: string
  starred: number
  missing: number
}

/** What this window knows and started, kept while it moves between pages:
 *  the Clip Editor page is unmounted on the way to Settings and back, and an
 *  export carries on meanwhile. */
export const exportSession: {
  jobs: Map<string, ExportJob>
  started: Set<string>
  batch: ExportBatch | null
} = { jobs: new Map(), started: new Set(), batch: null }

import { useEffect, useRef, useState } from 'react'
import { api, errorText } from './api'
import { t } from './i18n'
import { useEvents } from './useEvents'
import {
  exportSession,
  isActive,
  jobKey,
  mergeJobs,
  reconcileJobs,
  type ExportBatch
} from './exportJobs'
import type { ExportJob } from './types'

/** While an export runs, the lane is asked every this often as well as
 *  listened to: an event can be dropped (lib/useJobWatch.ts says how), and
 *  the last one is the one that says it is done. */
const RECHECK_MS = 10_000

export interface ExportTracker {
  /** Every export this window knows of, by jobKey. */
  jobs: Map<string, ExportJob>
  /** The keys of the exports this window started. */
  started: Set<string>
  /** The last Export all, while it runs and until its summary is put away. */
  batch: ExportBatch | null
  /** Exports an Export button just queued: shown at once and remembered as
   *  this window's. `batch` when they are Export all's. */
  start: (jobs: ExportJob[], batch?: Omit<ExportBatch, 'keys'>) => void
  /** Stop these exports. Throws the engine's words if one can't be stopped. */
  cancel: (jobs: ExportJob[]) => Promise<void>
  /** Put away a finished Export all's summary (one still running stays). */
  clearFinishedBatch: () => void
}

/** The editor lane's exports, kept current from the 'export' events.
 *  `onFinished` hears of each export this window saw running when it ends
 *  (done, failed or cancelled), so a page can show the clip's new star. */
export function useExportJobs(onFinished?: (job: ExportJob) => void): ExportTracker {
  const [, rerender] = useState(0)
  const finished = useRef(onFinished)
  finished.current = onFinished

  const take = (next: Map<string, ExportJob>): void => {
    const before = exportSession.jobs
    if (next === before) return
    exportSession.jobs = next
    rerender((n) => n + 1)
    for (const [key, job] of next) {
      const was = before.get(key)
      if (was && was !== job && isActive(was) && !isActive(job)) finished.current?.(job)
    }
  }

  useEvents((e) => {
    if (e.type === 'export' && e.export) take(mergeJobs(exportSession.jobs, [e.export]))
  })

  const recheck = (): void => {
    const asked = new Set<string>()
    for (const [key, job] of exportSession.jobs) if (isActive(job)) asked.add(key)
    api
      .exportJobs()
      .then((r) =>
        take(
          reconcileJobs(
            exportSession.jobs,
            r.jobs ?? [],
            asked,
            t('This export stopped when Clips Kitty’s engine restarted. Export it again.')
          )
        )
      )
      .catch(() => {
        /* the engine isn't answering: say nothing rather than wrongly end an export */
      })
  }

  // Catch up with what ran while this page was closed.
  useEffect(recheck, [])

  const anyActive = [...exportSession.jobs.values()].some(isActive)
  useEffect(() => {
    if (!anyActive) return
    const id = setInterval(recheck, RECHECK_MS)
    return () => clearInterval(id)
  }, [anyActive])

  const start = (jobs: ExportJob[], batch?: Omit<ExportBatch, 'keys'>): void => {
    for (const job of jobs) exportSession.started.add(jobKey(job))
    if (batch) exportSession.batch = { ...batch, keys: jobs.map(jobKey) }
    take(mergeJobs(exportSession.jobs, jobs))
    rerender((n) => n + 1) // the batch is new even when every job was already known
  }

  const cancel = async (jobs: ExportJob[]): Promise<void> => {
    // The waiting ones first: stopped the other way round, the lane would
    // start the next export the moment the running one ended.
    const order = [...jobs.filter((j) => j.status === 'queued'), ...jobs.filter((j) => j.status === 'running')]
    let problem: unknown = null
    for (const job of order) {
      try {
        take(mergeJobs(exportSession.jobs, [await api.cancelExport(job.export_id)]))
      } catch (e) {
        problem ??= e
      }
    }
    if (problem !== null) throw new Error(errorText(problem))
  }

  const clearFinishedBatch = (): void => {
    const batch = exportSession.batch
    if (!batch) return
    const running = batch.keys.some((key) => {
      const job = exportSession.jobs.get(key)
      return job !== undefined && isActive(job)
    })
    if (running) return
    exportSession.batch = null
    rerender((n) => n + 1)
  }

  return {
    jobs: exportSession.jobs,
    started: exportSession.started,
    batch: exportSession.batch,
    start,
    cancel,
    clearFinishedBatch
  }
}

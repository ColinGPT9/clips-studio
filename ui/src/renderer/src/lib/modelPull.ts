import type { ModelPull, StudioEvent } from './types'

/** How far a model download has got, for the Models page and setup.
 *
 *  Ollama fetches the bytes, then checks them and writes the model's manifest.
 *  On a model several gigabytes in size that check is a long, silent step with
 *  no percentage, and the bar used to sit at 100% through it, which read as
 *  finished. Quitting then stopped Ollama before the manifest was written, and
 *  the download had to start again from nothing (#148, #150). So nothing shows
 *  100% before the engine says the model is installed, and the checking step
 *  says to keep the app open.
 *
 *  Pure helpers, no React and no fetch, so both screens word it the same. */

export const CHECKING = 'Checking the download… keep Clips Kitty open'

/** Ollama's steps once the bytes are in. None of them has a percentage. */
export function isChecking(status: string | undefined): boolean {
  const s = status ?? ''
  return s.startsWith('verifying') || s.startsWith('writing') || s.startsWith('removing') || s === 'success'
}

/** Percent downloaded, rounded down and held at 99 until the engine says it
 *  is done: rounding 99.5% gave 100% with the download still going. */
export function pullPercent(completed?: number | null, total?: number | null): number | null {
  if (!completed || !total) return null
  return Math.min(99, Math.floor((completed / total) * 100))
}

/** The step in words. Ollama names each layer it fetches by its digest
 *  ("pulling 6a0746a1ec1a"), which means nothing to anyone. */
export function pullStep(status: string | undefined): string {
  const s = status ?? ''
  if (!s || s === 'starting' || s === 'pulling manifest') return 'Starting…'
  if (isChecking(s)) return CHECKING
  if (s.startsWith('pulling')) return 'Downloading'
  return s
}

/** One line for a running download: "Downloading 45%", or the checking step. */
export function pullText(p: Pick<ModelPull, 'status' | 'completed' | 'total'>): string {
  const step = pullStep(p.status)
  const pct = isChecking(p.status) ? null : pullPercent(p.completed, p.total)
  return pct === null ? step : `${step} ${pct}%`
}

/** A model_pull event folded into the downloads on screen, keyed by tag. A
 *  finished one leaves the list: it is in the installed models from then on. */
export function foldPullEvent(shown: Record<string, ModelPull>, e: StudioEvent): Record<string, ModelPull> {
  if (e.type !== 'model_pull' || !e.tag) return shown
  const next = { ...shown }
  if (e.status === 'done') {
    delete next[e.tag]
  } else if (e.status === 'error') {
    next[e.tag] = { tag: e.tag, status: 'error', completed: null, total: null, error: e.error ?? 'unknown error', done: true }
  } else {
    next[e.tag] = {
      tag: e.tag,
      status: e.status ?? '',
      completed: e.completed ?? null,
      total: e.total ?? null,
      error: null,
      done: false
    }
  }
  return next
}

/** What GET /models/pulls says, over what is on screen. The engine's word
 *  wins for every download it lists, which is how a window that missed the
 *  final event (a reconnect, a full event queue) still learns how it ended. A
 *  failure already shown stays until it is retried, though the engine forgets
 *  it after ten minutes. */
export function mergePulls(shown: Record<string, ModelPull>, server: ModelPull[]): Record<string, ModelPull> {
  const next: Record<string, ModelPull> = {}
  for (const p of Object.values(shown)) if (p.done) next[p.tag] = p
  for (const p of server) {
    if (p.status === 'done') delete next[p.tag]
    else next[p.tag] = p
  }
  return next
}

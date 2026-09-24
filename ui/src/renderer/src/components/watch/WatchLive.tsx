import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { AutomationActivity, StudioEvent } from '../../lib/types'
import { useEvents } from '../../lib/useEvents'
import { relative } from './WatchCard'
import { t } from '../../lib/i18n'

const TONE: Record<string, string> = {
  found: 'text-accent',
  queued: 'text-ink',
  posted: 'text-success',
  done: 'text-success',
  waiting: 'text-warn',
  retry: 'text-warn',
  error: 'text-error',
  info: 'text-muted',
  learned: 'text-accent'
}

/** The live panel: that Clips Kitty is watching, what it is doing this
 *  second, and the last few things it did. Big enough to read on a screen
 *  recording, and fed by the engine's own steps rather than guessed. */
export default function WatchLive(): JSX.Element | null {
  const [live, setLive] = useState<AutomationActivity | null>(null)
  const [now, setNow] = useState(Date.now() / 1000)
  const inFlight = useRef(false)

  const refresh = useCallback(async (): Promise<void> => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      setLive(await api.automationActivity())
    } catch {
      // The engine restarting; the next event or tick catches up.
    } finally {
      inFlight.current = false
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Steps arrive as events. Clipping progress arrives as the pipeline's own
  // progress events, so the percentage moves as the queue's does.
  useEvents((e: StudioEvent) => {
    if (e.type === 'automation' || e.type === 'progress' || e.type === 'job') void refresh()
  })

  // A clock for "next check in 12 min", and a slow poll in case a socket drops.
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000)
    const poll = setInterval(() => void refresh(), 5000)
    return () => {
      clearInterval(tick)
      clearInterval(poll)
    }
  }, [refresh])

  if (!live) return null
  const state = live.now.state
  const on = state !== 'off'
  const percent = live.now.progress?.percent

  return (
    <section
      className={`card space-y-3 border ${on ? 'border-success/40' : 'border-raised'}`}
      aria-label={t('Live')}
      aria-live="polite"
    >
      <div className="flex items-center gap-3">
        <span className="relative flex size-3 shrink-0" aria-hidden>
          {on && (
            <span className="absolute inline-flex size-full rounded-full bg-success opacity-60 animate-ping" />
          )}
          <span
            className={`relative inline-flex size-3 rounded-full ${on ? 'bg-success' : 'bg-muted'}`}
          />
        </span>
        <span className={`text-xs font-bold uppercase tracking-widest ${on ? 'text-success' : 'text-muted'}`}>
          {on ? t('Live') : t('Off')}
        </span>
        <p className="text-lg font-semibold min-w-0 truncate">{live.now.text}</p>
      </div>

      {state === 'busy' && percent != null && (
        <div className="space-y-1">
          <div className="h-2 rounded-full bg-raised overflow-hidden">
            <div className="h-full bg-accent transition-all" style={{ width: `${percent}%` }} />
          </div>
          <p className="text-xs text-muted">
            {percent}% · {live.now.progress?.label}
            {live.now.progress?.eta_seconds
              ? ` · ${t('about')} ${Math.max(1, Math.round(live.now.progress.eta_seconds / 60))} ${t('min left')}`
              : ''}
          </p>
        </div>
      )}

      {on && state !== 'busy' && live.next_check_at > 0 && (
        <p className="text-sm text-muted">
          {t('Next check')} {relative(live.next_check_at, now)}
        </p>
      )}

      {live.events.length > 0 && (
        <ul className="space-y-1 border-t border-raised/60 pt-2">
          {live.events.slice(0, 8).map((e, i) => (
            <li key={`${e.at}-${i}`} className="text-sm flex gap-3">
              <span className="text-xs text-muted w-20 shrink-0 tabular-nums pt-0.5">
                {relative(e.at, now)}
              </span>
              <span className="min-w-0">
                <span className={TONE[e.kind] ?? 'text-ink'}>{e.text}</span>
                {e.url && (
                  <button
                    className="block text-xs text-accent underline underline-offset-2 hover:text-ink truncate max-w-full text-left"
                    title={t('Open in your browser')}
                    onClick={() => void window.studio?.openExternal(e.url as string)}
                  >
                    {e.url.replace(/^https?:\/\/(www\.)?/, '')} ↗
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

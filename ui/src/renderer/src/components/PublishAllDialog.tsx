import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { Clip } from '../lib/types'
import {
  PROVIDER_LABEL,
  localZone,
  platformLabel,
  platformsFor,
  type Provider
} from '../lib/uploadpost'

/** Publish a whole batch of clips to several platforms.
 *
 *  A modal with an explicit confirm, deliberately, and deliberately NOT a
 *  twin of the Export button next to it. Export writes files to a folder and
 *  can be undone by deleting them; this posts publicly to every account the
 *  creator owns and cannot be taken back. Two actions with consequences that
 *  far apart should not be one misclick from each other, so this one states
 *  what it is about to do and waits to be told yes.
 *
 *  Spacing defaults to on, and to DAYS: a video's clips are a posting
 *  calendar, not an afternoon. Twelve landing on TikTok in the same second
 *  reads as spam and spends the per-account daily cap in one go. The
 *  provider's own scheduler does the spreading, so a run stretching over
 *  weeks keeps going with Clips Kitty closed.
 */
export default function PublishAllDialog({
  clips,
  provider,
  onClose
}: {
  clips: Clip[]
  provider: Provider
  onClose: () => void
}): JSX.Element {
  const [platforms, setPlatforms] = useState<string[]>([])
  const [connected, setConnected] = useState<string[]>([])
  const [every, setEvery] = useState(1)
  // Days by default: a video's clips are usually spread across a
  // posting calendar, not an afternoon.
  const [unit, setUnit] = useState<'hours' | 'days'>('days')
  const [spread, setSpread] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{ started: number; skipped: { clip_id: number; reason: string }[] } | null>(null)

  const backend =
    provider === 'woopsocial'
      ? { connections: api.woopSocialConnections, batch: api.woopSocialBatch }
      : { connections: api.uploadPostConnections, batch: api.uploadPostBatch }
  const usable = platformsFor(provider)

  useEffect(() => {
    backend
      .connections()
      .then((got) => {
        setConnected(got.connected)
        setPlatforms(got.connected.filter((p) => usable.some((x) => x.id === p)))
      })
      .catch(() => {
        /* not connected yet; the picker still works */
      })
  }, [])

  const toggle = (id: string): void =>
    setPlatforms((c) => (c.includes(id) ? c.filter((p) => p !== id) : [...c, id]))

  const run = async (): Promise<void> => {
    if (busy || !platforms.length || !clips.length) return
    setBusy(true)
    setError('')
    try {
      const got = await backend.batch({
        clip_ids: clips.map((c) => c.id),
        platforms,
        every_hours: spread ? hoursApart : 0,
        timezone: spread ? localZone() : ''
      })
      setDone({ started: got.started.length, skipped: got.skipped })
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const posts = clips.length * platforms.length
  const hoursApart = unit === 'days' ? every * 24 : every
  const lastAt =
    spread && clips.length > 1
      ? new Date(Date.now() + hoursApart * (clips.length - 1) * 3600_000)
      : null

  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Publish all clips"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-lg space-y-4 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
          <h3 className="font-semibold text-lg">
            {t('Publish')} {clips.length} {clips.length === 1 ? t('clip') : t('clips')}
          </h3>
          <p className="text-xs text-muted mt-1">
            {t('Each clip is uploaded once and sent to every platform you pick, through your')}{' '}
            {PROVIDER_LABEL[provider]} {t('account.')}
          </p>
        </div>

        {done ? (
          <div className="space-y-2 text-sm">
            <p className="text-success">
              ✓ {done.started} {done.started === 1 ? t('clip') : t('clips')} {t('sent to')}{' '}
              {platforms.length} {platforms.length === 1 ? t('platform') : t('platforms')}.
            </p>
            {done.skipped.length > 0 && (
              <div className="text-xs text-warn space-y-0.5">
                <p>{t('Skipped')}:</p>
                {done.skipped.map((s) => (
                  <p key={s.clip_id}>
                    {t('Clip')} {s.clip_id}: {s.reason}
                  </p>
                ))}
              </div>
            )}
            <p className="text-xs text-muted">
              {t('Open a clip and its Publish tab to watch each platform.')}
            </p>
            <button className="btn-accent w-full !py-2" onClick={onClose}>
              {t('Done')}
            </button>
          </div>
        ) : (
          <>
            <div>
              <p className="label mb-1.5">{t('Publish to')}</p>
              <div className="flex flex-wrap gap-2">
                {usable.map((p) => {
                  const on = platforms.includes(p.id)
                  const linked = !connected.length || connected.includes(p.id)
                  return (
                    <button
                      key={p.id}
                      onClick={() => toggle(p.id)}
                      aria-pressed={on}
                      className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                        on ? 'border-accent bg-accent/15 text-ink' : 'border-raised text-muted'
                      } ${!linked ? 'opacity-50' : ''}`}
                      title={
                        !linked
                          ? `${t('Not connected to')} ${PROVIDER_LABEL[provider]} ${t('yet - it will be skipped')}`
                          : ''
                      }
                    >
                      {on ? '✓ ' : !linked ? '○ ' : ''}
                      {p.label}
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={spread}
                  onChange={(e) => setSpread(e.target.checked)}
                />
                {t('Space them out')}
              </label>
              {spread ? (
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-muted text-xs">{t('One every')}</span>
                  <input
                    type="number"
                    min={0.5}
                    step={0.5}
                    className="input !py-1 text-sm !w-20"
                    value={every}
                    onChange={(e) => setEvery(Math.max(0.5, Number(e.target.value) || 1))}
                    aria-label="Time between posts"
                  />
                  <select
                    className="input !py-1 text-sm !w-24"
                    value={unit}
                    onChange={(e) => setUnit(e.target.value as 'hours' | 'days')}
                    aria-label="Unit"
                  >
                    <option value="hours">{t('hours')}</option>
                    <option value="days">{t('days')}</option>
                  </select>
                </div>
              ) : (
                /* Said plainly, because it is the choice that gets accounts
                   limited rather than a matter of taste. */
                <p className="text-xs text-warn">
                  {t(
                    'All of them go at once. Platforms treat a burst of posts as spam, and each account has a daily limit.'
                  )}
                </p>
              )}
            </div>

            {/* The whole point of the confirm: say what is about to happen,
                in the units that matter, before it happens. */}
            <div className="border border-raised rounded-lg p-3 text-xs space-y-1">
              <p className="font-medium text-sm">{t('About to')}</p>
              <p>
                {t('Create')} <span className="text-accent font-medium">{posts}</span>{' '}
                {posts === 1 ? t('post') : t('posts')} — {clips.length}{' '}
                {clips.length === 1 ? t('clip') : t('clips')} ×{' '}
                {platforms.length ? platforms.map(platformLabel).join(', ') : t('nothing yet')}.
              </p>
              {lastAt && (
                <p className="text-muted">
                  {t('First one now, last one around')} {lastAt.toLocaleString()}.
                </p>
              )}
              <p className="text-muted">{t('Uploads cannot be taken back.')}</p>
            </div>

            {error && <p className="text-xs text-error">{error}</p>}

            <div className="flex gap-2">
              <button className="btn-ghost flex-1 !py-2" onClick={onClose} disabled={busy}>
                {t('Cancel')}
              </button>
              <button
                className="btn-accent flex-1 !py-2"
                disabled={busy || !platforms.length || !clips.length}
                onClick={() => void run()}
              >
                {busy
                  ? t('Publishing…')
                  : `${t('Yes, publish')} ${posts} ${posts === 1 ? t('post') : t('posts')}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

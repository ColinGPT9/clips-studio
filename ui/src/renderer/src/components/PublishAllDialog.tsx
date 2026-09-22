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
  // A DAILY budget, because that is the shape posting limits take: WoopSocial
  // allows five YouTube posts a day, and sending 37 at once failed 32 of them.
  // One flat interval could not say "five a day, an hour apart" at all.
  const [perDay, setPerDay] = useState(5)
  const [gapHours, setGapHours] = useState(1)
  const [spread, setSpread] = useState(true)
  // Clips left out entirely, and clip -> platforms it should skip. Everything
  // starts included, so the common case costs nothing and only the exceptions
  // are work.
  const [dropped, setDropped] = useState<Set<number>>(new Set())
  const [excluded, setExcluded] = useState<Record<number, string[]>>({})
  // How much is already spoken for. A new batch queues behind it, so the
  // estimate has to as well or it promises a date that cannot happen.
  const [queued, setQueued] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{ started: number; skipped: { clip_id: number; reason: string }[] } | null>(null)

  const backend =
    provider === 'woopsocial'
      ? { connections: api.woopSocialConnections, batch: api.woopSocialBatch }
      : { connections: api.uploadPostConnections, batch: api.uploadPostBatch }
  const usable = platformsFor(provider)

  useEffect(() => {
    if (provider !== 'woopsocial') return
    api
      .woopSocialSchedule()
      .then((got) =>
        setQueued(
          got.posts.filter((x) => x.state === 'queued' || x.state === 'processing').length
        )
      )
      .catch(() => {
        /* no schedule yet is the same as nothing queued */
      })
  }, [])

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

  const single = clips.length === 1
  const chosen = clips.filter((c) => !dropped.has(c.id))
  /** Platforms this clip will actually go to. */
  const going = (id: number): string[] =>
    platforms.filter((p) => !(excluded[id] || []).includes(p))
  const posts = chosen.reduce((n, c) => n + going(c.id).length, 0)
  // Only clips with somewhere left to go: one excluded from everything is not
  // a post, and must not take a slot out of the day's budget either.
  const sending = chosen.filter((c) => going(c.id).length > 0)

  // Posts already queued come first, so this batch starts after them. Mirrors
  // schedule.daily_after(), which fills each day to per_day and then rolls
  // over, rather than approximating it — a preview that disagrees with what
  // gets sent is worse than no preview.
  const daysBefore = spread && perDay > 0 ? Math.floor(queued / perDay) : 0
  const totalDays =
    spread && perDay > 0 ? Math.ceil((queued + sending.length) / perDay) : 1
  const days = Math.max(1, totalDays - daysBefore)
  const startAt =
    spread && queued > 0 ? new Date(Date.now() + daysBefore * 86_400_000) : null
  const finishAt =
    spread && sending.length > 1
      ? new Date(Date.now() + Math.max(0, totalDays - 1) * 86_400_000)
      : null

  const toggleClip = (id: number): void =>
    setDropped((c) => {
      const next = new Set(c)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const togglePlatformFor = (id: number, platform: string): void =>
    setExcluded((c) => {
      const off = new Set(c[id] || [])
      if (off.has(platform)) off.delete(platform)
      else off.add(platform)
      return { ...c, [id]: [...off] }
    })

  const run = async (): Promise<void> => {
    if (busy || !platforms.length || !sending.length) return
    setBusy(true)
    setError('')
    try {
      // Only the exceptions travel, and only for clips being sent.
      const exclude: Record<string, string[]> = {}
      for (const c of sending) {
        const off = excluded[c.id] || []
        if (off.length) exclude[String(c.id)] = off
      }
      // Only WoopSocial understands a daily budget; Upload-Post's batch has
      // its own loop and would accept per_day and then ignore it, firing
      // everything at once. For that provider the budget becomes the nearest
      // flat interval it does honour, so the daily ceiling is still roughly
      // respected instead of silently abandoned.
      const daily =
        provider === 'woopsocial'
          ? { per_day: spread ? perDay : 0, gap_hours: gapHours, exclude }
          : { every_hours: spread ? 24 / Math.max(1, perDay) : 0 }
      const got = await backend.batch({
        clip_ids: sending.map((c) => c.id),
        platforms,
        ...daily,
        timezone: spread ? localZone() : ''
      })
      setDone({ started: got.started.length, skipped: got.skipped })
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

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
            {single ? t('Publish this clip') : `${t('Publish')} ${clips.length} ${t('clips')}`}
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
                <div className="flex items-center gap-2 text-sm flex-wrap">
                  <input
                    type="number"
                    min={1}
                    step={1}
                    className="input !py-1 text-sm !w-16"
                    value={perDay}
                    onChange={(e) => setPerDay(Math.max(1, Number(e.target.value) || 1))}
                    aria-label="Posts per day"
                  />
                  <span className="text-muted text-xs">{t('a day,')}</span>
                  <input
                    type="number"
                    min={0.5}
                    step={0.5}
                    className="input !py-1 text-sm !w-16"
                    value={gapHours}
                    onChange={(e) => setGapHours(Math.max(0.5, Number(e.target.value) || 1))}
                    aria-label="Hours between posts"
                  />
                  <span className="text-muted text-xs">{t('hours apart')}</span>
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

            {/* Which clip goes where. Pointless for a single clip, and the
                exceptions are the only interesting part, so everything starts
                on and a click is what takes something away. */}
            {!single && platforms.length > 0 && (
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <p className="label">{t('Clips')}</p>
                  <button
                    className="text-xs text-muted hover:text-accent"
                    onClick={() =>
                      setDropped((c) => (c.size ? new Set() : new Set(clips.map((x) => x.id))))
                    }
                  >
                    {dropped.size ? t('Select all') : t('Select none')}
                  </button>
                </div>
                <div className="max-h-48 overflow-y-auto border border-raised rounded-lg divide-y divide-raised">
                  {clips.map((c) => {
                    const on = !dropped.has(c.id)
                    return (
                      <div key={c.id} className="flex items-center gap-2 px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() => toggleClip(c.id)}
                          aria-label={`Include clip ${c.id}`}
                        />
                        <span
                          className={`text-xs flex-1 min-w-0 truncate ${on ? '' : 'text-muted line-through'}`}
                          title={c.title || c.hook || `Clip ${c.id}`}
                        >
                          {c.title || c.hook || `${t('Clip')} ${c.id}`}
                        </span>
                        <span className="flex gap-1 shrink-0">
                          {platforms.map((p) => {
                            const lit = on && going(c.id).includes(p)
                            return (
                              <button
                                key={p}
                                disabled={!on}
                                onClick={() => togglePlatformFor(c.id, p)}
                                aria-pressed={lit}
                                title={`${platformLabel(p)}${lit ? '' : ` - ${t('skipped')}`}`}
                                className={`px-1.5 py-0.5 rounded text-[10px] border ${
                                  lit
                                    ? 'border-accent bg-accent/15 text-ink'
                                    : 'border-raised text-muted line-through'
                                } ${on ? '' : 'opacity-40'}`}
                              >
                                {platformLabel(p)}
                              </button>
                            )
                          })}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* The whole point of the confirm: say what is about to happen,
                in the units that matter, before it happens. */}
            <div className="border border-raised rounded-lg p-3 text-xs space-y-1">
              <p className="font-medium text-sm">{t('About to')}</p>
              <p>
                {t('Create')} <span className="text-accent font-medium">{posts}</span>{' '}
                {posts === 1 ? t('post') : t('posts')} {t('from')} {sending.length}{' '}
                {sending.length === 1 ? t('clip') : t('clips')} {t('to')}{' '}
                {platforms.length ? platforms.map(platformLabel).join(', ') : t('nothing yet')}.
              </p>
              {finishAt && (
                <p className="text-muted">
                  {perDay} {t('a day')} - {t('finishes')} {finishAt.toLocaleDateString()} (
                  {days} {days === 1 ? t('day') : t('days')}).
                </p>
              )}
              {startAt && (
                <p className="text-muted">
                  {t('Starts')} {startAt.toLocaleDateString()}, {t('after the')} {queued}{' '}
                  {t('already queued.')}
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
                disabled={busy || !platforms.length || !sending.length}
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

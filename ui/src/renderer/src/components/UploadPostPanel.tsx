import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { Clip } from '../lib/types'
import {
  MAX_POLLS,
  describeState,
  localInputToIso,
  localZone,
  missingRequired,
  nextPollDelay,
  platformLabel,
  type Capabilities,
  type Overrides,
  platformsFor,
  PROVIDER_LABEL,
  type PlatformRow,
  type Provider,
  type UploadPostStatus
} from '../lib/uploadpost'

/** Publish one clip to several platforms in a single upload.
 *
 *  The metadata is written once. Upload-Post's own fallback chain applies the
 *  generic title and description to every platform that has no override, so
 *  the user is not asked for the same sentence eight times — which is the
 *  entire point of the feature.
 *
 *  Progress is polled rather than pushed. Upload-Post can call a webhook, but
 *  a desktop app has no public address to receive one at, so the panel asks
 *  for the status on a backing-off interval and stops the moment nothing is
 *  still in flight.
 */

export default function UploadPostPanel({
  clip,
  status,
  provider,
  otherAvailable,
  onSwitchProvider
}: {
  clip: Clip
  status: UploadPostStatus
  provider: Provider
  /** True when the other provider is also set up, so switching is offered. */
  otherAvailable: boolean
  onSwitchProvider: (p: Provider) => void
}): JSX.Element {
  const [platforms, setPlatforms] = useState<string[]>(
    status.platforms.length ? status.platforms : ['youtube']
  )
  const [title, setTitle] = useState(clip.title || clip.hook || '')
  const [description, setDescription] = useState(clip.description || '')
  // The clip stores hashtags as an array; the field edits them as one line.
  const [hashtags, setHashtags] = useState((clip.hashtags || []).join(' '))
  const [firstComment, setFirstComment] = useState('')
  const [useThumbnail, setUseThumbnail] = useState(false)
  const [rows, setRows] = useState<PlatformRow[]>([])
  const [requestId, setRequestId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [caps, setCaps] = useState<Capabilities | null>(null)
  // WoopSocial reaches eight of the nine; Upload-Post all of them.
  const usable = platformsFor(provider)
  // One place that knows which provider's routes to call, so nothing below
  // has to branch on it.
  const backend =
    provider === 'woopsocial'
      ? {
          connections: api.woopSocialConnections,
          publish: api.woopSocialPublish,
          refresh: api.refreshWoopSocial,
          retry: null
        }
      : {
          connections: api.uploadPostConnections,
          publish: api.uploadPostPublish,
          refresh: api.refreshUploadPost,
          retry: api.retryUploadPost
        }
  const [connected, setConnected] = useState<string[]>([])
  const [overrides, setOverrides] = useState<Overrides>({})
  const [expanded, setExpanded] = useState<string | null>(null)
  const [when, setWhen] = useState<'now' | 'schedule' | 'queue'>('now')
  const [at, setAt] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const attempts = useRef(0)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    api
      .clipUploadPostRows(clip.id)
      .then((got) => {
        if (!alive.current) return
        setRows(got.platforms)
        // Pick the fan-out back up if one is still running from last time.
        const live = got.platforms.find((r) => r.request_id)
        if (live) setRequestId(live.request_id)
      })
      .catch(() => {
        /* nothing published yet */
      })
    api
      .uploadPostCapabilities()
      .then((got) => alive.current && setCaps(got.platforms))
      .catch(() => {
        // Without this the advanced sections stay closed rather than
        // guessing at what a platform takes.
      })
    backend
      .connections()
      .then((got) => {
        if (!alive.current) return
        setConnected(got.connected)
        // Default to everything they have actually linked. The common case
        // is "put this everywhere", so that is what is pre-selected.
        if (got.connected.length) {
          setPlatforms(got.connected.filter((p) => usable.some((x) => x.id === p)))
        }
      })
      .catch(() => {
        /* fall back to the remembered selection */
      })
    return () => {
      alive.current = false
      if (timer.current) clearTimeout(timer.current)
    }
  }, [clip.id])

  const schedulePoll = (id: string): void => {
    if (timer.current) clearTimeout(timer.current)
    if (attempts.current >= MAX_POLLS) {
      setError(
        t(
          'Still working after a long time. Your clip may still publish - check the platforms directly.'
        )
      )
      return
    }
    timer.current = setTimeout(() => {
      if (!alive.current) return
      attempts.current += 1
      backend
        .refresh(id)
        .then((got) => {
          if (!alive.current) return
          setRows(got.platforms)
          if (!got.done) schedulePoll(id)
        })
        .catch(() => {
          // A single failed poll is not a failed upload. Keep asking.
          if (alive.current) schedulePoll(id)
        })
    }, nextPollDelay(attempts.current))
  }

  const publish = async (): Promise<void> => {
    if (busy || !platforms.length || !title.trim()) return
    setBusy(true)
    setError('')
    attempts.current = 0
    try {
      const tags = hashtags
        .split(/[\s,]+/)
        .map((h) => h.replace(/^#/, '').trim())
        .filter(Boolean)
      const got = await backend.publish(clip.id, {
        platforms,
        title: title.trim(),
        description,
        tags,
        // Upload-Post only; WoopSocial's API takes neither.
        ...(provider === 'uploadpost'
          ? { first_comment: firstComment, thumbnail: useThumbnail, add_to_queue: when === 'queue' }
          : {}),
        // Only the platforms actually selected — an override left behind
        // from a deselected platform must not travel with the request.
        overrides: Object.fromEntries(
          Object.entries(overrides).filter(([p]) => platforms.includes(p))
        ),
        scheduled_date: when === 'schedule' ? localInputToIso(at) : '',
        timezone: when === 'schedule' ? localZone() : '',
      })
      setRows(got.platforms)
      setRequestId(got.request_id)
      if (!got.done) schedulePoll(got.request_id)
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const retryFailed = async (): Promise<void> => {
    if (busy || !requestId) return
    setBusy(true)
    setError('')
    attempts.current = 0
    try {
      if (!backend.retry) return
      const got = await backend.retry(requestId)
      setRows(got.platforms)
      if (!got.done) schedulePoll(requestId)
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  // WoopSocial has no posting queue, so a selection left over from
  // Upload-Post would silently mean "publish now".
  useEffect(() => {
    if (provider !== 'uploadpost' && when === 'queue') setWhen('now')
  }, [provider, when])

  const setOverride = (platform: string, key: string, value: string): void =>
    setOverrides((current) => ({
      ...current,
      [platform]: { ...(current[platform] ?? {}), [key]: value }
    }))

  const toggle = (id: string): void =>
    setPlatforms((current) =>
      current.includes(id) ? current.filter((p) => p !== id) : [...current, id]
    )

  const open = (url: string) => () => void window.studio.openExternal(url)
  const inFlight = rows.some(
    (r) => r.state === 'queued' || r.state === 'processing' || r.state === 'sending'
  )
  const anyFailed = rows.some((r) => r.state === 'failed')
  const lacking = missingRequired(platforms, caps, overrides)
  // Everything they have linked that can take a video. Falls back to the
  // full list before the connection check has answered.
  const everywhere = connected.length
    ? usable.filter((p) => connected.includes(p.id)).map((p) => p.id)
    : usable.map((p) => p.id)
  // Chosen but not linked: these will come back skipped, which is worth
  // saying now rather than after the upload.
  const unlinked = connected.length ? platforms.filter((p) => !connected.includes(p)) : []

  if (!status.has_key) {
    return (
      <div className="space-y-3 text-sm">
        <p className="font-medium">{t('Upload-Post isn’t connected')}</p>
        <p className="text-xs text-muted">
          {t(
            'Add your Upload-Post API key in Settings to publish this clip to several platforms at once.'
          )}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4 text-sm">
      {/* Only shown when both are set up. With one provider there is no
          choice to make and a switcher would just be noise. */}
      {otherAvailable && (
        <div className="flex items-center gap-2 text-xs">
          <span className="label">{t('Through')}</span>
          {(['woopsocial', 'uploadpost'] as const).map((p) => (
            <button
              key={p}
              onClick={() => onSwitchProvider(p)}
              aria-pressed={provider === p}
              className={`px-2.5 py-1 rounded-lg border transition-colors ${
                provider === p
                  ? 'border-accent bg-accent/15 text-ink'
                  : 'border-raised text-muted hover:text-ink'
              }`}
            >
              {PROVIDER_LABEL[p]}
            </button>
          ))}
        </div>
      )}

      <div>
        <div className="flex items-center justify-between gap-3 mb-1.5">
          <p className="label">{t('Publish to')}</p>
          <div className="flex gap-2 text-[11px]">
            {/* The common case is "put this everywhere", so it is one click
                rather than nine. Only ever selects platforms actually
                linked, so it cannot silently ask for one that would be
                skipped. */}
            <button
              className="text-accent hover:underline"
              onClick={() => setPlatforms(everywhere)}
              disabled={!everywhere.length}
            >
              {t('Everywhere')}
            </button>
            <span className="text-muted">·</span>
            <button className="text-muted hover:text-ink" onClick={() => setPlatforms([])}>
              {t('None')}
            </button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {usable.map((p) => {
            const on = platforms.includes(p.id)
            // Only meaningful once we know; before that nothing is dimmed.
            const linked = !connected.length || connected.includes(p.id)
            return (
              <button
                key={p.id}
                onClick={() => toggle(p.id)}
                aria-pressed={on}
                title={
                  !linked
                    ? t('Not connected to Upload-Post yet - it will be skipped')
                    : p.note
                      ? t(p.note)
                      : undefined
                }
                className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                  on
                    ? 'border-accent bg-accent/15 text-ink'
                    : 'border-raised text-muted hover:text-ink'
                } ${!linked ? 'opacity-50' : ''}`}
              >
                {on ? '✓ ' : !linked ? '○ ' : ''}
                {p.label}
              </button>
            )
          })}
        </div>
        <p className="text-[11px] text-muted mt-1.5">
          {!platforms.length
            ? t('Pick at least one platform.')
            : unlinked.length
              ? `${t('One upload, sent to all of them.')} ${t('Not connected yet')}: ${unlinked
                  .map(platformLabel)
                  .join(', ')}.`
              : t('One upload, sent to all of them.')}
        </p>
      </div>

      <div className="space-y-2">
        <div>
          <label className="label block mb-1" htmlFor="up-title">
            {t('Title')}
          </label>
          <input
            id="up-title"
            className="input !py-1 text-sm"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div>
          <label className="label block mb-1" htmlFor="up-description">
            {t('Description')}
          </label>
          <textarea
            id="up-description"
            className="input text-sm !py-2"
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          {status.common_description && (
            <p className="text-[11px] text-muted mt-1">
              {t('Your standing text from Settings is added to the end of this.')}
            </p>
          )}
        </div>
        <div>
          <label className="label block mb-1" htmlFor="up-hashtags">
            {t('Hashtags')}
          </label>
          <input
            id="up-hashtags"
            className="input !py-1 text-sm"
            value={hashtags}
            placeholder="#gaming #twitch #clips"
            onChange={(e) => setHashtags(e.target.value)}
          />
        </div>
        {provider === 'uploadpost' && (
        <div>
          <label className="label block mb-1" htmlFor="up-first-comment">
            {t('First comment')}
          </label>
          <input
            id="up-first-comment"
            className="input !py-1 text-sm"
            value={firstComment}
            placeholder={t('Optional - posted under the clip')}
            onChange={(e) => setFirstComment(e.target.value)}
          />
        </div>
        )}
        {provider === 'uploadpost' && (
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={useThumbnail}
              onChange={(e) => setUseThumbnail(e.target.checked)}
            />
            {t('Use the thumbnail chosen for this clip')}
            <span className="text-muted">{t('(YouTube, LinkedIn and Facebook video)')}</span>
          </label>
        )}
      </div>

      {/* Per-platform overrides, folded away. The common metadata above is
          what almost everyone wants; this is for the person who writes a
          different hook for TikTok than for LinkedIn. */}
      {caps && platforms.length > 0 && (
        <div className="space-y-1.5">
          <p className="label">{t('Per-platform wording')}</p>
          {platforms.map((id) => {
            const cap = caps[id]
            if (!cap) return null
            const open = expanded === id
            const mine = overrides[id] ?? {}
            const needs = Object.entries(cap.requires)
            return (
              <div key={id} className="border border-raised/60 rounded-lg">
                <button
                  className="w-full flex items-center justify-between px-2.5 py-1.5 text-xs"
                  onClick={() => setExpanded(open ? null : id)}
                  aria-expanded={open}
                >
                  <span>{platformLabel(id)}</span>
                  <span className="text-muted">
                    {needs.length > 0 && !needs.every(([f]) => (mine[f] ?? '').trim())
                      ? t('needs setup')
                      : Object.values(mine).some((v) => (v ?? '').trim())
                        ? t('customised')
                        : t('using common')}
                    {open ? ' ▾' : ' ▸'}
                  </span>
                </button>
                {open && (
                  <div className="px-2.5 pb-2.5 space-y-2">
                    {/* Required first — without these the platform fails. */}
                    {needs.map(([fieldName, label]) => (
                      <div key={fieldName}>
                        <label className="label block mb-1 text-warn">
                          {t(label)} · {t('required')}
                        </label>
                        <input
                          className="input !py-1 text-xs"
                          value={mine[fieldName] ?? ''}
                          onChange={(e) => setOverride(id, fieldName, e.target.value)}
                        />
                      </div>
                    ))}
                    <div>
                      <label className="label block mb-1">{t('Title / caption')}</label>
                      <input
                        className="input !py-1 text-xs"
                        placeholder={title || t('Uses the common title')}
                        value={mine.title ?? ''}
                        onChange={(e) => setOverride(id, 'title', e.target.value)}
                      />
                    </div>
                    {cap.description && (
                      <div>
                        <label className="label block mb-1">{t('Description')}</label>
                        <textarea
                          className="input text-xs !py-1.5"
                          rows={2}
                          placeholder={t('Uses the common description')}
                          value={mine.description ?? ''}
                          onChange={(e) => setOverride(id, 'description', e.target.value)}
                        />
                      </div>
                    )}
                    {cap.first_comment && (
                      <div>
                        <label className="label block mb-1">{t('First comment')}</label>
                        <input
                          className="input !py-1 text-xs"
                          value={mine.first_comment ?? ''}
                          onChange={(e) => setOverride(id, 'first_comment', e.target.value)}
                        />
                      </div>
                    )}
                    {cap.ai_disclosure && (
                      <label className="flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={mine.ai_disclosure === 'true'}
                          onChange={(e) =>
                            setOverride(id, 'ai_disclosure', e.target.checked ? 'true' : '')
                          }
                        />
                        {t('Declare AI-generated content')}
                      </label>
                    )}
                    {/* Said plainly rather than leaving a missing control to
                        be interpreted as a bug. */}
                    {!cap.thumbnail && (
                      <p className="text-[11px] text-muted">
                        {t('This platform does not take a custom thumbnail through Upload-Post.')}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="space-y-2">
        <p className="label">{t('When')}</p>
        <div className="flex gap-2 flex-wrap text-xs">
          {(
            [
              ['now', t('Publish now')],
              ['schedule', t('Schedule')],
              ...(provider === 'uploadpost'
                ? ([['queue', t('Add to queue')]] as const)
                : ([] as const))
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setWhen(id)}
              aria-pressed={when === id}
              className={`px-3 py-1.5 rounded-lg border transition-colors ${
                when === id
                  ? 'border-accent bg-accent/15 text-ink'
                  : 'border-raised text-muted hover:text-ink'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {when === 'schedule' && (
          <div>
            <input
              type="datetime-local"
              className="input !py-1 text-sm !w-60"
              value={at}
              onChange={(e) => setAt(e.target.value)}
              aria-label="Scheduled time"
            />
            <p className="text-[11px] text-muted mt-1">
              {/* Their API defaults to UTC, so the zone travels with it. */}
              {t('Your time zone')}: {localZone()}
            </p>
          </div>
        )}
        {when === 'queue' && (
          <p className="text-[11px] text-muted">
            {t('Goes into the next free slot of your Upload-Post posting queue.')}
          </p>
        )}
      </div>

      {lacking.length > 0 && (
        <p className="text-xs text-warn">
          {t('Needed first')}: {lacking.join(', ')}.
        </p>
      )}

      <button
        className="btn-accent w-full !py-2"
        disabled={
          busy ||
          inFlight ||
          !platforms.length ||
          !title.trim() ||
          lacking.length > 0 ||
          (when === 'schedule' && !at)
        }
        onClick={() => void publish()}
      >
        {busy || inFlight
          ? t('Publishing…')
          : when === 'schedule'
            ? `${t('Schedule for')} ${platforms.length} ${
                platforms.length === 1 ? t('platform') : t('platforms')
              }`
            : when === 'queue'
              ? t('Add to queue')
              : `${t('Publish to')} ${platforms.length} ${
                  platforms.length === 1 ? t('platform') : t('platforms')
                }`}
      </button>

      {error && <p className="text-xs text-error">{error}</p>}

      {rows.length > 0 && (
        <div className="space-y-1.5 border-t border-raised/60 pt-3">
          <p className="label">{t('Where it went')}</p>
          {rows.map((row) => (
            <div key={row.platform} className="flex items-center justify-between gap-3 text-xs">
              <span className="flex items-center gap-2 min-w-0">
                <span aria-hidden>
                  {row.state === 'published'
                    ? '✓'
                    : row.state === 'failed'
                      ? '✗'
                      : row.state === 'skipped'
                        ? '○'
                        : '⏳'}
                </span>
                <span className="shrink-0">{platformLabel(row.platform)}</span>
                <span
                  className={`truncate ${
                    row.state === 'failed'
                      ? 'text-error'
                      : row.state === 'published'
                        ? 'text-success'
                        : 'text-muted'
                  }`}
                >
                  {describeState(row)}
                </span>
              </span>
              {/* Only when the provider actually returned one. A link that
                  goes nowhere is worse than no link. */}
              {row.post_url && (
                <button
                  className="text-accent hover:underline shrink-0"
                  onClick={open(row.post_url)}
                >
                  {t('Open')} ↗
                </button>
              )}
            </div>
          ))}
          {/* Only the platforms that failed, through Upload-Post's own retry —
              the clip is not sent again, so the ones that worked cannot end
              up posted twice. */}
          {anyFailed && requestId && !inFlight && backend.retry && (
            <button
              className="btn-ghost w-full !py-1.5 text-xs mt-1"
              disabled={busy}
              onClick={() => void retryFailed()}
            >
              {t('Retry the ones that failed')}
            </button>
          )}
          {requestId && (
            <p className="text-[11px] text-muted pt-1">
              {t('Upload reference')}: {requestId}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

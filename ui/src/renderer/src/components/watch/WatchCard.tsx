import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../../lib/api'
import type { AutomationStatus, Watch, WatchItem } from '../../lib/types'
import { platformLabel } from '../../lib/uploadpost'
import QueueItemSettings from '../queue/QueueItemSettings'
import WatchPublishSettings from './WatchPublishSettings'
import { t } from '../../lib/i18n'

export const WATCH_PLATFORM_LABEL: Record<string, string> = {
  youtube: 'YouTube',
  twitch: 'Twitch',
  kick: 'Kick'
}

/** How many videos the history shows before "show more". */
const SHOWN = 8

/** "14:30", or "Thu 14:30" when it is not in the next day. */
function clockTime(seconds: number): string {
  const when = new Date(seconds * 1000)
  const soon = Math.abs(seconds - Date.now() / 1000) < 20 * 3600
  return when.toLocaleString([], {
    ...(soon ? {} : { weekday: 'short' }),
    hour: '2-digit',
    minute: '2-digit'
  })
}

/** "5 min ago" / "in 12 min", from unix seconds. */
export function relative(seconds: number, now = Date.now() / 1000): string {
  const diff = Math.round(seconds - now)
  const abs = Math.abs(diff)
  // The engine's clock and this one differ by a fraction of a second, which
  // otherwise reads "checked in 0 sec" for something that just happened.
  if (abs < 10) return t('just now')
  const unit =
    abs < 90
      ? `${abs} ${t('sec')}`
      : abs < 5400
        ? `${Math.round(abs / 60)} ${t('min')}`
        : abs < 172800
          ? `${Math.round(abs / 3600)} ${t('h')}`
          : `${Math.round(abs / 86400)} ${t('days')}`
  return diff < 0 ? `${unit} ${t('ago')}` : `${t('in')} ${unit}`
}

/** One watched channel: its settings, and what became of each video it saw.
 *
 *  Everything shown is read from the server, which reads a video's state live
 *  from its job and its delivery rows. Nothing is applied optimistically; an
 *  action is sent, then the list is read again. */
export default function WatchCard({
  watch,
  automation,
  version,
  onChanged,
  onOpenInStudio,
  openSetup = false
}: {
  watch: Watch
  automation: AutomationStatus
  /** Bumped by the page whenever the server says something moved. */
  version: number
  onChanged: () => void
  onOpenInStudio?: (videoId: string) => void
  /** Just added: open everything so it is set up before the first new video.
   *  A new channel only records what is already there, so there is time. */
  openSetup?: boolean
}): JSX.Element {
  const [items, setItems] = useState<WatchItem[] | null>(null)
  const [openClips, setOpenClips] = useState(openSetup)
  const [openPublish, setOpenPublish] = useState(openSetup)
  const cardRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (openSetup) cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [openSetup])
  const [showAll, setShowAll] = useState(false)
  const [showEarlier, setShowEarlier] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (): Promise<void> => {
    try {
      setItems(await api.watchItems(watch.id, 200))
    } catch (e) {
      setError(errorText(e))
    }
  }, [watch.id])

  useEffect(() => {
    void load()
  }, [load, version])

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
      onChanged()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const now = Date.now() / 1000
  const status = !automation.enabled
    ? t('Watching is switched off.')
    : !watch.enabled
      ? t('Paused.')
      : watch.last_ok_poll_at
        ? `${t('Checked')} ${relative(watch.last_ok_poll_at, now)}` +
          (watch.next_poll_at > now ? ` · ${t('next check')} ${relative(watch.next_poll_at, now)}` : '')
        : t('Looking at the channel for the first time…')

  const visible = (items ?? []).filter((i) => showEarlier || i.status !== 'earlier')
  const earlier = (items ?? []).filter((i) => i.status === 'earlier').length
  const shown = showAll ? visible : visible.slice(0, SHOWN)

  return (
    <section ref={cardRef} className="card space-y-3" aria-label={watch.name}>
      <div className="flex items-start gap-3 flex-wrap">
        <span className="text-xs font-semibold uppercase tracking-wide bg-raised rounded px-2 py-1 shrink-0">
          {WATCH_PLATFORM_LABEL[watch.platform] ?? watch.platform}
        </span>
        <div className="min-w-0">
          <h3 className="font-semibold truncate flex items-center gap-2">
            {watch.name || watch.channel_key}
            {automation.enabled && watch.enabled && (
              <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-success bg-success/10 rounded-full px-2 py-0.5">
                <span className="size-1.5 rounded-full bg-success animate-pulse" aria-hidden />
                {t('Watching')}
              </span>
            )}
          </h3>
          <p className="text-xs text-muted">{status}</p>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <label className="inline-flex items-center gap-2 text-sm" title={t('Watch this channel')}>
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={watch.enabled}
              disabled={busy}
              onChange={(e) => void act(() => api.patchWatch(watch.id, { enabled: e.target.checked }))}
            />
            {watch.enabled ? t('On') : t('Off')}
          </label>
          <button
            className="btn-ghost !px-2 !py-1 text-xs"
            disabled={busy || !watch.enabled || !automation.enabled}
            onClick={() => void act(() => api.checkWatch(watch.id))}
          >
            {t('Check now')}
          </button>
          {confirmRemove ? (
            <span className="flex items-center gap-1.5">
              <span className="text-xs text-muted">{t('Stop watching?')}</span>
              <button
                className="btn-ghost !px-2 !py-1 text-xs text-error"
                disabled={busy}
                onClick={() => void act(() => api.deleteWatch(watch.id))}
              >
                {t('Remove')}
              </button>
              <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => setConfirmRemove(false)}>
                {t('Cancel')}
              </button>
            </span>
          ) : (
            <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => setConfirmRemove(true)}>
              {t('Remove')}
            </button>
          )}
        </div>
      </div>

      {watch.last_error && (
        <p className="text-sm text-error">
          {t('Could not check this channel:')} {watch.last_error}
          {watch.platform === 'kick' &&
            ` ${t('Kick has no official way to list videos, so this can stop working without notice.')}`}
        </p>
      )}

      {openSetup && (
        <p className="text-sm text-accent">
          {t('Set it up now. Nothing is clipped until the channel posts something new.')}
        </p>
      )}
      <div className="flex gap-2 flex-wrap">
        <button
          className="btn-ghost !px-2 !py-1 text-xs"
          aria-expanded={openClips}
          onClick={() => setOpenClips(!openClips)}
        >
          {t('Clip settings')} {openClips ? '▾' : '▸'}
        </button>
        <button
          className="btn-ghost !px-2 !py-1 text-xs"
          aria-expanded={openPublish}
          onClick={() => setOpenPublish(!openPublish)}
        >
          {t('Publishing')}: {t(publishModeLabel(watch.publish.mode))}{' '}
          {openPublish ? '▾' : '▸'}
        </button>
      </div>

      {openClips && (
        <div className="space-y-2">
          <label className="text-sm flex items-center gap-3 flex-wrap mt-3">
            <span className="label">{t('Preset')}</span>
            <select
              className="input !w-56"
              value={watch.preset}
              disabled={busy}
              onChange={(e) => void act(() => api.patchWatch(watch.id, { preset: e.target.value }))}
            >
              {automation.presets.map((p) => (
                <option key={p.id} value={p.id} title={p.description}>
                  {t(p.name)}
                </option>
              ))}
            </select>
          </label>
          <QueueItemSettings
            key={`watch-${watch.id}`}
            job={{ id: watch.id, settings: watch.options }}
            heading="Settings for every video from this channel"
            save={(patch) => api.patchWatch(watch.id, { options: patch })}
            onSaved={onChanged}
          />
        </div>
      )}
      {openPublish && <WatchPublishSettings watch={watch} onSaved={onChanged} />}

      {error && <p className="text-sm text-error">{error}</p>}

      <div className="space-y-2">
        {items === null ? (
          <p className="text-sm text-muted">{t('Loading…')}</p>
        ) : visible.length === 0 ? (
          <p className="text-sm text-muted">
            {earlier > 0
              ? t('Nothing new yet. New videos from this channel will show up here.')
              : t('Nothing found yet.')}
          </p>
        ) : (
          shown.map((item) => (
            <ItemRow
              key={item.id}
              item={item}
              busy={busy}
              act={act}
              onOpenInStudio={onOpenInStudio}
            />
          ))
        )}
        <div className="flex gap-3 flex-wrap">
          {visible.length > SHOWN && (
            <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => setShowAll(!showAll)}>
              {showAll ? t('Show fewer') : `${t('Show all')} (${visible.length})`}
            </button>
          )}
          {earlier > 0 && (
            <button
              className="btn-ghost !px-2 !py-1 text-xs"
              onClick={() => setShowEarlier(!showEarlier)}
              title={t('Videos that were already on the channel when you started watching it.')}
            >
              {showEarlier
                ? t('Hide earlier videos')
                : `${t('Show earlier videos')} (${earlier})`}
            </button>
          )}
        </div>
      </div>
    </section>
  )
}

function publishModeLabel(mode: Watch['publish']['mode']): string {
  return mode === 'auto' ? 'Automatic' : mode === 'ask' ? 'Ask first' : 'Off'
}

function statusText(item: WatchItem): { text: string; tone: string } {
  switch (item.status) {
    case 'earlier':
      return { text: 'Before you started watching', tone: 'text-muted' }
    case 'waiting_for_video':
      return { text: 'Waiting for the video to be ready', tone: 'text-warn' }
    case 'waiting_for_queue':
      return { text: 'Waiting for room in the queue', tone: 'text-warn' }
    case 'queued':
      return { text: 'In the queue', tone: 'text-ink' }
    case 'processing':
      return { text: 'Making clips', tone: 'text-accent' }
    case 'complete':
      return { text: 'Clips made', tone: 'text-accent' }
    case 'failed':
      return { text: 'Failed', tone: 'text-error' }
    case 'cancelled':
      return { text: 'Cancelled', tone: 'text-muted' }
    case 'skipped':
      return { text: 'Skipped', tone: 'text-muted' }
    default:
      return { text: 'Problem', tone: 'text-error' }
  }
}

/** Per-platform delivery counts, so twenty clips read as one line each. */
function deliverySummary(item: WatchItem): {
  platform: string
  published: number
  waiting: number
  failed: number
  skipped: number
  errors: string[]
}[] {
  const by = new Map<string, ReturnType<typeof deliverySummary>[number]>()
  for (const d of item.deliveries ?? []) {
    const row = by.get(d.platform) ?? {
      platform: d.platform,
      published: 0,
      waiting: 0,
      failed: 0,
      skipped: 0,
      errors: []
    }
    if (d.state === 'published') row.published += 1
    else if (d.state === 'failed') {
      row.failed += 1
      if (d.error && !row.errors.includes(d.error)) row.errors.push(d.error)
    } else if (d.state === 'skipped') row.skipped += 1
    else row.waiting += 1
    by.set(d.platform, row)
  }
  return [...by.values()]
}

function ItemRow({
  item,
  busy,
  act,
  onOpenInStudio
}: {
  item: WatchItem
  busy: boolean
  act: (fn: () => Promise<unknown>) => Promise<void>
  onOpenInStudio?: (videoId: string) => void
}): JSX.Element {
  const { text, tone } = statusText(item)
  const deliveries = deliverySummary(item)
  const anyFailed = deliveries.some((d) => d.failed > 0 || d.skipped > 0)
  const when = item.published_at || item.detected_at

  // The status already says it for the back catalogue; its reason would
  // only repeat it on every row.
  let detail = item.status === 'earlier' ? '' : item.reason
  if (item.status === 'queued') {
    detail = item.queue_paused
      ? t('The queue is stopped. Press Start queue on the Queue page.')
      : item.waiting_behind
        ? `${item.waiting_behind} ${t('ahead of it')}`
        : ''
  } else if (item.status === 'processing' && item.progress?.percent != null) {
    detail = `${item.progress.percent}% · ${item.progress.label ?? ''}`
  } else if (item.status === 'failed') {
    detail = item.retry_at
      ? `${t('Trying again at')} ${clockTime(item.retry_at)} (${t('retry')} ${item.retries + 1} ${t('of')} 2)`
      : item.details || ''
  } else if (item.status === 'complete') {
    detail = `${item.clips ?? 0} ${item.clips === 1 ? t('clip') : t('clips')}`
  }

  const small = 'btn-ghost !px-2 !py-1 text-xs'
  return (
    <div className="rounded-lg bg-raised/40 px-3 py-2 space-y-1.5">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="min-w-0 flex-1">
          <button
            className="text-sm font-medium text-left hover:text-accent truncate max-w-full block"
            title={item.url}
            onClick={() => void window.studio?.openExternal(item.url)}
          >
            {item.title || item.video_id}
          </button>
          <p className="text-xs text-muted">
            {when ? relative(when) : ''}
            <span className={`ml-2 ${tone}`}>{t(text)}</span>
            {detail && <span className="ml-2">{t(detail)}</span>}
          </p>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          {(item.status === 'earlier' || item.status === 'skipped' || item.status === 'error') && (
            <button
              className={small}
              disabled={busy}
              onClick={() => void act(() => api.clipWatchItem(item.id))}
            >
              {t('Clip this')}
            </button>
          )}
          {(item.status === 'waiting_for_video' || item.status === 'waiting_for_queue') && (
            <button
              className={small}
              disabled={busy}
              onClick={() => void act(() => api.skipWatchItem(item.id))}
            >
              {t('Skip')}
            </button>
          )}
          {item.status === 'failed' && (
            <button
              className={small}
              onClick={() => window.dispatchEvent(new CustomEvent('open-queue'))}
            >
              {t('Open queue')}
            </button>
          )}
          {item.status === 'complete' && onOpenInStudio && (
            <button className={small} onClick={() => onOpenInStudio(item.video_id)}>
              {t('Open in editor')}
            </button>
          )}
        </div>
      </div>

      {item.status === 'complete' && (
        <div className="text-xs space-y-1">
          {item.publish_state === 'ask' && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-warn">{t('Ready to publish.')}</span>
              <button
                className="btn-accent !px-2 !py-1 text-xs"
                disabled={busy}
                onClick={() => void act(() => api.publishWatchItem(item.id))}
              >
                {t('Publish')}
              </button>
              <button
                className={small}
                disabled={busy}
                onClick={() => void act(() => api.skipWatchItem(item.id))}
              >
                {t("Don't publish")}
              </button>
            </div>
          )}
          {item.publish_state === 'publishing' &&
            (item.publish_retry_at > Date.now() / 1000 ? (
              <span className="text-warn">
                {t('Publishing did not start.')} {t('Trying again at')}{' '}
                {clockTime(item.publish_retry_at)} ({t('attempt')} {item.publish_attempts + 1}{' '}
                {t('of')} 7)
              </span>
            ) : (
              <span className="text-accent">{t('Publishing…')}</span>
            ))}
          {item.publish_state === 'done' && item.publish_retry_at > 0 && (
            <span className="text-muted">
              {t('Sending the rejected ones again at')} {clockTime(item.publish_retry_at)}
            </span>
          )}
          {item.source_freed === 1 && (
            <span className="text-muted block">{t('Download deleted to save space.')}</span>
          )}
          {item.publish_state === 'off' && deliveries.length === 0 && (
            <span className="text-muted">{t('Not published.')}</span>
          )}
          {item.publish_error && <p className="text-warn">{item.publish_error}</p>}
          {deliveries.length > 0 && (
            <div className="flex gap-x-4 gap-y-1 flex-wrap items-center">
              {deliveries.map((d) => (
                <span key={d.platform} title={d.errors.join('\n')}>
                  <span className="font-medium">{platformLabel(d.platform)}</span>
                  {d.published > 0 && <span className="text-accent"> ✓ {d.published}</span>}
                  {d.waiting > 0 && <span className="text-muted"> ⏳ {d.waiting}</span>}
                  {d.failed > 0 && <span className="text-error"> ✗ {d.failed}</span>}
                  {d.skipped > 0 && <span className="text-warn"> – {d.skipped}</span>}
                </span>
              ))}
              {anyFailed && item.publish_state === 'done' && (
                <button
                  className={small}
                  disabled={busy}
                  onClick={() => void act(() => api.publishWatchItem(item.id))}
                  title={t('Send again only the ones that failed.')}
                >
                  {t('Retry failed')}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

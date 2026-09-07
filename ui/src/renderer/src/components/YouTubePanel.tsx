import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { Clip, StudioEvent } from '../lib/types'
import { useEvents } from '../lib/useEvents'
import {
  describeInstant,
  localInputToUtc,
  studioUrl,
  watchUrl,
  type PublishRecord,
  type YouTubeAccount,
  type YouTubeStatus
} from '../lib/youtube'
import YouTubeMetadataForm, { type Metadata } from './YouTubeMetadataForm'
import YouTubeSchedule from './YouTubeSchedule'
import YouTubeThumbnail from './YouTubeThumbnail'

/** Publishing, inside the editor.
 *
 *  The clip's own project is never touched by any of this. When there are
 *  unsaved edits the publish carries a render request, which runs through the
 *  SAME job the Apply edits button uses — so the uploaded video is exactly
 *  what the preview showed, and the timeline stays editable afterwards.
 */

interface Props {
  clip: Clip
  status: YouTubeStatus
  /** Unsaved timeline edits, if any. Passing these makes the publish render first. */
  pendingRender: { start?: number; end?: number; render_opts: Record<string, unknown> } | null
  duration: number
  currentTime: number
  onOpenSettings: () => void
}

interface Progress {
  fraction: number
  message: string
  phase: string
}

export default function YouTubePanel({
  clip,
  status,
  pendingRender,
  duration,
  currentTime,
  onOpenSettings
}: Props): JSX.Element {
  const settings = status.settings
  const [metadata, setMetadata] = useState<Metadata>(() => ({
    title: (clip.title || clip.hook || '').slice(0, 100),
    description: clip.description || '',
    tags: (clip.hashtags || []).map((h) => h.replace(/^#/, '')),
    category_id: settings?.category_id ?? '22',
    default_language: null,
    // No default: YouTube requires an explicit answer, and choosing on the
    // user's behalf is exactly the kind of guess that gets channels in trouble.
    made_for_kids: null,
    contains_synthetic_media: false,
    license: 'youtube',
    embeddable: true,
    public_stats_viewable: true,
    notify_subscribers: settings?.notify_subscribers ?? true,
    playlist_id: null
  }))
  const [privacy, setPrivacy] = useState(settings?.privacy ?? 'public')
  const [scheduledAt, setScheduledAt] = useState('')
  const [thumbnail, setThumbnail] = useState<string | null>(null)
  const accounts: YouTubeAccount[] = status.accounts ?? []
  const [channelId, setChannelId] = useState<string>(
    () => accounts.find((a) => a.default)?.id ?? accounts[0]?.id ?? ''
  )

  const [record, setRecord] = useState<PublishRecord | null>(null)
  const [jobId, setJobId] = useState<number | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [result, setResult] = useState<StudioEvent | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    api
      .clipPublishStatus(clip.id)
      .then((r) => {
        setRecord(r.upload)
        if (r.job && (r.job.status === 'queued' || r.job.status === 'running')) {
          setJobId(r.job.id)
        }
      })
      .catch(() => {
        /* not published yet, or the feature was just switched off */
      })
  }, [clip.id])

  useEffect(refresh, [refresh])

  useEvents((e: StudioEvent) => {
    if (e.type !== 'publish' || e.clip_id !== clip.id) return
    if (e.terminal) {
      setProgress(null)
      setBusy(false)
      setJobId(null)
      setResult(e)
      refresh()
      return
    }
    setProgress({
      fraction: e.fraction ?? 0,
      message: e.message ?? '',
      phase: e.phase ?? ''
    })
  })

  const scheduling = scheduledAt !== ''
  const publishAt = scheduling ? localInputToUtc(scheduledAt) : null
  const ready =
    metadata.title.trim().length > 0 &&
    metadata.made_for_kids !== null &&
    (!scheduling || publishAt !== null)

  const buttonLabel = useMemo(() => {
    if (busy) return t('Working…')
    if (scheduling) return t('Schedule on YouTube')
    if (pendingRender) return t('Apply edits & upload')
    return t('Upload now')
  }, [busy, scheduling, pendingRender])

  const publish = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    setResult(null)
    try {
      const { publish_job_id } = await api.publishClip(clip.id, {
        title: metadata.title,
        description: metadata.description,
        tags: metadata.tags,
        category_id: metadata.category_id,
        privacy: scheduling ? 'private' : privacy,
        publish_at: publishAt,
        made_for_kids: metadata.made_for_kids === true,
        contains_synthetic_media: metadata.contains_synthetic_media,
        embeddable: metadata.embeddable,
        public_stats_viewable: metadata.public_stats_viewable,
        license: metadata.license,
        default_language: metadata.default_language,
        notify_subscribers: metadata.notify_subscribers,
        playlist_id: metadata.playlist_id,
        // The backend looks the chosen thumbnail up from the clip id; it no
        // longer accepts a path, so this only says whether to use one.
        thumbnail: Boolean(thumbnail),
        channel_id: channelId || null,
        render_first: pendingRender ?? null
      })
      setJobId(publish_job_id)
      setProgress({ fraction: 0.01, message: t('Queued'), phase: 'prepare' })
    } catch (e) {
      setNotice(String(e).replace(/^Error:\s*/, ''))
      setBusy(false)
    }
  }

  const cancel = async (): Promise<void> => {
    if (jobId === null) return
    try {
      await api.cancelPublish(jobId)
    } catch {
      /* it may have finished in the meantime; the event stream will say */
    }
  }

  // ---- not connected ----------------------------------------------------

  if (!status.connected) {
    return (
      <div className="border border-raised/60 rounded-lg p-3 space-y-2">
        <p className="text-sm font-medium">{t('YouTube')}</p>
        <p className="text-xs text-muted">
          {status.has_client
            ? t('Connect your YouTube channel to publish from here.')
            : t('Add your Google API key in Settings to publish from here.')}
        </p>
        <button className="btn-accent w-full" onClick={onOpenSettings}>
          {t('Set up YouTube publishing')}
        </button>
      </div>
    )
  }

  // ---- in flight --------------------------------------------------------

  if (progress) {
    const pct = Math.round(progress.fraction * 100)
    return (
      <div className="border border-raised/60 rounded-lg p-3 space-y-3">
        <p className="text-sm font-medium">{progress.message || t('Publishing…')}</p>
        <div
          className="h-2 rounded-full bg-raised overflow-hidden"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={t('Upload progress')}
        >
          <div
            className="h-full bg-accent rounded-full transition-[width] duration-500"
            style={{ width: `${Math.max(2, pct)}%` }}
          />
        </div>
        <p className="text-xs text-muted tabular-nums">{pct}%</p>
        <button className="btn-ghost w-full !py-1.5 text-xs" onClick={cancel}>
          {t('Cancel upload')}
        </button>
        <p className="text-[11px] text-muted">
          {t('You can keep editing other clips while this uploads.')}
        </p>
      </div>
    )
  }

  // ---- the form ---------------------------------------------------------

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2 text-xs">
        {accounts.length > 1 ? (
          <label className="flex items-center gap-2 min-w-0">
            <span className="text-muted shrink-0">{t('Publishing to')}</span>
            <select
              className="input !py-1 !text-xs"
              value={channelId}
              disabled={busy}
              onChange={(e) => setChannelId(e.target.value)}
              aria-label={t('Which channel to publish to')}
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.handle || a.title}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="text-muted truncate">
            {t('Publishing to')}{' '}
            <span className="text-ink font-medium">
              {accounts[0]?.handle ||
                accounts[0]?.title ||
                status.channel?.handle ||
                status.channel?.title ||
                t('your channel')}
            </span>
          </span>
        )}
        {status.quota && (
          <span className="text-muted tabular-nums" title={t('Uploads left today on your API key')}>
            {status.quota.remaining}/{status.quota.uploads_limit}
          </span>
        )}
      </div>

      {record && <PublishedBanner record={record} />}
      {result && <ResultBanner event={result} />}

      <YouTubeMetadataForm
        value={metadata}
        onChange={(patch) => setMetadata((m) => ({ ...m, ...patch }))}
        region={settings?.region ?? 'US'}
        playlistsAvailable={Boolean(status.playlists_available)}
        disabled={busy}
      />

      <YouTubeThumbnail
        clipId={clip.id}
        duration={duration}
        currentTime={currentTime}
        value={thumbnail}
        onChange={setThumbnail}
        disabled={busy}
      />

      <YouTubeSchedule
        privacy={privacy}
        scheduledAt={scheduledAt}
        onChange={(patch) => {
          if (patch.privacy !== undefined) setPrivacy(patch.privacy)
          if (patch.scheduledAt !== undefined) setScheduledAt(patch.scheduledAt)
        }}
        disabled={busy}
      />

      {pendingRender && (
        <p className="text-[11px] text-warn">
          {t('You have unsaved edits. They will be rendered into the video before it uploads.')}
        </p>
      )}

      <button className="btn-accent w-full disabled:opacity-40" disabled={!ready || busy} onClick={publish}>
        {buttonLabel}
      </button>
      {notice && <p className="text-xs text-error">{notice}</p>}

      <details className="border border-raised/60 rounded-lg">
        <summary className="px-3 py-2 text-xs cursor-pointer hover:bg-raised/40 rounded-lg">
          {t("What Clips Kitty can't set")}
        </summary>
        <div className="p-3 pt-0 text-[11px] text-muted space-y-1">
          <p>
            {t(
              'YouTube does not let any app set these, so there are no controls for them here: monetization and ad breaks, paid promotion, end screens and cards, comment settings, age restriction, Premieres, and Shorts remix permissions.'
            )}
          </p>
          <p>{t('Set them in YouTube Studio after the video is up.')}</p>
        </div>
      </details>
    </div>
  )
}

function PublishedBanner({ record }: { record: PublishRecord }): JSX.Element {
  const scheduled = Boolean(record.publish_at)
  const locked = record.state === 'locked_private'
  const bad = locked || record.state === 'rejected' || record.state === 'failed'

  return (
    <div
      className={`border rounded-lg p-3 space-y-1 text-xs ${
        bad ? 'border-warn/60 bg-warn/10' : 'border-raised/60'
      }`}
    >
      <p className="font-medium">
        {locked
          ? `⚠ ${t('Locked to private by YouTube')}`
          : scheduled
            ? `◷ ${t('Scheduled')}`
            : `✓ ${t('Published')}`}
      </p>
      {scheduled && !locked && <p className="text-muted">{describeInstant(record.publish_at)}</p>}
      {record.error && <p className="text-warn">{record.error}</p>}
      {locked && (
        <p className="text-muted">
          {t(
            'This happens when the Google Cloud project has not passed YouTube’s free API audit. It cannot be changed in Studio — the video has to be uploaded again from an audited project.'
          )}
        </p>
      )}
      <div className="flex gap-3 pt-1">
        <button
          className="text-accent hover:underline"
          onClick={() => window.studio.openExternal(watchUrl(record.youtube_id))}
        >
          {t('Open on YouTube')}
        </button>
        <button
          className="text-accent hover:underline"
          onClick={() => window.studio.openExternal(studioUrl(record.youtube_id))}
        >
          {t('Manage in YouTube Studio')}
        </button>
      </div>
    </div>
  )
}

function ResultBanner({ event }: { event: StudioEvent }): JSX.Element | null {
  if (event.terminal === 'failed') {
    return <p className="text-xs text-error border border-error/40 rounded-lg p-2">{event.error}</p>
  }
  if (event.terminal === 'cancelled') {
    return <p className="text-xs text-muted">{event.message}</p>
  }
  if (!event.warnings?.length) return null
  return (
    <div className="border border-warn/60 bg-warn/10 rounded-lg p-2 space-y-1">
      {event.warnings.map((w, i) => (
        <p key={i} className="text-[11px] text-warn">
          {w}
        </p>
      ))}
    </div>
  )
}

import { useState } from 'react'
import { api } from '../../lib/api'
import type { CaptionStyle, JobOptions } from '../../lib/types'
import { DEFAULT_CAPTION_STYLE } from '../CaptionStyleControls'
import { watermarkSelection } from '../WatermarkCard'
import { Folder } from '../icons'
import { t } from '../../lib/i18n'

const REASONS: Record<string, string> = {
  already_processed: 'already processed — tick “Process again” to redo it',
  already_queued: 'already in the queue',
  unrecognized: 'not a link Clips Studio recognises'
}

/** Add several videos at once.
 *
 *  Options here seed EVERY video in the batch. Per-video differences are made
 *  afterwards on the queue row itself — asking for ten option sets up front
 *  would be a form to fill in, and the point of the queue is to start a batch
 *  quickly and walk away. */
export default function AddVideos({ onAdded }: { onAdded: () => void }): JSX.Element {
  const [text, setText] = useState('')
  const [files, setFiles] = useState<string[]>([])
  const [channel, setChannel] = useState(localStorage.getItem('upload-channel') ?? '')
  const [captions, setCaptions] = useState(localStorage.getItem('generate-captions') !== 'false')
  const [longClips, setLongClips] = useState(localStorage.getItem('generate-long-clips') === 'true')
  const [podcast, setPodcast] = useState(localStorage.getItem('generate-podcast') === 'true')
  const [longform, setLongform] = useState(localStorage.getItem('generate-longform') === 'true')
  const [longformMode] = useState(localStorage.getItem('generate-longform-mode') ?? 'short_clips')
  const [watermark, setWatermark] = useState(watermarkSelection().enabled)
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ added: number; skipped: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const urls = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
  const count = urls.length + files.length

  const options = (): JobOptions => {
    const o: JobOptions = { captions, force }
    if (longClips) o.long_clips = true
    if (podcast) o.podcast = true
    if (longform) o.longform = { mode: longformMode }
    if (watermark) {
      const { profileId } = watermarkSelection()
      if (profileId) o.watermark_profile_id = profileId
    }
    let style: Required<CaptionStyle> = { ...DEFAULT_CAPTION_STYLE }
    try {
      style = { ...style, ...JSON.parse(localStorage.getItem('generate-caption-style') ?? '{}') }
    } catch {
      /* a corrupt saved style should not stop a batch — the default is fine */
    }
    o.caption_style = style
    return o
  }

  const add = async (): Promise<void> => {
    if (count === 0) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const opts = options()
      const skipped: string[] = []
      let added = 0

      if (urls.length > 0) {
        const res = await api.createJobsBatch(urls, opts)
        added += res.created.length
        for (const s of res.skipped) {
          skipped.push(`${s.url} — ${REASONS[s.reason] ?? s.reason}`)
        }
      }

      // Local files go one at a time: each is remuxed or transcoded into the
      // pipeline's layout on the way in, which is real work per file rather
      // than a row insert.
      for (const path of files) {
        const base = path.split(/[\\/]/).pop() ?? path
        try {
          await api.addLocalVideo({
            path,
            title: base.replace(/\.[^.]+$/, ''),
            channel,
            captions,
            captionStyle: opts.caption_style,
            longClips,
            podcast
          })
          added += 1
        } catch (e) {
          skipped.push(`${base} — ${e instanceof Error ? e.message : String(e)}`)
        }
      }

      setResult({ added, skipped })
      if (added > 0) {
        setText('')
        setFiles([])
        onAdded()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const toggle = (
    label: string,
    hint: string,
    checked: boolean,
    onChange: (v: boolean) => void,
    title: string
  ): JSX.Element => (
    <label className="flex items-center gap-2 cursor-pointer text-sm" title={title}>
      <input
        type="checkbox"
        className="size-4 accent-[#38BDF8]"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {t(label)} {hint && <span className="text-muted">{t(hint)}</span>}
    </label>
  )

  return (
    <div className="card space-y-3">
      <div>
        <label htmlFor="queue-urls" className="label">
          {t('Paste video links — one per line')}
        </label>
        <textarea
          id="queue-urls"
          className="input mt-1 h-28 font-mono text-xs leading-relaxed"
          placeholder={'https://youtube.com/watch?v=…\nhttps://twitch.tv/videos/…\nhttps://kick.com/video/…'}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <button
          className="btn-ghost"
          onClick={async () => {
            const picked = await window.studio.pickVideoFiles()
            if (picked && picked.length > 0) setFiles((f) => [...new Set([...f, ...picked])])
          }}
        >
          <Folder className="mr-1.5" />
          {t('Add files from this computer')}
        </button>
        {files.length > 0 && (
          <>
            <span className="text-sm text-muted">
              {files.length} {t('file(s) selected')}
            </span>
            <button className="btn-ghost !px-2 !py-1" onClick={() => setFiles([])}>
              {t('Clear')}
            </button>
            <div className="w-full">
              <label className="label">{t('Creator / channel name')}</label>
              <input
                className="input mt-1 max-w-sm"
                placeholder="e.g. YourChannel"
                value={channel}
                onChange={(e) => {
                  setChannel(e.target.value)
                  localStorage.setItem('upload-channel', e.target.value)
                }}
              />
            </div>
          </>
        )}
      </div>

      <div className="border-t border-raised/60 pt-3 space-y-2">
        <p className="label">
          {t('Starting settings for these videos — change any of them per video afterwards')}
        </p>
        <div className="flex gap-x-5 gap-y-2 flex-wrap">
          {toggle('Captions', '', captions, setCaptions, 'Burn captions into the clips')}
          {toggle('60s+', '(TikTok monetization)', longClips, setLongClips,
            'TikTok monetization requires videos over 1 minute. On: clips run 61-180s.')}
          {toggle('Podcast', '(multi-cam)', podcast, setPodcast,
            'For multi-camera podcasts: each shot gets one steady crop on whoever is talking.')}
          {toggle('Longform', '(16:9)', longform, setLongform,
            'Horizontal 1920x1080 outputs using the same AI.')}
          {toggle('Watermark', '(branding)', watermark, setWatermark,
            'Burn your logo / channel handle into every clip.')}
          {toggle('Process again', '', force, setForce,
            'Queue these even if they have been processed before — new clips are added alongside the old ones.')}
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <button className="btn-accent" onClick={add} disabled={busy || count === 0}>
          {busy ? t('Adding…') : `${t('Add to queue')}${count > 0 ? ` (${count})` : ''}`}
        </button>
        {result && (
          <span className="text-sm">
            <span className="text-accent">
              {result.added} {t('added')}
            </span>
            {result.skipped.length > 0 && (
              <span className="text-muted">
                {' '}· {result.skipped.length} {t('skipped')}
              </span>
            )}
          </span>
        )}
      </div>

      {/* One bad link never blocks the rest of the batch — the others are
          queued and the failures are listed so they can be fixed and re-added. */}
      {result && result.skipped.length > 0 && (
        <ul className="text-xs text-muted space-y-0.5 border-t border-raised/60 pt-2">
          {result.skipped.map((s) => (
            <li key={s} className="break-words">
              {s}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="text-sm text-error">{error}</p>}
    </div>
  )
}

import { useState } from 'react'
import { api } from '../../lib/api'
import type { CaptionStyle, JobOptions, QueueJob } from '../../lib/types'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from '../CaptionStyleControls'
import { watermarkSelection } from '../WatermarkCard'
import { t } from '../../lib/i18n'

/** Settings for ONE queued video.
 *
 *  Every queued job carries its own snapshot of these options, so changing
 *  them here cannot reach any other video in the queue — that isolation is
 *  the whole reason the settings live on the job row rather than in the
 *  app-wide preferences the Generate bar writes to.
 *
 *  Editable only while a video is still waiting. Once the worker has claimed
 *  it, changing the configuration halfway would render some of its clips one
 *  way and the rest another, so the running item shows its settings read-only.
 *
 *  One Save posts the whole panel. Caption style alone has eight fields, and
 *  a request per keystroke would be absurd. */
export default function QueueItemSettings({
  job,
  onSaved
}: {
  job: QueueJob
  onSaved: () => void
}): JSX.Element {
  const s = job.settings ?? {}
  const [captions, setCaptions] = useState(s.captions !== false)
  const [longClips, setLongClips] = useState(Boolean(s.long_clips))
  const [podcast, setPodcast] = useState(Boolean(s.podcast))
  const [longform, setLongform] = useState(Boolean(s.longform))
  const [longformMode, setLongformMode] = useState(s.longform?.mode ?? 'short_clips')
  const [watermark, setWatermark] = useState(Boolean(s.watermark_profile_id))
  const [style, setStyle] = useState<Required<CaptionStyle>>({
    ...DEFAULT_CAPTION_STYLE,
    ...(s.caption_style ?? {})
  })
  const [styleOpen, setStyleOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const setStyleField = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void =>
    setStyle((prev) => ({ ...prev, [key]: value }))

  const save = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      // `clear` is how an option goes back OFF: an absent field means
      // "unchanged" on the server, so switching a toggle off has to say so.
      const clear: string[] = []
      const patch: Partial<JobOptions> & { clear?: string[] } = { captions }
      if (longClips) patch.long_clips = true
      else clear.push('long_clips')
      if (podcast) patch.podcast = true
      else clear.push('podcast')
      if (longform) patch.longform = { mode: longformMode }
      else clear.push('longform')
      if (watermark) {
        // Which branding profile is a single app-wide choice (Generate bar /
        // Creators tab); this toggle only decides whether THIS video uses it.
        const { profileId } = watermarkSelection()
        if (profileId) patch.watermark_profile_id = profileId
        else clear.push('watermark_profile_id')
      } else clear.push('watermark_profile_id')
      patch.caption_style = style
      patch.clear = clear
      await api.patchJob(job.id, patch)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
      onSaved()
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
    <div className="mt-3 pt-3 border-t border-raised/60 space-y-3">
      <p className="label">{t('Settings for this video only')}</p>
      <div className="flex gap-x-5 gap-y-2 flex-wrap">
        {toggle('Captions', '', captions, setCaptions, 'Burn captions into this video’s clips')}
        {toggle(
          '60s+',
          '(TikTok monetization)',
          longClips,
          setLongClips,
          'TikTok monetization requires videos over 1 minute. On: clips run 61-180s.'
        )}
        {toggle(
          'Podcast',
          '(multi-cam)',
          podcast,
          setPodcast,
          'For multi-camera podcasts: each shot gets one steady crop on whoever is talking.'
        )}
        {toggle(
          'Longform',
          '(16:9)',
          longform,
          setLongform,
          'Horizontal 1920x1080 outputs using the same AI.'
        )}
        {toggle(
          'Watermark',
          '(branding)',
          watermark,
          setWatermark,
          'Burn your logo / channel handle into every clip of this video.'
        )}
      </div>

      {longform && (
        <div className="flex items-center gap-3 flex-wrap">
          <p className="label shrink-0">{t('Longform output')}</p>
          <select
            className="input !w-64"
            value={longformMode}
            onChange={(e) => setLongformMode(e.target.value)}
            aria-label="Longform output type"
          >
            <option value="short_clips">Short Clips (up to 60s, horizontal)</option>
            <option value="clips_140">Clips (up to 140s — X/Twitter)</option>
            <option value="highlights">Highlights (best-of, 8-20 min by quality)</option>
            <option value="edited_stream">Edited Stream (downtime removed)</option>
          </select>
        </div>
      )}

      <button
        className="btn-ghost"
        onClick={() => setStyleOpen(!styleOpen)}
        aria-expanded={styleOpen}
        disabled={!captions}
      >
        {t('Caption style')} {styleOpen ? '▾' : '▸'}
      </button>
      {styleOpen && captions && (
        <CaptionStyleControls idPrefix={`q${job.id}`} style={style} onChange={setStyleField} />
      )}

      <div className="flex items-center gap-3">
        <button className="btn-accent" onClick={save} disabled={busy}>
          {busy ? t('Saving…') : t('Save settings')}
        </button>
        {saved && <span className="text-sm text-accent">{t('Saved')}</span>}
        {error && <span className="text-sm text-error">{error}</span>}
      </div>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { CaptionStyle, JobOptions, SportOption } from '../../lib/types'
import CaptionStyleControls, {
  DEFAULT_CAPTION_STYLE,
  PostStyleControls
} from '../CaptionStyleControls'
import SportFields from '../SportFields'
import { sportForVertical, startingSport, useSports, verticalSports, verticalValue } from '../../lib/sports'
import { watermarkSelection } from '../WatermarkCard'
import { t } from '../../lib/i18n'

/** Settings for ONE queued video, or for every video a watched channel posts.
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
  onSaved,
  save: saveTo,
  heading = 'Settings for this video only',
  autoSave = false
}: {
  job: { id: number; settings: JobOptions }
  onSaved: () => void
  /** Where the options go. A queued job by default; a watched channel passes
   *  its own, so both edit the same options through the same controls. */
  save?: (patch: Partial<JobOptions> & { clear?: string[] }) => Promise<unknown>
  heading?: string
  /** Save each change as it is made, with no Save button. For a watched
   *  channel, where a second Save button was one too many: captions were
   *  unticked, the other panel was saved, and clips came out with captions. */
  autoSave?: boolean
}): JSX.Element {
  const s = job.settings ?? {}
  const [captions, setCaptions] = useState(s.captions !== false)
  const [longClips, setLongClips] = useState(Boolean(s.long_clips))
  const [podcast, setPodcast] = useState(Boolean(s.podcast))
  const [verticalLive, setVerticalLive] = useState(Boolean(s.vertical_live))
  const [gamingScoring, setGamingScoring] = useState(Boolean(s.gaming_scoring))
  const [gaming, setGaming] = useState(Boolean(s.gaming))
  const [sport, setSport] = useState<SportOption | null>(s.sport ?? null)
  const sports = useSports() ?? []
  const [longform, setLongform] = useState(Boolean(s.longform))
  const [longformMode, setLongformMode] = useState(s.longform?.mode ?? 'short_clips')
  const [longformShorts, setLongformShorts] = useState(Boolean(s.longform?.shorts))
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
  // Every output 16:9: the post style draws nothing on those.
  const longformOnly = longform && !longformShorts

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
      if (verticalLive) patch.vertical_live = true
      else clear.push('vertical_live')
      if (verticalLive && gamingScoring && !sport) patch.gaming_scoring = true
      else clear.push('gaming_scoring')
      if (gaming) patch.gaming = true
      else clear.push('gaming', 'gaming_layout', 'gaming_remember')
      if (sport) patch.sport = sport
      else clear.push('sport')
      if (longform) patch.longform = { mode: longformMode, ...(longformShorts ? { shorts: true } : {}) }
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
      await (saveTo ? saveTo(patch) : api.patchJob(job.id, patch))
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  // Saves a moment after the last change, so a burst of clicks is one save,
  // and only when something actually differs from what was last saved.
  const current = JSON.stringify([
    captions,
    longClips,
    podcast,
    verticalLive,
    gamingScoring,
    gaming,
    sport,
    longform,
    longformMode,
    longformShorts,
    watermark,
    style
  ])
  const lastSaved = useRef(current)
  useEffect(() => {
    if (!autoSave || current === lastSaved.current) return
    const id = setTimeout(() => {
      lastSaved.current = current
      void save()
    }, 500)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSave, current])

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
      <p className="label">{t(heading)}</p>
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
          'Longform',
          '(16:9)',
          longform,
          (on) => {
            setLongform(on)
            if (on) {
              setVerticalLive(false)
              setGaming(false)
            }
          },
          'Horizontal 1920x1080 outputs using the same AI.'
        )}
        {toggle(
          'Vertical Live',
          '(9:16)',
          verticalLive,
          (on) => {
            // Keeps the live's own 9:16 layout, so the options that reframe
            // or change the shape go off with it.
            setVerticalLive(on)
            if (on) {
              setPodcast(false)
              setLongform(false)
              setGaming(false)
            }
          },
          'A livestream that was already vertical when it was streamed: keeps its own 9:16 layout, no face tracking or reframing. For a watched channel, videos with no vertical version are skipped.'
        )}
        {toggle(
          'Podcast',
          '(multi-cam)',
          podcast,
          (on) => {
            setPodcast(on)
            if (on) {
              setVerticalLive(false)
              setGaming(false)
              setSport(null)
            }
          },
          'For multi-camera podcasts: each shot gets one steady crop on whoever is talking.'
        )}
        {toggle(
          'Gaming / Reaction',
          '(split-screen)',
          gaming,
          (on) => {
            // Splits the webcam from the game: the other layout modes go off.
            setGaming(on)
            if (on) {
              setPodcast(false)
              setLongform(false)
              setVerticalLive(false)
              setSport(null)
            }
          },
          'Game streams and reaction videos: the streamer’s webcam in the top half, the game or the video they’re reacting to in the bottom half. With no webcam, the game fills the screen.'
        )}
        {(sports.length > 0 || sport) &&
          toggle(
            'Sports',
            '(match)',
            Boolean(sport),
            (on) => {
              // A match is scored as a match: not as a podcast or a game stream.
              setSport(on ? (sport ?? startingSport(sports)) : null)
              if (on) {
                setPodcast(false)
                setGaming(false)
                setGamingScoring(false)
              }
            },
            'A match or a game (Soccer, Basketball): its moments from the crowd, the commentary and the scoreboard, one clip per moment, and a 9:16 crop that follows the play.'
          )}
        {toggle(
          'Watermark',
          '(branding)',
          watermark,
          setWatermark,
          'Burn your logo / channel handle into every clip of this video.'
        )}
      </div>

      {sport && sports.length > 0 && (
        <div className="flex items-center gap-3 flex-wrap">
          <SportFields value={sport} sports={sports} onChange={setSport} />
        </div>
      )}

      {verticalLive && (
        <div className="flex items-center gap-3 flex-wrap">
          <p className="label shrink-0">{t('Vertical Live content')}</p>
          <select
            className="input !w-72"
            value={verticalValue(sport) ?? (gamingScoring ? 'gaming' : 'standard')}
            onChange={(e) => {
              if (e.target.value.startsWith('sport:')) {
                // A sport is the Sports switch's match scoring, for a match streamed 9:16.
                setSport(sportForVertical(e.target.value, sport, sports))
                setPodcast(false)
                setGaming(false)
                setGamingScoring(false)
              } else {
                setSport(null)
                setGamingScoring(e.target.value === 'gaming')
              }
            }}
            aria-label={t('Vertical Live content')}
            title={t('Gaming / reaction: what you say counts as on any stream, and in-game moments (a kill streak, a boss going down, a goal) and the reactions to them add to it, from chat and your voice, even when you say little. Game characters and people in a video you watch aren’t taken for you.')}
          >
            <option value="standard">{t('Talking / IRL')}</option>
            <option value="gaming">{t('Gaming / reaction')}</option>
            {verticalSports(sports).map((s) => (
              <option key={s.value} value={s.value}>
                {t(s.label)}
              </option>
            ))}
          </select>
        </div>
      )}

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
          <label
            className="flex items-center gap-2 text-sm cursor-pointer"
            title={t(
              'Makes the vertical 9:16 Shorts of this video too, in the same run: the Shorts first, then the 16:9 output. The horizontal clips are marked 16:9.'
            )}
          >
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={longformShorts}
              onChange={(e) => setLongformShorts(e.target.checked)}
            />
            {t('Also make 9:16 Shorts')}
          </label>
        </div>
      )}

      {/* Open with captions off too: the post style still applies then (a
          Highlights card goes on every clip), so it must stay in reach. */}
      <button
        className="btn-ghost"
        onClick={() => setStyleOpen(!styleOpen)}
        aria-expanded={styleOpen}
      >
        {captions ? t('Caption style') : t('Post style')} {styleOpen ? '▾' : '▸'}
      </button>
      {styleOpen && (
        <PostStyleControls
          idPrefix={`q${job.id}`}
          style={style}
          onChange={setStyleField}
          landscape={longformOnly}
          alsoLandscape={longform && longformShorts}
        />
      )}
      {/* Every caption control while any output is 16:9: those clips keep
          the standard look whatever the post style, so a Highlights pick
          must not hide the font, colour and position they burn with. */}
      {styleOpen && captions && (
        <CaptionStyleControls
          idPrefix={`q${job.id}`}
          style={style}
          onChange={setStyleField}
          landscape={longform}
        />
      )}

      <div className="flex items-center gap-3">
        {autoSave ? (
          <span className="text-xs text-muted">
            {busy ? t('Saving…') : t('Changes save as you make them.')}
          </span>
        ) : (
          <button className="btn-accent" onClick={save} disabled={busy}>
            {busy ? t('Saving…') : t('Save settings')}
          </button>
        )}
        {saved && <span className="text-sm text-accent">{t('Saved')}</span>}
        {error && <span className="text-sm text-error">{error}</span>}
      </div>
    </div>
  )
}

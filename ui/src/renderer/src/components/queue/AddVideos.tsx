import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { CaptionStyle, JobOptions } from '../../lib/types'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from '../CaptionStyleControls'
import BrandingEditor, { setWatermarkEnabled, watermarkSelection } from '../WatermarkCard'
import { Folder, Trash } from '../icons'
import { t } from '../../lib/i18n'

const DRAFT_KEY = 'queue-draft'

const REASONS: Record<string, string> = {
  already_processed: 'already processed',
  already_queued: 'already in the queue',
  unrecognized: 'not a link Clips Studio recognises',
  bad_option: 'a setting on this video was rejected',
  queue_full: 'the queue is full — let one finish first'
}

/** One video being set up. Owns its own options object, so editing this
 *  video's switches cannot reach any other. */
interface Slot {
  key: string
  /** A pasted link. Empty when this slot is a local file. */
  url: string
  /** A file on this computer. Null when this slot is a link. */
  path: string | null
  /** Editable name for a local file (a link gets its title from the source). */
  title: string
  options: JobOptions
  /** Why the server refused this one, kept so it can be fixed in place. */
  error?: string
}

type ToggleKey = 'captions' | 'long_clips' | 'podcast' | 'longform' | 'watermark'

/** The switches after Captions. Captions is rendered on its own so the
 *  "Caption style" button can sit immediately beside it, where it belongs —
 *  it configures that switch and nothing else. */
const TOGGLES: { key: ToggleKey; label: string; hint: string; title: string }[] = [
  {
    key: 'long_clips',
    label: '60s+',
    hint: '(TikTok monetization)',
    title:
      'TikTok monetization requires videos over 1 minute. On: this video’s clips run 61-180s. Off: a natural 10-60s.'
  },
  {
    key: 'longform',
    label: 'Longform',
    hint: '(16:9)',
    title:
      'Horizontal 1920x1080 outputs (YouTube, X/Twitter) using the same AI — the vertical Shorts workflow is unchanged.'
  },
  {
    key: 'podcast',
    label: 'Podcast',
    hint: '(multi-cam)',
    title:
      'For multi-camera podcasts (cuts between angles, several people). Frames shot by shot: each camera shot gets one steady crop centered on whoever is talking, and cuts land directly on the speaker’s face — no panning, no split screens. Leave OFF for normal one-camera streams.'
  },
  {
    key: 'watermark',
    label: 'Watermark',
    hint: '(branding)',
    title:
      'Burn your logo / channel handle into every clip of this video. Configure the branding profile below.'
  }
]

function savedStyle(): Required<CaptionStyle> {
  try {
    return {
      ...DEFAULT_CAPTION_STYLE,
      ...JSON.parse(localStorage.getItem('generate-caption-style') ?? '{}')
    }
  } catch {
    return { ...DEFAULT_CAPTION_STYLE } // a corrupt saved style must not block the list
  }
}

/** Starting options for the first row: whatever was last used, so the usual
 *  setup is already there. */
/** Keys seedOptions() restores from. Writing them is remember(), below —
 *  they were read but never written, so every choice was forgotten the moment
 *  the window closed and captions came back ticked however often you unticked
 *  it. */
const PREF = {
  captions: 'generate-captions',
  long_clips: 'generate-long-clips',
  podcast: 'generate-podcast',
  longform: 'generate-longform',
  longform_mode: 'generate-longform-mode'
} as const

function remember(key: ToggleKey, on: boolean, mode?: string): void {
  try {
    if (key === 'captions') localStorage.setItem(PREF.captions, String(on))
    else if (key === 'long_clips') localStorage.setItem(PREF.long_clips, String(on))
    else if (key === 'podcast') localStorage.setItem(PREF.podcast, String(on))
    else if (key === 'longform') {
      localStorage.setItem(PREF.longform, String(on))
      if (mode) localStorage.setItem(PREF.longform_mode, mode)
    }
    // 'watermark' is deliberately absent: WatermarkCard owns its own two keys
    // and writes them itself.
  } catch {
    // A full or blocked localStorage must not stop someone queueing a video.
  }
}

function seedOptions(): JobOptions {
  const wm = watermarkSelection()
  const o: JobOptions = {
    captions: localStorage.getItem(PREF.captions) !== 'false',
    caption_style: savedStyle()
  }
  if (localStorage.getItem(PREF.long_clips) === 'true') o.long_clips = true
  if (localStorage.getItem(PREF.podcast) === 'true') o.podcast = true
  if (localStorage.getItem(PREF.longform) === 'true') {
    o.longform = { mode: localStorage.getItem(PREF.longform_mode) ?? 'short_clips' }
  }
  if (wm.enabled && wm.profileId) o.watermark_profile_id = wm.profileId
  return o
}

let counter = 0
const newKey = (): string => `s${Date.now()}-${counter++}`

function emptySlot(from?: JobOptions): Slot {
  // A new row copies the one above it: a batch usually shares most settings,
  // and every switch is still overridable per video. Copied, not shared.
  return { key: newKey(), url: '', path: null, title: '', options: { ...(from ?? seedOptions()) } }
}

function loadDraft(): Slot[] {
  try {
    const raw = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null')
    if (Array.isArray(raw) && raw.length > 0) {
      return raw.map((s: Slot) => ({ ...s, key: newKey(), error: undefined }))
    }
  } catch {
    /* a corrupt draft is not worth failing over — start clean */
  }
  return [emptySlot()]
}

/** The generate bar: one video or ten, each with its own settings.
 *
 *  Nothing here touches the server until Generate. The list is local state
 *  (plus a saved draft), so a half-built list is not a queue: earlier versions
 *  created job rows as videos were added, which meant the queue began filling —
 *  and under the old auto-start, running — before the user had finished
 *  deciding. Building and committing are separate, and only Generate crosses
 *  that line.
 *
 *  Every row owns its own options object, so toggling video 2 writes video 2
 *  and nothing else. */
export default function AddVideos({ onAdded }: { onAdded?: () => void }): JSX.Element {
  const [slots, setSlots] = useState<Slot[]>(loadDraft)
  const [channel, setChannel] = useState(localStorage.getItem('upload-channel') ?? '')
  const [openStyle, setOpenStyle] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [added, setAdded] = useState<number | null>(null)
  // Links refused only because they have been clipped before. Offered as a
  // prompt rather than a permanent "process again" switch: it is the rare
  // case, and a checkbox that matters once in twenty uses is clutter the
  // other nineteen times.
  const [alreadyDone, setAlreadyDone] = useState<Slot[]>([])
  // Whether a batch is already under way, so this doesn't offer to start
  // something already running. Read here rather than passed in, so it behaves
  // the same on the Dashboard and on the Queue page.
  const [queueRunning, setQueueRunning] = useState(false)
  // Free slots on the server. The list can hold at most this many, so the cap
  // is visible while building rather than a refusal after pressing Generate.
  const [capacity, setCapacity] = useState<number | null>(null)
  const [maxActive, setMaxActive] = useState(5)

  useEffect(() => {
    void api
      .queue()
      .then((q) => {
        setQueueRunning(!q.paused && q.processing.length + q.queued.length > 0)
        setCapacity(q.capacity)
        setMaxActive(q.max_active)
      })
      .catch(() => undefined) // backend not up yet; Generate still works
  }, [added])

  // Keep the draft. Several pasted links must not die to an accidental close.
  useEffect(() => {
    const keep = slots.filter((s) => s.url.trim() || s.path)
    if (keep.length > 0) localStorage.setItem(DRAFT_KEY, JSON.stringify(keep))
    else localStorage.removeItem(DRAFT_KEY)
  }, [slots])

  const ready = slots.filter((s) => s.url.trim() || s.path)
  const hasFiles = ready.some((s) => s.path)
  const wantsWatermark = slots.some((s) => s.options.watermark_profile_id)
  const room = capacity ?? maxActive
  const full = slots.length >= room

  const patch = (key: string, change: Partial<Slot>): void =>
    setSlots((prev) => prev.map((s) => (s.key === key ? { ...s, ...change, error: undefined } : s)))

  /** Merge a few fields into this video's options — only used where nothing
   *  needs removing (longform mode, caption style). */
  const patchOptions = (key: string, change: JobOptions): void =>
    setSlots((prev) =>
      prev.map((s) =>
        s.key === key ? { ...s, options: { ...s.options, ...change }, error: undefined } : s
      )
    )

  /** REPLACE this video's options wholesale.
   *
   *  Switching an option off deletes its key (an absent key is what the
   *  pipeline reads as "default"), and a spread merge cannot express a
   *  deletion — `{...old, ...new}` keeps `podcast: true` when `new` simply
   *  lacks `podcast`. Merging here is why unticking a box did nothing. */
  const replaceOptions = (key: string, options: JobOptions): void =>
    setSlots((prev) => prev.map((s) => (s.key === key ? { ...s, options, error: undefined } : s)))

  const toggle = (o: JobOptions, key: ToggleKey, on: boolean): JobOptions => {
    const next = { ...o }
    if (key === 'captions') next.captions = on
    else if (key === 'long_clips') {
      if (on) next.long_clips = true
      else delete next.long_clips
    } else if (key === 'podcast') {
      if (on) next.podcast = true
      else delete next.podcast
    } else if (key === 'longform') {
      if (on) next.longform = { mode: next.longform?.mode ?? 'short_clips' }
      else delete next.longform
    } else if (key === 'watermark') {
      const { profileId } = watermarkSelection()
      if (on && profileId) next.watermark_profile_id = profileId
      else delete next.watermark_profile_id
      // Ticking with no profile saved cannot do anything — the checkbox is
      // disabled in that case rather than silently refusing to stay ticked.
      setWatermarkEnabled(on && Boolean(profileId))
    }
    remember(key, on, next.longform?.mode)
    return next
  }

  const isOn = (o: JobOptions, key: ToggleKey): boolean => {
    if (key === 'captions') return o.captions !== false
    if (key === 'long_clips') return Boolean(o.long_clips)
    if (key === 'podcast') return Boolean(o.podcast)
    if (key === 'longform') return Boolean(o.longform)
    return Boolean(o.watermark_profile_id)
  }

  const addSlot = (): void =>
    setSlots((prev) => [...prev, emptySlot(prev[prev.length - 1]?.options)])

  const addFiles = async (): Promise<void> => {
    const picked = await window.studio.pickVideoFiles()
    if (!picked || picked.length === 0) return
    setSlots((prev) => {
      const known = new Set(prev.map((s) => s.path))
      const base = prev[prev.length - 1]?.options
      const fresh = picked
        .filter((p) => !known.has(p))
        .map((p) => ({
          key: newKey(),
          url: '',
          path: p,
          title: (p.split(/[\\/]/).pop() ?? p).replace(/\.[^.]+$/, ''),
          options: { ...(base ?? seedOptions()) }
        }))
      const kept = prev.filter((s) => s.url.trim() || s.path)
      return [...kept, ...fresh]
    })
  }

  /** Queue everything, then — and only then — start processing.
   *  `force` re-runs videos that were refused for having been clipped before. */
  const generate = async (force = false, only?: Slot[]): Promise<void> => {
    const list = only ?? ready
    if (list.length === 0) return
    setBusy(true)
    setError(null)
    setAdded(null)
    if (!force) setAlreadyDone([])
    try {
      const failures = new Map<string, string>()
      const done: Slot[] = []
      let ok = 0

      const links = list.filter((s) => !s.path)
      if (links.length > 0) {
        const res = await api.createJobsBatch(
          links.map((s) => ({ url: s.url.trim(), ...s.options, ...(force ? { force: true } : {}) }))
        )
        ok += res.created.length
        for (const s of res.skipped) {
          if (s.reason === 'already_processed') {
            const slot = links.find((l) => l.url.trim() === s.url)
            if (slot) done.push(slot)
          }
          const detail = REASONS[s.reason] ?? s.reason
          failures.set(s.url, s.detail ? `${detail}: ${s.detail}` : detail)
        }
      }

      // Local files go one at a time: each is remuxed or transcoded into the
      // pipeline's layout on the way in, which is real work per file.
      for (const slot of list.filter((s) => s.path)) {
        try {
          await api.addLocalVideo({
            path: slot.path as string,
            title: slot.title,
            channel: channel.trim(),
            ...slot.options,
            ...(force ? { force: true } : {})
          })
          ok += 1
        } catch (e) {
          failures.set(slot.path as string, e instanceof Error ? e.message : String(e))
        }
      }

      // Only now does anything begin.
      if (ok > 0 && !queueRunning) await api.resumeQueue()

      setAdded(ok)
      setAlreadyDone(done)
      // Rejected videos stay in the list, with their reason, so they can be
      // fixed rather than re-typed. Everything that went through is cleared.
      const left = slots
        .filter((s) => {
          const id = s.path ?? s.url.trim()
          return id !== '' && failures.has(id)
        })
        .map((s) => ({ ...s, error: failures.get(s.path ?? s.url.trim()) }))
      setSlots(left.length > 0 ? left : [emptySlot()])
      if (left.length === 0) localStorage.removeItem(DRAFT_KEY)
      if (ok > 0) onAdded?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="card space-y-2">
        {slots.map((slot, n) => (
          <div key={slot.key}>
            <div className="flex gap-3 items-center flex-wrap">
              {slot.path ? (
                <input
                  className="input w-72 max-w-full"
                  aria-label={`Title for video ${n + 1}`}
                  title={slot.path}
                  value={slot.title}
                  placeholder={t('Video title')}
                  onChange={(e) => patch(slot.key, { title: e.target.value })}
                />
              ) : (
                <input
                  className="input w-72 max-w-full"
                  placeholder={t('Paste a YouTube, Twitch, or Kick URL…')}
                  aria-label={`Video URL ${n + 1}`}
                  value={slot.url}
                  onChange={(e) => patch(slot.key, { url: e.target.value })}
                  onKeyDown={(e) => e.key === 'Enter' && generate()}
                />
              )}

              <label
                className="flex items-center gap-2 cursor-pointer text-sm shrink-0 whitespace-nowrap"
                title="Burn captions into this video’s clips"
              >
                <input
                  type="checkbox"
                  className="size-4 accent-[#38BDF8]"
                  checked={slot.options.captions !== false}
                  onChange={(e) =>
                    replaceOptions(slot.key, toggle(slot.options, 'captions', e.target.checked))
                  }
                />
                {t('Captions')}
              </label>

              {/* Immediately beside Captions: it configures that switch. */}
              <button
                className="btn-ghost shrink-0"
                onClick={() => setOpenStyle(openStyle === slot.key ? null : slot.key)}
                aria-expanded={openStyle === slot.key}
                disabled={slot.options.captions === false}
              >
                {t('Caption style')} {openStyle === slot.key ? '▾' : '▸'}
              </button>

              {TOGGLES.map((tg) => {
                // Watermark needs a saved branding profile to point at. Without
                // one there is nothing to burn in, so the box could be ticked
                // and would simply un-tick itself — which reads as a broken
                // checkbox. Disable it and say what is missing instead.
                const needsProfile = tg.key === 'watermark' && !watermarkSelection().profileId
                return (
                  <label
                    key={tg.key}
                    className={`flex items-center gap-2 text-sm shrink-0 whitespace-nowrap ${
                      needsProfile ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
                    }`}
                    title={
                      needsProfile
                        ? 'Create a branding profile below first — there is no logo to burn in yet.'
                        : tg.title
                    }
                  >
                    <input
                      type="checkbox"
                      className="size-4 accent-[#38BDF8]"
                      checked={isOn(slot.options, tg.key)}
                      disabled={needsProfile}
                      onChange={(e) =>
                        replaceOptions(slot.key, toggle(slot.options, tg.key, e.target.checked))
                      }
                    />
                    {t(tg.label)} <span className="text-muted">{t(tg.hint)}</span>
                  </label>
                )
              })}

              {/* Always removable once it holds something. Hiding this on the
                  last row trapped a single uploaded file: its name is not an
                  editable URL, so with no remove button there was no way back
                  to an empty link row. Removing the last one leaves a fresh
                  empty row rather than nothing. */}
              {(slots.length > 1 || slot.url.trim() || slot.path) && (
                <button
                  className="btn-ghost shrink-0 !px-2"
                  title={t('Remove this video')}
                  aria-label={t('Remove this video')}
                  onClick={() =>
                    setSlots((prev) => {
                      const left = prev.filter((s) => s.key !== slot.key)
                      return left.length > 0 ? left : [emptySlot(slot.options)]
                    })
                  }
                >
                  <Trash />
                </button>
              )}
            </div>

            {slot.options.longform && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <span className="label shrink-0">{t('Longform output')}</span>
                <select
                  className="input !w-64"
                  value={slot.options.longform.mode}
                  onChange={(e) => patchOptions(slot.key, { longform: { mode: e.target.value } })}
                  aria-label={`Longform output type for video ${n + 1}`}
                >
                  <option value="short_clips">Short Clips (up to 60s, horizontal)</option>
                  <option value="clips_140">Clips (up to 140s — X/Twitter)</option>
                  <option value="highlights">Highlights (best-of, 8-20 min by quality)</option>
                  <option value="edited_stream">Edited Stream (downtime removed)</option>
                </select>
              </div>
            )}

            {openStyle === slot.key && slot.options.captions !== false && (
              <div className="w-full space-y-3 border-t border-raised/60 pt-3 mt-2">
                <p className="label">
                  {t('Caption style for')} {slot.path ? slot.title || t('this file') : t('this video')}
                </p>
                <CaptionStyleControls
                  idPrefix={`slot-${slot.key}`}
                  style={{ ...DEFAULT_CAPTION_STYLE, ...(slot.options.caption_style ?? {}) }}
                  onChange={(k, v) =>
                    patchOptions(slot.key, {
                      caption_style: {
                        ...DEFAULT_CAPTION_STYLE,
                        ...(slot.options.caption_style ?? {}),
                        [k]: v
                      }
                    })
                  }
                />
              </div>
            )}

            {slot.error && <p className="text-sm text-error mt-1">{slot.error}</p>}
          </div>
        ))}

        <div className="flex gap-3 items-center flex-wrap pt-1">
          <button
            className="btn-ghost shrink-0"
            onClick={addSlot}
            disabled={full}
            title={
              full
                ? `${t('The queue holds')} ${maxActive} ${t('videos at a time')}`
                : t('Add another video')
            }
          >
            + {t('Add video')}
          </button>
          <button className="btn-ghost shrink-0" onClick={addFiles} disabled={full}>
            <Folder className="mr-1.5" />
            {t('Upload video file')}
          </button>
          {/* Greyed out until there is an upload, the same way Caption style
              is greyed out until Captions is ticked. A downloaded video takes
              its channel from the source metadata; only a file off your
              computer has nobody to file it under.

              Shown blank while disabled: a greyed-out box still displaying the
              last creator reads as stuck rather than inapplicable. The value
              is remembered and returns with the next upload. */}
          <input
            className="input !w-44 shrink-0"
            placeholder={t('Creator profile')}
            aria-label="Creator profile for uploaded files"
            disabled={!hasFiles}
            title={
              hasFiles
                ? 'Files these uploads under this creator in your library and the Creators tab. Leave blank to skip.'
                : 'For uploaded files only — a downloaded video brings its own channel name'
            }
            value={hasFiles ? channel : ''}
            onChange={(e) => {
              setChannel(e.target.value)
              localStorage.setItem('upload-channel', e.target.value)
            }}
          />
          <button
            className="btn-accent shrink-0 ml-auto"
            onClick={() => generate()}
            disabled={busy || ready.length === 0}
          >
            {busy
              ? t('Starting…')
              : queueRunning
                ? `${t('Add to queue')}${ready.length > 1 ? ` (${ready.length})` : ''}`
                : `${t('Generate clips')}${ready.length > 1 ? ` (${ready.length})` : ''}`}
          </button>
        </div>

        {wantsWatermark && <BrandingEditor />}
      </div>

      {full && (
        <p className="text-xs text-muted px-1">
          {t('The queue holds')} {maxActive} {t('videos at a time — start these, then add more when one finishes.')}
        </p>
      )}

      {added !== null && added > 0 && (
        <p className="text-sm text-accent px-1">
          {added === 1
            ? t('Started — watch the progress below.')
            : `${added} ${t('videos queued — working through them one at a time.')}`}{' '}
          <button
            className="underline hover:text-ink"
            onClick={() => window.dispatchEvent(new CustomEvent('open-queue'))}
          >
            {t('View queue')}
          </button>
        </p>
      )}
      {/* Only shown when it actually happened — the common case never sees it. */}
      {alreadyDone.length > 0 && (
        <div className="card flex items-center gap-3 flex-wrap">
          <p className="text-sm flex-1 min-w-64">
            {alreadyDone.length === 1
              ? t('That video was already processed.')
              : `${alreadyDone.length} ${t('of those were already processed.')}`}{' '}
            {t('Make clips again with the settings you chose? Existing clips are kept — new ones are added alongside them.')}
          </p>
          <button
            className="btn-accent shrink-0"
            disabled={busy}
            onClick={() => generate(true, alreadyDone)}
          >
            {t('Process again')}
          </button>
          <button className="btn-ghost shrink-0" onClick={() => setAlreadyDone([])}>
            {t('Cancel')}
          </button>
        </div>
      )}
      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}
    </div>
  )
}

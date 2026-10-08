import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { CaptionStyle, JobOptions, PipelineChoice } from '../../lib/types'
import CaptionStyleControls, {
  DEFAULT_CAPTION_STYLE,
  PostStyleControls
} from '../CaptionStyleControls'
import BrandingEditor, { setWatermarkEnabled, watermarkSelection } from '../WatermarkCard'
import GamingLayoutEditor from '../GamingLayoutEditor'
import SportFields from '../SportFields'
import PipelineFields from '../PipelineFields'
import StepFields from './StepFields'
import { useEditPlugins, usePipelines, useStepPlugins } from '../../lib/plugins'
import { fittingSettings, type InstalledPlugin } from '../../lib/marketplace'
import {
  MAX_PER_STEP,
  MOMENT_STEPS,
  firstStepPlugin,
  hasSteps,
  offers,
  stepProblemKeys,
  usableFor,
  type JobStep
} from '../../lib/steps'
import { PRESETS } from '../../lib/gamingLayout'
import {
  fitSport,
  lastSport,
  rememberSport,
  sportForVertical,
  startingSport,
  useSports,
  verticalSports,
  verticalValue
} from '../../lib/sports'
import { Folder, Trash } from '../icons'
import { t } from '../../lib/i18n'

const DRAFT_KEY = 'queue-draft'

const REASONS: Record<string, string> = {
  already_processed: 'already processed',
  already_queued: 'already in the queue',
  unrecognized: 'not a link Clips Kitty recognises',
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
  /** A local file's size and shape, read when it is picked: drives the
   *  Vertical Live suggestion and the "not 9:16" warning. */
  shape?: { width: number; height: number; orientation: 'vertical' | 'horizontal' | 'other' }
  /** A local file's original link (a downloaded live's page), when known. */
  sourceUrl?: string
}

type ToggleKey =
  | 'captions'
  | 'long_clips'
  | 'podcast'
  | 'vertical_live'
  | 'gaming'
  | 'sport'
  | 'longform'
  | 'pipeline'
  | 'steps'
  | 'edit'
  | 'watermark'

/** Each of these decides what the frame is, so only one can be on. */
const LAYOUT_MODES = ['podcast', 'vertical_live', 'gaming', 'longform'] as const

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
    key: 'vertical_live',
    label: 'Vertical Live',
    hint: '(9:16)',
    title:
      'For a livestream that was already vertical when it was streamed (a YouTube vertical live, the vertical feed of a Twitch or Streamlabs dual-format stream, or a downloaded Instagram/TikTok live). Keeps the stream’s own 9:16 layout: no face tracking or reframing. The moments are still picked the usual way.'
  },
  {
    key: 'podcast',
    label: 'Podcast',
    hint: '(multi-cam)',
    title:
      'For multi-camera podcasts (cuts between angles, several people). Frames shot by shot: each camera shot gets one steady crop centered on whoever is talking, and cuts land directly on the speaker’s face — no panning, no split screens. Leave OFF for normal one-camera streams.'
  },
  {
    key: 'gaming',
    label: 'Gaming / Reaction',
    hint: '(split-screen)',
    title:
      'For game streams and reaction videos: the streamer’s webcam in the top half, the game (or the video they’re reacting to) in the bottom half. With no webcam, the game fills the screen. The streamer is whoever is talking, never the biggest face. Fix a clip in the editor by drawing the webcam and game area.'
  },
  {
    key: 'sport',
    label: 'Sports',
    hint: '(match)',
    title:
      'For a match or a game (Soccer, Basketball): its moments (goals and saves, or dunks, threes, blocks and the reactions to them) are found from the crowd, the commentary and the scoreboard, one clip per moment with its build-up, and the 9:16 crop follows the play. Choose the sport and which moments to keep below.'
  },
  {
    key: 'pipeline',
    label: 'Pipeline',
    hint: '(Marketplace)',
    title:
      'A pipeline you installed from the Marketplace finds this video’s moments its own way; Clips Kitty still cuts, frames and captions the clips. Not with Sports or Longform. Choose which pipeline below.'
  },
  {
    key: 'steps',
    label: 'Rate & understand',
    hint: '(Marketplace)',
    title:
      'Plugins you installed from the Marketplace look at the moments once they’re found, by Clips Kitty, Sports, Gaming scoring or a pipeline. One that understands says what happens in each moment, so the titles, descriptions and hashtags can say it. One that rates gives each moment its own score, which decides which clips are made and their order. Clips Kitty does any step you leave to it. Not with Longform.'
  },
  {
    key: 'edit',
    label: 'Suggest edits',
    hint: '(Marketplace)',
    title:
      'Plugins you installed from the Marketplace look at each clip Clips Kitty is about to make and suggest edits: cuts, mutes, fades, speed, a hook title or the layout. Suggestions wait for you in the editor. Clips Kitty doesn’t put a suggestion into a clip until you use it in the editor and apply your edits (Apply edits, or Apply edits & upload). Not with Longform.'
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
  vertical_live: 'generate-vertical-live',
  gaming: 'generate-gaming',
  gaming_scoring: 'generate-gaming-scoring',
  sport: 'generate-sport',
  longform: 'generate-longform',
  longform_mode: 'generate-longform-mode',
  longform_shorts: 'generate-longform-shorts',
  // The Suggest edits plugins last chosen, as a list of choices.
  edit: 'generate-edit'
} as const

/** The Suggest edits plugins last chosen in the Generate bar, or null. They
 *  start this bar's rows only: once the installed plugins are known, one
 *  that has gone is dropped and settings are fitted, as for a draft. */
function rememberedEdit(): PipelineChoice[] | null {
  try {
    const raw = JSON.parse(localStorage.getItem(PREF.edit) ?? 'null')
    if (!Array.isArray(raw)) return null
    const list = raw
      .filter((c) => c && typeof c.id === 'string' && c.id)
      .slice(0, MAX_PER_STEP)
      .map((c): PipelineChoice =>
        c.settings && typeof c.settings === 'object' && !Array.isArray(c.settings)
          ? { id: c.id, settings: c.settings }
          : { id: c.id }
      )
    return list.length > 0 ? list : null
  } catch {
    return null // a corrupt saved choice only means it isn't remembered
  }
}

/** Remember the Suggest edits plugins for the next video; none forgets them. */
function rememberEdit(list: PipelineChoice[] | null | undefined): void {
  try {
    if (list?.length) localStorage.setItem(PREF.edit, JSON.stringify(list))
    else localStorage.removeItem(PREF.edit)
  } catch {
    // Not remembered for next time; this video still gets it.
  }
}

/** A new row's options in this bar: the remembered settings, with the
 *  Suggest edits plugins last chosen (never with Longform). */
function startingOptions(): JobOptions {
  const o = seedOptions()
  const edit = rememberedEdit()
  if (edit && !o.longform) o.edit = edit
  return o
}

/** A step's chosen plugins that can still do it, each with its settings
 *  fitted to the installed version; a plugin that has gone is dropped. */
function fittedChoices(list: PipelineChoice[], able: InstalledPlugin[]): PipelineChoice[] {
  return list.flatMap((choice): PipelineChoice[] => {
    const plugin = able.find((p) => p.id === choice.id)
    if (!plugin) return []
    const settings = fittingSettings(choice.settings, plugin.settings)
    if (JSON.stringify(settings) === JSON.stringify(choice.settings ?? {})) return [choice]
    const { settings: _old, ...kept } = choice
    return [Object.keys(settings).length > 0 ? { ...kept, settings } : kept]
  })
}

/** "Also make 9:16 Shorts" under Longform, as last chosen. */
function rememberedShorts(): boolean {
  try {
    return localStorage.getItem(PREF.longform_shorts) === 'true'
  } catch {
    return false
  }
}

function remember(key: ToggleKey, on: boolean, mode?: string): void {
  try {
    if (key === 'captions') localStorage.setItem(PREF.captions, String(on))
    else if (key === 'long_clips') localStorage.setItem(PREF.long_clips, String(on))
    else if (key === 'podcast') localStorage.setItem(PREF.podcast, String(on))
    else if (key === 'vertical_live') localStorage.setItem(PREF.vertical_live, String(on))
    else if (key === 'gaming') localStorage.setItem(PREF.gaming, String(on))
    else if (key === 'sport') localStorage.setItem(PREF.sport, String(on))
    else if (key === 'edit' && !on) localStorage.removeItem(PREF.edit) // on, rememberEdit keeps the list
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

/** The Generate bar's current settings.
 *
 *  Exported because the assistant needs them too: a job queued by asking the
 *  chat box used to ignore every one of these, so captions came back burned
 *  in however often the box was unticked. One reader, not two. */
export function seedOptions(): JobOptions {
  const wm = watermarkSelection()
  const o: JobOptions = {
    captions: localStorage.getItem(PREF.captions) !== 'false',
    caption_style: savedStyle()
  }
  if (localStorage.getItem(PREF.long_clips) === 'true') o.long_clips = true
  if (localStorage.getItem(PREF.podcast) === 'true') o.podcast = true
  if (localStorage.getItem(PREF.longform) === 'true') {
    o.longform = {
      mode: localStorage.getItem(PREF.longform_mode) ?? 'short_clips',
      ...(rememberedShorts() ? { shorts: true } : {})
    }
  }
  // Vertical Live can't be combined with Podcast or Longform; if an older
  // remembered pair says otherwise, those win and it stays off.
  if (localStorage.getItem(PREF.vertical_live) === 'true' && !o.podcast && !o.longform) {
    o.vertical_live = true
    if (localStorage.getItem(PREF.gaming_scoring) === 'true') o.gaming_scoring = true
  }
  // Gaming / Reaction likewise, and the older modes win over it.
  if (localStorage.getItem(PREF.gaming) === 'true' && !o.podcast && !o.longform && !o.vertical_live) {
    o.gaming = true
  }
  // Sports too: Podcast and Gaming each score the video their own way, and
  // win over it.
  const match = localStorage.getItem(PREF.sport) === 'true' && !o.podcast && !o.gaming ? lastSport() : null
  if (match) {
    o.sport = match
    delete o.gaming_scoring
  }
  if (wm.enabled && wm.profileId) o.watermark_profile_id = wm.profileId
  return o
}

let counter = 0
const newKey = (): string => `s${Date.now()}-${counter++}`

/** Settings to copy onto another video: everything but the split, which
 *  was set up on this video's own frames. */
function copyable(o: JobOptions): JobOptions {
  const { gaming_layout: _layout, gaming_remember: _remember, ...rest } = o
  // A match's events belong to that match, not the next video's.
  if (rest.sport?.events) {
    const { events: _events, ...sport } = rest.sport
    return { ...rest, sport }
  }
  return rest
}

/** A Pipeline also chosen in a Rate or Understand row is dropped from that
 *  row: the job's own pipeline already describes and scores what it finds,
 *  and the engine refuses the pair. Options without steps come back as they are. */
function withoutPipelineInSteps(o: JobOptions): JobOptions {
  const id = o.pipeline?.id
  if (!id || !MOMENT_STEPS.some((step) => o[step]?.some((c) => c.id === id))) return o
  const next = { ...o }
  for (const step of MOMENT_STEPS) {
    const kept = (next[step] ?? []).filter((c) => c.id !== id)
    if (kept.length) next[step] = kept
    else delete next[step]
  }
  return next
}

function emptySlot(from?: JobOptions): Slot {
  // A new row copies the one above it: a batch usually shares most settings,
  // and every switch is still overridable per video. Copied, not shared.
  return { key: newKey(), url: '', path: null, title: '', options: copyable(from ?? startingOptions()) }
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
  // The row whose Gaming / Reaction split is being set up, on its own frames.
  const [splitFor, setSplitFor] = useState<string | null>(null)
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
  // The sports the engine offers; the Sports toggle shows only when it has one.
  const sports = useSports()
  const offered = sports ?? []
  // Installed community pipelines that are on; the Pipeline switch shows only
  // when there is one.
  const pipelines = usePipelines()
  const usable = pipelines ?? []
  // Installed plugins that can understand or rate moments; the Rate &
  // understand switch shows only when there is one.
  const stepPlugins = useStepPlugins()
  const stepUsable = stepPlugins ?? []
  // Installed plugins that can suggest edits for the clips; the Suggest
  // edits switch shows only when there is one.
  const editPlugins = useEditPlugins()
  const editUsable = editPlugins ?? []
  // Rows whose pipeline settings hold a value that can't be sent yet: the
  // Pipeline row under the slot's key, each Rate & understand row under
  // `{slot}:{step}:{plugin id}` (lib/steps.ts stepProblemKeys).
  const [badSettings, setBadSettings] = useState<Record<string, boolean>>({})

  // Once the engine has said which sports it has, a remembered or copied
  // choice it no longer offers is fixed or dropped, rather than sent and
  // refused at Generate.
  useEffect(() => {
    if (!sports) return
    setSlots((prev) =>
      prev.map((s) => {
        if (!s.options.sport) return s
        const fit = fitSport(s.options.sport, sports)
        if (JSON.stringify(fit) === JSON.stringify(s.options.sport)) return s
        const options = { ...s.options }
        if (fit) options.sport = fit
        else delete options.sport
        return { ...s, options }
      })
    )
  }, [sports])

  // Likewise a pipeline removed or turned off since this list was drafted is
  // dropped, and a setting its installed version no longer has, or no longer
  // allows that value for, goes back to its default: neither is refused at
  // Generate over something the form doesn't show.
  useEffect(() => {
    if (!pipelines) return
    setSlots((prev) =>
      prev.map((s) => {
        const choice = s.options.pipeline
        if (!choice) return s
        const plugin = pipelines.find((p) => p.id === choice.id)
        if (!plugin) {
          const { pipeline: _gone, ...options } = s.options
          return { ...s, options }
        }
        const settings = fittingSettings(choice.settings, plugin.settings)
        if (JSON.stringify(settings) === JSON.stringify(choice.settings ?? {})) return s
        const { settings: _old, ...kept } = choice
        const pipeline = Object.keys(settings).length > 0 ? { ...kept, settings } : kept
        return { ...s, options: { ...s.options, pipeline } }
      })
    )
  }, [pipelines])

  // The same for Rate & understand: a row whose plugin has gone, or can no
  // longer do that step, is dropped, and its settings are fitted to the
  // installed version.
  useEffect(() => {
    if (!stepPlugins) return
    setSlots((prev) =>
      prev.map((s) => {
        if (!hasSteps(s.options)) return s
        const options = { ...s.options }
        for (const step of MOMENT_STEPS) {
          const list = s.options[step]
          if (!list) continue
          const fitted = fittedChoices(list, usableFor(stepPlugins, step))
          if (fitted.length > 0) options[step] = fitted
          else delete options[step]
        }
        const next = withoutPipelineInSteps(options)
        return JSON.stringify(next) === JSON.stringify(s.options) ? s : { ...s, options: next }
      })
    )
  }, [stepPlugins])

  // And for Suggest edits, whose rows may name the video's own Pipeline.
  useEffect(() => {
    if (!editPlugins) return
    setSlots((prev) =>
      prev.map((s) => {
        const list = s.options.edit
        if (!list) return s
        const fitted = fittedChoices(list, editPlugins)
        if (JSON.stringify(fitted) === JSON.stringify(list)) return s
        const options = { ...s.options }
        if (fitted.length > 0) options.edit = fitted
        else delete options.edit
        return { ...s, options }
      })
    )
  }, [editPlugins])

  // A Rate & understand or Suggest edits row that is gone (removed,
  // unticked, dropped) takes its settings problem with it, so it can never
  // hold Generate.
  useEffect(() => {
    const live = new Set(slots.flatMap((s) => stepProblemKeys(s.key, s.options)))
    setBadSettings((b) => {
      const gone = Object.keys(b).filter((k) => k.includes(':') && !live.has(k))
      if (gone.length === 0) return b
      const next = { ...b }
      for (const k of gone) delete next[k]
      return next
    })
  }, [slots])

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
  const settingsBad = ready.some(
    (s) =>
      (s.options.pipeline && badSettings[s.key]) ||
      stepProblemKeys(s.key, s.options).some((k) => badSettings[k])
  )
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

  /** This video's Pipeline choice. A plugin it names that is also in a Rate
   *  or Understand row leaves that row. */
  const patchPipeline = (key: string, pipeline: PipelineChoice): void =>
    setSlots((prev) =>
      prev.map((s) =>
        s.key === key
          ? { ...s, options: withoutPipelineInSteps({ ...s.options, pipeline }), error: undefined }
          : s
      )
    )

  /** One Rate & understand step's plugins for this video, or its Suggest
   *  edits plugins. An empty list leaves the step to Clips Kitty; with no
   *  plugin left, the switch is off. */
  const setStep = (key: string, step: JobStep, list: PipelineChoice[]): void => {
    if (step === 'edit') rememberEdit(list)
    setSlots((prev) =>
      prev.map((s) => {
        if (s.key !== key) return s
        const options = { ...s.options }
        if (list.length > 0) options[step] = list
        else delete options[step]
        return { ...s, options, error: undefined }
      })
    )
  }

  /** This video's caption style, whole, and a change to one field of it: the
   *  post style and the caption controls edit the same object. */
  const slotStyle = (slot: Slot): Required<CaptionStyle> => ({
    ...DEFAULT_CAPTION_STYLE,
    ...(slot.options.caption_style ?? {})
  })
  const setSlotStyle =
    (slot: Slot) =>
    <K extends keyof CaptionStyle>(k: K, v: CaptionStyle[K]): void =>
      patchOptions(slot.key, { caption_style: { ...slotStyle(slot), [k]: v } })
  /** Every output of this video is 16:9 (Longform with no 9:16 Shorts),
   *  where a post style draws nothing. */
  const longformOnly = (slot: Slot): boolean =>
    Boolean(slot.options.longform && !slot.options.longform.shorts)

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
    } else if (key === 'vertical_live') {
      if (on) next.vertical_live = true
      else {
        delete next.vertical_live
        delete next.gaming_scoring // offered with Vertical Live, so it goes with it
      }
    } else if (key === 'gaming') {
      if (on) next.gaming = true
      else {
        delete next.gaming
        delete next.gaming_layout
        delete next.gaming_remember
      }
    } else if (key === 'sport') {
      const start = on ? (next.sport ?? startingSport(offered)) : null
      if (start) {
        next.sport = start
        rememberSport(start)
      } else delete next.sport
    } else if (key === 'longform') {
      if (on)
        next.longform = {
          mode: next.longform?.mode ?? 'short_clips',
          ...(rememberedShorts() ? { shorts: true } : {})
        }
      else delete next.longform
    } else if (key === 'pipeline') {
      const first = usable[0]
      if (on && (next.pipeline || first)) next.pipeline = next.pipeline ?? { id: first.id }
      else delete next.pipeline
    } else if (key === 'steps') {
      // The first plugin that can, in every step it can do: one that does
      // both is in both lists, and runs once.
      const first = firstStepPlugin(stepUsable, next)
      if (on && !hasSteps(next) && first) {
        const can = offers(first)
        if (can.includes('understand')) next.understand = [{ id: first.id }]
        if (can.includes('rate')) next.rate = [{ id: first.id }]
      } else if (!on) {
        delete next.rate
        delete next.understand
      }
    } else if (key === 'edit') {
      // The first plugin that can suggest edits. The video's own Pipeline
      // may be it: a find run is never asked to edit.
      const first = editUsable[0]
      if (on && !next.edit?.length && first) next.edit = [{ id: first.id }]
      else if (!on) delete next.edit
    } else if (key === 'watermark') {
      const { profileId } = watermarkSelection()
      if (on && profileId) next.watermark_profile_id = profileId
      else delete next.watermark_profile_id
      // Ticking with no profile saved cannot do anything — the checkbox is
      // disabled in that case rather than silently refusing to stay ticked.
      setWatermarkEnabled(on && Boolean(profileId))
    }
    // Vertical Live keeps the live's own layout, Gaming splits webcam and game,
    // Podcast reframes a multi-camera set and Longform makes 16:9: each is a
    // different answer to what the frame is, so turning one on turns the rest
    // off. (Podcast and Longform have always been allowed together.)
    if (on && (LAYOUT_MODES as readonly string[]).includes(key)) {
      for (const other of LAYOUT_MODES) {
        if (other === key || (key === 'podcast' && other === 'longform') || (key === 'longform' && other === 'podcast'))
          continue
        if (next[other]) {
          delete next[other]
          remember(other, false)
        }
      }
    }
    // Sports scores a match as a match; Podcast and Gaming each score and
    // frame the video their own way. It goes with Longform (16:9) and
    // Vertical Live (a match filmed 9:16), which only decide the shape.
    if (on && key === 'sport') {
      for (const other of ['podcast', 'gaming'] as const) {
        if (next[other]) {
          delete next[other]
          remember(other, false)
        }
      }
      delete next.gaming_layout
      delete next.gaming_remember
      delete next.gaming_scoring
    } else if (on && (key === 'podcast' || key === 'gaming') && next.sport) {
      delete next.sport
      remember('sport', false)
    }
    // A pipeline picks the moments itself, so it can't share a video with the
    // modes that pick them their own way (Sports, Gaming scoring, Longform's
    // outputs). Layouts (Vertical Live, Gaming, Podcast) still apply to it.
    if (on && key === 'pipeline' && next.pipeline) {
      for (const other of ['sport', 'longform'] as const) {
        if (next[other]) {
          delete next[other]
          remember(other, false)
        }
      }
      delete next.gaming_scoring
    } else if (on && (key === 'sport' || key === 'longform') && next.pipeline) {
      delete next.pipeline
    }
    // Rate & understand work on the moments a Shorts run finds, whoever finds
    // them. Longform picks and writes its clips its own way.
    if (on && key === 'steps' && hasSteps(next) && next.longform) {
      delete next.longform
      remember('longform', false)
    } else if (on && key === 'longform') {
      delete next.rate
      delete next.understand
    }
    // Suggest edits likewise: it looks at the clips a Shorts run makes.
    if (on && key === 'edit' && next.edit?.length && next.longform) {
      delete next.longform
      remember('longform', false)
    } else if (on && key === 'longform' && next.edit) {
      delete next.edit
      remember('edit', false)
    }
    remember(key, on && (key !== 'sport' || Boolean(next.sport)), next.longform?.mode)
    if (key === 'edit' && on) rememberEdit(next.edit)
    return withoutPipelineInSteps(next)
  }

  /** Longform's "Also make 9:16 Shorts": both formats from one run (#98). */
  const setLongformShorts = (slot: Slot, on: boolean): void => {
    if (!slot.options.longform) return
    const { shorts: _drop, ...rest } = slot.options.longform
    patchOptions(slot.key, { longform: on ? { ...rest, shorts: true } : rest })
    try {
      localStorage.setItem(PREF.longform_shorts, String(on))
    } catch {
      // Not remembered for next time; this video still gets it.
    }
  }

  /** Under Vertical Live, like Longform's output: what the live is. Gaming /
   *  reaction scores it as a gaming stream (gaming_scoring). */
  /** What a Vertical Live is, which decides how it's scored. Soccer is the
   *  Sports switch's match scoring, picked here for a match streamed 9:16. */
  const setVerticalContent = (slot: Slot, content: string): void => {
    if (content.startsWith('sport:')) {
      const next = toggle(slot.options, 'sport', true)
      const sport = sportForVertical(content, next.sport, offered)
      if (sport) {
        next.sport = sport
        rememberSport(sport)
      }
      replaceOptions(slot.key, next)
      return
    }
    const next = slot.options.sport ? toggle(slot.options, 'sport', false) : { ...slot.options }
    if (content === 'gaming') {
      next.gaming_scoring = true
      delete next.pipeline // gaming scoring picks the moments: not with a pipeline
    } else delete next.gaming_scoring
    try {
      localStorage.setItem(PREF.gaming_scoring, String(content === 'gaming'))
    } catch {
      // a blocked localStorage only means it isn't remembered
    }
    replaceOptions(slot.key, next)
  }

  const isOn = (o: JobOptions, key: ToggleKey): boolean => {
    if (key === 'captions') return o.captions !== false
    if (key === 'long_clips') return Boolean(o.long_clips)
    if (key === 'podcast') return Boolean(o.podcast)
    if (key === 'vertical_live') return Boolean(o.vertical_live)
    if (key === 'gaming') return Boolean(o.gaming)
    if (key === 'sport') return Boolean(o.sport)
    if (key === 'longform') return Boolean(o.longform)
    if (key === 'pipeline') return Boolean(o.pipeline)
    if (key === 'steps') return Boolean(o.rate?.length || o.understand?.length)
    if (key === 'edit') return Boolean(o.edit?.length)
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
          options: copyable(base ?? startingOptions())
        }))
      const kept = prev.filter((s) => s.url.trim() || s.path)
      return [...kept, ...fresh]
    })
    // Each file's shape, for the Vertical Live suggestion. Best effort: a
    // file that can't be read just gets no suggestion.
    for (const path of picked) {
      void api
        .localVideoShape(path)
        .then((shape) => setSlots((prev) => prev.map((s) => (s.path === path ? { ...s, shape } : s))))
        .catch(() => undefined)
    }
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
          const res = await api.addLocalVideo({
            path: slot.path as string,
            title: slot.title,
            channel: channel.trim(),
            source_url: slot.sourceUrl?.trim() ?? '',
            ...slot.options,
            ...(force ? { force: true } : {})
          })
          // The same file clipped before: offered "Make clips again", as a link is.
          if (res.already_processed) {
            done.push(slot)
            failures.set(slot.path as string, REASONS.already_processed)
            continue
          }
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
                  onChange={(e) =>
                    patch(slot.key, {
                      url: e.target.value,
                      ...(slot.options.gaming_layout ? { options: copyable(slot.options) } : {})
                    })
                  }
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

              {/* Immediately beside Captions: it configures that switch. It
                  opens with captions off too, for the post style, which still
                  applies then (a Highlights card goes on every clip). */}
              <button
                className="btn-ghost shrink-0"
                onClick={() => setOpenStyle(openStyle === slot.key ? null : slot.key)}
                aria-expanded={openStyle === slot.key}
              >
                {slot.options.captions === false ? t('Post style') : t('Caption style')}{' '}
                {openStyle === slot.key ? '▾' : '▸'}
              </button>

              {TOGGLES.map((tg) => {
                // Sports shows once the engine says it has a sport (and stays
                // while it's on, so it can always be turned off).
                if (tg.key === 'sport' && offered.length === 0 && !slot.options.sport) return null
                // Pipeline likewise: only once a community pipeline is installed and on.
                if (tg.key === 'pipeline' && usable.length === 0 && !slot.options.pipeline) return null
                // Rate & understand likewise: once a plugin that can rate or understand is.
                if (tg.key === 'steps' && stepUsable.length === 0 && !hasSteps(slot.options)) return null
                // Suggest edits likewise: once a plugin that can suggest edits is.
                if (tg.key === 'edit' && editUsable.length === 0 && !slot.options.edit?.length) return null
                // Watermark needs a saved branding profile to point at. Without
                // one there is nothing to burn in, so the box could be ticked
                // and would simply un-tick itself — which reads as a broken
                // checkbox. Disable it and say what is missing instead.
                const needsProfile = tg.key === 'watermark' && !watermarkSelection().profileId
                // Rate & understand the same way, when the only plugin that
                // could do it is this video's Pipeline: the API refuses a
                // pipeline named again, so ticking would do nothing.
                const onlyThePipeline =
                  tg.key === 'steps' && !hasSteps(slot.options) && !firstStepPlugin(stepUsable, slot.options)
                const disabled = needsProfile || onlyThePipeline
                const label = (
                  <label
                    key={tg.key}
                    className={`flex items-center gap-2 text-sm shrink-0 whitespace-nowrap ${
                      disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
                    }`}
                    title={
                      needsProfile
                        ? 'Create a branding profile below first — there is no logo to burn in yet.'
                        : onlyThePipeline
                          ? t(
                              'The only plugin you can use to rate or understand moments is this video’s Pipeline, and it can’t also be chosen here. Install or turn on another one in the Marketplace, or choose a different Pipeline.'
                            )
                          : tg.title
                    }
                  >
                    <input
                      type="checkbox"
                      className="size-4 accent-[#38BDF8]"
                      checked={isOn(slot.options, tg.key)}
                      disabled={disabled}
                      onChange={(e) => {
                        replaceOptions(slot.key, toggle(slot.options, tg.key, e.target.checked))
                        if (tg.key === 'gaming' && e.target.checked && (slot.path || slot.url.trim()))
                          setSplitFor(slot.key)
                      }}
                    />
                    {t(tg.label)} <span className="text-muted">{t(tg.hint)}</span>
                  </label>
                )
                return label
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

            {slot.options.sport && offered.length > 0 && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <SportFields
                  value={slot.options.sport}
                  sports={offered}
                  remember
                  name={`${n + 1}`}
                  onChange={(sport) => patchOptions(slot.key, { sport })}
                />
              </div>
            )}

            {slot.options.pipeline && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <PipelineFields
                  value={slot.options.pipeline}
                  pipelines={pipelines}
                  name={`${n + 1}`}
                  onChange={(pipeline) => patchPipeline(slot.key, pipeline)}
                  onProblem={(bad) => setBadSettings((b) => ({ ...b, [slot.key]: bad }))}
                />
              </div>
            )}

            {hasSteps(slot.options) && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <StepFields
                  options={slot.options}
                  plugins={stepPlugins}
                  pipelines={pipelines}
                  sports={offered}
                  name={`${n + 1}`}
                  onChange={(step, list) => setStep(slot.key, step, list)}
                  onProblem={(step, id, bad) =>
                    setBadSettings((b) => ({ ...b, [`${slot.key}:${step}:${id}`]: bad }))
                  }
                />
              </div>
            )}

            {Boolean(slot.options.edit?.length) && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <StepFields
                  steps={['edit']}
                  options={slot.options}
                  plugins={editPlugins}
                  pipelines={pipelines}
                  sports={offered}
                  name={`${n + 1}`}
                  onChange={(step, list) => setStep(slot.key, step, list)}
                  onProblem={(step, id, bad) =>
                    setBadSettings((b) => ({ ...b, [`${slot.key}:${step}:${id}`]: bad }))
                  }
                />
              </div>
            )}

            {/* What the live is: talking, a game, or a match (Sports). */}
            {slot.options.vertical_live && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <span className="label shrink-0">{t('Vertical Live content')}</span>
                <select
                  className="input !w-72"
                  value={verticalValue(slot.options.sport) ?? (slot.options.gaming_scoring ? 'gaming' : 'standard')}
                  onChange={(e) => setVerticalContent(slot, e.target.value)}
                  aria-label={`${t('Vertical Live content')} ${n + 1}`}
                  title={t('Gaming / reaction: what you say counts as on any stream, and in-game moments (a kill streak, a boss going down, a goal) and the reactions to them add to it, from chat and your voice, even when you say little. Game characters and people in a video you watch aren’t taken for you.')}
                >
                  <option value="standard">{t('Talking / IRL')}</option>
                  <option value="gaming">{t('Gaming / reaction')}</option>
                  {verticalSports(offered).map((s) => (
                    <option key={s.value} value={s.value}>
                      {t(s.label)}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {slot.options.longform && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <span className="label shrink-0">{t('Longform output')}</span>
                <select
                  className="input !w-64"
                  value={slot.options.longform.mode}
                  onChange={(e) =>
                    patchOptions(slot.key, { longform: { ...slot.options.longform, mode: e.target.value } })
                  }
                  aria-label={`Longform output type for video ${n + 1}`}
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
                    checked={Boolean(slot.options.longform.shorts)}
                    onChange={(e) => setLongformShorts(slot, e.target.checked)}
                  />
                  {t('Also make 9:16 Shorts')}
                </label>
              </div>
            )}

            {openStyle === slot.key && (
              <div className="w-full space-y-3 border-t border-raised/60 pt-3 mt-2">
                <p className="label">
                  {slot.options.captions === false ? t('Post style for') : t('Caption style for')}{' '}
                  {slot.path ? slot.title || t('this file') : t('this video')}
                </p>
                <PostStyleControls
                  idPrefix={`slot-${slot.key}`}
                  style={slotStyle(slot)}
                  onChange={setSlotStyle(slot)}
                  landscape={longformOnly(slot)}
                  alsoLandscape={Boolean(slot.options.longform?.shorts)}
                />
                {/* Every caption control while any output is 16:9: those
                    clips keep the standard look whatever the post style, so
                    a Highlights pick must not hide the font, colour and
                    position they burn with. */}
                {slot.options.captions !== false && (
                  <CaptionStyleControls
                    idPrefix={`slot-${slot.key}`}
                    style={slotStyle(slot)}
                    onChange={setSlotStyle(slot)}
                    landscape={Boolean(slot.options.longform)}
                  />
                )}
              </div>
            )}

            {slot.options.gaming && (
              <div className="flex items-center gap-3 flex-wrap mt-2 text-xs">
                <span className="label shrink-0">{t('Layout')}</span>
                <span className="text-muted">
                  {slot.options.gaming_layout
                    ? `${t(PRESETS[slot.options.gaming_layout.preset ?? 'half']?.label ?? 'Split')}${
                        slot.options.gaming_layout.order === 'game_top' &&
                        PRESETS[slot.options.gaming_layout.preset ?? 'half']?.type === 'stack'
                          ? ` · ${t('game on top')}`
                          : ''
                      }${slot.options.gaming_remember ? ` · ${t('remembered for the creator')}` : ''}`
                    : t('Not chosen yet: pick a layout on the video’s own frames before processing.')}
                </span>
                <button
                  className="btn-ghost !py-1 shrink-0"
                  disabled={!slot.path && !slot.url.trim()}
                  title={!slot.path && !slot.url.trim() ? t('Paste a link or add a file first') : undefined}
                  onClick={() => setSplitFor(slot.key)}
                >
                  {slot.options.gaming_layout ? t('Change layout…') : t('Choose layout…')}
                </button>
              </div>
            )}
            {splitFor === slot.key && (
              <GamingLayoutEditor
                source={slot.path ? { path: slot.path } : { url: slot.url.trim() }}
                context="video"
                settings={slot.options.gaming_layout ?? {}}
                remember={Boolean(slot.options.gaming_remember)}
                onClose={() => setSplitFor(null)}
                onDone={(layout, remember) =>
                  replaceOptions(slot.key, {
                    ...slot.options,
                    gaming_layout: layout,
                    ...(remember ? { gaming_remember: true } : {}),
                    ...(remember ? {} : { gaming_remember: undefined })
                  })
                }
              />
            )}
            {slot.path && (
              <VerticalHint
                slot={slot}
                onUse={() => replaceOptions(slot.key, toggle(slot.options, 'vertical_live', true))}
              />
            )}
            {slot.path && slot.options.vertical_live && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <span className="label shrink-0">{t('Original link')}</span>
                <input
                  className="input !w-80 max-w-full"
                  placeholder={t('Where the live was streamed (optional)')}
                  aria-label={`Original link for video ${n + 1}`}
                  value={slot.sourceUrl ?? ''}
                  onChange={(e) => patch(slot.key, { sourceUrl: e.target.value })}
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
          {settingsBad && (
            <span className="text-xs text-error ml-auto">{t('Fix the pipeline setting marked in red first.')}</span>
          )}
          <button
            className={`btn-accent shrink-0 ${settingsBad ? '' : 'ml-auto'}`}
            onClick={() => generate()}
            disabled={busy || ready.length === 0 || settingsBad}
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
          {t('The queue holds')} {maxActive} {t('videos at a time - start these, then add more when one finishes.')}
        </p>
      )}

      {added !== null && added > 0 && (
        <p className="text-sm text-accent px-1">
          {added === 1
            ? t('Started - watch the progress below.')
            : `${added} ${t('videos queued - working through them one at a time.')}`}{' '}
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
            {t('Make clips again with the settings you chose? Existing clips are kept - new ones are added alongside them.')}
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

/** Under a local file: suggests Vertical Live for a 9:16 file (a suggestion
 *  only; the toggle decides), and warns when it is on for a file that isn't
 *  9:16, which the engine would refuse. */
function VerticalHint({ slot, onUse }: { slot: Slot; onUse: () => void }): JSX.Element | null {
  const shape = slot.shape
  if (!shape) return null
  if (slot.options.vertical_live && shape.orientation !== 'vertical') {
    return (
      <p className="text-xs text-amber-400 mt-1">
        ⚠ {t('This video is')} {shape.width}×{shape.height},{' '}
        {t('not 9:16. Vertical Live expects a vertically composed video, so it would be refused.')}
      </p>
    )
  }
  // A match filmed 9:16 needs no switch: Sports keeps it as filmed on its own
  // (core/pipeline.py), so Vertical Live isn't offered for it.
  if (!slot.options.vertical_live && shape.orientation === 'vertical' && slot.options.sport) {
    return (
      <p className="text-xs text-muted mt-1">
        {t('This video is 9:16: Sports keeps it as filmed and finds the moments the same way.')}
      </p>
    )
  }
  if (
    !slot.options.vertical_live &&
    shape.orientation === 'vertical' &&
    !slot.options.podcast &&
    !slot.options.longform &&
    !slot.options.gaming
  ) {
    return (
      <p className="text-xs text-muted mt-1">
        {t('This video is 9:16. Is it a vertical live?')}{' '}
        <button className="text-accent hover:underline" onClick={onUse}>
          {t('Use Vertical Live')}
        </button>
      </p>
    )
  }
  return null
}

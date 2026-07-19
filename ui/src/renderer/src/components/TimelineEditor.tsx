import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type {
  CaptionLine,
  CaptionStyle,
  Clip,
  EditData,
  LiveOverlay,
  WatermarkConfig,
  Word
} from '../lib/types'
import WatermarkControls, { DEFAULT_WATERMARK } from './WatermarkControls'
import {
  Badge,
  Ban,
  Film,
  Folder,
  Keyboard,
  Note,
  Palette,
  Pencil,
  Refresh,
  Scissors,
  Sparkle,
  Trash,
  TrimEnd,
  TrimStart,
  Undo as UndoIcon,
  Zap
} from './icons'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'
import ColorControls from './ColorControls'
import ReactionRegions from './ReactionRegions'
import EditChat from './EditChat'

type Tab = 'captions' | 'audio' | 'motion' | 'watermark' | 'color' | 'ai'
const TABS: { id: Tab; label: string; icon: JSX.Element }[] = [
  { id: 'captions', label: 'Captions', icon: <span className="font-bold text-[11px] leading-none">Aa</span> },
  { id: 'audio', label: 'Audio', icon: <Note /> },
  { id: 'motion', label: 'Effects', icon: <Zap /> },
  { id: 'watermark', label: 'Watermark', icon: <Badge /> },
  { id: 'color', label: 'Color', icon: <Palette /> },
  { id: 'ai', label: 'AI edit', icon: <Sparkle /> }
]

/** A user text correction for one transcript word (misheard by Whisper). */
interface WordEdit {
  start: number
  from: string
  to: string
}

/** Shorts finishing editor: trim / split / delete sections, mute sections or
 *  single words, volume + fades — all non-destructive. Edits are stored with
 *  the clip and applied by a re-render; the preview SIMULATES them on the
 *  current file (skips removed parts, mutes muted ones), so nothing renders
 *  until Apply.
 *
 *  All edit coordinates are seconds on the clip's ORIGINAL timeline. The
 *  preview file may already have earlier edits baked in, so playback times
 *  are mapped through the baked keep-ranges in both directions. */

type Range = [number, number]

const MIN_SEG = 0.25
const PAUSE_GAP = 0.6 // "tighten pauses" removes silences longer than this
const PAUSE_PAD = 0.12
const MAX_ZOOM = 8 // timeline zoom: 1x = whole clip fits, 8x = frame-accurate

// Words that commonly get Shorts age-restricted, demonetized or suppressed.
// "Censor" mutes their audio AND strips them from the burned captions —
// platform moderation reads both. Exact-token match after normalization.
const PROFANITY = new Set([
  'fuck', 'fucking', 'fucked', 'fucker', 'fuckin', 'motherfucker', 'motherfucking',
  'shit', 'shitty', 'bullshit', 'shits',
  'bitch', 'bitches', 'asshole', 'assholes',
  'dick', 'dickhead', 'pussy', 'cunt', 'cock',
  'bastard', 'whore', 'slut', 'hoe', 'hoes', 'tits', 'titties',
  'nigga', 'niggas', 'nigger', 'faggot', 'fag', 'retard', 'retarded',
  'goddamn', 'goddamnit'
])

const normToken = (w: string): string => w.toLowerCase().replace(/[^a-z]/g, '')

function defaultEdit(duration: number): EditData {
  return {
    keep: [[0, duration]],
    mutes: [],
    muted_words: [],
    volume: 1,
    mute_all: false,
    fade_in: 0,
    fade_out: 0,
    speed: 1,
    hook: null,
    music: null
  }
}

function isDefault(e: EditData, duration: number): boolean {
  const keep = e.keep ?? [[0, duration]]
  return (
    keep.length === 1 &&
    keep[0][0] < 0.05 &&
    keep[0][1] > duration - 0.05 &&
    e.mutes.length === 0 &&
    !e.mute_all &&
    Math.abs(e.volume - 1) < 0.01 &&
    e.fade_in === 0 &&
    e.fade_out === 0 &&
    Math.abs((e.speed ?? 1) - 1) < 0.01 &&
    !e.hook?.text &&
    !e.music?.path
  )
}

/** original-timeline -> baked-preview-file time (edits already rendered). */
function origToBaked(t: number, bakedKeep: Range[] | undefined): number {
  if (!bakedKeep) return t
  let offset = 0
  for (const [a, b] of bakedKeep) {
    if (t <= b) return offset + Math.max(0, t - a)
    offset += b - a
  }
  return offset
}

export function bakedToOrig(t: number, bakedKeep: Range[] | undefined): number {
  if (!bakedKeep) return t
  let offset = 0
  for (const [a, b] of bakedKeep) {
    if (t - offset <= b - a) return a + (t - offset)
    offset += b - a
  }
  return bakedKeep[bakedKeep.length - 1]?.[1] ?? t
}

/** ranges in `base` that are NOT covered by keep (i.e. removed). */
function removedRanges(keep: Range[], duration: number): Range[] {
  const out: Range[] = []
  let cursor = 0
  for (const [a, b] of keep) {
    if (a - cursor > 0.02) out.push([cursor, a])
    cursor = Math.max(cursor, b)
  }
  if (duration - cursor > 0.02) out.push([cursor, duration])
  return out
}

export default function TimelineEditor({
  clip,
  videoRef,
  onChanged,
  onPreview,
  onLiveOverlay,
  watermark,
  setWatermark
}: {
  clip: Clip
  videoRef: React.RefObject<HTMLVideoElement>
  onChanged: () => void
  onPreview: (url: string | null) => void
  onLiveOverlay: (o: LiveOverlay | null) => void
  watermark: WatermarkConfig | null
  setWatermark: (w: WatermarkConfig | null) => void
}): JSX.Element {
  const duration = clip.end_s - clip.start_s
  const baked = useMemo<EditData | null>(() => clip.render_opts?.edit ?? null, [clip.id])
  const [edit, setEdit] = useState<EditData>(() => ({
    ...defaultEdit(duration),
    ...(clip.render_opts?.edit ?? {})
  }))
  const [history, setHistory] = useState<EditData[]>([])
  const [words, setWords] = useState<Word[]>([])
  const [captionBase, setCaptionBase] = useState<CaptionLine[] | null>(null)
  const [playhead, setPlayhead] = useState(0) // original-timeline seconds
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  // Layout override: Auto = the AI decides (tracking/letterbox), Letterbox =
  // force the full frame on a blurred backdrop, Center = static center crop.
  const storedCrop = clip.render_opts?.crop ?? 'track'
  const [layout, setLayout] = useState<string>(storedCrop)
  // Gaming split layout: which band the facecam occupies (only affects
  // clips the tracker rendered as gameplay+facecam).
  const storedSplitPos = clip.render_opts?.split_position ?? 'top'
  const [splitPos, setSplitPos] = useState<'top' | 'bottom'>(storedSplitPos)
  const isLandscape = !!clip.render_opts?.profile
  // Caption style (font/size/colour/position) for THIS clip.
  const storedStyle: Required<CaptionStyle> = {
    ...DEFAULT_CAPTION_STYLE,
    ...(clip.render_opts?.caption_style ?? {})
  }
  const [captionStyle, setCaptionStyle] = useState<Required<CaptionStyle>>(storedStyle)
  // Which editing panel is open (CapCut-style tabs replace the old stack).
  const [activeTab, setActiveTab] = useState<Tab>('captions')
  // Watermark / branding for THIS clip — state lifted to EditorModal so the
  // live draggable overlay on the preview and these controls stay in sync.
  const storedWatermark = clip.render_opts?.watermark ?? null
  // Caption text corrections: with "Edit caption text" ON, clicking a
  // transcript word opens a text box instead of muting it.
  const [textMode, setTextMode] = useState(false)
  const [wordEdits, setWordEdits] = useState<WordEdit[]>([])
  const [editingWord, setEditingWord] = useState<{ i: number; value: string } | null>(null)
  // Draft preview: when set, the video element shows a low-res render with
  // ALL edits baked in — live simulation must be off (it would double-apply).
  const [draftEditJson, setDraftEditJson] = useState<string | null>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const dragging = useRef<'start' | 'end' | null>(null)
  // Timeline zoom: the bar grows to zoom × the container width inside a
  // horizontal scroller, so 1 pixel covers less time — precise trims/splits.
  const [zoom, setZoom] = useState(1)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pendingScroll = useRef<number | null>(null)
  const [scrollW, setScrollW] = useState(600) // container width, for tick spacing

  const keep: Range[] = edit.keep ?? [[0, duration]]
  const removed = removedRanges(keep, duration)
  const bakedRemoved = baked?.keep ? removedRanges(baked.keep, duration) : []

  useEffect(() => {
    setEdit({ ...defaultEdit(duration), ...(clip.render_opts?.edit ?? {}) })
    setHistory([])
    setNotice('')
    setDraftEditJson(null)
    setLayout(clip.render_opts?.crop ?? 'track')
    setSplitPos(clip.render_opts?.split_position ?? 'top')
    setCaptionStyle({ ...DEFAULT_CAPTION_STYLE, ...(clip.render_opts?.caption_style ?? {}) })
    setWordEdits([])
    setEditingWord(null)
    setZoom(1)
    onPreview(null)
    api
      .clipWords(clip.id)
      .then((r) => setWords(r.words))
      .catch(() => setWords([]))
    api
      .captions(clip.id)
      .then((r) => setCaptionBase(r.lines))
      .catch(() => setCaptionBase(null))
  }, [clip.id])

  // ---- preview simulation: skip newly-removed sections, mute new mutes ----
  // Runs on requestAnimationFrame, not timeupdate: timeupdate only fires a
  // few times per second, which sails right past a 0.3s muted word — the
  // mute would never audibly engage in the preview.
  const draftActive = draftEditJson !== null

  // Live edit state read by the animation loop through refs, so the loop is
  // set up ONCE per clip — not torn down and recreated on every trim-drag
  // frame (that churn was what froze the video while trimming).
  const editRef = useRef(edit)
  const removedRef = useRef(removed)
  const bakedRemovedRef = useRef(bakedRemoved)
  const scrubbing = useRef(false) // suppress auto-skip while seeking/dragging
  const scrubDrag = useRef(false) // true while dragging the playhead
  const [showKeys, setShowKeys] = useState(false)
  const [showRegions, setShowRegions] = useState(false)
  useEffect(() => {
    editRef.current = edit
    removedRef.current = removed
    bakedRemovedRef.current = bakedRemoved
  })

  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    if (draftActive) {
      el.playbackRate = 1
      return
    }
    let raf = 0
    let lastShown = -1
    const tick = (): void => {
      raf = requestAnimationFrame(tick)
      const e = editRef.current
      const tOrig = bakedToOrig(el.currentTime, baked?.keep)
      if (Math.abs(tOrig - lastShown) > 0.03) {
        lastShown = tOrig
        setPlayhead(tOrig)
      }
      // Skip over removed sections ONLY while genuinely playing and not while
      // the user is scrubbing/dragging — otherwise it fights manual seeks.
      if (!el.paused && !scrubbing.current && !dragging.current) {
        for (const [a, b] of removedRef.current) {
          const alreadyBaked = bakedRemovedRef.current.some(([x, y]) => a >= x - 0.05 && b <= y + 0.05)
          if (!alreadyBaked && tOrig > a + 0.02 && tOrig < b - 0.02) {
            el.currentTime = origToBaked(Math.min(b + 0.02, duration), baked?.keep)
            return
          }
        }
      }
      const inNewMute =
        e.mutes.some(([a, b]) => tOrig >= a - 0.02 && tOrig <= b + 0.02) &&
        !(baked?.mutes ?? []).some(([a, b]) => tOrig >= a && tOrig <= b)
      el.muted = e.mute_all || inNewMute
      if (!el.muted) el.volume = Math.max(0, Math.min(1, e.volume))
      el.playbackRate = Math.max(0.25, (e.speed ?? 1) / (baked?.speed ?? 1))
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      el.muted = false
      el.volume = 1
      el.playbackRate = 1
    }
  }, [baked, duration, videoRef, draftActive])

  const push = (next: EditData): void => {
    setHistory((h) => [...h.slice(-30), edit])
    setEdit(next)
  }
  const undo = (): void => {
    setHistory((h) => {
      if (h.length === 0) return h
      setEdit(h[h.length - 1])
      return h.slice(0, -1)
    })
  }

  const seekOrig = (t: number, hold = false): void => {
    const el = videoRef.current
    if (!el) return
    const clamped = Math.max(0, Math.min(duration, t))
    scrubbing.current = true // keep the auto-skip from yanking the playhead back
    el.currentTime = origToBaked(clamped, baked?.keep)
    setPlayhead(clamped)
    if (!hold) window.setTimeout(() => (scrubbing.current = false), 250)
  }

  // ---- scrubbing: press and DRAG anywhere on the timeline to move through
  // the video (not just click). Pointer capture keeps it tracking even when
  // the cursor leaves the bar.
  const timeFromX = (clientX: number): number => {
    const rect = barRef.current?.getBoundingClientRect()
    if (!rect) return playhead
    return Math.max(0, Math.min(duration, ((clientX - rect.left) / rect.width) * duration))
  }
  const startScrub = (e: React.PointerEvent): void => {
    if (dragging.current) return // a trim handle owns this gesture
    scrubDrag.current = true
    e.currentTarget.setPointerCapture(e.pointerId)
    seekOrig(timeFromX(e.clientX), true)
  }
  const moveScrub = (e: React.PointerEvent): void => {
    if (!scrubDrag.current) return
    seekOrig(timeFromX(e.clientX), true)
  }
  const endScrub = (): void => {
    if (!scrubDrag.current) return
    scrubDrag.current = false
    window.setTimeout(() => (scrubbing.current = false), 150)
  }

  const fmt = (t: number): string =>
    `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}.${Math.floor((t % 1) * 10)}`

  // ---- timeline zoom -------------------------------------------------------
  /** Zoom so the given original-timeline moment stays under the same pixel
   *  (like CapCut: Ctrl+scroll zooms around the cursor, buttons around the
   *  playhead). The scroll correction applies after React lays out the new
   *  bar width. */
  const zoomTo = (next: number, focusT?: number): void => {
    const clamped = Math.max(1, Math.min(MAX_ZOOM, next))
    const sc = scrollRef.current
    if (sc && clamped !== zoom) {
      const f = focusT ?? playhead
      const viewX = (f / duration) * sc.clientWidth * zoom - sc.scrollLeft
      pendingScroll.current = (f / duration) * sc.clientWidth * clamped - viewX
    }
    setZoom(clamped)
  }
  useLayoutEffect(() => {
    if (pendingScroll.current !== null && scrollRef.current) {
      scrollRef.current.scrollLeft = Math.max(0, pendingScroll.current)
      pendingScroll.current = null
    }
  }, [zoom])

  // Ctrl+scroll (and trackpad pinch) zooms around the cursor. Native
  // listener because React's root-level wheel handlers are passive —
  // preventDefault there can't stop the browser's page zoom.
  const wheelZoom = useRef<(e: WheelEvent) => void>(() => {})
  useEffect(() => {
    wheelZoom.current = (e: WheelEvent): void => {
      zoomTo(zoom * (e.deltaY < 0 ? 1.25 : 0.8), timeFromX(e.clientX))
    }
  })
  useEffect(() => {
    const sc = scrollRef.current
    if (!sc) return
    const onWheel = (e: WheelEvent): void => {
      if (e.ctrlKey) {
        e.preventDefault()
        wheelZoom.current(e)
        return
      }
      // Plain wheel pans the zoomed timeline (there is no visible
      // scrollbar to grab — panning is wheel / playhead-follow only).
      if (sc.scrollWidth > sc.clientWidth) {
        e.preventDefault()
        sc.scrollLeft += e.deltaY + e.deltaX
      }
    }
    sc.addEventListener('wheel', onWheel, { passive: false })
    return () => sc.removeEventListener('wheel', onWheel)
  }, [])

  // Track the visible width so ruler tick spacing adapts to the window size.
  useEffect(() => {
    const sc = scrollRef.current
    if (!sc) return
    const ro = new ResizeObserver(() => setScrollW(sc.clientWidth || 600))
    ro.observe(sc)
    return () => ro.disconnect()
  }, [])

  // While playing zoomed in, page the view so the playhead stays visible
  // (only when it leaves the viewport — free scrolling is never fought).
  useEffect(() => {
    if (zoom <= 1 || scrubDrag.current || dragging.current) return
    const sc = scrollRef.current
    const bar = barRef.current
    if (!sc || !bar) return
    const x = (playhead / duration) * bar.clientWidth
    if (x < sc.scrollLeft + 4 || x > sc.scrollLeft + sc.clientWidth - 4) {
      sc.scrollLeft = Math.max(0, x - sc.clientWidth * 0.2)
    }
  }, [playhead, zoom, duration])

  // Ruler ticks: pick the step so labels sit ~70px+ apart at the current zoom.
  const ruler = useMemo(() => {
    const pxPerSec = (scrollW * zoom) / Math.max(duration, 0.1)
    const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120]
    const step = steps.find((s) => s * pxPerSec >= 70) ?? 300
    const ticks: number[] = []
    for (let t = 0; t <= duration - step * 0.3; t += step) ticks.push(Number(t.toFixed(2)))
    return { step, ticks }
  }, [scrollW, zoom, duration])

  const fmtTick = (t: number): string =>
    ruler.step >= 1
      ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')}`
      : fmt(t)

  // The kept segment the playhead is in — highlighted, and what "Delete
  // section" removes (no more click-to-select fighting click-to-seek).
  const playheadSeg = keep.findIndex(([a, b]) => playhead >= a - 0.01 && playhead <= b + 0.01)

  const trimToPlayhead = (edge: 'start' | 'end'): void => {
    const k = keep.map((r) => [...r] as Range)
    if (edge === 'start') k[0][0] = Math.min(playhead, k[0][1] - MIN_SEG)
    else k[k.length - 1][1] = Math.max(playhead, k[k.length - 1][0] + MIN_SEG)
    push({ ...edit, keep: k })
  }

  const splitAtPlayhead = (): void => {
    const t = playhead
    const idx = keep.findIndex(([a, b]) => t > a + MIN_SEG && t < b - MIN_SEG)
    if (idx === -1) return
    const next = [...keep]
    const [a, b] = next[idx]
    next.splice(idx, 1, [a, Number(t.toFixed(2))], [Number(t.toFixed(2)), b])
    push({ ...edit, keep: next })
  }

  const deleteSelected = (): void => {
    if (playheadSeg < 0 || keep.length <= 1) return
    push({ ...edit, keep: keep.filter((_, i) => i !== playheadSeg) })
  }

  const tightenPauses = (): void => {
    if (words.length < 2) return
    let ranges = [...keep]
    for (let i = 1; i < words.length; i++) {
      const gapA = words[i - 1].end + PAUSE_PAD
      const gapB = words[i].start - PAUSE_PAD
      if (gapB - gapA < PAUSE_GAP) continue
      ranges = ranges.flatMap<Range>(([a, b]) => {
        if (gapA <= a && gapB >= b) return []
        if (gapB <= a || gapA >= b) return [[a, b]]
        const out: Range[] = []
        if (gapA - a >= MIN_SEG) out.push([a, gapA])
        if (b - gapB >= MIN_SEG) out.push([gapB, b])
        return out
      })
    }
    if (ranges.length !== keep.length) push({ ...edit, keep: ranges })
    setNotice(`Removed ${ranges.length - keep.length} pause(s)`)
  }

  const censorProfanity = (): void => {
    const flagged = words.filter((w) => PROFANITY.has(normToken(w.word)) && !wordMuted(w))
    if (flagged.length === 0) {
      setNotice('No flagged words found in this clip')
      return
    }
    push({
      ...edit,
      muted_words: [
        ...edit.muted_words,
        ...flagged.map((w) => ({ start: w.start, end: w.end, word: w.word }))
      ],
      mutes: [
        ...edit.mutes,
        ...flagged.map(
          (w): Range => [
            Number(Math.max(0, w.start - 0.04).toFixed(2)),
            Number(Math.min(duration, w.end + 0.04).toFixed(2))
          ]
        )
      ]
    })
    setNotice(`Muted ${flagged.length} flagged word(s) — audio + captions clean after Apply`)
  }

  const wordMuted = (w: Word): boolean =>
    edit.muted_words.some((m) => Math.abs(m.start - w.start) < 0.03 && m.word === w.word)

  const toggleWord = (w: Word): void => {
    if (wordMuted(w)) {
      push({
        ...edit,
        muted_words: edit.muted_words.filter(
          (m) => !(Math.abs(m.start - w.start) < 0.03 && m.word === w.word)
        ),
        mutes: edit.mutes.filter(
          (m) => !(Math.abs(m[0] - Math.max(0, w.start - 0.04)) < 0.05)
        )
      })
    } else {
      const range: Range = [
        Number(Math.max(0, w.start - 0.04).toFixed(2)),
        Number(Math.min(duration, w.end + 0.04).toFixed(2))
      ]
      push({
        ...edit,
        muted_words: [...edit.muted_words, { start: w.start, end: w.end, word: w.word }],
        mutes: [...edit.mutes, range]
      })
    }
  }

  // Trim handles: drag the outer edges of the first/last kept segment. Global
  // listeners are attached ONCE (they read live state via refs), so dragging
  // doesn't churn subscriptions.
  const dragStartEdit = useRef<EditData | null>(null)
  useEffect(() => {
    const move = (e: PointerEvent): void => {
      if (!dragging.current) return
      const rect = barRef.current?.getBoundingClientRect()
      if (!rect) return
      const t = Math.max(0, Math.min(duration, ((e.clientX - rect.left) / rect.width) * duration))
      setEdit((prev) => {
        const k = (prev.keep ?? [[0, duration]]).map((r) => [...r] as Range)
        if (dragging.current === 'start') k[0][0] = Math.min(t, k[0][1] - MIN_SEG)
        else k[k.length - 1][1] = Math.max(t, k[k.length - 1][0] + MIN_SEG)
        return { ...prev, keep: k }
      })
    }
    const up = (): void => {
      if (dragging.current) {
        dragging.current = null
        if (dragStartEdit.current) {
          const before = dragStartEdit.current
          setHistory((h) => [...h.slice(-30), before]) // undo restores pre-drag
          dragStartEdit.current = null
        }
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [duration])

  const layoutDirty = layout !== storedCrop
  const splitDirty = splitPos !== storedSplitPos
  const styleDirty = JSON.stringify(captionStyle) !== JSON.stringify(storedStyle)
  const wmDirty = JSON.stringify(watermark) !== JSON.stringify(storedWatermark)
  const dirty =
    layoutDirty ||
    splitDirty ||
    styleDirty ||
    wmDirty ||
    wordEdits.length > 0 ||
    JSON.stringify(edit) !== JSON.stringify({ ...defaultEdit(duration), ...(baked ?? {}) })
  const pendingJson = (): string =>
    JSON.stringify({ e: edit, l: layout, p: splitPos, s: captionStyle, w: wordEdits, m: watermark })
  const draftStale = draftActive && draftEditJson !== pendingJson()

  // Word mutes also CENSOR the word in the burned captions (f**k), so
  // platform moderation can't read it from the screen either. Recomputed
  // from the base lines every time, so un-muting restores the real text.
  // Whisper words often carry punctuation ("word," / "word.") — matching
  // uses the stripped core so the caption is always found and replaced.
  const censorMatch = (matched: string): string =>
    matched.length <= 2 ? '**' : matched[0] + '*'.repeat(matched.length - 2) + matched[matched.length - 1]

  const applyTextEdits = (base: CaptionLine[]): CaptionLine[] => {
    const coreRe = (word: string): RegExp | null => {
      const core = word.replace(/[^a-zA-Z0-9']/g, '')
      if (!core) return null
      return new RegExp(`\\b${core.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i')
    }
    return base.map((line) => {
      let text = line.text
      // Text corrections first (double-clicked words) …
      for (const e of wordEdits) {
        if (e.start >= line.start - 0.05 && e.start < line.end + 0.05) {
          const re = coreRe(e.from)
          if (re) text = text.replace(re, e.to)
        }
      }
      // … then censoring of muted words.
      for (const m of edit.muted_words) {
        if (m.end > line.start && m.start < line.end) {
          const re = coreRe(m.word)
          if (re) text = text.replace(re, censorMatch)
        }
      }
      return { ...line, text }
    })
  }

  const pendingCaptionLines = (): CaptionLine[] | null => {
    const hasMutes = edit.muted_words.length > 0 || (baked?.muted_words?.length ?? 0) > 0
    if (!captionBase || (!hasMutes && wordEdits.length === 0)) return null
    return applyTextEdits(captionBase)
  }

  // ---- live text overlay ---------------------------------------------------
  // Pending hook titles and caption changes (style, corrections, censoring)
  // are drawn as DOM text over the preview video, so they show INSTANTLY —
  // no Update-preview render needed just to see a font or a fixed word.
  // Runs every render but is JSON-guarded, so the parent only re-renders
  // when the payload actually changes (no setState feedback loop).
  const lastOverlayJson = useRef('')
  useEffect(() => {
    let payload: LiveOverlay | null = null
    if (!draftActive) {
      const hookPending =
        edit.hook?.text && JSON.stringify(edit.hook) !== JSON.stringify(baked?.hook ?? null)
          ? edit.hook
          : null
      const capsDirty =
        styleDirty ||
        wordEdits.length > 0 ||
        edit.muted_words.length > (baked?.muted_words?.length ?? 0)
      let captions: LiveOverlay['captions'] = null
      if (capsDirty && captionBase && captionBase.length > 0) {
        let base = captionBase
        const n = Math.max(1, captionStyle.words_per_caption)
        // Regroup from the transcript when words-per-caption changed, the
        // same way the render does (captionBase kept the old grouping).
        if (words.length > 0 && n !== storedStyle.words_per_caption) {
          base = []
          for (let i = 0; i < words.length; i += n) {
            const g = words.slice(i, i + n)
            base.push({
              start: g[0].start,
              end: g[g.length - 1].end,
              text: g.map((w) => w.word).join(' ')
            })
          }
        }
        captions = { lines: applyTextEdits(base), style: captionStyle }
      }
      if (hookPending || captions) {
        // Old text already burned into the preview file — the overlay blurs
        // it out underneath the pending text so it doesn't ghost through.
        const captionsBurned =
          clip.render_opts?.captions !== false && (captionBase?.length ?? 0) > 0
        payload = {
          hook: hookPending,
          captions,
          bakedKeep: baked?.keep,
          keep,
          burned:
            captions && captionsBurned && captionBase
              ? { lines: captionBase, style: storedStyle }
              : null,
          burnedHook: hookPending && baked?.hook?.text ? { seconds: baked.hook.seconds } : null
        }
      }
    }
    const json = JSON.stringify(payload)
    if (json !== lastOverlayJson.current) {
      lastOverlayJson.current = json
      onLiveOverlay(payload)
    }
  })
  // Clear the overlay when the editor unmounts (e.g. back to clips).
  useEffect(() => () => onLiveOverlay(null), [])

  const updatePreview = async (): Promise<void> => {
    setBusy(true)
    setNotice('Rendering preview (real render — same framing as the export, up to a minute)…')
    try {
      const res = await api.previewClip(
        clip.id,
        isDefault(edit, duration) ? null : edit,
        pendingCaptionLines(),
        layout,
        styleDirty ? captionStyle : null,
        wmDirty ? (watermark ?? {}) : undefined,
        splitDirty ? splitPos : null
      )
      setDraftEditJson(pendingJson())
      onPreview(res.url)
      setNotice('Preview loaded — this is exactly how the export will look')
    } catch (e) {
      setNotice(String(e))
    } finally {
      setBusy(false)
    }
  }

  const backToOriginal = (): void => {
    setDraftEditJson(null)
    onPreview(null)
  }

  const apply = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const cleared = isDefault(edit, duration)
      const renderOpts: Record<string, unknown> = { edit: cleared ? null : edit }
      if (layoutDirty) renderOpts.crop = layout
      if (splitDirty) renderOpts.split_position = splitPos
      if (styleDirty) renderOpts.caption_style = captionStyle
      if (wmDirty) renderOpts.watermark = watermark
      const lines = pendingCaptionLines()
      if (lines) renderOpts.caption_lines = lines
      await api.rerenderClip(clip.id, undefined, renderOpts)
      setNotice(cleared ? 'Restoring original — re-rendering…' : 'Applying edits — re-rendering…')
      onChanged()
    } catch (e) {
      setNotice(String(e))
    } finally {
      setBusy(false)
    }
  }

  const pct = (t: number): string => `${((t / duration) * 100).toFixed(2)}%`

  // ---- keyboard shortcuts -------------------------------------------------
  // Bound on the window so they work wherever you are in the editor, but
  // NEVER while typing (inputs, textareas, contenteditable) — otherwise
  // pressing "s" in the hook-title field would split the clip.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      const typing =
        !!t &&
        (t.tagName === 'INPUT' ||
          t.tagName === 'TEXTAREA' ||
          t.tagName === 'SELECT' ||
          t.isContentEditable)
      if (e.key === '?' && !typing) {
        e.preventDefault()
        setShowKeys((s) => !s)
        return
      }
      if (showKeys && e.key === 'Escape') {
        // Capture phase + stopPropagation: dismiss the cheat sheet WITHOUT
        // also tripping EditorModal's Escape-closes-the-editor handler.
        e.preventDefault()
        e.stopPropagation()
        setShowKeys(false)
        return
      }
      if (typing || busy) return

      const el = videoRef.current
      const mod = e.ctrlKey || e.metaKey
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        undo()
        return
      }
      if (mod) return // leave other Ctrl/Cmd combos to the OS

      switch (e.key) {
        case ' ':
        case 'k':
        case 'K':
          e.preventDefault()
          if (el) el.paused ? void el.play() : el.pause()
          break
        case 'ArrowLeft':
          e.preventDefault()
          seekOrig(playhead - (e.shiftKey ? 1 : 0.1))
          break
        case 'ArrowRight':
          e.preventDefault()
          seekOrig(playhead + (e.shiftKey ? 1 : 0.1))
          break
        case 'j':
        case 'J':
          e.preventDefault()
          seekOrig(playhead - 1)
          break
        case 'l':
        case 'L':
          e.preventDefault()
          seekOrig(playhead + 1)
          break
        case 's':
        case 'S':
          e.preventDefault()
          splitAtPlayhead()
          break
        case 'i':
        case 'I':
          e.preventDefault()
          trimToPlayhead('start')
          break
        case 'o':
        case 'O':
          e.preventDefault()
          trimToPlayhead('end')
          break
        case 'm':
        case 'M': {
          e.preventDefault()
          const w = words.find((x) => playhead >= x.start - 0.05 && playhead <= x.end + 0.05)
          if (w) toggleWord(w)
          else setNotice('No transcript word at the playhead to mute')
          break
        }
        case 'Delete':
        case 'Backspace':
          e.preventDefault()
          deleteSelected()
          break
        case '+':
        case '=':
          e.preventDefault()
          zoomTo(zoom * 1.5)
          break
        case '-':
        case '_':
          e.preventDefault()
          zoomTo(zoom / 1.5)
          break
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  })

  return (
    <div className="border border-raised/60 rounded-lg p-3 space-y-3">
      {showRegions && (
        <ReactionRegions
          clipId={clip.id}
          onClose={() => setShowRegions(false)}
          onSaved={() => {
            setNotice('Regions saved — re-rendering this clip with them')
            onChanged()
          }}
        />
      )}
      {showKeys && (
        <div
          className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6"
          onClick={() => setShowKeys(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Keyboard shortcuts"
        >
          <div
            className="bg-surface border border-raised/60 rounded-2xl p-5 w-full max-w-md"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <p className="font-semibold inline-flex items-center gap-2">
                <Keyboard size={16} /> Keyboard shortcuts
              </p>
              <button
                className="text-muted hover:text-ink text-lg leading-none px-1"
                onClick={() => setShowKeys(false)}
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <dl className="text-sm space-y-1.5">
              {(
                [
                  ['Space / K', 'Play or pause'],
                  ['J / L', 'Back / forward 1 second'],
                  ['← / →', 'Step 0.1s (hold Shift for 1s)'],
                  ['S', 'Split at the playhead'],
                  ['I', 'Trim the start to the playhead'],
                  ['O', 'Trim the end to the playhead'],
                  ['M', 'Mute / un-mute the word at the playhead'],
                  ['Delete', 'Delete the section at the playhead'],
                  ['+ / −', 'Zoom the timeline (or Ctrl+scroll on it)'],
                  ['Ctrl + Z', 'Undo'],
                  ['?', 'Show or hide this list']
                ] as const
              ).map(([k, what]) => (
                <div key={k} className="flex items-center gap-3">
                  <dt className="shrink-0">
                    <kbd className="bg-raised border border-raised/80 rounded px-1.5 py-0.5 text-xs font-mono">
                      {k}
                    </kbd>
                  </dt>
                  <dd className="text-muted">{what}</dd>
                </div>
              ))}
            </dl>
            <p className="text-[11px] text-muted/70 mt-3">
              Shortcuts are ignored while you&apos;re typing in a text field.
            </p>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <p className="font-medium text-sm inline-flex items-center gap-1.5">
          <Film size={15} /> Edit video
        </p>
        <div className="flex gap-3 text-xs items-center">
          <button
            className="text-muted hover:text-ink inline-flex items-center gap-1.5"
            onClick={() => setShowKeys(true)}
            title="Keyboard shortcuts (?)"
            aria-label="Show keyboard shortcuts"
          >
            <Keyboard /> Shortcuts
          </button>
          <button
            className="text-muted hover:text-ink disabled:opacity-40 inline-flex items-center gap-1.5"
            disabled={history.length === 0}
            onClick={undo}
            title="Undo (Ctrl+Z)"
          >
            <UndoIcon /> Undo
          </button>
          <button
            className="text-muted hover:text-red-400"
            onClick={() => {
              push(defaultEdit(duration))
              setLayout('track')
              setSplitPos('top')
              setCaptionStyle({ ...DEFAULT_CAPTION_STYLE, ...(clip.render_opts?.caption_style ?? {}) })
              setWatermark(clip.render_opts?.watermark ?? null)
              setWordEdits([])
              setEditingWord(null)
            }}
          >
            Reset
          </button>
        </div>
      </div>

      {/* timeline — press and DRAG anywhere to scrub; zoom in for precise cuts */}
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 text-xs">
          <span className="text-muted mr-auto">
            Timeline · Ctrl+scroll to zoom{zoom > 1 ? ' · scroll to move around' : ''}
          </span>
          <button
            className="bg-raised w-6 h-6 rounded hover:bg-raised/70 disabled:opacity-40 leading-none"
            onClick={() => zoomTo(zoom / 1.5)}
            disabled={zoom <= 1}
            title="Zoom out (−)"
            aria-label="Zoom timeline out"
          >
            −
          </button>
          <span className="text-muted tabular-nums w-9 text-center">{zoom.toFixed(1)}×</span>
          <button
            className="bg-raised w-6 h-6 rounded hover:bg-raised/70 disabled:opacity-40 leading-none"
            onClick={() => zoomTo(zoom * 1.5)}
            disabled={zoom >= MAX_ZOOM}
            title="Zoom in (+)"
            aria-label="Zoom timeline in"
          >
            +
          </button>
          <button
            className="bg-raised px-2 h-6 rounded hover:bg-raised/70 disabled:opacity-40"
            onClick={() => zoomTo(1)}
            disabled={zoom <= 1}
            title="Fit the whole clip in view"
          >
            Fit
          </button>
        </div>
        <div ref={scrollRef} className="overflow-x-auto no-scrollbar bg-base rounded-md">
          <div
            ref={barRef}
            className="relative h-16 cursor-ew-resize select-none touch-none"
            style={{ width: `${zoom * 100}%` }}
            onPointerDown={startScrub}
            onPointerMove={moveScrub}
            onPointerUp={endScrub}
            onPointerCancel={endScrub}
            role="slider"
            aria-label="Clip timeline — drag to scrub, arrow keys to step"
            aria-valuemin={0}
            aria-valuemax={duration}
            aria-valuenow={playhead}
            aria-valuetext={fmt(playhead)}
            tabIndex={0}
          >
            {/* time ruler */}
            <div className="absolute top-0 inset-x-0 h-4 border-b border-raised/40 pointer-events-none">
              {ruler.ticks.map((t) => (
                <div key={t} className="absolute top-0 h-full" style={{ left: pct(t) }}>
                  <div className="absolute bottom-0 left-0 w-px h-1.5 bg-muted/60" />
                  <span className="absolute top-0 left-1 text-[9px] leading-4 text-muted/80 tabular-nums whitespace-nowrap">
                    {fmtTick(t)}
                  </span>
                </div>
              ))}
              {/* minor tick between each label */}
              {ruler.ticks.map((t) => (
                <div
                  key={`h${t}`}
                  className="absolute bottom-0 w-px h-1 bg-muted/30"
                  style={{ left: pct(t + ruler.step / 2) }}
                />
              ))}
            </div>
            {keep.map(([a, b], i) => (
              <div
                key={i}
                className={`absolute top-4 bottom-0 rounded-sm pointer-events-none ${
                  i === playheadSeg && keep.length > 1
                    ? 'bg-accent/60 ring-1 ring-accent'
                    : 'bg-accent/25'
                }`}
                style={{ left: pct(a), width: pct(b - a) }}
              />
            ))}
            {edit.mutes.map(([a, b], i) => (
              <div
                key={`m${i}`}
                className="absolute bottom-0 h-1.5 bg-red-500/80 rounded-sm pointer-events-none"
                style={{ left: pct(a), width: pct(Math.max(b - a, 0.1)) }}
              />
            ))}
            {/* trim handles */}
            <div
              className="absolute top-4 bottom-0 w-2 bg-accent rounded-l-md cursor-ew-resize"
              style={{ left: `calc(${pct(keep[0][0])} - 4px)` }}
              onPointerDown={(e) => {
                e.stopPropagation()
                dragging.current = 'start'
                dragStartEdit.current = editRef.current
              }}
              title="Drag to trim the start"
            />
            <div
              className="absolute top-4 bottom-0 w-2 bg-accent rounded-r-md cursor-ew-resize"
              style={{ left: `calc(${pct(keep[keep.length - 1][1])} - 4px)` }}
              onPointerDown={(e) => {
                e.stopPropagation()
                dragging.current = 'end'
                dragStartEdit.current = editRef.current
              }}
              title="Drag to trim the end"
            />
            {/* playhead: line + grab knob + live time readout */}
            <div
              className="absolute top-0 h-full pointer-events-none z-10"
              style={{ left: pct(playhead) }}
            >
              <div className="absolute inset-y-0 -left-px w-0.5 bg-white shadow" />
              <div className="absolute top-0 -left-1.5 w-3 h-3 rounded-full bg-white shadow ring-1 ring-black/30" />
              <span
                className={`absolute top-0 text-[10px] tabular-nums bg-black/70 text-white px-1 py-0.5 rounded whitespace-nowrap ${
                  playhead / Math.max(duration, 0.1) > 0.85 ? 'right-2.5' : 'left-2.5'
                }`}
              >
                {fmt(playhead)}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="flex gap-2 flex-wrap text-xs">
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 inline-flex items-center gap-1.5"
          onClick={() => trimToPlayhead('start')}
          title="Set the clip's START to the playhead (I)"
        >
          <TrimStart /> Trim start here
        </button>
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 inline-flex items-center gap-1.5"
          onClick={() => trimToPlayhead('end')}
          title="Set the clip's END to the playhead (O)"
        >
          <TrimEnd /> Trim end here
        </button>
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 inline-flex items-center gap-1.5"
          onClick={splitAtPlayhead}
          title="Split the section at the playhead (S)"
        >
          <Scissors /> Split at playhead
        </button>
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 disabled:opacity-40 inline-flex items-center gap-1.5"
          disabled={playheadSeg < 0 || keep.length <= 1}
          onClick={deleteSelected}
          title="Delete the section the playhead is in (Delete)"
        >
          <Trash /> Delete section
        </button>
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 disabled:opacity-40 inline-flex items-center gap-1.5"
          disabled={words.length < 2}
          onClick={tightenPauses}
          title="Automatically remove silences longer than 0.6s"
        >
          <Zap /> Tighten pauses
        </button>
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 disabled:opacity-40 inline-flex items-center gap-1.5"
          disabled={words.length === 0}
          onClick={censorProfanity}
          title="Mute every swear word — audio silenced and captions censored (f**k), so platforms can't flag either"
        >
          <Ban /> Censor swearing
        </button>
        <span className="text-muted self-center ml-auto tabular-nums">
          {keep.reduce((s, [a, b]) => s + (b - a), 0).toFixed(1)}s / {duration.toFixed(1)}s
        </span>
      </div>

      {/* ── editing tabs (grouped, CapCut-style) ── */}
      <div className="flex gap-0.5 border-b border-raised/60 text-xs overflow-x-auto" role="tablist">
        {TABS.map((t) => {
          const changed =
            (t.id === 'captions' && (styleDirty || wordEdits.length > 0)) ||
            (t.id === 'watermark' && wmDirty) ||
            (t.id === 'motion' &&
              ((edit.speed ?? 1) !== 1 || !!edit.hook || !!edit.music || layoutDirty || splitDirty))
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={activeTab === t.id}
              onClick={() => setActiveTab(t.id)}
              className={`px-3 py-1.5 -mb-px border-b-2 whitespace-nowrap ${
                activeTab === t.id
                  ? 'border-accent text-accent font-medium'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              <span aria-hidden>{t.icon}</span> {t.label}
              {changed && <span className="text-accent"> •</span>}
            </button>
          )
        })}
      </div>

      {/* transcript — click a word to mute it, or turn on text-editing mode */}
      {activeTab === 'captions' && words.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setTextMode(!textMode)
                setEditingWord(null)
              }}
              className={`text-xs px-2.5 py-1 rounded-md ${
                textMode ? 'bg-accent/20 text-accent font-medium' : 'bg-raised text-muted hover:text-ink'
              }`}
              title="Fix words the transcription got wrong — the burned caption text updates on Apply"
            >
              <Pencil className="mr-1.5" />
              Edit caption text{textMode ? ' — ON' : ''}
            </button>
            <span className="text-[11px] text-muted">
              {textMode
                ? 'Click a word below to retype it. Click the button again when done.'
                : 'Click a word to mute it (audio silent, caption censored).'}
            </span>
          </div>
          <div className="max-h-28 overflow-y-auto bg-base rounded-md p-2 leading-6">
            {words.map((w, i) => {
              if (editingWord?.i === i) {
                return (
                  <input
                    key={i}
                    autoFocus
                    value={editingWord.value}
                    onChange={(e) => setEditingWord({ i, value: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        const to = editingWord.value.trim()
                        setWordEdits((prev) => [
                          ...prev.filter((x) => x.start !== w.start),
                          ...(to && to !== w.word ? [{ start: w.start, from: w.word, to }] : [])
                        ])
                        setEditingWord(null)
                      }
                      if (e.key === 'Escape') setEditingWord(null)
                    }}
                    onBlur={() => setEditingWord(null)}
                    className="text-xs mr-1 w-24 bg-raised border border-accent rounded px-1"
                    aria-label={`Correct the word ${w.word}`}
                  />
                )
              }
              const inRemoved = removed.some(([a, b]) => w.start >= a && w.end <= b)
              const muted = wordMuted(w)
              const corrected = wordEdits.find((x) => x.start === w.start)
              return (
                <button
                  key={i}
                  onClick={() =>
                    textMode
                      ? setEditingWord({ i, value: corrected?.to ?? w.word })
                      : toggleWord(w)
                  }
                  className={`text-xs mr-1 rounded px-0.5 ${
                    muted
                      ? 'line-through text-red-400 bg-red-500/10'
                      : corrected
                        ? 'text-accent bg-accent/10'
                        : inRemoved
                          ? 'text-muted/40 line-through'
                          : 'text-muted hover:text-ink hover:bg-raised'
                  }`}
                  title={
                    textMode
                      ? corrected
                        ? `Caption says “${corrected.to}” — click to change`
                        : 'Click to retype this word'
                      : muted
                        ? 'Un-mute this word (audio and caption come back)'
                        : 'Mute this word — audio goes silent and the caption shows it censored (f**k)'
                  }
                >
                  {corrected?.to ?? w.word}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* caption font & style for this clip */}
      {activeTab === 'captions' && (
        <div className="border border-raised/60 rounded-lg p-3">
          <p className="label mb-2">Caption font &amp; style</p>
          <CaptionStyleControls
            idPrefix={`clip-${clip.id}`}
            style={captionStyle}
            onChange={(key, value) => setCaptionStyle((s) => ({ ...s, [key]: value }))}
          />
        </div>
      )}

      {/* watermark / branding for this clip */}
      {activeTab === 'watermark' && (
        <div className="border border-raised/60 rounded-lg p-3 space-y-2">
          {!watermark ? (
            <button
              className="bg-raised px-2.5 py-1.5 rounded-md text-xs hover:bg-raised/70"
              onClick={() => setWatermark({ ...DEFAULT_WATERMARK })}
            >
              + Add a watermark to this clip
            </button>
          ) : (
            <>
              <div className="flex justify-end">
                <button
                  className="text-xs text-muted hover:text-red-400"
                  onClick={() => setWatermark(null)}
                  title="Remove the watermark from this clip"
                >
                  Remove watermark
                </button>
              </div>
              <WatermarkControls
                config={watermark}
                landscape={isLandscape}
                onChange={(patch) => setWatermark({ ...(watermark ?? DEFAULT_WATERMARK), ...patch })}
              />
              <p className="text-[11px] text-muted/70">Tip: drag it on the preview to place it exactly.</p>
            </>
          )}
        </div>
      )}

      {/* audio controls */}
      {activeTab === 'audio' && (
        <div className="flex items-center gap-3 flex-wrap text-xs">
        <label
          className="flex items-center gap-1.5 cursor-pointer"
          title="Remove ALL of the clip's audio — e.g. to post it with background music only"
        >
          <input
            type="checkbox"
            checked={edit.mute_all}
            onChange={(e) => push({ ...edit, mute_all: e.target.checked })}
          />
          Mute all
        </label>
        <label className="flex items-center gap-1.5">
          Volume
          <input
            type="range"
            min={0}
            max={200}
            value={Math.round(edit.volume * 100)}
            disabled={edit.mute_all}
            className="w-24 accent-[#38BDF8]"
            onChange={(e) => setEdit({ ...edit, volume: Number(e.target.value) / 100 })}
            onMouseUp={() => setHistory((h) => [...h.slice(-30), edit])}
          />
          <span className="tabular-nums w-8">{Math.round(edit.volume * 100)}%</span>
        </label>
        {(['fade_in', 'fade_out'] as const).map((k) => (
          <label key={k} className="flex items-center gap-1.5">
            {k === 'fade_in' ? 'Fade in' : 'Fade out'}
            <select
              value={edit[k]}
              onChange={(e) => push({ ...edit, [k]: Number(e.target.value) })}
              className="bg-base border border-raised rounded px-1 py-0.5"
            >
              <option value={0}>off</option>
              <option value={0.3}>0.3s</option>
              <option value={0.5}>0.5s</option>
              <option value={1}>1s</option>
            </select>
          </label>
        ))}
        </div>
      )}

      {/* layout override (vertical Shorts only) — in the Effects tab */}
      {activeTab === 'motion' && !isLandscape && (
        <div className="flex items-center gap-2 text-xs flex-wrap">
          <span className="text-muted">Layout</span>
          {(
            [
              ['track', 'Auto (AI)', 'The AI picks: subject tracking or letterbox as needed'],
              ['letterbox', 'Letterbox', 'Force the FULL frame on a blurred backdrop — use when the crop cuts someone off'],
              ['center', 'Center', 'Static center crop, no tracking'],
              [
                'reaction',
                'Reaction',
                'Reaction layout: keeps BOTH the creator and what they are reacting to visible — for webcam-over-content clips'
              ]
            ] as const
          ).map(([value, label, tip]) => (
            <button
              key={value}
              onClick={() => setLayout(value)}
              className={`px-2.5 py-1 rounded-md ${
                layout === value
                  ? 'bg-accent/20 text-accent font-medium'
                  : 'bg-raised text-muted hover:text-ink'
              }`}
              title={tip}
            >
              {label}
            </button>
          ))}
          {layout === 'reaction' && (
            <button
              className="px-2.5 py-1 rounded-md bg-accent/20 text-accent font-medium"
              onClick={() => setShowRegions(true)}
              title="Draw the webcam and content boxes on a frame of the source — exact, and remembered for this creator"
            >
              {t('Mark regions…')}
            </button>
          )}
          {layoutDirty && (
            <span className="text-muted">— shows in “Update preview”, saved on Apply</span>
          )}
          {/* Gaming split layout: which band the facecam goes in. Only kicks
              in when the AI detected a gameplay+webcam layout for this clip. */}
          <span className="text-muted ml-3">Facecam</span>
          {(
            [
              ['top', 'Top', 'Webcam band above the gameplay (classic Shorts layout)'],
              ['bottom', 'Bottom', 'Gameplay on top, webcam below — some creators prefer the game up high']
            ] as const
          ).map(([value, label, tip]) => (
            <button
              key={value}
              onClick={() => setSplitPos(value)}
              className={`px-2.5 py-1 rounded-md ${
                splitPos === value
                  ? 'bg-accent/20 text-accent font-medium'
                  : 'bg-raised text-muted hover:text-ink'
              }`}
              title={`${tip} — only affects gaming clips with a detected facecam`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {/* speed / hook title / music — the Effects tab */}
      {activeTab === 'motion' && (
        <div className="space-y-2 text-xs">
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-1.5">
            Speed
            <select
              value={edit.speed ?? 1}
              onChange={(e) => push({ ...edit, speed: Number(e.target.value) })}
              className="bg-base border border-raised rounded px-1 py-0.5"
            >
              {[0.75, 1, 1.25, 1.5, 2].map((s) => (
                <option key={s} value={s}>
                  {s}x
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 flex-1 min-w-48">
            Hook title
            <input
              value={edit.hook?.text ?? ''}
              placeholder="big top text for the first seconds, e.g. WAIT FOR IT…"
              className="flex-1 bg-base border border-raised rounded px-2 py-1"
              onChange={(e) =>
                setEdit({
                  ...edit,
                  hook: e.target.value.trim()
                    ? { text: e.target.value, seconds: edit.hook?.seconds ?? 3 }
                    : null
                })
              }
              onBlur={() => setHistory((h) => [...h.slice(-30), edit])}
            />
          </label>
          {edit.hook && (
            <select
              value={edit.hook.seconds}
              onChange={(e) => push({ ...edit, hook: { ...edit.hook!, seconds: Number(e.target.value) } })}
              className="bg-base border border-raised rounded px-1 py-0.5"
              aria-label="How long the hook title stays on screen"
            >
              {[2, 3, 5, 8].map((s) => (
                <option key={s} value={s}>
                  {s}s
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-1.5 flex-1 min-w-56">
            Music
            <input
              value={edit.music?.path ?? ''}
              placeholder="background music — loops under the clip"
              className="flex-1 bg-base border border-raised rounded px-2 py-1"
              onChange={(e) =>
                setEdit({
                  ...edit,
                  music: e.target.value.trim()
                    ? {
                        path: e.target.value,
                        volume: edit.music?.volume ?? 0.25,
                        duck: edit.music?.duck ?? true
                      }
                    : null
                })
              }
              onBlur={() => setHistory((h) => [...h.slice(-30), edit])}
            />
          </label>
          <button
            className="bg-raised px-2.5 py-1 rounded-md hover:bg-raised/70"
            onClick={async () => {
              const path = await window.studio.pickAudioFile()
              if (path) {
                push({
                  ...edit,
                  music: {
                    path,
                    volume: edit.music?.volume ?? 0.25,
                    duck: edit.music?.duck ?? true
                  }
                })
              }
            }}
            title="Choose a music file from your computer"
          >
            <Folder className="mr-1.5" />
            Browse…
          </button>
          {edit.music && (
            <>
              <label className="flex items-center gap-1.5">
                Vol
                <input
                  type="range"
                  min={5}
                  max={100}
                  value={Math.round(edit.music.volume * 100)}
                  className="w-20 accent-[#38BDF8]"
                  onChange={(e) =>
                    setEdit({ ...edit, music: { ...edit.music!, volume: Number(e.target.value) / 100 } })
                  }
                />
              </label>
              <label className="flex items-center gap-1.5 cursor-pointer" title="Music automatically dips whenever the creator talks">
                <input
                  type="checkbox"
                  checked={edit.music.duck}
                  onChange={(e) => push({ ...edit, music: { ...edit.music!, duck: e.target.checked } })}
                />
                Duck under voice
              </label>
            </>
          )}
        </div>
        </div>
      )}

      {/* Color and AI-edit tabs (moved in from the editor's side column). */}
      {activeTab === 'color' && (
        <ColorControls clip={clip} videoRef={videoRef} onChanged={onChanged} />
      )}
      {activeTab === 'ai' && <EditChat clip={clip} onQueued={setNotice} />}

      {/* draft preview: the real result — captions, hook, music, everything */}
      <div className="flex gap-2 items-center flex-wrap">
        <button
          className="bg-raised px-3 py-1.5 rounded-md text-xs font-medium hover:bg-raised/70 disabled:opacity-40"
          disabled={busy}
          onClick={updatePreview}
          title="Renders the clip for real with ALL pending changes — same framing/zoom, tracking, captions, hook, music and speed as the final export. Can take up to a minute."
        >
          {busy ? (
            'Rendering…'
          ) : (
            <span className="inline-flex items-center gap-1.5">
              <Refresh /> Update preview
            </span>
          )}
        </button>
        {draftActive && (
          <button
            className="text-xs text-muted hover:text-ink inline-flex items-center gap-1.5"
            onClick={backToOriginal}
          >
            <Film /> Show rendered clip
          </button>
        )}
        {draftStale && (
          <span className="text-xs text-yellow-400">
            Edits changed since this draft — update the preview
          </span>
        )}
      </div>

      <button
        className="btn-accent w-full disabled:opacity-40"
        disabled={busy || !dirty}
        onClick={apply}
        title={dirty ? 'Re-render the clip with your changes' : 'No changes yet — edit something first'}
      >
        {busy ? 'Queuing…' : dirty ? 'Apply edits (re-render)' : 'Apply edits — no changes yet'}
      </button>
      {notice && <p className="text-xs text-muted">{notice}</p>}
      <p className="text-[11px] text-muted/70">
        Cuts, mutes and speed are simulated instantly as you play. For the TRUE result — updated
        caption text, hook title, music, exact framing — press “Update preview” (a real render,
        up to a minute). Nothing is final until Apply.
      </p>
    </div>
  )
}

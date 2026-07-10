import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { CaptionLine, Clip, EditData, Word } from '../lib/types'

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

function bakedToOrig(t: number, bakedKeep: Range[] | undefined): number {
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
  onPreview
}: {
  clip: Clip
  videoRef: React.RefObject<HTMLVideoElement>
  onChanged: () => void
  onPreview: (url: string | null) => void
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
  const [selectedSeg, setSelectedSeg] = useState<number | null>(null)
  const [playhead, setPlayhead] = useState(0) // original-timeline seconds
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  // Layout override: Auto = the AI decides (tracking/letterbox), Letterbox =
  // force the full frame on a blurred backdrop, Center = static center crop.
  const storedCrop = clip.render_opts?.crop ?? 'track'
  const [layout, setLayout] = useState<string>(storedCrop)
  const isLandscape = !!clip.render_opts?.profile
  // Draft preview: when set, the video element shows a low-res render with
  // ALL edits baked in — live simulation must be off (it would double-apply).
  const [draftEditJson, setDraftEditJson] = useState<string | null>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const dragging = useRef<'start' | 'end' | null>(null)

  const keep: Range[] = edit.keep ?? [[0, duration]]
  const removed = removedRanges(keep, duration)
  const bakedRemoved = baked?.keep ? removedRanges(baked.keep, duration) : []

  useEffect(() => {
    setEdit({ ...defaultEdit(duration), ...(clip.render_opts?.edit ?? {}) })
    setHistory([])
    setSelectedSeg(null)
    setNotice('')
    setDraftEditJson(null)
    setLayout(clip.render_opts?.crop ?? 'track')
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

  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    if (draftActive) {
      // The preview file already has every edit baked in — play it as-is
      // (don't force-unmute: that made toggles feel dead while previewing).
      el.playbackRate = 1
      return
    }
    let raf = 0
    let lastShown = -1
    const tick = (): void => {
      raf = requestAnimationFrame(tick)
      const tOrig = bakedToOrig(el.currentTime, baked?.keep)
      if (Math.abs(tOrig - lastShown) > 0.03) {
        lastShown = tOrig
        setPlayhead(tOrig)
      }
      if (!el.paused) {
        // Removed in the working edit but not yet baked -> jump over it.
        for (const [a, b] of removed) {
          const alreadyBaked = bakedRemoved.some(([x, y]) => a >= x - 0.05 && b <= y + 0.05)
          if (!alreadyBaked && tOrig > a + 0.02 && tOrig < b - 0.02) {
            el.currentTime = origToBaked(Math.min(b + 0.02, duration), baked?.keep)
            return
          }
        }
      }
      const inNewMute =
        edit.mutes.some(([a, b]) => tOrig >= a - 0.02 && tOrig <= b + 0.02) &&
        !(baked?.mutes ?? []).some(([a, b]) => tOrig >= a && tOrig <= b)
      el.muted = edit.mute_all || inNewMute
      if (!el.muted) el.volume = Math.max(0, Math.min(1, edit.volume))
      // Live speed preview (relative to any speed already baked in).
      el.playbackRate = Math.max(0.25, (edit.speed ?? 1) / (baked?.speed ?? 1))
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      el.muted = false
      el.volume = 1
      el.playbackRate = 1
    }
  }, [edit, baked, removed, bakedRemoved, duration, videoRef, draftActive])

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

  const seekOrig = (t: number): void => {
    const el = videoRef.current
    if (el) el.currentTime = origToBaked(Math.max(0, Math.min(duration, t)), baked?.keep)
  }

  const clickTimeline = (e: React.MouseEvent): void => {
    const rect = barRef.current?.getBoundingClientRect()
    if (!rect) return
    seekOrig(((e.clientX - rect.left) / rect.width) * duration)
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
    if (selectedSeg === null || keep.length <= 1) return
    push({ ...edit, keep: keep.filter((_, i) => i !== selectedSeg) })
    setSelectedSeg(null)
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

  // Trim handles: drag the outer edges of the first/last kept segment.
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
        setHistory((h) => [...h.slice(-30), edit])
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [duration, edit])

  const layoutDirty = layout !== storedCrop
  const dirty =
    layoutDirty ||
    JSON.stringify(edit) !== JSON.stringify({ ...defaultEdit(duration), ...(baked ?? {}) })
  const draftStale = draftActive && draftEditJson !== JSON.stringify({ e: edit, l: layout })

  // Word mutes also CENSOR the word in the burned captions (f**k), so
  // platform moderation can't read it from the screen either. Recomputed
  // from the base lines every time, so un-muting restores the real text.
  // Whisper words often carry punctuation ("word," / "word.") — matching
  // uses the stripped core so the caption is always found and replaced.
  const censorMatch = (matched: string): string =>
    matched.length <= 2 ? '**' : matched[0] + '*'.repeat(matched.length - 2) + matched[matched.length - 1]

  const pendingCaptionLines = (): CaptionLine[] | null => {
    if (!captionBase || (edit.muted_words.length === 0 && (baked?.muted_words?.length ?? 0) === 0))
      return null
    return captionBase.map((line) => {
      let text = line.text
      for (const m of edit.muted_words) {
        if (m.end > line.start && m.start < line.end) {
          const core = m.word.replace(/[^a-zA-Z0-9']/g, '')
          if (!core) continue
          const re = new RegExp(`\\b${core.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i')
          text = text.replace(re, censorMatch)
        }
      }
      return { ...line, text }
    })
  }

  const updatePreview = async (): Promise<void> => {
    setBusy(true)
    setNotice('Rendering preview (real render — same framing as the export, up to a minute)…')
    try {
      const res = await api.previewClip(
        clip.id,
        isDefault(edit, duration) ? null : edit,
        pendingCaptionLines(),
        layout
      )
      setDraftEditJson(JSON.stringify({ e: edit, l: layout }))
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

  return (
    <div className="border border-raised/60 rounded-lg p-3 space-y-3">
      <div className="flex items-center justify-between">
        <p className="font-medium text-sm">✂ Edit video</p>
        <div className="flex gap-2 text-xs">
          <button className="text-muted hover:text-ink disabled:opacity-40" disabled={history.length === 0} onClick={undo}>
            ↩ Undo
          </button>
          <button
            className="text-muted hover:text-red-400"
            onClick={() => push(defaultEdit(duration))}
          >
            Reset
          </button>
        </div>
      </div>

      {/* timeline */}
      <div
        ref={barRef}
        className="relative h-10 bg-base rounded-md cursor-pointer select-none touch-none"
        onClick={clickTimeline}
        role="slider"
        aria-label="Clip timeline — click to seek"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={playhead}
        tabIndex={0}
      >
        {keep.map(([a, b], i) => (
          <div
            key={i}
            onClick={(e) => {
              e.stopPropagation()
              setSelectedSeg(i === selectedSeg ? null : i)
            }}
            className={`absolute top-0 h-full rounded-sm ${
              i === selectedSeg ? 'bg-accent/60 ring-1 ring-accent' : 'bg-accent/25 hover:bg-accent/35'
            }`}
            style={{ left: pct(a), width: pct(b - a) }}
            title={`${a.toFixed(1)}s – ${b.toFixed(1)}s${keep.length > 1 ? ' (click to select)' : ''}`}
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
          className="absolute top-0 h-full w-2 bg-accent rounded-l-md cursor-ew-resize"
          style={{ left: `calc(${pct(keep[0][0])} - 4px)` }}
          onPointerDown={(e) => {
            e.stopPropagation()
            dragging.current = 'start'
          }}
          title="Drag to trim the start"
        />
        <div
          className="absolute top-0 h-full w-2 bg-accent rounded-r-md cursor-ew-resize"
          style={{ left: `calc(${pct(keep[keep.length - 1][1])} - 4px)` }}
          onPointerDown={(e) => {
            e.stopPropagation()
            dragging.current = 'end'
          }}
          title="Drag to trim the end"
        />
        <div
          className="absolute top-0 h-full w-0.5 bg-ink pointer-events-none"
          style={{ left: pct(playhead) }}
        />
      </div>

      <div className="flex gap-2 flex-wrap text-xs">
        <button className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70" onClick={splitAtPlayhead}>
          Split at playhead
        </button>
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 disabled:opacity-40"
          disabled={selectedSeg === null || keep.length <= 1}
          onClick={deleteSelected}
        >
          Delete section
        </button>
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 disabled:opacity-40"
          disabled={words.length < 2}
          onClick={tightenPauses}
          title="Automatically remove silences longer than 0.6s"
        >
          ⚡ Tighten pauses
        </button>
        <button
          className="bg-raised px-2.5 py-1.5 rounded-md hover:bg-raised/70 disabled:opacity-40"
          disabled={words.length === 0}
          onClick={censorProfanity}
          title="Mute every swear word — audio silenced and captions censored (f**k), so platforms can't flag either"
        >
          🚫 Censor swearing
        </button>
        <span className="text-muted self-center ml-auto tabular-nums">
          {keep.reduce((s, [a, b]) => s + (b - a), 0).toFixed(1)}s / {duration.toFixed(1)}s
        </span>
      </div>

      {/* transcript — click a word to mute it (audio + caption) */}
      {words.length > 0 && (
        <div className="max-h-28 overflow-y-auto bg-base rounded-md p-2 leading-6">
          {words.map((w, i) => {
            const inRemoved = removed.some(([a, b]) => w.start >= a && w.end <= b)
            const muted = wordMuted(w)
            return (
              <button
                key={i}
                onClick={() => toggleWord(w)}
                className={`text-xs mr-1 rounded px-0.5 ${
                  muted
                    ? 'line-through text-red-400 bg-red-500/10'
                    : inRemoved
                      ? 'text-muted/40 line-through'
                      : 'text-muted hover:text-ink hover:bg-raised'
                }`}
                title={
                  muted
                    ? 'Un-mute this word (audio and caption come back)'
                    : 'Mute this word — audio goes silent and the caption shows it censored (f**k)'
                }
              >
                {w.word}
              </button>
            )
          })}
        </div>
      )}

      {/* audio controls */}
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

      {/* layout override (vertical Shorts only) */}
      {!isLandscape && (
        <div className="flex items-center gap-2 text-xs flex-wrap">
          <span className="text-muted">Layout</span>
          {(
            [
              ['track', 'Auto (AI)', 'The AI picks: subject tracking or letterbox as needed'],
              ['letterbox', 'Letterbox', 'Force the FULL frame on a blurred backdrop — use when the crop cuts someone off'],
              ['center', 'Center', 'Static center crop, no tracking']
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
          {layoutDirty && (
            <span className="text-muted">— shows in “Update preview”, saved on Apply</span>
          )}
        </div>
      )}

      {/* speed / hook title / music */}
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
            📂 Browse…
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

      {/* draft preview: the real result — captions, hook, music, everything */}
      <div className="flex gap-2 items-center flex-wrap">
        <button
          className="bg-raised px-3 py-1.5 rounded-md text-xs font-medium hover:bg-raised/70 disabled:opacity-40"
          disabled={busy}
          onClick={updatePreview}
          title="Renders the clip for real with ALL pending changes — same framing/zoom, tracking, captions, hook, music and speed as the final export. Can take up to a minute."
        >
          {busy ? 'Rendering…' : '🔄 Update preview'}
        </button>
        {draftActive && (
          <button className="text-xs text-muted hover:text-ink" onClick={backToOriginal}>
            ⟲ Show rendered clip
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

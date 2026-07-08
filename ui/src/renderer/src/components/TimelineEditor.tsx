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

function defaultEdit(duration: number): EditData {
  return {
    keep: [[0, duration]],
    mutes: [],
    muted_words: [],
    volume: 1,
    mute_all: false,
    fade_in: 0,
    fade_out: 0
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
    e.fade_out === 0
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
  onChanged
}: {
  clip: Clip
  videoRef: React.RefObject<HTMLVideoElement>
  onChanged: () => void
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
  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    const onTime = (): void => {
      const tOrig = bakedToOrig(el.currentTime, baked?.keep)
      setPlayhead(tOrig)
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
        edit.mutes.some(([a, b]) => tOrig >= a && tOrig <= b) &&
        !(baked?.mutes ?? []).some(([a, b]) => tOrig >= a && tOrig <= b)
      el.muted = edit.mute_all || inNewMute
      if (!el.muted) el.volume = Math.max(0, Math.min(1, edit.volume))
    }
    el.addEventListener('timeupdate', onTime)
    return () => {
      el.removeEventListener('timeupdate', onTime)
      el.muted = false
      el.volume = 1
    }
  }, [edit, baked, removed, bakedRemoved, duration, videoRef])

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

  const dirty = JSON.stringify(edit) !== JSON.stringify({ ...defaultEdit(duration), ...(baked ?? {}) })

  const apply = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const cleared = isDefault(edit, duration)
      const renderOpts: Record<string, unknown> = { edit: cleared ? null : edit }
      // Word mutes also remove the word from the captions: strip each muted
      // word from the caption line covering it (recomputed from the base
      // lines every time, so un-muting restores the text).
      if (captionBase && (edit.muted_words.length > 0 || (baked?.muted_words?.length ?? 0) > 0)) {
        renderOpts.caption_lines = captionBase.map((line) => {
          let text = line.text
          for (const m of edit.muted_words) {
            if (m.end > line.start && m.start < line.end) {
              const re = new RegExp(`\\s*\\b${m.word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i')
              text = text.replace(re, '').trim()
            }
          }
          return { ...line, text }
        })
      }
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
                title={muted ? 'Un-mute this word' : 'Mute this word (audio + caption)'}
              >
                {w.word}
              </button>
            )
          })}
        </div>
      )}

      {/* audio controls */}
      <div className="flex items-center gap-3 flex-wrap text-xs">
        <label className="flex items-center gap-1.5 cursor-pointer">
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

      {dirty && (
        <button className="btn-accent w-full" disabled={busy} onClick={apply}>
          {busy ? 'Queuing…' : 'Apply edits (re-render)'}
        </button>
      )}
      {notice && <p className="text-xs text-muted">{notice}</p>}
      <p className="text-[11px] text-muted/70">
        Preview simulates your edits instantly — nothing is final until you press Apply. Removed
        sections are skipped during playback; muted words are struck through.
      </p>
    </div>
  )
}

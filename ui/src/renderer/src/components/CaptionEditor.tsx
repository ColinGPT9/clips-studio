import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { CaptionLine, CaptionStyle, Clip } from '../lib/types'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'

/** View + edit the burned-in captions of one clip: fix transcription
 *  mistakes line by line, and restyle (font, colour, size, position, casing).
 *  Every save queues a re-render that burns the changes in. */
export default function CaptionEditor({
  clip,
  onQueued
}: {
  clip: Clip
  onQueued: (msg: string) => void
}): JSX.Element {
  const [lines, setLines] = useState<CaptionLine[] | null>(null)
  const [style, setStyle] = useState<Required<CaptionStyle>>({
    ...DEFAULT_CAPTION_STYLE,
    ...clip.render_opts?.caption_style
  })
  const [burn, setBurn] = useState<boolean>(clip.render_opts?.captions ?? true)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    setLines(null)
    setDirty(false)
    setOpen(false)
    setStyle({ ...DEFAULT_CAPTION_STYLE, ...clip.render_opts?.caption_style })
    setBurn(clip.render_opts?.captions ?? true)
  }, [clip.id])

  const load = async (): Promise<void> => {
    try {
      const res = await api.captions(clip.id)
      setLines(res.lines)
    } catch {
      setLines([])
    }
  }

  const toggleOpen = (): void => {
    setOpen(!open)
    if (!open && lines === null) load()
  }

  const editLine = (i: number, text: string): void => {
    if (!lines) return
    const next = [...lines]
    next[i] = { ...next[i], text }
    setLines(next)
    setDirty(true)
  }

  const setStyleField = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void => {
    setStyle((s) => ({ ...s, [key]: value }))
    setDirty(true)
  }

  const apply = async (): Promise<void> => {
    setBusy(true)
    try {
      await api.rerenderClip(clip.id, undefined, {
        captions: burn,
        caption_style: style,
        ...(lines ? { caption_lines: lines } : {})
      })
      setDirty(false)
      onQueued('Caption changes queued — the clip is re-rendering.')
    } catch (e) {
      onQueued(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-raised/60 rounded-lg">
      <button
        className="w-full text-left px-3 py-2 font-medium flex justify-between items-center hover:bg-raised/40 rounded-lg"
        onClick={toggleOpen}
        aria-expanded={open}
      >
        Captions &amp; style
        <span aria-hidden>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="p-3 space-y-4">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={burn}
              onChange={(e) => {
                setBurn(e.target.checked)
                setDirty(true)
              }}
            />
            Burn captions into this clip
          </label>

          <CaptionStyleControls idPrefix={`clip-${clip.id}`} style={style} onChange={setStyleField} />

          <div>
            <p className="label mb-1">Caption text (fix any transcription mistakes)</p>
            {lines === null ? (
              <p className="text-muted text-xs">Loading captions…</p>
            ) : lines.length === 0 ? (
              <p className="text-muted text-xs">No captions in this clip.</p>
            ) : (
              <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
                {lines.map((line, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className="text-[10px] text-muted tabular-nums w-14 shrink-0">
                      {line.start.toFixed(1)}s
                    </span>
                    <input
                      className="input !py-1 text-sm"
                      aria-label={`Caption at ${line.start.toFixed(1)} seconds`}
                      value={line.text}
                      onChange={(e) => editLine(i, e.target.value)}
                    />
                  </div>
                ))}
              </div>
            )}
            {lines !== null && lines.length > 0 && (
              <p className="text-[10px] text-muted mt-1">
                Tip: clear a line's text to remove that caption entirely.
              </p>
            )}
          </div>

          <button className="btn-accent w-full" onClick={apply} disabled={busy || !dirty}>
            {busy ? 'Queueing…' : 'Apply captions & style (re-render)'}
          </button>
        </div>
      )}
    </div>
  )
}

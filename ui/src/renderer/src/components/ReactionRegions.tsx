import { useEffect, useRef, useState } from 'react'
import { API_BASE, api } from '../lib/api'
import { t } from '../lib/i18n'

/** Draw the two regions a reaction clip is built from, on a real frame of
 *  the source video: the creator's webcam, and what they're reacting to.
 *
 *  This exists because detection cannot do it reliably — reaction content
 *  is full of other people (a speaker in the video looks exactly like a
 *  webcam), and the reacted content is often PAUSED while the creator
 *  talks over it, so motion and detail point at chat and UI instead.
 *  Drawing the boxes takes seconds and is exact; they're remembered for
 *  the creator, so their next video needs no setup at all. */

type Box = { x: number; y: number; w: number; h: number }
type Which = 'cam' | 'content'

// Mirrors reaction/compose.py plan(): the preview must show exactly what
// the render will produce, so the numbers live in one shape in both places.
const OUT_W = 1080
const OUT_H = 1920
const CAM_MIN_H = 380

function planPanes(
  cam: Box,
  content: Box,
  srcW: number,
  srcH: number,
  camTop: boolean
): { camY: number; camH: number; contentY: number; contentH: number; contentW: number } {
  const cPxW = Math.max(1, content.w * srcW)
  const cPxH = Math.max(1, content.h * srcH)
  let contentW = OUT_W
  let contentH = Math.round(OUT_W * (cPxH / cPxW))
  if (contentH > OUT_H - CAM_MIN_H) {
    contentH = OUT_H - CAM_MIN_H
    contentW = Math.min(OUT_W, Math.round(contentH * (cPxW / cPxH)))
  }
  const camH = Math.max(CAM_MIN_H, OUT_H - contentH)
  const y0 = Math.max(0, Math.round((OUT_H - contentH - camH) / 2))
  return camTop
    ? { camY: y0, camH, contentY: y0 + camH, contentH, contentW }
    : { contentY: y0, contentH, contentW, camY: y0 + contentH, camH }
}

/** Style that shows exactly crop `box` of the frame, filling a pane of the
 *  given size the way FFmpeg does (cover for the cam, exact for content). */
function cropStyle(
  box: Box,
  srcW: number,
  srcH: number,
  paneW: number,
  paneH: number
): React.CSSProperties {
  const cropW = Math.max(1, box.w * srcW)
  const cropH = Math.max(1, box.h * srcH)
  const scale = Math.max(paneW / cropW, paneH / cropH)
  return {
    position: 'absolute',
    width: srcW * scale,
    height: srcH * scale,
    left: -(box.x * srcW * scale) + (paneW - cropW * scale) / 2,
    top: -(box.y * srcH * scale),
    maxWidth: 'none'
  }
}

const DEFAULTS: Record<Which, Box> = {
  cam: { x: 0.02, y: 0.55, w: 0.3, h: 0.42 },
  content: { x: 0.02, y: 0.02, w: 0.95, h: 0.5 }
}

export default function ReactionRegions({
  clipId,
  frameUrl,
  initial,
  onClose,
  onSaved
}: {
  /** Editor: mark regions for an existing clip (saves + re-renders). */
  clipId?: number
  /** Dashboard: mark them before processing, on a frame from the URL. */
  frameUrl?: string
  initial?: { cam: number[]; content: number[]; camTop?: boolean } | null
  onClose: () => void
  /** Dashboard passes a handler; the editor omits it and we save + re-render. */
  onSaved: (regions?: { cam: number[]; content: number[] }, camTop?: boolean) => void
}): JSX.Element {
  const [boxes, setBoxes] = useState<Record<Which, Box>>(DEFAULTS)
  const [active, setActive] = useState<Which>('cam')
  const [applyCreator, setApplyCreator] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const [src, setSrc] = useState({ w: 1920, h: 1080 })
  const [camTop, setCamTop] = useState(true)
  const drag = useRef<{ mode: 'move' | 'resize'; ox: number; oy: number; box: Box } | null>(null)

  // Start from whatever is already known: the caller's values, else what is
  // saved for this clip (or this creator).
  useEffect(() => {
    if (initial) {
      setBoxes({ cam: fromArray(initial.cam), content: fromArray(initial.content) })
      if (initial.camTop !== undefined) setCamTop(initial.camTop)
      return
    }
    if (clipId === undefined) return
    api
      .reactionRegions(clipId)
      .then((r) => {
        if (r.regions) {
          setBoxes({
            cam: fromArray(r.regions.cam),
            content: fromArray(r.regions.content)
          })
        }
      })
      .catch(() => {})
  }, [clipId])

  const fromArray = (a: number[]): Box => ({ x: a[0], y: a[1], w: a[2], h: a[3] })

  const pos = (e: React.PointerEvent): { x: number; y: number } => {
    const r = frameRef.current!.getBoundingClientRect()
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }
  }

  const startDrag = (e: React.PointerEvent, which: Which, mode: 'move' | 'resize'): void => {
    e.stopPropagation()
    setActive(which)
    const p = pos(e)
    drag.current = { mode, ox: p.x, oy: p.y, box: { ...boxes[which] } }
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }

  const onMove = (e: React.PointerEvent): void => {
    if (!drag.current) return
    const p = pos(e)
    const d = drag.current
    const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))
    setBoxes((prev) => {
      const b = { ...d.box }
      if (d.mode === 'move') {
        b.x = clamp(d.box.x + (p.x - d.ox), 0, 1 - d.box.w)
        b.y = clamp(d.box.y + (p.y - d.oy), 0, 1 - d.box.h)
      } else {
        b.w = clamp(d.box.w + (p.x - d.ox), 0.05, 1 - d.box.x)
        b.h = clamp(d.box.h + (p.y - d.oy), 0.05, 1 - d.box.y)
      }
      return { ...prev, [active]: b }
    })
  }

  const save = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      const asArrays = {
        cam: [boxes.cam.x, boxes.cam.y, boxes.cam.w, boxes.cam.h],
        content: [boxes.content.x, boxes.content.y, boxes.content.w, boxes.content.h]
      }
      if (clipId === undefined) {
        // Dashboard: hand the regions back for the job, nothing to render yet.
        onSaved(asArrays, camTop)
        onClose()
        return
      }
      await api.saveReactionRegions(clipId, {
        cam: [boxes.cam.x, boxes.cam.y, boxes.cam.w, boxes.cam.h],
        content: [boxes.content.x, boxes.content.y, boxes.content.w, boxes.content.h],
        apply_to_creator: applyCreator,
        cam_position: camTop ? 'top' : 'bottom'
      })
      onSaved()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const colour: Record<Which, string> = { cam: '#38BDF8', content: '#22C55E' }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Reaction regions"
    >
      <div
        className="bg-surface border border-raised/60 rounded-2xl p-4 w-full max-w-3xl space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <p className="font-semibold">{t('Mark the webcam and the content')}</p>
          <button className="text-muted hover:text-ink px-1 text-lg leading-none" onClick={onClose}>
            ✕
          </button>
        </div>
        <p className="text-xs text-muted">
          {t(
            'Drag each box over the right part of the frame — blue around the creator’s webcam, green around what they are reacting to. Corner handle resizes.'
          )}
        </p>

        <div className="flex gap-2 text-xs">
          {(['cam', 'content'] as Which[]).map((w) => (
            <button
              key={w}
              onClick={() => setActive(w)}
              className={`px-3 py-1.5 rounded-md ${
                active === w ? 'bg-accent/20 text-accent font-medium' : 'bg-raised text-muted'
              }`}
            >
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm mr-1.5 align-middle"
                style={{ background: colour[w] }}
              />
              {w === 'cam' ? t('Webcam') : t('Reacting to')}
            </button>
          ))}
        </div>

        <div className="flex gap-4 items-start">
        <div
          ref={frameRef}
          className="relative flex-1 min-w-0 select-none touch-none bg-base rounded-lg overflow-hidden"
          onPointerMove={onMove}
          onPointerUp={() => (drag.current = null)}
          onPointerCancel={() => (drag.current = null)}
        >
          <img
            src={frameUrl ?? `${API_BASE}/clips/${clipId}/source-frame`}
            alt="Source frame"
            className="w-full block"
            draggable={false}
            onLoad={(e) =>
              setSrc({
                w: (e.target as HTMLImageElement).naturalWidth || 1920,
                h: (e.target as HTMLImageElement).naturalHeight || 1080
              })
            }
          />
          {(['content', 'cam'] as Which[]).map((w) => (
            <div
              key={w}
              className="absolute cursor-move"
              style={{
                left: `${boxes[w].x * 100}%`,
                top: `${boxes[w].y * 100}%`,
                width: `${boxes[w].w * 100}%`,
                height: `${boxes[w].h * 100}%`,
                border: `3px solid ${colour[w]}`,
                boxShadow: active === w ? `0 0 0 2px ${colour[w]}55` : 'none'
              }}
              onPointerDown={(e) => startDrag(e, w, 'move')}
            >
              <span
                className="absolute -top-5 left-0 text-[10px] px-1 rounded"
                style={{ background: colour[w], color: '#0B1220' }}
              >
                {w === 'cam' ? t('Webcam') : t('Reacting to')}
              </span>
              <span
                className="absolute -right-2 -bottom-2 w-4 h-4 rounded-sm cursor-nwse-resize"
                style={{ background: colour[w] }}
                onPointerDown={(e) => startDrag(e, w, 'resize')}
              />
            </div>
          ))}
        </div>

        {/* Live preview: the actual composition, updating as you drag. */}
        <div className="shrink-0 w-40">
          <p className="label mb-1">{t('Preview')}</p>
          <div
            className="relative bg-black rounded-lg overflow-hidden"
            style={{ width: 160, height: 284 }}
          >
            {(() => {
              const pw = 160
              const ph = 284
              const s2 = pw / OUT_W
              const pl = planPanes(boxes.cam, boxes.content, src.w, src.h, camTop)
              const panes: { key: Which; top: number; h: number; w: number }[] = [
                { key: 'cam', top: pl.camY * s2, h: pl.camH * s2, w: OUT_W * s2 },
                { key: 'content', top: pl.contentY * s2, h: pl.contentH * s2, w: pl.contentW * s2 }
              ]
              return panes.map((pane) => (
                <div
                  key={pane.key}
                  className="absolute overflow-hidden"
                  style={{
                    top: pane.top,
                    left: (pw - pane.w) / 2,
                    width: pane.w,
                    height: pane.h
                  }}
                >
                  <img
                    src={frameUrl ?? `${API_BASE}/clips/${clipId}/source-frame`}
                    alt=""
                    draggable={false}
                    style={cropStyle(boxes[pane.key], src.w, src.h, pane.w, pane.h)}
                  />
                </div>
              ))
            })()}
          </div>
          <button
            className="btn-ghost w-full mt-2 !py-1 text-xs"
            onClick={() => setCamTop(!camTop)}
            title="Which band the webcam goes in"
          >
            {camTop ? t('Camera on top') : t('Camera on bottom')}
          </button>
        </div>
        </div>

        {error && <p className="text-sm text-error">{error}</p>}
        <div className="flex items-center gap-3 justify-end">
          <label
            className={`flex items-center gap-2 text-xs text-muted mr-auto cursor-pointer ${
              clipId === undefined ? 'invisible' : ''
            }`}
          >
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={applyCreator}
              onChange={(e) => setApplyCreator(e.target.checked)}
            />
            {t('Remember for this creator’s future videos')}
          </label>
          <button className="btn-ghost" onClick={onClose} disabled={busy}>
            {t('Cancel')}
          </button>
          <button className="btn-accent" onClick={save} disabled={busy}>
            {busy ? t('Saving…') : clipId === undefined ? t('Use these regions') : t('Save & re-render')}
          </button>
        </div>
      </div>
    </div>
  )
}

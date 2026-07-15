import { useEffect, useRef, useState } from 'react'
import { API_BASE, api } from '../lib/api'
import type { Clip, WatermarkConfig } from '../lib/types'
import { Scissors } from './icons'
import TimelineEditor from './TimelineEditor'

/** Live, draggable watermark preview over the editor video. Shows the
 *  text/logo where it will burn in, updates as the controls change, and
 *  drag repositions it (switching to a 'custom' point) so a static
 *  watermark can be placed off anything important. */
function WatermarkOverlay({
  config,
  onChange
}: {
  config: WatermarkConfig
  onChange: (patch: Partial<WatermarkConfig>) => void
}): JSX.Element {
  const boxRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  const dragging = useRef(false)

  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const pos = config.position ?? 'bottom_right'
  const pad = (config.padding ?? 0.04) * 100
  const opacity = config.opacity ?? 0.85
  const moving = pos === 'moving'

  const place = (p: string): React.CSSProperties => {
    const s: React.CSSProperties = { position: 'absolute' }
    if (p === 'custom') {
      s.left = `${(config.x ?? 0.5) * 100}%`
      s.top = `${(config.y ?? 0.5) * 100}%`
      s.transform = 'translate(-50%, -50%)'
      return s
    }
    if (p.includes('top')) s.top = `${pad}%`
    if (p.includes('bottom')) s.bottom = `${pad}%`
    if (p.includes('left')) s.left = `${pad}%`
    if (p.includes('right')) s.right = `${pad}%`
    if (p === 'center') {
      s.top = '50%'
      s.left = '50%'
      s.transform = 'translate(-50%, -50%)'
    }
    return s
  }

  const Mark = ({ ghost }: { ghost?: boolean }): JSX.Element => {
    const showImg = (config.type === 'image' || config.type === 'both') && config.image_asset
    const showTxt = (config.type === 'text' || config.type === 'both') && config.text
    return (
      <div
        className="flex flex-col items-center gap-0.5 leading-none pointer-events-none"
        style={{ opacity: ghost ? opacity * 0.4 : opacity }}
      >
        {showImg && (
          <img
            src={api.brandingAssetUrl(config.image_asset!)}
            alt=""
            style={{ width: `${(config.scale ?? 0.18) * box.w}px` }}
            className="max-w-none"
            draggable={false}
          />
        )}
        {showTxt && (
          <span
            style={{
              color: config.color ?? '#FFFFFF',
              fontFamily: config.font,
              fontSize: `${Math.max(8, (config.font_size ?? 42) * (box.h / 1920))}px`,
              textShadow: config.shadow ? '0 1px 3px rgba(0,0,0,0.9)' : 'none',
              transform: config.rotation ? `rotate(${config.rotation}deg)` : undefined,
              fontWeight: 700,
              whiteSpace: 'nowrap'
            }}
          >
            {config.text}
          </span>
        )}
      </div>
    )
  }

  const startDrag = (e: React.PointerEvent): void => {
    if (moving) return
    dragging.current = true
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }
  const onMove = (e: React.PointerEvent): void => {
    if (!dragging.current) return
    const r = boxRef.current?.getBoundingClientRect()
    if (!r) return
    const x = Math.max(0.02, Math.min(0.98, (e.clientX - r.left) / r.width))
    const y = Math.max(0.02, Math.min(0.98, (e.clientY - r.top) / r.height))
    onChange({ position: 'custom', x: Number(x.toFixed(3)), y: Number(y.toFixed(3)) })
  }

  return (
    <div ref={boxRef} className="absolute inset-0 z-10 pointer-events-none">
      {moving ? (
        // Two faint ghosts at the side edge-centres show the L/R travel.
        <>
          <div style={{ position: 'absolute', top: '50%', left: `${pad}%`, transform: 'translateY(-50%)' }}>
            <Mark ghost />
          </div>
          <div style={{ position: 'absolute', top: '50%', right: `${pad}%`, transform: 'translateY(-50%)' }}>
            <Mark ghost />
          </div>
          <span className="absolute bottom-14 left-1/2 -translate-x-1/2 text-[10px] text-white/80 bg-black/40 px-1.5 py-0.5 rounded">
            moves left ↔ right
          </span>
        </>
      ) : (
        <div
          style={place(pos)}
          className="pointer-events-auto cursor-move touch-none ring-1 ring-white/0 hover:ring-white/40 rounded"
          onPointerDown={startDrag}
          onPointerMove={onMove}
          onPointerUp={() => (dragging.current = false)}
          title="Drag to reposition"
        >
          <Mark />
        </div>
      )}
    </div>
  )
}

/** Full-page editing workspace. */
export default function EditorView({
  clip,
  onClose,
  onChanged
}: {
  clip: Clip
  onClose: () => void
  onChanged: () => void
}): JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [previewSrc, setPreviewSrc] = useState<string | null>(null)
  const [watermark, setWatermark] = useState<WatermarkConfig | null>(clip.render_opts?.watermark ?? null)
  const isLandscape = !!clip.render_opts?.profile

  useEffect(() => {
    setPreviewSrc(null)
    setWatermark(clip.render_opts?.watermark ?? null)
  }, [clip.id])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={onClose}
          className="px-3 py-1.5 rounded-lg bg-raised text-sm hover:bg-raised/70"
        >
          ← Back to clips
        </button>
        <p className="font-semibold truncate inline-flex items-center gap-2">
          <Scissors size={15} /> Editing — {clip.title || clip.hook || 'Untitled clip'}
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5 items-start">
        <div className="lg:col-span-2 sticky top-6 space-y-2">
          <div
            className={`relative mx-auto max-w-full ${
              isLandscape ? 'w-full aspect-video' : 'h-[74vh] aspect-[9/16]'
            }`}
          >
            <video
              key={previewSrc ?? `clip-${clip.id}`}
              ref={videoRef}
              src={previewSrc ? `${API_BASE}${previewSrc}` : api.mediaUrl(clip.id)}
              controls
              autoPlay
              className="absolute inset-0 w-full h-full object-contain rounded-xl bg-base"
              aria-label="Editing preview"
            />
            {/* Live watermark overlay — hidden while a baked draft is shown. */}
            {watermark && !previewSrc && (
              <WatermarkOverlay
                config={watermark}
                onChange={(patch) => setWatermark({ ...watermark, ...patch })}
              />
            )}
            {previewSrc && (
              <span className="absolute top-2 left-2 z-20 bg-accent/90 text-black text-[10px] font-bold px-2 py-0.5 rounded">
                PREVIEW — all edits applied (not saved until Apply)
              </span>
            )}
          </div>
          {isLandscape && (
            <p className="text-center text-xs text-muted">Longform clip — 1920×1080 horizontal</p>
          )}
        </div>
        <div className="lg:col-span-3 min-w-0 space-y-4">
          <TimelineEditor
            clip={clip}
            videoRef={videoRef}
            onChanged={onChanged}
            onPreview={setPreviewSrc}
            watermark={watermark}
            setWatermark={setWatermark}
          />
        </div>
      </div>
    </div>
  )
}

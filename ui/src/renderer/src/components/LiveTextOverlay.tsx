import { useEffect, useRef, useState } from 'react'
import type { LiveOverlay } from '../lib/types'
import { bakedToOrig } from './TimelineEditor'

/** Live DOM rendition of PENDING burned-in text — the hook title and
 *  restyled/corrected captions — drawn over the editor video in the same
 *  place and proportions the render will burn them (ASS styles are
 *  calibrated on a 1920-tall canvas; everything here scales by
 *  boxHeight/1920, which holds for portrait and landscape alike).
 *  Shown only while those changes are pending, so users see caption style,
 *  text fixes, censoring and hooks instantly instead of waiting for the
 *  ~1 minute Update-preview render. */
export default function LiveTextOverlay({
  videoRef,
  overlay
}: {
  videoRef: React.RefObject<HTMLVideoElement>
  overlay: LiveOverlay | null
}): JSX.Element | null {
  const boxRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  const [t, setT] = useState(0) // playhead in original-clip seconds

  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [overlay === null])

  // Follow playback on rAF (timeupdate is too coarse for 1-3 word captions).
  const bakedKeep = overlay?.bakedKeep
  useEffect(() => {
    if (!overlay) return
    let raf = 0
    const tick = (): void => {
      raf = requestAnimationFrame(tick)
      const el = videoRef.current
      if (!el) return
      const tOrig = bakedToOrig(el.currentTime, bakedKeep)
      setT((prev) => (Math.abs(prev - tOrig) > 0.02 ? tOrig : prev))
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [videoRef, bakedKeep, overlay === null])

  if (!overlay || (!overlay.hook && !overlay.captions)) return null

  const s = box.h / 1920 // ASS values are calibrated at a 1920-tall canvas

  // The hook burns at 0..seconds of the FINAL cut — convert the original-
  // timeline playhead to elapsed kept-time under the current edit.
  const elapsed = overlay.keep.reduce((acc, [a, b]) => acc + Math.max(0, Math.min(t, b) - a), 0)
  const showHook = overlay.hook && overlay.hook.text && elapsed <= overlay.hook.seconds

  const line = overlay.captions?.lines.find((l) => t >= l.start && t <= l.end)
  const st = overlay.captions?.style
  const capSize = st ? Math.max(40, Math.min(140, st.font_size)) * s : 0
  const capPos: React.CSSProperties =
    st?.position === 'top'
      ? { top: 140 * s }
      : st?.position === 'middle'
        ? { top: '50%', transform: 'translateY(-50%)' }
        : { bottom: 440 * s }

  // Frosted strip over text that's ALREADY burned into the preview file, so
  // the old caption/hook doesn't ghost through behind the pending one.
  const burnedLine = overlay.burned?.lines.find((l) => t >= l.start && t <= l.end)
  const maskBand = (position: string, fontSizeAss: number, marginAss: number): JSX.Element => {
    const size = Math.max(40, Math.min(140, fontSizeAss)) * s
    const band: React.CSSProperties =
      position === 'top'
        ? { top: Math.max(0, marginAss * s - size * 0.35), height: size * 3 }
        : position === 'middle'
          ? { top: '50%', transform: 'translateY(-50%)', height: size * 3 }
          : { bottom: Math.max(0, marginAss * s - size * 0.35), height: size * 3 }
    return (
      <div
        className="absolute inset-x-0"
        style={{
          ...band,
          margin: `0 ${30 * s}px`,
          borderRadius: 10 * s,
          backdropFilter: 'blur(16px)',
          background: 'rgba(0,0,0,0.35)'
        }}
      />
    )
  }

  const outline = (px: number): React.CSSProperties => ({
    WebkitTextStroke: `${Math.max(1, px)}px black`,
    textShadow: `0 ${Math.max(1, 2 * s)}px ${Math.max(2, 5 * s)}px rgba(0,0,0,0.85)`
  })

  return (
    <div ref={boxRef} className="absolute inset-0 z-10 pointer-events-none overflow-hidden">
      {/* masks go first so pending text draws on top of them */}
      {burnedLine &&
        overlay.burned &&
        maskBand(
          overlay.burned.style.position,
          overlay.burned.style.font_size,
          overlay.burned.style.position === 'top' ? 140 : 440
        )}
      {overlay.burnedHook &&
        elapsed <= overlay.burnedHook.seconds &&
        maskBand('top', 96, 190)}
      {showHook && overlay.hook && (
        <p
          className="absolute inset-x-0 text-center font-black leading-tight"
          style={{
            top: 190 * s,
            fontFamily: '"Arial Black", Arial, sans-serif',
            fontSize: `${96 * s}px`,
            color: '#FFFFFF',
            padding: `0 ${60 * s}px`,
            ...outline(4.5 * s)
          }}
        >
          {overlay.hook.text}
        </p>
      )}
      {line && st && (
        <p
          className="absolute inset-x-0 text-center leading-snug"
          style={{
            ...capPos,
            fontFamily: st.font,
            fontSize: `${capSize}px`,
            fontWeight: 700,
            color: st.color,
            padding: `0 ${60 * s}px`,
            ...outline(3.5 * s)
          }}
        >
          {st.uppercase ? line.text.toUpperCase() : line.text}
        </p>
      )}
      {overlay.captions && (
        <span className="absolute top-2 right-2 bg-black/60 text-white/80 text-[10px] px-1.5 py-0.5 rounded">
          caption preview — Apply burns it in
        </span>
      )}
    </div>
  )
}

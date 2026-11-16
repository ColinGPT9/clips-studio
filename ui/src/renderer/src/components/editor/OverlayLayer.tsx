/** The overlay tracks (V2 and up) drawn over the program while it plays: each
 *  item on screen at the playhead, in its place, size, rotation and opacity,
 *  framed fit (the rest clear) or fill, as the export draws them. A muted
 *  video track is hidden, as the export hides it. Their sound is step 10's. */
import { useEffect, useRef } from 'react'
import { engineUrl } from '../../lib/editorApi'
import { overlayTracks, sourceAt } from '../../lib/editorProgram'
import { usePlayheadTime, usePlaying, type PlayheadStore } from '../../lib/editorPlayhead'
import type { Item, ProjectDoc } from '../../lib/editorTypes'
import { framingStyle, transformCss } from './programPlayer'
import { isInfo, playable, type MediaInfos } from './useMediaInfos'

function boxStyle(item: Item): React.CSSProperties {
  const f = framingStyle(item)
  const tr = transformCss(item.transform)
  return {
    objectFit: f.fit,
    objectPosition: f.position,
    transform: tr.transform || undefined,
    opacity: tr.opacity
  }
}

function OverlayVideo({ item, url, offsetUs, t, playing }: { item: Item; url: string; offsetUs: number; t: number; playing: boolean }): JSX.Element {
  const ref = useRef<HTMLVideoElement>(null)
  const target = (sourceAt(item, t) + offsetUs) / 1e6
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.playbackRate = item.speed || 1
    if (playing) {
      // Kept within a tenth of a second of the program; closer would seek
      // on every frame.
      if (Math.abs(el.currentTime - target) > 0.1) el.currentTime = target
      if (el.paused) void el.play().catch(() => undefined)
    } else {
      if (!el.paused) el.pause()
      if (Math.abs(el.currentTime - target) > 0.001) el.currentTime = target
    }
  }, [target, playing, item.speed])
  return (
    <video
      ref={ref}
      src={engineUrl(url)}
      muted
      playsInline
      preload="auto"
      aria-hidden="true"
      className="absolute inset-0 w-full h-full pointer-events-none"
      style={boxStyle(item)}
    />
  )
}

export default function OverlayLayer({
  doc,
  infos,
  playhead
}: {
  doc: ProjectDoc
  infos: MediaInfos
  playhead: PlayheadStore
}): JSX.Element | null {
  const t = usePlayheadTime(playhead)
  const playing = usePlaying(playhead)
  const tracks = overlayTracks(doc).filter((tr) => !tr.mute)
  if (tracks.length === 0) return null
  return (
    <>
      {tracks.map((track) =>
        track.items
          .filter((it) => it.media_id && it.start_us <= t && t < it.start_us + it.duration_us)
          .map((it) => {
            const info = infos[it.media_id as string]
            if (!isInfo(info)) return null
            if (info.kind === 'image')
              return (
                <img
                  key={it.id}
                  src={engineUrl(info.urls.source)}
                  alt=""
                  className="absolute inset-0 w-full h-full pointer-events-none"
                  style={boxStyle(it)}
                />
              )
            const p = playable(info)
            if (!p) return null
            return <OverlayVideo key={it.id} item={it} url={p.url} offsetUs={p.offsetUs} t={t} playing={playing} />
          })
      )}
    </>
  )
}

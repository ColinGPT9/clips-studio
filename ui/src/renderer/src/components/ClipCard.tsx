import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Clip } from '../lib/types'
import ScoreBadge from './ScoreBadge'

const PROFILE_BADGE: Record<string, string> = {
  short_clips: '▭ 16:9',
  clips_140: '▭ 16:9',
  highlights: '▭ Highlights',
  edited_stream: '▭ Edited stream'
}

export default function ClipCard({
  clip,
  selected,
  onClick
}: {
  clip: Clip
  selected: boolean
  onClick: () => void
}): JSX.Element {
  const duration = Math.round(clip.end_s - clip.start_s)
  const name = clip.title || clip.hook || 'Untitled clip'
  const profile = clip.render_opts?.profile
  const badge = profile ? (PROFILE_BADGE[profile] ?? '▭ 16:9') : null

  // Lazy-load the thumbnail. Chromium allows only ~6 connections per host, so
  // a grid of 100+ <video> elements pointed at the local server starves its
  // own connection pool — thumbnails stay blank AND the editor's own video
  // can't get a connection to play. Load a clip's video only once its card
  // nears the viewport, capping concurrent loads to what's on screen.
  const boxRef = useRef<HTMLDivElement>(null)
  const [show, setShow] = useState(false)
  useEffect(() => {
    const el = boxRef.current
    if (!el || show) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShow(true)
          io.disconnect()
        }
      },
      { rootMargin: '300px' } // start loading just before it scrolls in
    )
    io.observe(el)
    return () => io.disconnect()
  }, [show])
  // Matched a natural-language request. request_low_confidence means it was
  // guaranteed a slot despite being below the normal quality bar.
  const requested = clip.scores?.request
  const lowConf = clip.scores?.request_low_confidence
  return (
    <button
      onClick={onClick}
      aria-label={`${name}, ${duration} seconds, score ${clip.score}${
        badge ? ', horizontal longform' : ', vertical Short'
      }${selected ? ', selected' : ''}`}
      aria-pressed={selected}
      className={`text-left rounded-xl overflow-hidden bg-surface border transition-colors ${
        selected ? 'border-accent' : 'border-raised/60 hover:border-raised'
      }`}
    >
      <div ref={boxRef} className="aspect-[9/16] bg-base relative">
        {show ? (
          <video
            src={api.mediaUrl(clip.id)}
            preload="metadata"
            muted
            className={`w-full h-full ${badge ? 'object-contain' : 'object-cover'}`}
          />
        ) : (
          // Placeholder until the card scrolls into view — no network load.
          <div className="w-full h-full bg-base flex items-center justify-center text-muted/30 text-2xl">
            ▶
          </div>
        )}
        <span className="absolute top-2 left-2">
          <ScoreBadge score={clip.score} />
        </span>
        {badge && (
          <span className="absolute top-2 right-2 bg-amber-500/90 text-black px-1.5 py-0.5 rounded text-[10px] font-bold">
            {badge}
          </span>
        )}
        {requested && (
          <span
            className={`absolute ${badge ? 'top-8' : 'top-2'} right-2 px-1.5 py-0.5 rounded text-[10px] font-bold ${
              lowConf ? 'bg-sky-400/80 text-black' : 'bg-sky-500 text-white'
            }`}
            title={`You asked for: ${requested}${lowConf ? ' (closest match — lower confidence)' : ''}`}
          >
            ★ {lowConf ? 'Requested?' : 'Requested'}
          </span>
        )}
        <span className="absolute bottom-2 right-2 bg-base/80 px-1.5 py-0.5 rounded text-xs tabular-nums">
          {duration}s
        </span>
      </div>
      <div className="p-2.5">
        <p className="text-sm font-medium line-clamp-2">{clip.title || clip.hook || 'Untitled clip'}</p>
        {requested && (
          <p className="text-[11px] text-sky-400/90 mt-0.5 line-clamp-1" title={requested}>
            {requested}
          </p>
        )}
      </div>
    </button>
  )
}

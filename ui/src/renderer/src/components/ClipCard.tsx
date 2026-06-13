import { api } from '../lib/api'
import type { Clip } from '../lib/types'
import ScoreBadge from './ScoreBadge'

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
  return (
    <button
      onClick={onClick}
      aria-label={`${name}, ${duration} seconds, score ${clip.score}${selected ? ', selected' : ''}`}
      aria-pressed={selected}
      className={`text-left rounded-xl overflow-hidden bg-surface border transition-colors ${
        selected ? 'border-accent' : 'border-raised/60 hover:border-raised'
      }`}
    >
      <div className="aspect-[9/16] bg-base relative">
        <video
          src={api.mediaUrl(clip.id)}
          preload="metadata"
          muted
          className="w-full h-full object-cover"
        />
        <span className="absolute top-2 left-2">
          <ScoreBadge score={clip.score} />
        </span>
        <span className="absolute bottom-2 right-2 bg-base/80 px-1.5 py-0.5 rounded text-xs tabular-nums">
          {duration}s
        </span>
      </div>
      <div className="p-2.5">
        <p className="text-sm font-medium line-clamp-2">{clip.title || clip.hook || 'Untitled clip'}</p>
      </div>
    </button>
  )
}

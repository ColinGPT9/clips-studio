import { useEffect, useRef } from 'react'
import { api } from '../lib/api'
import type { Clip } from '../lib/types'
import TimelineEditor from './TimelineEditor'

/** Full-screen editing workspace: big preview on the left, the timeline
 *  editor with room to breathe on the right. Esc or ✕ closes it. */
export default function EditorModal({
  clip,
  onClose,
  onChanged
}: {
  clip: Clip
  onClose: () => void
  onChanged: () => void
}): JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Edit clip: ${clip.title || clip.hook || 'untitled'}`}
    >
      <div
        className="bg-surface border border-raised/60 rounded-2xl w-full max-w-6xl max-h-[92vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-raised/60 shrink-0">
          <p className="font-semibold truncate pr-4">
            ✂ Editing — {clip.title || clip.hook || 'Untitled clip'}
          </p>
          <button
            onClick={onClose}
            className="text-muted hover:text-ink text-xl leading-none px-2"
            aria-label="Close editor"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-5 gap-5 p-5 overflow-y-auto">
          <div className="md:col-span-2 flex items-start justify-center">
            <video
              key={clip.id}
              ref={videoRef}
              src={api.mediaUrl(clip.id)}
              controls
              autoPlay
              className="rounded-lg bg-base w-full max-h-[78vh] object-contain"
              aria-label="Editing preview — your edits are simulated live"
            />
          </div>
          <div className="md:col-span-3 min-w-0">
            <TimelineEditor clip={clip} videoRef={videoRef} onChanged={onChanged} />
          </div>
        </div>
      </div>
    </div>
  )
}

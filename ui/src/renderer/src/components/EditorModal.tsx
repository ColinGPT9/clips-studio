import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Clip } from '../lib/types'
import CaptionEditor from './CaptionEditor'
import ColorControls from './ColorControls'
import EditChat from './EditChat'
import TimelineEditor from './TimelineEditor'

/** Full-page editing workspace: replaces the clip grid while editing, so the
 *  normal app window can be moved/resized/maximized and the editor reflows
 *  with it. Big preview left; timeline, color, captions and AI chat right.
 *  Esc or "Back" returns to the clips. */
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
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const flash = (msg: string): void => {
    setNotice(msg)
    setTimeout(() => setNotice(''), 5000)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={onClose}
          className="px-3 py-1.5 rounded-lg bg-raised text-sm hover:bg-raised/70"
        >
          ← Back to clips
        </button>
        <p className="font-semibold truncate">
          ✂ Editing — {clip.title || clip.hook || 'Untitled clip'}
        </p>
        {notice && <p className="text-xs text-accent ml-auto">{notice}</p>}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5 items-start">
        <div className="lg:col-span-2 sticky top-6">
          <video
            key={clip.id}
            ref={videoRef}
            src={api.mediaUrl(clip.id)}
            controls
            autoPlay
            className="rounded-lg bg-base w-full max-h-[80vh] object-contain"
            aria-label="Editing preview — your edits are simulated live"
          />
        </div>
        <div className="lg:col-span-3 min-w-0 space-y-4">
          <TimelineEditor clip={clip} videoRef={videoRef} onChanged={onChanged} />
          <ColorControls clip={clip} videoRef={videoRef} onChanged={onChanged} />
          <CaptionEditor clip={clip} onQueued={flash} />
          <EditChat clip={clip} onQueued={flash} />
        </div>
      </div>
    </div>
  )
}

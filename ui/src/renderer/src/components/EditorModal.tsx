import { useEffect, useRef, useState } from 'react'
import { API_BASE, api } from '../lib/api'
import type { Clip } from '../lib/types'
import CaptionEditor from './CaptionEditor'
import ColorControls from './ColorControls'
import EditChat from './EditChat'
import PlatformOverlay, { PLATFORMS, type Platform } from './PlatformOverlay'
import TimelineEditor from './TimelineEditor'

/** Full-page editing workspace: replaces the clip grid while editing, so the
 *  normal app window can be moved/resized/maximized and the editor reflows
 *  with it. Big preview left (with TikTok/YT/IG UI overlays and draft
 *  previews); timeline, color, captions and AI chat right. */
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
  const [previewSrc, setPreviewSrc] = useState<string | null>(null)
  const [platform, setPlatform] = useState<Platform>('none')
  // Longform clips are 16:9 — the preview box follows the actual video.
  const isLandscape = !!clip.render_opts?.profile

  useEffect(() => {
    setPreviewSrc(null)
  }, [clip.id])

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
              className="absolute inset-0 w-full h-full object-cover rounded-xl bg-base"
              aria-label="Editing preview"
            />
            {!isLandscape && <PlatformOverlay platform={platform} />}
            {previewSrc && (
              <span className="absolute top-2 left-2 z-20 bg-accent/90 text-black text-[10px] font-bold px-2 py-0.5 rounded">
                PREVIEW — all edits applied (not saved until Apply)
              </span>
            )}
          </div>
          {!isLandscape && (
            <div className="flex justify-center gap-1.5">
              {PLATFORMS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setPlatform(p.id)}
                  className={`px-2.5 py-1 rounded-md text-xs ${
                    platform === p.id ? 'bg-accent/20 text-accent font-medium' : 'bg-raised text-muted hover:text-ink'
                  }`}
                  title="Preview how this platform's UI covers your video"
                >
                  {p.label}
                </button>
              ))}
            </div>
          )}
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
          />
          <ColorControls clip={clip} videoRef={videoRef} onChanged={onChanged} />
          <CaptionEditor clip={clip} onQueued={flash} />
          <EditChat clip={clip} onQueued={flash} />
        </div>
      </div>
    </div>
  )
}

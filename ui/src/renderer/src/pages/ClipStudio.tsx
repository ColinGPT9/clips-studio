import { useEffect, useMemo, useRef, useState } from 'react'
import ClipCard from '../components/ClipCard'
import ClipEditor from '../components/ClipEditor'
import EditorView from '../components/EditorModal'
import ProcessingBar from '../components/ProcessingBar'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import type { StudioTarget } from '../App'
import type { Clip, StudioEvent, Video } from '../lib/types'

/** Browse and edit the clips of processed videos. New videos are started from
 *  the Dashboard; clicking a clip there navigates here with it selected. */
export default function ClipStudio({
  target,
  onTargetConsumed
}: {
  target: StudioTarget | null
  onTargetConsumed: () => void
}): JSX.Element {
  const [videos, setVideos] = useState<Video[]>([])
  const [activeVideo, setActiveVideo] = useState<string | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [selectedClip, setSelectedClip] = useState<number | null>(null)
  const [editingClipId, setEditingClipId] = useState<number | null>(null)
  const [videoSearch, setVideoSearch] = useState('')
  const [clipType, setClipType] = useState<'all' | 'shorts' | 'longform'>('all')
  const pendingClip = useRef<number | null>(null)

  const refreshVideos = async (): Promise<void> => {
    try {
      const v = await api.videos()
      setVideos(v)
      if (!activeVideo && !target && v.length > 0) setActiveVideo(v[0].video_id)
    } catch {
      /* backend starting up */
    }
  }

  const deleteClip = async (clipId: number): Promise<void> => {
    try {
      await api.deleteClip(clipId)
      if (selectedClip === clipId) setSelectedClip(null)
      setClips((cur) => cur.filter((c) => c.id !== clipId)) // drop it immediately
    } catch (e) {
      window.alert(`Could not delete: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const refreshClips = async (videoId: string): Promise<void> => {
    try {
      const c = await api.clips(videoId)
      setClips(c)
      if (pendingClip.current !== null) {
        if (c.some((x) => x.id === pendingClip.current)) setSelectedClip(pendingClip.current)
        pendingClip.current = null
      }
    } catch {
      setClips([])
    }
  }

  useEffect(() => {
    refreshVideos()
  }, [])

  // Navigated here from a Dashboard clip link: jump to that video + clip.
  useEffect(() => {
    if (!target) return
    pendingClip.current = target.clipId ?? null
    setActiveVideo(target.videoId)
    onTargetConsumed()
  }, [target])

  useEffect(() => {
    if (activeVideo) refreshClips(activeVideo)
    if (pendingClip.current === null) setSelectedClip(null)
  }, [activeVideo])

  useEvents((e: StudioEvent) => {
    if (e.type === 'progress' && (e.stage === 'render' || e.stage === 'done') && e.video_id) {
      refreshVideos()
      if (activeVideo === e.video_id || !activeVideo) {
        if (!activeVideo) setActiveVideo(e.video_id)
        refreshClips(e.video_id)
      }
    }
    if (e.type === 'job' && e.status === 'done' && activeVideo) refreshClips(activeVideo)
  })

  const current = useMemo(() => clips.find((c) => c.id === selectedClip) ?? null, [clips, selectedClip])
  const editingClip = useMemo(
    () => clips.find((c) => c.id === editingClipId) ?? null,
    [clips, editingClipId]
  )

  const shownVideos = useMemo(() => {
    const q = videoSearch.trim().toLowerCase()
    if (!q) return videos
    return videos.filter(
      (v) =>
        (v.title || '').toLowerCase().includes(q) || (v.channel_name || '').toLowerCase().includes(q)
    )
  }, [videos, videoSearch])

  if (editingClip) {
    return (
      <div className="p-6">
        <EditorView
          clip={editingClip}
          onClose={() => setEditingClipId(null)}
          onChanged={() => activeVideo && refreshClips(activeVideo)}
        />
      </div>
    )
  }

  return (
    <div className="p-6 space-y-5">
      <h2 className="text-2xl font-bold">Clip Studio</h2>

      <ProcessingBar />

      {videos.length === 0 ? (
        <div className="card text-muted text-sm">
          No videos yet — head to the <span className="text-accent">Dashboard</span> and paste a
          link to make clips.
        </div>
      ) : (
        <input
          type="search"
          className="input !w-72"
          placeholder="Search your videos or channels…"
          aria-label="Search processed videos by title or channel"
          value={videoSearch}
          onChange={(e) => setVideoSearch(e.target.value)}
        />
      )}

      {shownVideos.length > 0 && (
        <div className="flex gap-2 flex-wrap">
          {shownVideos.map((v) => (
            <button
              key={v.video_id}
              onClick={() => setActiveVideo(v.video_id)}
              className={`px-3 py-1.5 rounded-lg text-sm max-w-64 truncate ${
                activeVideo === v.video_id
                  ? 'bg-accent/15 text-accent'
                  : 'bg-raised text-muted hover:text-ink'
              }`}
            >
              {v.channel_name ? `${v.channel_name} — ` : ''}
              {v.title || v.video_id}
            </button>
          ))}
        </div>
      )}

      {videos.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-5 gap-5 items-start">
          <div className="xl:col-span-3 space-y-3">
            <div className="flex gap-1.5" role="group" aria-label="Filter clips by format">
              {(
                [
                  ['all', 'All'],
                  ['shorts', '📱 Shorts'],
                  ['longform', '▭ Longform']
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setClipType(value)}
                  className={`px-2.5 py-1 rounded-md text-xs ${
                    clipType === value
                      ? 'bg-accent/20 text-accent font-medium'
                      : 'bg-raised text-muted hover:text-ink'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {clips
                .filter((c) =>
                  clipType === 'all'
                    ? true
                    : clipType === 'longform'
                      ? !!c.render_opts?.profile
                      : !c.render_opts?.profile
                )
                .map((clip) => (
                  <ClipCard
                    key={clip.id}
                    clip={clip}
                    selected={clip.id === selectedClip}
                    onClick={() => setSelectedClip(clip.id)}
                    onDelete={() => deleteClip(clip.id)}
                  />
                ))}
              {clips.length === 0 && (
                <p className="text-muted text-sm col-span-full">
                  No clips for this video yet — or pick another video above.
                </p>
              )}
            </div>
          </div>
          {/* sticky + self-start so the preview/player stays pinned while the
              clip grid scrolls; its containing block is the tall grid, giving
              it room to travel (an inner sticky can't — the cell is only as
              tall as its content). */}
          <div className="xl:col-span-2 self-start xl:sticky xl:top-6 xl:max-h-[calc(100vh-3rem)] xl:overflow-y-auto">
            {current ? (
              <ClipEditor
                clip={current}
                onChanged={() => activeVideo && refreshClips(activeVideo)}
                onOpenEditor={() => setEditingClipId(current.id)}
              />
            ) : (
              <div className="card text-muted text-sm">Select a clip to preview and edit it.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

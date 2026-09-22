import { useEffect, useMemo, useRef, useState } from 'react'
import NoClipsExplanation from '../components/NoClipsExplanation'
import ClipCard from '../components/ClipCard'
import ClipEditor from '../components/ClipEditor'
import EditorView from '../components/EditorModal'
import ProcessingBar from '../components/ProcessingBar'
import PublishAllDialog from '../components/PublishAllDialog'
import ScheduleView from '../components/ScheduleView'
import { api } from '../lib/api'
import type { Provider } from '../lib/uploadpost'
import { getExportFolder } from '../lib/exportFolder'
import { useEvents } from '../lib/useEvents'
import { useJobWatch } from '../lib/useJobWatch'
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
  // One clip published on its own. The same dialog as Publish all,
  // handed a list of one, so the platform picker and the daily budget
  // do not need a second implementation.
  const [publishOne, setPublishOne] = useState<Clip | null>(null)
  const [showSchedule, setShowSchedule] = useState(false)
  const [editingClipId, setEditingClipId] = useState<number | null>(null)
  const [videoSearch, setVideoSearch] = useState('')
  // Publishing is only offered once Upload-Post is switched on AND a key
  // is stored — the same rule the editor tab follows.
  const [publishReady, setPublishReady] = useState(false)
  // Which provider a batch goes through. WoopSocial when it is set up.
  const [publishProvider, setPublishProvider] = useState<Provider>('woopsocial')
  const [publishing, setPublishing] = useState(false)
  const [clipType, setClipType] = useState<'all' | 'shorts' | 'longform'>('all')
  const [exportingAll, setExportingAll] = useState(false)
  const [exportNotice, setExportNotice] = useState<string | null>(null)
  const pendingClip = useRef<number | null>(null)
  const lastEventAt = useRef(Date.now())
  // Only while something is in flight, so an idle page never polls.
  const busy = videos.some((v) => v.status !== 'done' && v.status !== 'failed')

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

  // The star on a card: mark or unmark a clip as exported by hand.
  const toggleExported = async (clip: Clip): Promise<void> => {
    try {
      const updated = await api.patchClip(clip.id, { exported: !clip.exported_at })
      setClips((cur) => cur.map((c) => (c.id === updated.id ? updated : c)))
    } catch (e) {
      window.alert(`Could not update: ${e instanceof Error ? e.message : String(e)}`)
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
    // Either provider makes batch publishing available.
    Promise.allSettled([api.woopSocialStatus(), api.uploadPostStatus()]).then(
      ([woop, up]) => {
        const wsReady =
          woop.status === 'fulfilled' && Boolean(woop.value.enabled && woop.value.has_key)
        const upReady =
          up.status === 'fulfilled' && Boolean(up.value.enabled && up.value.has_key)
        setPublishReady(wsReady || upReady)
        setPublishProvider(wsReady ? 'woopsocial' : 'uploadpost')
      }
    )
  }, [])

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
    setExportNotice(null)
  }, [activeVideo])

  useEvents((e: StudioEvent) => {
    lastEventAt.current = Date.now()
    if (e.type === 'progress' && (e.stage === 'render' || e.stage === 'done') && e.video_id) {
      refreshVideos()
      if (activeVideo === e.video_id || !activeVideo) {
        if (!activeVideo) setActiveVideo(e.video_id)
        refreshClips(e.video_id)
      }
    }
    if (e.type === 'job' && e.status === 'done' && activeVideo) refreshClips(activeVideo)
  })

  // Same reason as the Dashboard: the terminal event can be dropped for a
  // client that falls behind, and this page would then keep showing the clip
  // list from before the render.
  useJobWatch({
    active: busy,
    lastEventAt,
    onSettled: () => {
      refreshVideos()
      if (activeVideo) refreshClips(activeVideo)
    }
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

  const shownClips = useMemo(
    () =>
      clips.filter((c) =>
        clipType === 'all'
          ? true
          : clipType === 'longform'
            ? !!c.render_opts?.profile
            : !c.render_opts?.profile
      ),
    [clips, clipType]
  )
  const toExport = useMemo(() => shownClips.filter((c) => !c.exported_at), [shownClips])

  // Every clip under the current format filter that isn't starred yet, into
  // the same folder single exports use. Exporting stars them.
  const exportAll = async (): Promise<void> => {
    if (toExport.length === 0) return
    const folder = await getExportFolder()
    const skipped = shownClips.length - toExport.length
    setExportingAll(true)
    setExportNotice(null)
    try {
      const res = await api.exportBatch(
        toExport.map((c) => c.id),
        folder
      )
      const n = res.exported.length
      const missing = toExport.length - n
      setExportNotice(
        `Exported ${n} clip${n === 1 ? '' : 's'} to ${folder}.` +
          (skipped > 0 ? ` Skipped ${skipped} already starred as exported.` : '') +
          (missing > 0 ? ` ${missing} had no video file to copy.` : '')
      )
      if (activeVideo) await refreshClips(activeVideo)
    } catch (e) {
      setExportNotice(`Export failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setExportingAll(false)
    }
  }

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
      <h2 className="text-2xl font-bold">Clip Editor</h2>

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
            <div className="flex flex-wrap items-center gap-2">
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
              <button
                className="btn-accent !py-1 !px-3 text-xs ml-auto"
                onClick={exportAll}
                disabled={exportingAll || toExport.length === 0}
                title={
                  toExport.length === 0
                    ? 'Every clip here is already starred as exported'
                    : `Export the ${toExport.length} clip${toExport.length === 1 ? '' : 's'} here that are not starred yet`
                }
              >
                {exportingAll ? 'Exporting…' : `Export all (${toExport.length})`}
              </button>
              {/* Deliberately not styled as a twin of Export beside it.
                  Export writes files you can delete; this posts publicly and
                  cannot be undone, so it is quieter to look at and opens a
                  confirm rather than acting on the click. */}
              {publishReady && (
                <>
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  onClick={() => setPublishing(true)}
                  disabled={shownClips.length === 0}
                  title={`Publish the ${shownClips.length} clip${shownClips.length === 1 ? '' : 's'} shown here to your social accounts`}
                >
                  {`Publish all (${shownClips.length}) ↗`}
                </button>
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  onClick={() => setShowSchedule(true)}
                  title="When each scheduled post is due, and what actually posted"
                >
                  Schedule
                </button>
                </>
              )}
            </div>
            {exportNotice && <p className="text-sm text-accent">{exportNotice}</p>}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {shownClips.map((clip) => (
                <ClipCard
                  key={clip.id}
                  clip={clip}
                  selected={clip.id === selectedClip}
                  onClick={() => setSelectedClip(clip.id)}
                  onDelete={() => deleteClip(clip.id)}
                  onToggleExported={() => toggleExported(clip)}
                  onPublish={() => setPublishOne(clip)}
                />
              ))}
              {clips.length === 0 && (
                <div className="col-span-full">
                  <NoClipsExplanation
                    outcome={videos.find((v) => v.video_id === activeVideo)?.outcome}
                  />
                </div>
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
                onPublish={publishReady ? () => setPublishOne(current) : undefined}
              />
            ) : (
              <div className="card text-muted text-sm">Select a clip to preview and edit it.</div>
            )}
          </div>
        </div>
      )}

      {showSchedule && <ScheduleView onClose={() => setShowSchedule(false)} />}

      {publishOne && (
        <PublishAllDialog
          clips={[publishOne]}
          provider={publishProvider}
          onClose={() => {
            setPublishOne(null)
            if (activeVideo) refreshClips(activeVideo)
          }}
        />
      )}

      {publishing && (
        <PublishAllDialog
          clips={shownClips}
          provider={publishProvider}
          onClose={() => {
            setPublishing(false)
            if (activeVideo) refreshClips(activeVideo)
          }}
        />
      )}
    </div>
  )
}

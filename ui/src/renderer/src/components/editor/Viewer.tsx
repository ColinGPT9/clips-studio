/** The viewer, in the middle of the editor.
 *
 *  - Playing, it plays the program from the media themselves (ProgramPlayer:
 *    two alternating players, the next cut waiting on its first frame), with
 *    the overlay tracks drawn over it in their place.
 *  - Paused, it shows the engine's own frame for that moment ("pause shows the
 *    truth"): captions, text, watermark and colour exactly as the export
 *    draws them. One still is asked for at a time; a newer one cancels the
 *    older.
 *  - Preview renders the draft (a quick small copy of the whole video) and
 *    plays it.
 *  - A media from the bin can be shown on its own, to mark a part of it (I and
 *    O) and add that part to the end of the main track.
 *  - The phone apps' screens (TikTok, Reels, Shorts) can be drawn over a 9:16
 *    video, as the gaming layout preview does. */
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { t } from '../../lib/i18n'
import { EditorError, editorApi, engineUrl, errorText } from '../../lib/editorApi'
import {
  formatTime,
  frameAt,
  frameUs,
  isVertical916,
  isAiItem,
  programLength,
  v1Items
} from '../../lib/editorProgram'
import { createPlayhead, usePlayheadTime, usePlaying, type PlayheadStore } from '../../lib/editorPlayhead'
import type { EditorEvent, Fps, MediaJob, Project } from '../../lib/editorTypes'
import { loadPlatform, PLATFORM_CHOICES, savePlatform, type PlatformChoice } from '../../lib/newEditor'
import { SAFE_ZONES } from '../../lib/gamingLayout'
import type { StudioEvent } from '../../lib/types'
import { useEvents } from '../../lib/useEvents'
import PlatformOverlay, { PLATFORM_UI } from '../PlatformOverlay'
import OverlayLayer from './OverlayLayer'
import { ProgramPlayer } from './programPlayer'
import { isInfo, playable, type MediaInfos } from './useMediaInfos'

export interface ViewerHandle {
  togglePlay: () => void
  step: (frames: number) => void
  toStart: () => void
  toEnd: () => void
  seek: (tUs: number) => void
  markIn: () => void
  markOut: () => void
}

/** Key names in tooltips. */
const KEY_IN = 'I'
const KEY_OUT = 'O'

type Zoom = 'fit' | 0.25 | 0.5 | 1
const ZOOMS: Zoom[] = ['fit', 0.25, 0.5, 1]

/** The keys the viewer answers, for the tooltip that lists them. */
export const VIEWER_SHORTCUTS: [string, string][] = [
  ['Space', 'Play or pause'],
  ['← →', 'One frame back or forward'],
  [', .', 'One frame back or forward'],
  ['Home', 'Go to the start'],
  ['End', 'Go to the end'],
  ['I', 'Mark where the part starts'],
  ['O', 'Mark where the part ends']
]

interface Props {
  project: Project
  infos: MediaInfos
  playhead: PlayheadStore
  /** A media of the project shown on its own, or null for the program. */
  source: string | null
  onSource: (mediaId: string | null) => void
  /** Add a part of a media to the end of the main track. */
  onAddRange: (mediaId: string, inUs: number, outUs: number) => void
}

function TimeReadout({ store, fps, length }: { store: PlayheadStore; fps: Fps; length: number }): JSX.Element {
  const now = usePlayheadTime(store)
  return (
    <span className="tabular-nums text-xs text-muted" aria-live="off">
      <span className="text-ink">{formatTime(now, fps)}</span> / {formatTime(length, fps)}
    </span>
  )
}

/** The engine's frame for the moment under the playhead, while paused: the
 *  export's own picture ("pause shows the truth"). One request at a time: a
 *  newer one aborts the older, and a superseded answer (409) is ignored. It is
 *  decoded and drawn on a canvas: the packaged page's rules let no blob: or
 *  data: address into an <img>. Its own component, so the playhead moving
 *  while playing doesn't draw the whole viewer again. */
function StillLayer({
  projectId,
  revision,
  playhead,
  fps,
  length,
  active,
  onState
}: {
  projectId: string
  revision: number
  playhead: PlayheadStore
  fps: Fps
  length: number
  active: boolean
  onState: (s: { approximate: boolean; error: string }) => void
}): JSX.Element {
  const ref = useRef<HTMLCanvasElement>(null)
  // The frame under the playhead; a constant while playing.
  const frame = useSyncExternalStore(playhead.subscribe, () => (active ? frameAt(playhead.get(), fps) : -1))
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.visibility = 'hidden' // never an older frame's still
    if (!active || frame < 0) return
    const ask = new AbortController()
    const at = frameUs(Math.min(frame, frameAt(Math.max(0, length - 1), fps)), fps)
    // A short wait first, so dragging the playhead asks only where it rests.
    const timer = window.setTimeout(() => {
      editorApi
        .still(projectId, revision, at, ask.signal)
        .then((s) => {
          if (ask.signal.aborted) {
            s.bitmap.close()
            return
          }
          el.width = s.bitmap.width
          el.height = s.bitmap.height
          el.getContext('2d')?.drawImage(s.bitmap, 0, 0)
          s.bitmap.close()
          el.style.visibility = 'visible'
          onState({ approximate: s.approximate, error: '' })
        })
        .catch((e) => {
          if (ask.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
          // A newer still took its place, or the project moved on (the new
          // revision's still is asked for as soon as it arrives).
          if (e instanceof EditorError && e.status === 409) return
          onState({ approximate: false, error: errorText(e) })
        })
    }, 40)
    // The next frame's request aborts this one.
    return () => {
      window.clearTimeout(timer)
      ask.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, frame, projectId, revision, length, fps])
  return <canvas ref={ref} className="absolute inset-0 w-full h-full" style={{ visibility: 'hidden' }} aria-hidden="true" />
}

/** The bar under a media shown on its own: drag to move through it (a long
 *  video has no timeline of its own here), with the marked part shaded. */
function SourceScrub({
  store,
  length,
  marks,
  onSeek
}: {
  store: PlayheadStore
  length: number
  marks: { in: number; out: number }
  onSeek: (tUs: number) => void
}): JSX.Element {
  const now = usePlayheadTime(store)
  const pct = (us: number): string => `${Math.min(100, Math.max(0, (us / length) * 100))}%`
  return (
    <div className="relative px-2 pt-2">
      <div className="relative h-1.5 rounded bg-raised" aria-hidden="true">
        <div className="absolute inset-y-0 bg-accent/40 rounded" style={{ left: pct(marks.in), width: `calc(${pct(marks.out)} - ${pct(marks.in)})` }} />
      </div>
      <input
        type="range"
        min={0}
        max={length}
        step={1000}
        value={Math.min(now, length)}
        onChange={(e) => onSeek(Number(e.target.value))}
        className="absolute inset-x-2 top-0.5 w-[calc(100%-1rem)] opacity-0 cursor-pointer h-4"
        aria-label={t('Position in this video')}
        title={t('Drag to move through this video')}
      />
      <div className="absolute top-1 h-3.5 w-0.5 bg-ink rounded pointer-events-none" style={{ left: `calc(0.5rem + (100% - 1rem) * ${Math.min(1, now / length)})` }} aria-hidden="true" />
    </div>
  )
}

const Viewer = forwardRef<ViewerHandle, Props>(function Viewer(
  { project, infos, playhead, source, onSource, onAddRange },
  ref
) {
  const doc = project.document
  const canvas = doc.canvas
  const items = useMemo(() => v1Items(doc), [doc])
  const length = useMemo(() => programLength(doc), [doc])
  const a1Muted = doc.tracks.find((tr) => tr.id === 'A1')?.mute ?? false
  const playing = usePlaying(playhead)

  // ---- the stage's size ----
  const stageRef = useRef<HTMLDivElement>(null)
  const [stage, setStage] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const el = stageRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setStage({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const [zoom, setZoom] = useState<Zoom>('fit')
  const scale =
    zoom === 'fit'
      ? Math.max(0.01, Math.min((stage.w - 16) / canvas.width, (stage.h - 16) / canvas.height))
      : zoom
  const boxW = Math.round(canvas.width * scale)
  const boxH = Math.round(canvas.height * scale)

  // ---- the program player ----
  const aRef = useRef<HTMLVideoElement>(null)
  const bRef = useRef<HTMLVideoElement>(null)
  const bgRef = useRef<HTMLCanvasElement>(null)
  const player = useRef<ProgramPlayer | null>(null)
  const [stalled, setStalled] = useState(false)
  useEffect(() => {
    if (!aRef.current || !bRef.current || !bgRef.current) return
    const p = new ProgramPlayer(aRef.current, bRef.current, bgRef.current, playhead, () => setStalled(true))
    player.current = p
    return () => {
      p.destroy()
      player.current = null
    }
  }, [playhead])
  useEffect(() => {
    player.current?.setProgram(items, infos, canvas, a1Muted, length, (it) => isAiItem(doc, it))
    setStalled(false)
  }, [items, infos, canvas, a1Muted, length, doc])

  // ---- the still, while paused (StillLayer asks for it) ----
  const [stillState, setStillState] = useState<{ approximate: boolean; error: string }>({ approximate: false, error: '' })
  const showStill = !playing && source === null && length > 0

  // ---- the draft (Preview) ----
  const [draft, setDraft] = useState<{ status: 'making' | 'ready' | 'failed'; fraction: number; url: string; error: string; job?: number } | null>(null)
  const draftRev = useRef(-1)
  const startDraft = async (): Promise<void> => {
    player.current?.pause()
    draftRev.current = project.revision
    setDraft({ status: 'making', fraction: 0, url: '', error: '' })
    try {
      const ans = await editorApi.draft(project.id, project.revision)
      if (draftRev.current !== project.revision) return
      if (ans.draft) setDraft({ status: 'ready', fraction: 1, url: engineUrl(ans.draft.url), error: '' })
      else setDraft({ status: 'making', fraction: ans.job?.fraction ?? 0, url: '', error: '', job: ans.job?.job_id })
    } catch (e) {
      setDraft({ status: 'failed', fraction: 0, url: '', error: errorText(e) })
    }
  }
  // Progress from the lane's events, and a slow check in case one is missed.
  const followDraft = (job: MediaJob): void => {
    setDraft((d) => {
      if (!d || d.status !== 'making' || (d.job !== undefined && d.job !== job.job_id)) return d
      if (job.status === 'failed' || job.status === 'cancelled')
        return { ...d, status: 'failed', error: job.error || t('The preview could not be made.') }
      if (job.status === 'done') {
        void startDraft()
        return d
      }
      return { ...d, fraction: job.fraction ?? d.fraction, job: job.job_id }
    })
  }
  useEvents((e: StudioEvent) => {
    const ev = e as unknown as EditorEvent
    if (ev.type === 'media_job' && ev.job?.kind === 'project_draft' && ev.job.project_id === project.id) followDraft(ev.job)
  })
  const making = draft?.status === 'making'
  useEffect(() => {
    if (!making) return
    const timer = window.setInterval(() => {
      editorApi
        .draft(project.id, draftRev.current)
        .then((ans) => {
          if (ans.draft) setDraft({ status: 'ready', fraction: 1, url: engineUrl(ans.draft.url), error: '' })
          else if (ans.job) followDraft(ans.job)
        })
        .catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [making, project.id])
  // An edit makes the draft out of date.
  useEffect(() => {
    if (draft && draftRev.current !== project.revision) setDraft(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.revision])

  // ---- the phone app over the video ----
  const [platform, setPlatform] = useState<PlatformChoice>(loadPlatform)
  const vertical = isVertical916(canvas.width, canvas.height)

  // ---- a media on its own (source mode) ----
  const sourceHead = useMemo(() => createPlayhead(), [])
  const sourcePlaying = usePlaying(sourceHead)
  const srcRef = useRef<HTMLVideoElement>(null)
  const sourceEntry = source ? doc.media[source] : undefined
  const sourceInfo = source ? infos[source] : undefined
  const sourcePlay = isInfo(sourceInfo) ? playable(sourceInfo) : null
  const sourceFps: Fps = sourceEntry?.fps ?? canvas.fps
  const sourceLength = sourceEntry?.duration_us ?? 0
  const [marks, setMarks] = useState<{ in: number; out: number }>({ in: 0, out: 0 })
  useEffect(() => {
    sourceHead.set(0)
    sourceHead.setPlaying(false)
    setMarks({ in: 0, out: sourceEntry?.duration_us ?? 0 })
    if (source) player.current?.pause()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source])
  useEffect(() => {
    const el = srcRef.current as (HTMLVideoElement & { requestVideoFrameCallback?: (cb: (n: number, m: { mediaTime: number }) => void) => number }) | null
    if (!el || !sourcePlay) return
    let alive = true
    const tickOn = (): void => {
      if (!alive) return
      if (el.requestVideoFrameCallback)
        el.requestVideoFrameCallback((_n, meta) => {
          if (!alive) return
          if (!el.paused) sourceHead.set(meta.mediaTime * 1e6 - sourcePlay.offsetUs)
          tickOn()
        })
      else window.requestAnimationFrame(() => {
        if (!el.paused) sourceHead.set(el.currentTime * 1e6 - sourcePlay.offsetUs)
        tickOn()
      })
    }
    tickOn()
    const ended = (): void => sourceHead.setPlaying(false)
    // Wherever the player was moved to, the readout and the marks follow.
    const seeked = (): void => {
      if (el.paused) sourceHead.set(el.currentTime * 1e6 - sourcePlay.offsetUs)
    }
    el.addEventListener('ended', ended)
    el.addEventListener('pause', ended)
    el.addEventListener('seeked', seeked)
    return () => {
      alive = false
      el.removeEventListener('ended', ended)
      el.removeEventListener('pause', ended)
      el.removeEventListener('seeked', seeked)
    }
  }, [sourcePlay?.url, sourcePlay?.offsetUs, sourceHead])
  const sourceSeek = (tUs: number): void => {
    const el = srcRef.current
    const tt = Math.min(Math.max(0, tUs), Math.max(0, sourceLength - 1))
    const fstart = frameUs(frameAt(tt, sourceFps), sourceFps)
    sourceHead.set(fstart)
    if (el && sourcePlay) el.currentTime = (fstart + sourcePlay.offsetUs) / 1e6
  }

  // ---- what the keys and buttons do ----
  const inSource = source !== null
  const handle: ViewerHandle = {
    togglePlay: () => {
      if (draft) return
      if (inSource) {
        const el = srcRef.current
        if (!el || !sourcePlay) return
        if (el.paused) {
          if (sourceHead.get() >= sourceLength - 50000) sourceSeek(0)
          void el.play().catch(() => undefined)
          sourceHead.setPlaying(true)
        } else {
          el.pause()
          sourceSeek(sourceHead.get())
        }
        return
      }
      setStalled(false)
      const p = player.current
      if (!p) return
      if (p.isPlaying()) p.pause()
      else p.play()
    },
    step: (frames) => {
      if (draft) return
      if (inSource) {
        srcRef.current?.pause()
        sourceSeek(frameUs(Math.max(0, frameAt(sourceHead.get(), sourceFps) + frames), sourceFps))
        return
      }
      player.current?.step(frames)
    },
    toStart: () => (inSource ? sourceSeek(0) : player.current?.seek(0)),
    toEnd: () => (inSource ? sourceSeek(sourceLength) : player.current?.seek(length)),
    seek: (tUs) => {
      if (inSource) onSource(null)
      player.current?.seek(tUs)
    },
    markIn: () => {
      if (!inSource) return
      const at = sourceHead.get()
      setMarks((m) => ({ in: at, out: m.out > at ? m.out : sourceLength }))
    },
    markOut: () => {
      if (!inSource) return
      const at = Math.min(sourceLength, sourceHead.get() + (frameUs(1, sourceFps) || 0))
      setMarks((m) => ({ in: m.in < at ? m.in : 0, out: at }))
    }
  }
  useImperativeHandle(ref, () => handle)

  const zoneLabel = (id: string): string =>
    id === 'none' ? t('No overlay') : `${SAFE_ZONES[id]?.label ?? id} ${t('preview')}`
  const rangeOk = sourceEntry?.kind === 'video' && marks.out - marks.in >= frameUs(1, canvas.fps)
  const shortcutList = VIEWER_SHORTCUTS.map(([k, what]) => `${k.length > 1 && /^[A-Za-z]+$/.test(k) ? t(k) : k}: ${t(what)}`).join('\n')
  const btn = 'px-2 py-1 rounded-md text-sm bg-raised text-muted hover:text-ink disabled:opacity-40'

  return (
    <section className="flex flex-col min-h-0 min-w-0 h-full" aria-label={t('Viewer')}>
      {/* ---- the bar above ---- */}
      <div className="flex items-center gap-2 px-2 py-1.5 border-b border-raised/60 text-xs">
        <div className="flex gap-1" role="group" aria-label={t('What the viewer shows')}>
          <button
            type="button"
            className={`px-2 py-0.5 rounded ${!inSource ? 'bg-accent/20 text-accent' : 'bg-raised text-muted hover:text-ink'}`}
            aria-pressed={!inSource}
            onClick={() => onSource(null)}
            title={t('Your video as it is on the timeline')}
          >
            {t('Timeline')}
          </button>
          {sourceEntry && (
            <button
              type="button"
              className={`px-2 py-0.5 rounded max-w-48 truncate ${inSource ? 'bg-accent/20 text-accent' : 'bg-raised text-muted hover:text-ink'}`}
              aria-pressed={inSource}
              onClick={() => onSource(source)}
              title={sourceEntry.name || t('Media')}
            >
              {sourceEntry.name || t('Media')}
            </button>
          )}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <select
            className="input !py-0.5 !w-48 text-xs"
            value={vertical ? platform : 'none'}
            disabled={!vertical || inSource}
            onChange={(e) => {
              const p = e.target.value as PlatformChoice
              setPlatform(p)
              savePlatform(p)
            }}
            aria-label={t('Platform whose UI to show')}
            title={
              vertical
                ? t('Show what TikTok, Reels or Shorts draw over your video, to keep faces and text clear of it')
                : t('The phone app screens are drawn for 9:16 videos. Change the canvas to 9:16 to see them.')
            }
          >
            {PLATFORM_CHOICES.map((id) => (
              <option key={id} value={id}>
                {zoneLabel(id)}
              </option>
            ))}
          </select>
          <select
            className="input !py-0.5 !w-24 text-xs"
            value={String(zoom)}
            onChange={(e) => setZoom(e.target.value === 'fit' ? 'fit' : (Number(e.target.value) as Zoom))}
            aria-label={t('Viewer size')}
            title={t('Fit the video to the window, or show it at a set size')}
          >
            {ZOOMS.map((z) => (
              <option key={String(z)} value={String(z)}>
                {z === 'fit' ? t('Fit') : `${Math.round(z * 100)}%`}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-ghost !py-0.5 !px-2 text-xs"
            onClick={() => (draft ? setDraft(null) : void startDraft())}
            disabled={length === 0 || inSource}
            title={t('Make a quick small copy of the whole video and play it, with everything the export draws')}
          >
            {draft ? t('Back to editing') : t('Preview')}
          </button>
        </div>
      </div>

      {/* ---- the picture ---- */}
      <div ref={stageRef} className="relative flex-1 min-h-0 overflow-auto bg-base/60 flex">
        <div className="m-auto p-2">
          <div
            className="relative overflow-hidden bg-black shadow-lg"
            style={{ width: boxW, height: boxH }}
            aria-label={t('Video')}
            role="img"
          >
            {/* The program: pad, two players, overlays, the still. */}
            <canvas
              ref={bgRef}
              width={48}
              height={Math.max(2, Math.round((48 * canvas.height) / canvas.width))}
              className="absolute inset-0 w-full h-full"
              style={{ filter: 'blur(10px) brightness(0.75)', transform: 'scale(1.08)', visibility: inSource || draft ? 'hidden' : 'visible' }}
              aria-hidden="true"
            />
            <video ref={aRef} playsInline preload="auto" className="absolute inset-0 w-full h-full" aria-hidden="true" />
            <video ref={bRef} playsInline preload="auto" className="absolute inset-0 w-full h-full" aria-hidden="true" />
            {!inSource && !draft && <OverlayLayer doc={doc} infos={infos} playhead={playhead} />}
            <StillLayer
              projectId={project.id}
              revision={project.revision}
              playhead={playhead}
              fps={canvas.fps}
              length={length}
              active={showStill}
              onState={setStillState}
            />
            {/* A media on its own. */}
            {inSource && (
              <div className="absolute inset-0 bg-black">
                {sourceEntry?.kind === 'image' && isInfo(sourceInfo) ? (
                  <img src={engineUrl(sourceInfo.urls.source)} alt="" className="w-full h-full object-contain" />
                ) : sourcePlay ? (
                  <video
                    ref={srcRef}
                    key={sourcePlay.url}
                    src={engineUrl(sourcePlay.url)}
                    playsInline
                    preload="auto"
                    className="w-full h-full object-contain"
                    aria-hidden="true"
                  />
                ) : (
                  <p className="absolute inset-0 flex items-center justify-center text-xs text-muted p-4 text-center">
                    {isInfo(sourceInfo) || !sourceInfo ? t('Getting this video ready to play…') : sourceInfo.error}
                  </p>
                )}
              </div>
            )}
            {/* The draft, when Preview made it. */}
            {draft && (
              <div className="absolute inset-0 bg-black flex items-center justify-center">
                {draft.status === 'ready' ? (
                  <video src={draft.url} controls autoPlay playsInline className="w-full h-full object-contain" aria-label={t('Preview of the whole video')} />
                ) : draft.status === 'making' ? (
                  <div className="w-2/3 space-y-2 text-center" role="status">
                    <p className="text-xs text-muted">
                      {t('Making the preview…')} {Math.round(draft.fraction * 100)}%
                    </p>
                    <div className="h-1.5 rounded bg-raised overflow-hidden">
                      <div className="h-full bg-accent transition-all" style={{ width: `${Math.round(draft.fraction * 100)}%` }} />
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-red-300 p-4 text-center">{draft.error}</p>
                )}
              </div>
            )}
            {vertical && !inSource && PLATFORM_UI[platform] && <PlatformOverlay platform={platform} />}
            {length === 0 && !inSource && (
              <p className="absolute inset-0 flex items-center justify-center text-xs text-muted p-4 text-center">
                {t('The main track is empty. Add a video to it to see it here.')}
              </p>
            )}
            {stalled && !inSource && !draft && (
              <p className="absolute bottom-2 inset-x-2 text-[11px] text-center bg-black/70 rounded px-2 py-1">
                {t('This part is still being made ready to play. Pause shows it as it will look.')}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* ---- a media on its own: where in it, and the marked part ---- */}
      {inSource && sourceEntry?.kind === 'video' && sourceLength > 0 && (
        <SourceScrub store={sourceHead} length={sourceLength} marks={marks} onSeek={sourceSeek} />
      )}
      {/* ---- the controls ---- */}
      <div className="flex items-center gap-1.5 px-2 py-1.5 border-t border-raised/60 flex-wrap">
        <button type="button" className={btn} onClick={handle.toStart} aria-label={t('Go to the start')} title={`${t('Go to the start')} (${t('Home')})`}>
          <span aria-hidden>⏮</span>
        </button>
        <button type="button" className={btn} onClick={() => handle.step(-1)} aria-label={t('One frame back')} title={`${t('One frame back')} (← ,)`}>
          <span aria-hidden>◀|</span>
        </button>
        <button
          type="button"
          className="px-3 py-1 rounded-md text-sm bg-accent/20 text-accent hover:bg-accent/30 disabled:opacity-40"
          onClick={handle.togglePlay}
          disabled={Boolean(draft) || (inSource ? !sourcePlay : length === 0)}
          aria-label={(inSource ? sourcePlaying : playing) ? t('Pause') : t('Play')}
          title={`${t('Play or pause')} (${t('Space')})`}
        >
          <span aria-hidden>{(inSource ? sourcePlaying : playing) ? '❚❚' : '▶'}</span>
        </button>
        <button type="button" className={btn} onClick={() => handle.step(1)} aria-label={t('One frame forward')} title={`${t('One frame forward')} (→ .)`}>
          <span aria-hidden>|▶</span>
        </button>
        <button type="button" className={btn} onClick={handle.toEnd} aria-label={t('Go to the end')} title={`${t('Go to the end')} (${t('End')})`}>
          <span aria-hidden>⏭</span>
        </button>
        <span className="ml-2">
          {inSource ? (
            <TimeReadout store={sourceHead} fps={sourceFps} length={sourceLength} />
          ) : (
            <TimeReadout store={playhead} fps={canvas.fps} length={length} />
          )}
        </span>
        {!inSource && stillState.approximate && !playing && (
          <span className="text-[11px] text-amber-300" title={t('The AI framing of this part is worked out when you export or preview it, so this frame is close but not exact.')}>
            {t('Approximate frame')}
          </span>
        )}
        {!inSource && stillState.error && !playing && (
          <span className="text-[11px] text-red-300 truncate max-w-64" title={stillState.error}>
            {stillState.error}
          </span>
        )}
        {inSource && sourceEntry?.kind === 'video' && (
          <span className="flex items-center gap-1.5 ml-2 text-xs">
            <button type="button" className={btn} onClick={handle.markIn} title={`${t('Mark where the part starts')} (${KEY_IN})`}>
              {t('Mark in')}
            </button>
            <button type="button" className={btn} onClick={handle.markOut} title={`${t('Mark where the part ends')} (${KEY_OUT})`}>
              {t('Mark out')}
            </button>
            <span className="tabular-nums text-muted">
              {formatTime(marks.in, sourceFps)} – {formatTime(marks.out, sourceFps)}
            </span>
            <button
              type="button"
              className="btn-accent !py-1 !px-2.5 text-xs"
              disabled={!rangeOk || project.read_only}
              onClick={() => source && onAddRange(source, marks.in, marks.out)}
              title={t('Add the marked part to the end of the main track')}
            >
              {t('Add range')}
            </button>
          </span>
        )}
        <span className="ml-auto text-muted text-xs cursor-help px-1" title={shortcutList} aria-label={t('Keyboard shortcuts')} role="note">
          ⌨
        </span>
      </div>
    </section>
  )
})

export default Viewer

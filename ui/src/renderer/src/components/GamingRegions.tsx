import { useEffect, useRef, useState } from 'react'
import { BAND_H, OUT_H, OUT_W, plan, type PxBox } from '../lib/gamingLayout'
import { t } from '../lib/i18n'
import type { FrameBox, GamingSettings } from '../lib/types'

/** Set up a Gaming / Reaction split on real frames of the video: where the
 *  streamer's webcam is, and (optionally) where the game or the video being
 *  reacted to is, with a live preview of the 9:16 result.
 *
 *  Two places use it:
 *  - before processing (the Generate bar), on frames of the link or file, so
 *    a video isn't processed with a bad crop. Detection is good but every
 *    game and overlay is different; checking takes seconds.
 *  - in the editor, to fix one clip.
 *
 *  Nothing is saved here: the result goes back to the caller, which sends it
 *  with the job (Generate) or as a pending edit (editor: Update preview and
 *  Apply, like every other edit). */

type Which = 'cam' | 'game'
type CamMode = 'auto' | 'draw' | 'none'
type Drag = { which: Which; mode: 'move' | 'nw' | 'se'; ox: number; oy: number; box: FrameBox }

const COLOUR: Record<Which, string> = { cam: '#38BDF8', game: '#22C55E' }
const NEW_CAM: FrameBox = [0.02, 0.6, 0.25, 0.36]
const NEW_GAME: FrameBox = [0.1, 0.02, 0.8, 0.8]
const FRAMES = [0.1, 0.3, 0.5, 0.7, 0.9]

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))

/** Style that shows crop `box` (source px) of the frame in a pane the way
 *  FFmpeg does: 'cover' scales it up uniformly and trims the overflow
 *  centrally; 'contain' shows it whole, centred. */
function cropStyle(
  box: PxBox,
  srcW: number,
  srcH: number,
  paneW: number,
  paneH: number,
  mode: 'cover' | 'contain' = 'cover'
): React.CSSProperties {
  const [bx, by, bw, bh] = box
  const scale = (mode === 'cover' ? Math.max : Math.min)(paneW / bw, paneH / bh)
  return {
    position: 'absolute',
    width: srcW * scale,
    height: srcH * scale,
    left: -bx * scale + (paneW - bw * scale) / 2,
    top: -by * scale + (paneH - bh * scale) / 2,
    maxWidth: 'none',
    // 'contain' shows only the box: clip away the rest of the frame.
    clipPath:
      mode === 'contain'
        ? `inset(${by * scale}px ${(srcW - bx - bw) * scale}px ${(srcH - by - bh) * scale}px ${bx * scale}px)`
        : undefined
  }
}

/** A frame from the API: loading, loaded, or the server's own words for why
 *  not (a live stream, a link that isn't a video). The image loads straight
 *  from the local API, which the app's content policy allows; only when it
 *  fails is the same URL fetched again, to read the reason. */
function useFrame(url: string): {
  loaded: boolean
  error: string | null
  onLoad: () => void
  onError: () => void
} {
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setLoaded(false)
    setError(null)
  }, [url])
  const onError = (): void => {
    fetch(url)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}))
        setError(body.detail || t('Couldn’t read a frame of this video.'))
      })
      .catch(() => setError(t('Couldn’t reach Clips Kitty’s engine for a frame.')))
  }
  return { loaded, error, onLoad: () => setLoaded(true), onError }
}

export default function GamingRegions({
  frameAt,
  context,
  settings,
  remember: rememberInitially,
  canRemember = true,
  onClose,
  onDone
}: {
  /** The URL of a frame `at` (0-1) of the way through the video or clip. */
  frameAt: (at: number) => string
  /** 'video': set up before processing. 'clip': fixing one clip in the editor. */
  context: 'video' | 'clip'
  settings: GamingSettings
  remember: boolean
  canRemember?: boolean
  onClose: () => void
  onDone: (settings: GamingSettings, remember: boolean) => void
}): JSX.Element {
  // What the webcam is now: drawn or remembered by a person, "no webcam", or
  // found automatically (by the video's search, or still to be found).
  const decided = settings.by === 'user' || settings.by === 'creator'
  const found = settings.used_cam ?? (settings.by === 'video' ? settings.cam : null) ?? null
  const [camMode, setCamMode] = useState<CamMode>(
    decided && 'cam' in settings ? (settings.cam ? 'draw' : 'none') : 'auto'
  )
  const [cam, setCam] = useState<FrameBox>((decided ? settings.cam : found) ?? NEW_CAM)
  const [game, setGame] = useState<FrameBox | null>(settings.game_box ?? null)
  const [camPosition, setCamPosition] = useState<'top' | 'bottom'>(settings.cam_position ?? 'top')
  const [gameAlign, setGameAlign] = useState<'left' | 'center' | 'right'>(settings.game_align ?? 'center')
  const [gameFit, setGameFit] = useState<'fit' | 'fill'>(settings.game_fit ?? 'fit')
  const [remember, setRemember] = useState(rememberInitially)
  const [active, setActive] = useState<Which>('cam')
  const [frameIndex, setFrameIndex] = useState(context === 'video' ? 1 : 2)
  const [src, setSrc] = useState({ w: 1920, h: 1080 })
  const frameRef = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)

  const frameUrl = frameAt(FRAMES[frameIndex])
  const frame = useFrame(frameUrl)
  const drawnCam = camMode === 'draw' ? cam : null
  // The preview's webcam: drawn, or the one found automatically when known.
  const previewCam: FrameBox | null = camMode === 'draw' ? cam : camMode === 'auto' ? found : null
  const autoUnknown = camMode === 'auto' && !found
  const boxes: Record<Which, FrameBox | null> = { cam: drawnCam, game }
  const setBox = (which: Which, box: FrameBox): void => (which === 'cam' ? setCam(box) : setGame(box))

  const pos = (e: React.PointerEvent): { x: number; y: number } => {
    const r = frameRef.current!.getBoundingClientRect()
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }
  }

  const startDrag = (e: React.PointerEvent, which: Which, mode: Drag['mode']): void => {
    const box = boxes[which]
    if (!box) return
    e.stopPropagation()
    setActive(which)
    const p = pos(e)
    drag.current = { which, mode, ox: p.x, oy: p.y, box: [...box] as FrameBox }
    frameRef.current?.setPointerCapture(e.pointerId)
  }

  const onMove = (e: React.PointerEvent): void => {
    const d = drag.current
    if (!d) return
    const p = pos(e)
    const dx = p.x - d.ox
    const dy = p.y - d.oy
    const [x, y, w, h] = d.box
    const MIN = 0.04
    if (d.mode === 'move') {
      setBox(d.which, [clamp(x + dx, 0, 1 - w), clamp(y + dy, 0, 1 - h), w, h])
    } else if (d.mode === 'se') {
      setBox(d.which, [x, y, clamp(w + dx, MIN, 1 - x), clamp(h + dy, MIN, 1 - y)])
    } else {
      const nx = clamp(x + dx, 0, x + w - MIN)
      const ny = clamp(y + dy, 0, y + h - MIN)
      setBox(d.which, [nx, ny, x + w - nx, y + h - ny])
    }
  }
  const endDrag = (): void => {
    drag.current = null
  }

  const done = (): void => {
    const next: GamingSettings = { cam_position: camPosition, game_align: gameAlign, game_fit: gameFit }
    if (game) next.game_box = game
    if (camMode === 'draw') Object.assign(next, { cam, by: 'user' })
    else if (camMode === 'none') Object.assign(next, { cam: null, by: 'user' })
    else if (context === 'clip') {
      // Back to automatic: the video's webcam if the video found one, else
      // found in this clip.
      if (settings.by === 'video') Object.assign(next, { cam: settings.cam ?? null, by: 'video' })
      else next.by = 'clip'
    }
    onDone(next, remember)
    onClose()
  }

  // The composition, exactly as gaming/layout.py will crop it.
  const p = plan(src.w, src.h, autoUnknown ? [0, 0, 0.2, 0.2] : previewCam, {
    camPosition,
    gameAlign,
    gameBox: game,
    gameFit
  })
  const PW = 162
  const PH = Math.round((PW * OUT_H) / OUT_W)
  const bandH = Math.round((PH * BAND_H) / OUT_H)
  const panes: { key: string; box: PxBox; top: number; h: number; whole: boolean }[] =
    p.kind === 'fill'
      ? [{ key: 'game', box: p.game, top: 0, h: PH, whole: gameFit === 'fit' }]
      : [
          { key: 'cam', box: p.cam!, top: camPosition === 'top' ? 0 : PH - bandH, h: bandH, whole: false },
          {
            key: 'game',
            box: p.game,
            top: camPosition === 'top' ? bandH : 0,
            h: PH - bandH,
            whole: gameFit === 'fit'
          }
        ]

  const segment = <V extends string>(options: [V, string][], value: V, onChange: (v: V) => void): JSX.Element => (
    <div className="inline-flex rounded-md overflow-hidden flex-wrap">
      {options.map(([v, label]) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className={`px-2 py-1 text-xs ${
            value === v ? 'bg-accent/20 text-accent font-medium' : 'bg-raised text-muted hover:text-ink'
          }`}
        >
          {t(label)}
        </button>
      ))}
    </div>
  )

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={t('Set up the split')}
    >
      <div
        className="bg-surface border border-raised/60 rounded-2xl p-4 w-full max-w-5xl space-y-3 max-h-full overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <p className="font-semibold">
            {context === 'video' ? t('Set up the split before processing') : t('Webcam and game area')}
          </p>
          <button className="text-muted hover:text-ink px-1 text-lg leading-none" onClick={onClose} aria-label={t('Close')}>
            ✕
          </button>
        </div>
        <p className="text-xs text-muted">
          {t(
            'Pick a frame where the webcam and the game both show. Draw the blue box over the streamer’s webcam, and the green box around the game (or the video being reacted to) to leave out chat and panels. Drag a box to move it, its corners to resize.'
          )}
        </p>

        <div className="flex gap-4 items-start">
          <div className="flex-1 min-w-0 space-y-2">
            <div
              ref={frameRef}
              className="relative select-none touch-none bg-base rounded-lg overflow-hidden"
              // The source's own shape, so a box drawn here is where it is in the video.
              style={{ aspectRatio: `${src.w} / ${src.h}` }}
              onPointerMove={onMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
            >
              {frame.error ? (
                <p className="p-8 text-sm text-error">{frame.error}</p>
              ) : (
                <>
                  {!frame.loaded && (
                    <p className="absolute inset-0 p-8 text-sm text-muted">{t('Loading the frame…')}</p>
                  )}
                  <img
                    src={frameUrl}
                    alt={t('A frame of the video')}
                    className={`w-full h-full block ${frame.loaded ? '' : 'invisible'}`}
                    draggable={false}
                    onError={frame.onError}
                    onLoad={(e) => {
                      frame.onLoad()
                      setSrc({
                        w: (e.target as HTMLImageElement).naturalWidth || 1920,
                        h: (e.target as HTMLImageElement).naturalHeight || 1080
                      })
                    }}
                  />
                </>
              )}
              {camMode === 'auto' && found && frame.loaded && (
                <div
                  className="absolute pointer-events-none"
                  style={{
                    left: `${found[0] * 100}%`,
                    top: `${found[1] * 100}%`,
                    width: `${found[2] * 100}%`,
                    height: `${found[3] * 100}%`,
                    border: `2px dashed ${COLOUR.cam}`
                  }}
                >
                  <span className="absolute top-0 left-0 text-[10px] px-1 rounded-br" style={{ background: COLOUR.cam, color: '#0B1220' }}>
                    {t('Found automatically')}
                  </span>
                </div>
              )}
              {frame.loaded &&
                (['game', 'cam'] as Which[]).map((which) => {
                  const box = boxes[which]
                  if (!box) return null
                  const [x, y, w, h] = box
                  return (
                    <div
                      key={which}
                      className="absolute cursor-move"
                      style={{
                        left: `${x * 100}%`,
                        top: `${y * 100}%`,
                        width: `${w * 100}%`,
                        height: `${h * 100}%`,
                        border: `3px solid ${COLOUR[which]}`,
                        boxShadow: active === which ? `0 0 0 2px ${COLOUR[which]}55` : 'none'
                      }}
                      onPointerDown={(e) => startDrag(e, which, 'move')}
                    >
                      <span
                        className="absolute top-0 left-0 text-[10px] px-1 rounded-br"
                        style={{ background: COLOUR[which], color: '#0B1220' }}
                      >
                        {which === 'cam' ? t('Webcam') : t('Game')}
                      </span>
                      {(['nw', 'se'] as const).map((corner) => (
                        <span
                          key={corner}
                          className={`absolute w-4 h-4 rounded-sm cursor-nwse-resize ${
                            corner === 'nw' ? '-left-2 -top-2' : '-right-2 -bottom-2'
                          }`}
                          style={{ background: COLOUR[which] }}
                          onPointerDown={(e) => startDrag(e, which, corner)}
                        />
                      ))}
                    </div>
                  )
                })}
            </div>
            <div className="flex gap-2" role="group" aria-label={t('Frames')}>
              {FRAMES.map((at, i) => (
                <button
                  key={at}
                  onClick={() => setFrameIndex(i)}
                  className={`flex-1 aspect-video rounded-md overflow-hidden bg-base border-2 ${
                    frameIndex === i ? 'border-accent' : 'border-transparent hover:border-raised'
                  }`}
                  aria-label={`${t('Frame')} ${i + 1}`}
                  aria-pressed={frameIndex === i}
                >
                  <img
                    src={frameAt(at)}
                    alt=""
                    loading="lazy"
                    className="w-full h-full object-cover"
                    onError={(e) => ((e.target as HTMLImageElement).style.visibility = 'hidden')}
                  />
                </button>
              ))}
            </div>
          </div>

          <div className="shrink-0 w-60 space-y-3 text-xs">
            <div className="space-y-1.5">
              <p className="label" style={{ color: COLOUR.cam }}>
                {t('Webcam')}
              </p>
              {segment(
                [
                  ['auto', 'Find it'],
                  ['draw', 'Draw it'],
                  ['none', 'No webcam']
                ],
                camMode,
                (m) => {
                  setCamMode(m)
                  if (m === 'draw') setActive('cam')
                }
              )}
              <p className="text-muted">
                {camMode === 'auto'
                  ? found
                    ? t('Found automatically (dashed). Draw it if that’s wrong.')
                    : t('Found by who is talking when the video is processed.')
                  : camMode === 'none'
                    ? t('The game fills the screen.')
                    : t('Drag the blue box over the webcam.')}
              </p>
              {camMode !== 'none' && (
                <div className="flex items-center gap-2">
                  <span className="text-muted">{t('Camera')}</span>
                  {segment(
                    [
                      ['top', 'Top'],
                      ['bottom', 'Bottom']
                    ],
                    camPosition,
                    setCamPosition
                  )}
                </div>
              )}
            </div>

            <div className="space-y-1.5">
              <p className="label" style={{ color: COLOUR.game }}>
                {t('Game')}
              </p>
              <div className="flex items-center gap-2">
                <span className="text-muted">{t('Show')}</span>
                {segment(
                  [
                    ['fit', 'Whole game'],
                    ['fill', 'Zoom to fill']
                  ],
                  gameFit,
                  setGameFit
                )}
              </div>
              {gameFit === 'fit' && (
                <p className="text-muted">{t('All of it, with a blurred copy behind instead of black bars.')}</p>
              )}
              {game ? (
                <button className="btn-ghost !py-1 w-full" onClick={() => setGame(null)}>
                  {t('Let Clips Kitty pick the game area')}
                </button>
              ) : (
                <>
                  {gameFit === 'fill' && (
                    <div className="flex items-center gap-2">
                      <span className="text-muted">{t('Crop')}</span>
                      {segment(
                        [
                          ['left', 'Left'],
                          ['center', 'Centre'],
                          ['right', 'Right']
                        ],
                        gameAlign,
                        setGameAlign
                      )}
                    </div>
                  )}
                  <button
                    className="btn-ghost !py-1 w-full"
                    onClick={() => {
                      setGame(NEW_GAME)
                      setActive('game')
                    }}
                  >
                    {t('Draw the game area')}
                  </button>
                </>
              )}
            </div>

            <div>
              <p className="label mb-1">{t('Preview')}</p>
              <div className="relative bg-black rounded-lg overflow-hidden" style={{ width: PW, height: PH }}>
                {frame.loaded &&
                  panes.map((pane) =>
                    pane.key === 'cam' && autoUnknown ? (
                      <div
                        key={pane.key}
                        className="absolute flex items-center justify-center text-center text-[10px] text-muted bg-raised/40 px-2"
                        style={{ top: pane.top, left: 0, width: PW, height: pane.h }}
                      >
                        {t('Webcam, found when processing')}
                      </div>
                    ) : (
                      <div
                        key={pane.key}
                        className="absolute overflow-hidden"
                        style={{ top: pane.top, left: 0, width: PW, height: pane.h }}
                      >
                        <img
                          src={frameUrl}
                          alt=""
                          draggable={false}
                          style={{
                            ...cropStyle(pane.box, src.w, src.h, PW, pane.h),
                            // The render's blurred backdrop behind a whole game.
                            ...(pane.whole ? { filter: 'blur(6px)' } : {})
                          }}
                        />
                        {pane.whole && (
                          <img
                            src={frameUrl}
                            alt=""
                            draggable={false}
                            style={cropStyle(pane.box, src.w, src.h, PW, pane.h, 'contain')}
                          />
                        )}
                      </div>
                    )
                  )}
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3 justify-end">
          {canRemember && (
            <label className="flex items-center gap-2 text-xs text-muted mr-auto cursor-pointer">
              <input
                type="checkbox"
                className="size-4 accent-[#38BDF8]"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              {t('Remember for this creator’s next videos')}
            </label>
          )}
          <button className="btn-ghost" onClick={onClose}>
            {t('Cancel')}
          </button>
          <button className="btn-accent" onClick={done}>
            {context === 'video' ? t('Use this split') : t('Done')}
          </button>
        </div>
      </div>
    </div>
  )
}

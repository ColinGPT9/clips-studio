/** The media bin, on the left: the project's videos, pictures and sounds, with
 *  a picture of each.
 *
 *  - Choosing a video shows it on its own in the viewer, to mark a part of it
 *    (I and O) and add that part to the main track.
 *  - "+" adds the whole of it to the end of the main track; "Overlay" puts it
 *    over the video at the playhead (on the first overlay track with room,
 *    or a new one). It can also be dragged onto a track.
 *  - "Import" brings in a file from this PC (step 6's import; the project
 *    takes it with add_media), with no AI run. */
import { useEffect, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { editorApi, errorText } from '../../lib/editorApi'
import { formatLength, programLength } from '../../lib/editorProgram'
import type { PlayheadStore } from '../../lib/editorPlayhead'
import type { MediaEntry, Op, OpsAnswer, ProjectDoc } from '../../lib/editorTypes'
import { itemEnd } from '../../lib/editorTimeline'
import { importFinished } from './LibraryEntry'
import { MEDIA_DRAG, PICTURE_US } from './TimelinePanel'
import { isInfo, type MediaInfos } from './useMediaInfos'

const KIND_ICON: Record<string, string> = { video: '▶', image: '▣', audio: '♪' }

/** The media's first thumbnail, drawn on a canvas (the page's rules let no
 *  blob: or data: address into an <img>). */
function Thumb({ url }: { url: string | undefined }): JSX.Element {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    if (!url) return
    const ask = new AbortController()
    let tries = 0
    const load = async (): Promise<void> => {
      const index = await editorApi.thumbs(url, ask.signal)
      if (!index) {
        if (tries++ < 20) window.setTimeout(() => void load().catch(() => undefined), 3000)
        return
      }
      const pick = Math.min(index.count - 1, Math.floor(index.count / 3))
      const sheet = index.sheets[Math.floor(pick / 100)]
      if (!sheet) return
      const bitmap = await editorApi.bitmap(sheet.url, ask.signal)
      const el = ref.current
      if (!el) return bitmap.close()
      const local = pick % 100
      el.width = index.tile.width
      el.height = index.tile.height
      el.getContext('2d')?.drawImage(
        bitmap,
        (local % 10) * index.tile.width,
        Math.floor(local / 10) * index.tile.height,
        index.tile.width,
        index.tile.height,
        0,
        0,
        index.tile.width,
        index.tile.height
      )
      bitmap.close()
    }
    void load().catch(() => undefined)
    return () => ask.abort()
  }, [url])
  return <canvas ref={ref} width={16} height={9} className="w-12 h-7 rounded bg-black/60 object-contain shrink-0" aria-hidden="true" />
}

export default function MediaBin({
  doc,
  infos,
  source,
  onSource,
  apply,
  playhead,
  readOnly
}: {
  doc: ProjectDoc
  infos: MediaInfos
  source: string | null
  onSource: (mediaId: string | null) => void
  apply: (ops: Op[], label?: string) => Promise<OpsAnswer | null>
  playhead: PlayheadStore
  readOnly: boolean
}): JSX.Element {
  const media = Object.values(doc.media).sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const canPick = typeof window.studio?.pickVideoFile === 'function'

  const importFile = async (): Promise<void> => {
    setError('')
    const path = await window.studio?.pickVideoFile?.()
    if (!path) return
    setBusy(t('Copying the video…'))
    try {
      const answer = await editorApi.importMedia(path)
      const asset = 'job' in answer ? await importFinished(answer.job, setBusy) : answer
      await apply([{ op: 'add_media', asset: asset.sha256 }], t('Import'))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy('')
    }
  }

  const stretch = (m: MediaEntry): number => (m.kind === 'image' ? PICTURE_US : m.duration_us)

  const toMain = (m: MediaEntry): void =>
    void apply(
      [{ op: 'insert_clip', track_id: 'V1', media_id: m.id, source_in_us: 0, source_out_us: stretch(m) }],
      t('Add to the main track')
    )

  /** Over the video at the playhead: the lowest overlay track with room
   *  there, or a new one (one undo step either way). */
  const toOverlay = (m: MediaEntry): void => {
    const end = programLength(doc)
    const at = Math.min(playhead.get(), Math.max(0, end - 1))
    // As long as it is, but no further than the video's end.
    const length = Math.max(Math.min(stretch(m), end - at), Math.min(stretch(m), 1_000_000))
    const overlays = doc.tracks.filter((tr) => tr.kind === 'video' && tr.id !== 'V1')
    const free = overlays.find((tr) => tr.items.every((it) => itemEnd(it) <= at || it.start_us >= at + length))
    const insert = (trackId: string): Op => ({
      op: 'insert_clip',
      track_id: trackId,
      media_id: m.id,
      source_in_us: 0,
      source_out_us: length,
      start_us: at
    })
    if (free) {
      void apply([insert(free.id)], t('Add as an overlay'))
      return
    }
    // add_track names the new track V<highest + 1> (step 5).
    const taken = doc.tracks.filter((tr) => /^V\d+$/.test(tr.id)).map((tr) => Number(tr.id.slice(1)))
    const id = `V${Math.max(1, ...taken) + 1}`
    void apply([{ op: 'add_track', kind: 'video' }, insert(id)], t('Add as an overlay'))
  }

  const btn = 'px-1.5 py-0.5 rounded text-[10px] bg-raised text-muted hover:text-ink disabled:opacity-40'

  return (
    <aside className="min-h-0 flex flex-col bg-surface" aria-label={t('Media')}>
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-raised/60">
        <h3 className="text-xs font-semibold text-muted flex-1">{t('Media')}</h3>
        {canPick && (
          <button
            type="button"
            className="btn-ghost !py-0.5 !px-2 text-xs"
            onClick={() => void importFile()}
            disabled={readOnly || busy !== ''}
            title={t('Bring a video or picture from this PC into this project. Nothing is changed in the file.')}
          >
            {t('Import')}
          </button>
        )}
      </div>
      {(busy || error) && (
        <p className={`px-3 py-1 text-[11px] ${error ? 'text-red-300' : 'text-muted'}`} role="status">
          {error || busy}
        </p>
      )}
      <ul className="flex-1 overflow-y-auto p-1.5 space-y-1">
        {media.map((m) => {
          const info = infos[m.id]
          const missing = info !== undefined && !isInfo(info)
          const canShow = m.kind !== 'audio' && !missing
          const name = m.name || (isInfo(info) ? info.name : '') || m.id
          const canPlace = !readOnly && !missing && m.kind !== 'audio'
          return (
            <li key={m.id} className={`rounded-md ${source === m.id ? 'bg-accent/15' : 'hover:bg-raised/60'}`}>
              <button
                type="button"
                disabled={!canShow}
                draggable={canPlace}
                onDragStart={(e) => {
                  e.dataTransfer.setData(MEDIA_DRAG, m.id)
                  e.dataTransfer.effectAllowed = 'copy'
                }}
                aria-pressed={source === m.id}
                onClick={() => onSource(source === m.id ? null : m.id)}
                title={missing ? (info as { error: string }).error : canShow ? t('Show it in the viewer, to mark a part to add') : name}
                className={`w-full text-left px-2 pt-1.5 pb-1 text-xs flex items-center gap-2 ${
                  source === m.id ? 'text-accent' : 'text-ink disabled:text-muted'
                }`}
              >
                {m.kind === 'audio' ? (
                  <span aria-hidden className="w-12 h-7 rounded bg-black/40 flex items-center justify-center text-muted shrink-0">
                    {KIND_ICON.audio}
                  </span>
                ) : (
                  <Thumb url={isInfo(info) ? info.urls.thumbs : undefined} />
                )}
                <span className="flex-1 min-w-0">
                  <span className="block truncate">{name}</span>
                  <span className="block tabular-nums text-muted text-[10px]">
                    {KIND_ICON[m.kind] ?? '·'} {m.duration_us > 0 ? formatLength(m.duration_us) : ''}
                  </span>
                </span>
              </button>
              {missing && <p className="px-2 text-[11px] text-red-300 truncate">{t('Missing')}</p>}
              {canPlace && (
                <div className="flex gap-1 px-2 pb-1.5">
                  <button type="button" className={btn} onClick={() => toMain(m)} title={t('Add all of it to the end of the main track')}>
                    + {t('Main track')}
                  </button>
                  <button
                    type="button"
                    className={btn}
                    onClick={() => toOverlay(m)}
                    disabled={programLength(doc) === 0}
                    title={t('Put it over the video at the playhead, on an overlay track')}
                  >
                    + {t('Overlay')}
                  </button>
                </div>
              )}
            </li>
          )
        })}
        {media.length === 0 && <li className="px-2 py-1 text-xs text-muted">{t('No media yet')}</li>}
      </ul>
    </aside>
  )
}

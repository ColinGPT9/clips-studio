/** What the engine knows about each of a project's media (step 6's info
 *  route), sounds included for their waveforms: its playable address with its token, its start time, whether it
 *  needs the 540p proxy and that proxy's state.
 *
 *  A media that needs a proxy (HEVC, 10-bit, Matroska, above 1080p60) gets it
 *  asked for once; until it is ready the viewer shows the engine's stills.
 *  The infos are read again when a proxy job finishes (`media_job` events) and,
 *  as a fallback for a missed event, every few seconds while one is made. */
import { useEffect, useRef, useState } from 'react'
import { editorApi, projectMediaId } from '../../lib/editorApi'
import type { EditorEvent, MediaInfo, ProjectDoc } from '../../lib/editorTypes'
import type { StudioEvent } from '../../lib/types'
import { useEvents } from '../../lib/useEvents'

export type MediaInfos = Record<string, MediaInfo | { error: string }>

export function isInfo(v: MediaInfos[string] | undefined): v is MediaInfo {
  return v !== undefined && !('error' in v)
}

/** Where a `<video>` plays this media, and how far its own clock runs ahead of
 *  the document's source time (the source's first timestamp; a proxy starts at
 *  0). null while there is nothing playable yet. */
export function playable(info: MediaInfo | undefined): { url: string; offsetUs: number } | null {
  if (!info || info.kind === 'audio') return null
  if (!info.needs_proxy) return { url: info.urls.source, offsetUs: info.start_us || 0 }
  return info.proxy.state === 'ready' ? { url: info.urls.proxy, offsetUs: 0 } : null
}

export function useMediaInfos(projectId: string, doc: ProjectDoc | null): MediaInfos {
  const [infos, setInfos] = useState<MediaInfos>({})
  const asked = useRef(new Set<string>())
  // Which media the document names, with what each is: a media id whose
  // source changes is read again.
  const key = doc
    ? Object.values(doc.media)
        .map((m) => `${m.id}=${JSON.stringify(m.source)}`)
        .sort()
        .join('|')
    : ''
  const [round, setRound] = useState(0)

  useEffect(() => {
    if (!doc) return
    let alive = true
    // Sounds too (part C): the timeline draws their waveforms.
    const ids = Object.values(doc.media).map((m) => m.id)
    void Promise.all(
      ids.map(async (id): Promise<[string, MediaInfo | { error: string }]> => {
        try {
          const info = await editorApi.mediaInfo(projectMediaId(projectId, id))
          if (info.needs_proxy && info.proxy.state === 'none' && !asked.current.has(id)) {
            asked.current.add(id)
            void editorApi.requestProxy(projectMediaId(projectId, id)).catch(() => undefined)
          }
          return [id, info]
        } catch (e) {
          return [id, { error: e instanceof Error ? e.message : String(e) }]
        }
      })
    ).then((pairs) => {
      if (alive) setInfos(Object.fromEntries(pairs))
    })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, key, round])

  // A proxy being made: read again when it is done.
  const waiting = Object.values(infos).some(
    (i) => isInfo(i) && i.needs_proxy && (i.proxy.state === 'queued' || i.proxy.state === 'running' || i.proxy.state === 'none')
  )
  useEffect(() => {
    if (!waiting) return
    const timer = window.setInterval(() => setRound((r) => r + 1), 5000)
    return () => window.clearInterval(timer)
  }, [waiting])
  useEvents((e: StudioEvent) => {
    const ev = e as unknown as EditorEvent
    if (ev.type === 'media_job' && ev.job?.kind === 'proxy' && ev.job.status === 'done') setRound((r) => r + 1)
  })

  return infos
}

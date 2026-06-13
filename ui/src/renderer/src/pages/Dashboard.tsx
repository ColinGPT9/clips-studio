import { useEffect, useMemo, useState } from 'react'
import SystemStats from '../components/SystemStats'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import type { Clip, Settings, StudioEvent, Video } from '../lib/types'

type SortMode = 'newest' | 'channel'

function describeEvent(e: StudioEvent): string {
  if (e.type === 'progress') {
    if (e.stage === 'render') return `Rendering clip ${e.clip}/${e.total}`
    if (e.stage === 'done') return `Finished — ${e.clips} clip(s) created`
    return `Stage: ${e.stage}${e.title ? ` — ${e.title}` : ''}`
  }
  if (e.type === 'job') return `Job ${e.job_id}: ${e.status}${e.error ? ` (${e.error})` : ''}`
  if (e.type === 'model_pull') return `Model ${e.tag}: ${e.status}`
  return JSON.stringify(e)
}

export default function Dashboard(): JSX.Element {
  const [videos, setVideos] = useState<Video[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [sort, setSort] = useState<SortMode>('newest')
  const [channelFilter, setChannelFilter] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [clipsByVideo, setClipsByVideo] = useState<Record<string, Clip[]>>({})

  const refresh = async (): Promise<void> => {
    try {
      setVideos(await api.videos())
      setSettings(await api.settings())
    } catch {
      /* backend not up yet */
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  useEvents((e) => {
    const line = `${new Date().toLocaleTimeString()}  ${describeEvent(e)}`
    setLog((prev) => [line, ...prev].slice(0, 200))
    if (e.type === 'job' && (e.status === 'done' || e.status === 'failed')) refresh()
  })

  const toggleExpand = async (videoId: string): Promise<void> => {
    if (expanded === videoId) {
      setExpanded(null)
      return
    }
    setExpanded(videoId)
    if (!clipsByVideo[videoId]) {
      try {
        const clips = await api.clips(videoId)
        setClipsByVideo((prev) => ({ ...prev, [videoId]: clips }))
      } catch {
        /* leave empty */
      }
    }
  }

  const shown = useMemo(() => {
    let list = [...videos]
    if (channelFilter) list = list.filter((v) => (v.channel_name || 'Unknown channel') === channelFilter)
    if (sort === 'channel') {
      list.sort(
        (a, b) =>
          (a.channel_name || 'zzz').localeCompare(b.channel_name || 'zzz') ||
          b.created_at.localeCompare(a.created_at)
      )
    } else {
      list.sort((a, b) => b.created_at.localeCompare(a.created_at))
    }
    return list
  }, [videos, sort, channelFilter])

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Dashboard</h2>
        {settings && (
          <span className="bg-raised px-3 py-1.5 rounded-lg text-sm">
            model: <span className="text-accent font-medium">{settings.model}</span>
          </span>
        )}
      </div>

      <SystemStats />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <section className="card" aria-label="Processed videos">
          <div className="flex items-center justify-between mb-3 gap-3">
            <h3 className="font-semibold">Processed videos</h3>
            <div className="flex items-center gap-2">
              {channelFilter && (
                <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => setChannelFilter(null)}>
                  {channelFilter} ✕
                </button>
              )}
              <label htmlFor="sort-videos" className="label">
                Sort
              </label>
              <select
                id="sort-videos"
                className="input !w-32 !py-1 text-sm"
                value={sort}
                onChange={(e) => setSort(e.target.value as SortMode)}
              >
                <option value="newest">Newest</option>
                <option value="channel">Channel A–Z</option>
              </select>
            </div>
          </div>

          {shown.length === 0 ? (
            <p className="text-muted text-sm">Nothing yet — paste a URL in Clip Studio.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="label text-left">
                  <th className="pb-2 font-normal">Channel</th>
                  <th className="pb-2 font-normal">Title</th>
                  <th className="pb-2 font-normal">Status</th>
                  <th className="pb-2 font-normal text-right">Clips</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((v) => (
                  <>
                    <tr key={v.video_id} className="border-t border-raised/50">
                      <td className="py-2 pr-2 max-w-36">
                        <button
                          className="text-accent hover:underline truncate block max-w-full text-left"
                          onClick={() => setChannelFilter(v.channel_name || 'Unknown channel')}
                          aria-label={`Show only videos from ${v.channel_name || 'unknown channel'}`}
                        >
                          {v.channel_name || '—'}
                        </button>
                      </td>
                      <td className="py-2 pr-3">
                        <button
                          className="text-left hover:text-accent truncate block max-w-64"
                          onClick={() => toggleExpand(v.video_id)}
                          aria-expanded={expanded === v.video_id}
                          aria-label={`Show generated clip titles for ${v.title || v.video_id}`}
                        >
                          {expanded === v.video_id ? '▾ ' : '▸ '}
                          {v.title || v.video_id}
                        </button>
                      </td>
                      <td className="py-2">
                        <span
                          className={
                            v.status === 'done'
                              ? 'text-success'
                              : v.status === 'failed'
                                ? 'text-error'
                                : 'text-warn'
                          }
                        >
                          {v.status}
                        </span>
                      </td>
                      <td className="py-2 text-right tabular-nums">{v.clip_count}</td>
                    </tr>
                    {expanded === v.video_id && (
                      <tr key={`${v.video_id}-clips`}>
                        <td colSpan={4} className="pb-3 pl-6">
                          {clipsByVideo[v.video_id] === undefined ? (
                            <p className="text-muted text-xs">Loading clips…</p>
                          ) : clipsByVideo[v.video_id].length === 0 ? (
                            <p className="text-muted text-xs">No clips generated.</p>
                          ) : (
                            <ul className="space-y-1 text-xs text-muted list-disc pl-4">
                              {clipsByVideo[v.video_id].map((c) => (
                                <li key={c.id}>
                                  <span className="text-ink">{c.title || c.hook || 'Untitled'}</span>{' '}
                                  ({Math.round(c.end_s - c.start_s)}s · score {c.score})
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="card" aria-label="Activity log">
          <h3 className="font-semibold mb-3">Activity</h3>
          <div className="h-72 overflow-y-auto font-mono text-xs space-y-1 text-muted" role="log">
            {log.length === 0 ? (
              <p>Waiting for events…</p>
            ) : (
              log.map((line, i) => <p key={i}>{line}</p>)
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

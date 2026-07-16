import { useEffect, useMemo, useState } from 'react'
import FeedbackHub from '../components/FeedbackHub'
import GenerateBar from '../components/GenerateBar'
import { Trash } from '../components/icons'
import ProcessingBar from '../components/ProcessingBar'
import SystemStats from '../components/SystemStats'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import type { Clip, Settings, StudioEvent, Video } from '../lib/types'

const DONATE_URL = 'https://github.com/sponsors/ColinGPT9'

type SortMode = 'newest' | 'channel'

function describeEvent(e: StudioEvent): string {
  if (e.type === 'progress') {
    if (e.stage === 'render') return `Rendering clip ${e.clip}/${e.total}`
    if (e.stage === 'done') return `Finished — ${e.clips} clip(s) created`
    if (e.stage === 'prefetch') return 'Downloading the next queued video in the background'
    return `Stage: ${e.stage}${e.title ? ` — ${e.title}` : ''}`
  }
  if (e.type === 'job') return `Job ${e.job_id}: ${e.status}${e.error ? ` (${e.error})` : ''}`
  if (e.type === 'model_pull') return `Model ${e.tag}: ${e.status}`
  return JSON.stringify(e)
}

export default function Dashboard({
  onOpenInStudio
}: {
  onOpenInStudio: (videoId: string, clipId?: number) => void
}): JSX.Element {
  const [videos, setVideos] = useState<Video[]>([])
  const [settings, setSettings] = useState<Settings | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [sort, setSort] = useState<SortMode>('newest')
  const [channelFilter, setChannelFilter] = useState<{
    creatorId: number | null
    label: string
  } | null>(null)
  const [search, setSearch] = useState('')
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
    const msg = describeEvent(e)
    const line = `${new Date().toLocaleTimeString()}  ${msg}`
    setLog((prev) => {
      // Skip consecutive duplicates: only log when the message actually
      // changes, so a stage that emits every second doesn't spam the feed.
      if (prev.length && prev[0].slice(prev[0].indexOf('  ') + 2) === msg) return prev
      return [line, ...prev].slice(0, 200)
    })
    if (e.type === 'job' && (e.status === 'done' || e.status === 'failed')) refresh()
  })

  const remove = async (videoId: string, label: string): Promise<void> => {
    if (!window.confirm(`Delete "${label}" and all its clips? This removes the files from disk too.`))
      return
    try {
      await api.deleteVideo(videoId)
      refresh()
    } catch (e) {
      window.alert(`Could not delete: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const fmtTime = (s: number): string => {
    if (!s) return '—'
    const m = Math.floor(s / 60)
    return m > 0 ? `${m}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`
  }

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
    if (channelFilter) {
      // Creator-aware: clicking a channel shows ALL of that creator's
      // channels (e.g. their Twitch and YouTube accounts linked in the
      // Creators tab), falling back to the exact channel string for
      // videos with no creator profile.
      list = list.filter((v) =>
        channelFilter.creatorId != null
          ? v.creator_id === channelFilter.creatorId
          : (v.channel_name || 'Unknown channel') === channelFilter.label
      )
    }
    const q = search.trim().toLowerCase()
    if (q) {
      list = list.filter(
        (v) =>
          (v.title || '').toLowerCase().includes(q) ||
          (v.channel_name || '').toLowerCase().includes(q)
      )
    }
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
    <div className="h-full flex flex-col p-6 gap-4">
      {/* Pinned top: title + post bar always visible */}
      <div className="shrink-0 space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-2xl font-bold">Dashboard</h2>
          <div className="flex items-center gap-2">
            <FeedbackHub />
            {settings && (
              <span className="bg-raised px-3 py-1.5 rounded-lg text-sm">
                model: <span className="text-accent font-medium">{settings.model}</span>
              </span>
            )}
          </div>
        </div>
        <GenerateBar />
        <ProcessingBar />
      </div>

      {/* Middle: videos + activity, each scrolls on its own */}
      <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-2 gap-5">
        <section className="card flex flex-col overflow-hidden" aria-label="Processed videos">
          <div className="flex items-center justify-between mb-3 gap-3 flex-wrap shrink-0">
            <h3 className="font-semibold">Processed videos</h3>
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="search"
                className="input !w-44 !py-1 text-sm"
                placeholder="Search title or channel…"
                aria-label="Search processed videos by title or channel"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {channelFilter && (
                <button className="btn-ghost !px-2.5 !py-1 text-xs" onClick={() => setChannelFilter(null)}>
                  {channelFilter.label} ✕
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

          <div className="overflow-y-auto flex-1 min-h-0">
          {shown.length === 0 ? (
            <p className="text-muted text-sm">Nothing yet — paste a link above to make your first clips.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="label text-left">
                  <th className="pb-2 font-normal">Channel</th>
                  <th className="pb-2 font-normal">Title</th>
                  <th className="pb-2 font-normal">Status</th>
                  <th className="pb-2 font-normal text-right">Clips</th>
                  <th className="pb-2 font-normal text-right">Time</th>
                  <th className="pb-2 font-normal"></th>
                </tr>
              </thead>
              <tbody>
                {shown.map((v) => (
                  <>
                    <tr key={v.video_id} className="border-t border-raised/50">
                      <td className="py-2 pr-2 max-w-36">
                        <button
                          className="text-accent hover:underline truncate block max-w-full text-left"
                          onClick={() =>
                            setChannelFilter({
                              creatorId: v.creator_id ?? null,
                              label: v.creator_name || v.channel_name || 'Unknown channel'
                            })
                          }
                          aria-label={`Show all videos from ${v.creator_name || v.channel_name || 'unknown channel'}`}
                          title={
                            v.creator_id != null
                              ? 'Show this creator’s videos from all their linked channels'
                              : undefined
                          }
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
                      <td className="py-2 text-right tabular-nums text-muted">
                        {fmtTime(v.process_seconds)}
                      </td>
                      <td className="py-2 text-right">
                        <button
                          className="text-muted hover:text-error px-1"
                          onClick={() => remove(v.video_id, v.title || v.video_id)}
                          aria-label={`Delete ${v.title || v.video_id}`}
                          title="Delete this video and its clips"
                        >
                          <Trash />
                        </button>
                      </td>
                    </tr>
                    {expanded === v.video_id && (
                      <tr key={`${v.video_id}-clips`}>
                        <td colSpan={6} className="pb-3 pl-6">
                          {clipsByVideo[v.video_id] === undefined ? (
                            <p className="text-muted text-xs">Loading clips…</p>
                          ) : clipsByVideo[v.video_id].length === 0 ? (
                            <p className="text-muted text-xs">No clips generated.</p>
                          ) : (
                            <ul className="space-y-1 text-xs text-muted pl-1">
                              {clipsByVideo[v.video_id].map((c) => (
                                <li key={c.id}>
                                  <button
                                    className="text-left text-accent hover:underline"
                                    onClick={() => onOpenInStudio(v.video_id, c.id)}
                                    title="Open this clip in Clip Studio"
                                  >
                                    {c.title || c.hook || 'Untitled'}
                                  </button>{' '}
                                  <span className="text-muted">
                                    ({Math.round(c.end_s - c.start_s)}s · score {c.score})
                                  </span>
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
          </div>
        </section>

        <section className="card flex flex-col overflow-hidden" aria-label="Activity log">
          <h3 className="font-semibold mb-3 shrink-0">Activity</h3>
          <div className="overflow-y-auto flex-1 min-h-0 font-mono text-xs space-y-1 text-muted" role="log">
            {log.length === 0 ? (
              <p>Waiting for events…</p>
            ) : (
              log.map((line, i) => <p key={i}>{line}</p>)
            )}
          </div>
          {/* System stats tucked at the bottom of Activity, out of the way */}
          <div className="shrink-0 mt-3 pt-3 border-t border-raised/60">
            <SystemStats compact />
          </div>
        </section>
      </div>

      {/* Pinned bottom: prominent, persistent donate section */}
      <div className="shrink-0 card flex items-center justify-between gap-6 flex-wrap bg-gradient-to-r from-accent/15 to-accent/25 border border-accent/40 !py-5">
        <div>
          <p className="font-bold text-xl text-ink">Clips Studio is free &amp; open source ❤️</p>
          <p className="text-base text-ink/80 mt-1">
            It runs entirely on your PC with no fees. Please consider donating to help cover
            development costs and keep it free for everyone.
          </p>
        </div>
        <a
          href={DONATE_URL}
          target="_blank"
          rel="noreferrer"
          className="btn-accent shrink-0 no-underline text-lg px-8 py-3 font-semibold"
        >
          Donate to the project
        </a>
      </div>
    </div>
  )
}

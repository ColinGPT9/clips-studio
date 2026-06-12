import { useEffect, useState } from 'react'
import SystemStats from '../components/SystemStats'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import type { Settings, StudioEvent, Video } from '../lib/types'

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
        <section className="card">
          <h3 className="font-semibold mb-3">Processed videos</h3>
          {videos.length === 0 ? (
            <p className="text-muted text-sm">Nothing yet — paste a URL in Clip Studio.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="label text-left">
                  <th className="pb-2 font-normal">Title</th>
                  <th className="pb-2 font-normal">Status</th>
                  <th className="pb-2 font-normal text-right">Clips</th>
                </tr>
              </thead>
              <tbody>
                {videos.map((v) => (
                  <tr key={v.video_id} className="border-t border-raised/50">
                    <td className="py-2 pr-3 truncate max-w-72">{v.title || v.video_id}</td>
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
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="card">
          <h3 className="font-semibold mb-3">Activity</h3>
          <div className="h-72 overflow-y-auto font-mono text-xs space-y-1 text-muted">
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

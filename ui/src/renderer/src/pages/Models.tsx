import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import type { ModelsInfo } from '../lib/types'

export default function Models(): JSX.Element {
  const [info, setInfo] = useState<ModelsInfo | null>(null)
  const [offline, setOffline] = useState(false)
  const [pullTag, setPullTag] = useState('')
  const [pullStatus, setPullStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = async (): Promise<void> => {
    try {
      setInfo(await api.models())
      setOffline(false)
    } catch {
      setOffline(true)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  useEvents((e) => {
    if (e.type !== 'model_pull') return
    if (e.status === 'done') {
      setPullStatus(null)
      refresh()
    } else if (e.status === 'error') {
      setPullStatus(`Download failed: ${e.error ?? 'unknown error'}`)
    } else {
      const pct = e.completed && e.total ? ` ${Math.round((e.completed / e.total) * 100)}%` : ''
      setPullStatus(`${e.tag}: ${e.status}${pct}`)
    }
  })

  const activate = async (tag: string): Promise<void> => {
    setBusy(tag)
    try {
      await api.activateModel(tag)
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  const remove = async (tag: string): Promise<void> => {
    setBusy(tag)
    try {
      await api.deleteModel(tag)
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  const pull = async (): Promise<void> => {
    if (!pullTag.trim()) return
    setPullStatus(`${pullTag}: starting…`)
    await api.pullModel(pullTag.trim())
    setPullTag('')
  }

  if (offline) {
    return (
      <div className="p-6">
        <h2 className="text-2xl font-bold mb-4">Models</h2>
        <div className="card text-warn">
          Ollama isn’t reachable. Make sure it’s installed and running, then reopen this page.
        </div>
      </div>
    )
  }
  if (!info) return <div className="p-6 text-muted">Loading…</div>

  return (
    <div className="p-6 space-y-5 max-w-3xl">
      <h2 className="text-2xl font-bold">Models</h2>

      <section className="card space-y-3">
        <h3 className="font-semibold">Installed</h3>
        {info.installed.map((m) => {
          const isActive = info.active === `ollama/${m.name}`
          return (
            <div key={m.name} className="flex items-center gap-3 border-t border-raised/50 pt-3">
              <div className="flex-1">
                <p className="font-medium">
                  {m.name}
                  {isActive && <span className="ml-2 text-xs bg-accent/15 text-accent px-2 py-0.5 rounded">active</span>}
                </p>
                <p className="text-xs text-muted">{m.size_gb.toFixed(1)} GB on disk</p>
              </div>
              {!isActive && (
                <>
                  <button className="btn-accent !py-1.5" onClick={() => activate(m.name)} disabled={busy !== null}>
                    Use
                  </button>
                  <button className="btn-ghost !py-1.5" onClick={() => remove(m.name)} disabled={busy !== null}>
                    Remove
                  </button>
                </>
              )}
            </div>
          )
        })}
      </section>

      <section className="card space-y-3">
        <h3 className="font-semibold">Download a model</h3>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="e.g. gemma3:12b"
            value={pullTag}
            onChange={(e) => setPullTag(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && pull()}
          />
          <button className="btn-accent" onClick={pull}>
            Download
          </button>
        </div>
        {pullStatus && <p className="text-sm text-accent">{pullStatus}</p>}
        <table className="w-full text-sm mt-2">
          <thead>
            <tr className="label text-left">
              <th className="pb-2 font-normal">Your hardware</th>
              <th className="pb-2 font-normal">Recommended</th>
              <th className="pb-2 font-normal">Why</th>
            </tr>
          </thead>
          <tbody>
            {info.recommendations.map((r) => (
              <tr key={r.hardware} className="border-t border-raised/50">
                <td className="py-2 text-muted">{r.hardware}</td>
                <td className="py-2 font-mono text-xs">{r.model}</td>
                <td className="py-2 text-muted">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}

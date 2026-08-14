import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { speedNote } from '../lib/modelSpeed'
import { useEvents } from '../lib/useEvents'
import type { ModelsInfo } from '../lib/types'

export default function Models(): JSX.Element {
  const [info, setInfo] = useState<ModelsInfo | null>(null)
  const [offline, setOffline] = useState(false)
  const [pullTag, setPullTag] = useState('')
  const [pullStatus, setPullStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  // For the "won't fit your card" warning — the one speed difference big
  // enough that people report it as the app being broken.
  const [vram, setVram] = useState<number | null>(null)

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
    // Best-effort: with no reading, the notes still describe relative speed,
    // they just cannot warn that a model will not fit the card.
    api
      .systemStats()
      .then((s) => setVram(s.gpu?.vram_total ?? null))
      .catch(() => setVram(null))
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
                {(() => {
                  const note = speedNote(m, info.installed, vram)
                  if (!note) return null
                  return (
                    <p
                      className={`text-xs mt-0.5 ${
                        note.tone === 'warn'
                          ? 'text-red-400'
                          : note.tone === 'slow'
                            ? 'text-amber-400'
                            : 'text-muted'
                      }`}
                    >
                      {note.tone !== 'ok' && <span aria-hidden="true">⚠ </span>}
                      {note.text}
                    </p>
                  )
                })()}
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
        {/* Two tables, because there are two different questions. This one
            answers "what will my machine run"; every row is a VRAM tier. */}
        {/* pr-6 on every cell but the last: without it the columns touch, and
            the headings render as one word — "RECOMMENDEDWHY". The model
            column is nowrap so a tag never wraps mid-name, and align-top keeps
            short cells level with notes that run to two lines. */}
        <table className="w-full text-sm mt-2">
          <thead>
            <tr className="label text-left">
              <th className="pb-2 pr-6 font-normal">Your hardware</th>
              <th className="pb-2 pr-6 font-normal">Recommended</th>
              <th className="pb-2 font-normal">Why</th>
            </tr>
          </thead>
          <tbody>
            {info.recommendations.map((r) => (
              <tr key={r.hardware} className="border-t border-raised/50">
                <td className="py-2 pr-6 align-top text-muted">{r.hardware}</td>
                <td className="py-2 pr-6 align-top font-mono text-xs whitespace-nowrap">
                  {r.model}
                </td>
                <td className="py-2 align-top text-muted">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* And this one answers "what should I use it for" — translation,
            licence, newer releases. Mixing the two under one heading is what
            made the old single table read as nonsense. */}
        {info.other_models && info.other_models.length > 0 && (
          <>
            <h3 className="label mt-6">Other models</h3>
            <p className="text-xs text-muted mt-1">
              Picked for a purpose rather than for your graphics card. All run locally, and all
              are free to use on clips you earn from.
            </p>
            <p className="text-xs text-muted mt-1">
              Only <code>gemma:7b</code>, <code>gemma3:4b</code> and <code>gemma3:12b</code> have
              been tested against real streams. The rest should work &mdash; they are all driven
              the same way &mdash; but nobody has measured whether they pick better clips.
            </p>
            <table className="w-full text-sm mt-2">
              <thead>
                <tr className="label text-left">
                  <th className="pb-2 pr-6 font-normal">For</th>
                  <th className="pb-2 pr-6 font-normal">Model</th>
                  <th className="pb-2 font-normal">Notes</th>
                </tr>
              </thead>
              <tbody>
                {info.other_models.map((r) => (
                  <tr key={r.purpose} className="border-t border-raised/50">
                    <td className="py-2 pr-6 align-top text-muted">{r.purpose}</td>
                    <td className="py-2 pr-6 align-top font-mono text-xs whitespace-nowrap">
                      {r.model}
                    </td>
                    <td className="py-2 align-top text-muted">{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
    </div>
  )
}

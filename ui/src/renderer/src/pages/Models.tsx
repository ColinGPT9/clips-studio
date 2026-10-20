import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../lib/api'
import { foldPullEvent, mergePulls, pullText } from '../lib/modelPull'
import { speedNote } from '../lib/modelSpeed'
import { useEvents } from '../lib/useEvents'
import type { ModelPull, ModelsInfo } from '../lib/types'
import OpenRouterPrompt from '../components/OpenRouterPrompt'

export default function Models(): JSX.Element {
  const [info, setInfo] = useState<ModelsInfo | null>(null)
  const [offline, setOffline] = useState(false)
  const [pullTag, setPullTag] = useState('')
  // Every download on screen, by tag: two can run at once, and a single line
  // showed whichever had spoken last.
  const [pulls, setPulls] = useState<Record<string, ModelPull>>({})
  const pullsRef = useRef(pulls)
  pullsRef.current = pulls
  const [busy, setBusy] = useState<string | null>(null)
  // For the "won't fit your card" warning — the one speed difference big
  // enough that people report it as the app being broken.
  const [vram, setVram] = useState<number | null>(null)
  // A cloud provider in use on the user's own key: local models are then
  // only needed to switch back, and Ollama not running is not a problem.
  const [cloud, setCloud] = useState('')
  useEffect(() => {
    api
      .ai()
      .then((s) => {
        const label = s.providers.find((p) => p.id === s.active.provider)?.label
        setCloud(s.active.local ? '' : `${label ?? s.active.provider} (${s.active.model})`)
      })
      .catch(() => setCloud(''))
  }, [])
  const cloudNote = cloud && (
    <div className="card text-sm">
      You&apos;re using <span className="text-ink">{cloud}</span> with your own API key, chosen in{' '}
      <button className="text-accent hover:underline" onClick={() => window.dispatchEvent(new Event('open-settings'))}>
        Settings → AI
      </button>
      . Local models are only needed if you switch back to running AI on this PC.
    </div>
  )

  const refresh = useCallback(async (): Promise<void> => {
    try {
      setInfo(await api.models())
      setOffline(false)
    } catch {
      setOffline(true)
    }
  }, [])

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
    if (e.status === 'done') void refresh()
    setPulls((shown) => foldPullEvent(shown, e))
  })

  // The events can miss the one that matters: the engine drops them for a
  // window that falls behind, and a reconnect leaves a gap. Missing "done" or
  // "error" left a download at its last percentage for good, so the engine's
  // own record is read on opening the page and every few seconds while
  // anything is downloading.
  const catchUp = useCallback(async (): Promise<void> => {
    const server = await api
      .pullStatus()
      .then((r) => r.pulls)
      .catch(() => null)
    if (!server) return // engine not answering: the next look, or an event, will say
    const before = pullsRef.current
    if (server.some((p) => p.status === 'done' && before[p.tag] && !before[p.tag].done)) void refresh()
    setPulls((shown) => mergePulls(shown, server))
  }, [refresh])

  useEffect(() => {
    void catchUp()
  }, [catchUp])

  const downloading = Object.values(pulls).some((p) => !p.done)
  useEffect(() => {
    if (!downloading) return
    const timer = setInterval(() => void catchUp(), 3000)
    return () => clearInterval(timer)
  }, [downloading, catchUp])

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

  const startPull = async (tag: string): Promise<void> => {
    try {
      await api.pullModel(tag)
    } catch (e) {
      // Shown, rather than lost in an unhandled promise with the button
      // looking as if it did nothing.
      setPulls((shown) => ({
        ...shown,
        [tag]: { tag, status: 'error', completed: null, total: null, error: errorText(e), done: true }
      }))
      return
    }
    if (tag === pullTag.trim()) setPullTag('')
    // Its first event may already be in; that is fresher than "starting".
    setPulls((shown) =>
      shown[tag] && !shown[tag].done
        ? shown
        : { ...shown, [tag]: { tag, status: 'starting', completed: null, total: null, error: null, done: false } }
    )
  }

  const pull = async (): Promise<void> => {
    const tag = pullTag.trim()
    if (tag) await startPull(tag)
  }

  if (offline) {
    return (
      <div className="p-6">
        <h2 className="text-2xl font-bold mb-4">Models</h2>
        {cloudNote || (
          <div className="card text-warn">
            Ollama isn’t reachable. Make sure it’s installed and running, then reopen this page.
          </div>
        )}
      </div>
    )
  }
  if (!info) return <div className="p-6 text-muted">Loading…</div>

  return (
    <div className="p-6 space-y-5 max-w-3xl">
      <h2 className="text-2xl font-bold">Models</h2>
      {cloudNote || (
        // Highlighted when the card is under the "Under 6 GB VRAM" tier or
        // there is no reading of one: the model guide's own threshold.
        <OpenRouterPrompt
          prominent={vram !== null && vram < 5.5 * 1024 ** 3}
          title="Don't want to run models locally?"
          body="Try OpenRouter with your own key: one key for many cloud models, billed by OpenRouter to your account."
        />
      )}

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
        {Object.values(pulls).map((p) => (
          <div key={p.tag} className="flex items-center gap-3">
            <p className={`flex-1 text-sm ${p.status === 'error' ? 'text-red-400' : 'text-accent'}`}>
              {p.tag}: {p.status === 'error' ? `Download failed: ${p.error || 'unknown error'}` : pullText(p)}
            </p>
            {p.status === 'error' && (
              <button className="btn-ghost !py-1.5" onClick={() => void startPull(p.tag)}>
                Try again
              </button>
            )}
          </div>
        ))}
        {/* Two tables, because there are two different questions. This one
            answers "what will my machine run"; every row is a VRAM tier. */}
        {/* The middle column is only as wide as a model tag, and the heading
            was "Recommended" — uppercased and letter-spaced by .label, that is
            wider than the column, so it overflowed and ran into the next one:
            "RECOMMENDEDWHY". Padding cannot fix a heading wider than its
            column. "Model" fits, says what the column holds, and matches the
            second table. whitespace-nowrap stops any heading wrapping if the
            window narrows. */}
        <table className="w-full text-sm mt-2">
          <thead>
            <tr className="label text-left">
              <th className="pb-2 pr-6 font-normal whitespace-nowrap">Your hardware</th>
              <th className="pb-2 pr-6 font-normal whitespace-nowrap">Model</th>
              <th className="pb-2 font-normal whitespace-nowrap">Why</th>
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
                  <th className="pb-2 pr-6 font-normal whitespace-nowrap">For</th>
                  <th className="pb-2 pr-6 font-normal whitespace-nowrap">Model</th>
                  <th className="pb-2 font-normal whitespace-nowrap">Notes</th>
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

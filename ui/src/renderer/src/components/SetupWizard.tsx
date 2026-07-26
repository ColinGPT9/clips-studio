import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import type { Preflight, PreflightCheck, SystemStats } from '../lib/types'

const DONE_KEY = 'setup-wizard-done'

/** Has the wizard been completed on this machine? */
export function setupDone(): boolean {
  return localStorage.getItem(DONE_KEY) === '1'
}

export function resetSetup(): void {
  localStorage.removeItem(DONE_KEY)
}

const LABEL: Record<string, string> = {
  ffmpeg: 'Video engine',
  ffprobe: 'Video inspector',
  ollama: 'AI runtime (Ollama)',
  model: 'AI model',
  gpu: 'Graphics card',
  disk: 'Free space'
}

function CheckRow({ check }: { check: PreflightCheck }): JSX.Element {
  const state = check.ok ? 'ok' : check.blocking ? 'bad' : 'warn'
  const mark = { ok: '✓', bad: '✕', warn: '!' }[state]
  const tone = {
    ok: 'bg-green-500/15 text-green-400',
    bad: 'bg-red-500/15 text-red-400',
    warn: 'bg-yellow-500/15 text-yellow-400'
  }[state]
  return (
    <div className="flex gap-3 py-2.5 border-b border-raised/40 last:border-0">
      <span
        className={`size-6 shrink-0 rounded-full grid place-items-center text-xs font-bold ${tone}`}
        aria-label={state === 'ok' ? 'ready' : state === 'bad' ? 'missing' : 'warning'}
      >
        {mark}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{LABEL[check.name] ?? check.name}</p>
        <p className="text-xs text-muted truncate" title={check.detail}>
          {check.detail}
        </p>
        {!check.ok && check.fix && <p className="text-xs text-accent mt-1">{check.fix}</p>}
      </div>
    </div>
  )
}

/** First-run setup. Walks a new creator from "just installed" to "ready to
 *  make a clip", which is the gap the installer alone can't close: Ollama and
 *  the model are deliberately not bundled, so somebody has to explain that
 *  rather than letting the first video fail with a stack trace. */
export default function SetupWizard({ onClose }: { onClose: () => void }): JSX.Element {
  const [step, setStep] = useState(0)
  const [pre, setPre] = useState<Preflight | null>(null)
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [rec, setRec] = useState<{ model: string; reason: string } | null>(null)
  const [checking, setChecking] = useState(false)
  const [pullStatus, setPullStatus] = useState<string | null>(null)
  const [pullPct, setPullPct] = useState<number | null>(null)
  const [error, setError] = useState('')

  const refresh = useCallback(async (): Promise<void> => {
    setChecking(true)
    setError('')
    try {
      // The recommendation comes from the SERVER, which owns the one hardware
      // table. Deciding it here as well produced advice that contradicted the
      // Models page for the same graphics card.
      const [p, s, m] = await Promise.all([
        api.preflight(),
        api.systemStats().catch(() => null),
        api.models().catch(() => null)
      ])
      setPre(p)
      if (s) setStats(s)
      if (m?.recommended) setRec(m.recommended)
    } catch (e) {
      setError(
        'Could not reach the Clips Studio engine. If you just installed, give it a few seconds and try again.'
      )
    } finally {
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEvents((e) => {
    if (e.type !== 'model_pull') return
    if (e.status === 'done') {
      setPullStatus('Downloaded')
      setPullPct(100)
      void refresh()
    } else if (e.status === 'error') {
      setPullStatus(`Download failed: ${e.error ?? 'unknown error'}`)
      setPullPct(null)
    } else {
      setPullStatus(e.status ?? 'downloading')
      setPullPct(e.completed && e.total ? Math.round((e.completed / e.total) * 100) : null)
    }
  })

  const check = (name: string): PreflightCheck | undefined =>
    pre?.checks.find((c) => c.name === name)
  const ollamaOk = check('ollama')?.ok ?? false
  const modelOk = check('model')?.ok ?? false

  const finish = (): void => {
    localStorage.setItem(DONE_KEY, '1')
    onClose()
  }

  const steps = ['Welcome', 'Checks', 'AI model', 'Ready']

  return (
    <div className="fixed inset-0 z-50 bg-base/95 backdrop-blur-sm grid place-items-center p-6">
      <div className="w-full max-w-2xl bg-surface border border-raised/60 rounded-2xl shadow-2xl overflow-hidden">
        {/* progress rail */}
        <div className="flex">
          {steps.map((label, i) => (
            <div
              key={label}
              className={`flex-1 h-1 ${i <= step ? 'bg-accent' : 'bg-raised'}`}
              aria-hidden
            />
          ))}
        </div>

        <div className="p-7">
          <p className="text-xs text-muted mb-1">
            Step {step + 1} of {steps.length} · {steps[step]}
          </p>

          {/* ---------------------------------------------- 0: welcome */}
          {step === 0 && (
            <>
              <h2 className="text-2xl font-bold">Welcome to Clips Studio</h2>
              <p className="text-sm text-muted mt-3 leading-relaxed">
                Paste a link to a YouTube video, Twitch VOD or Kick VOD and Clips Studio finds
                the best moments, crops them to fit a phone screen, and writes captions and
                titles for you.
              </p>
              <p className="text-sm text-muted mt-3 leading-relaxed">
                All of it runs on this computer. Your videos are never uploaded anywhere, there
                is no subscription, and there is no limit on how many clips you make.
              </p>
              <p className="text-sm text-muted mt-3 leading-relaxed">
                This takes about a minute. We&apos;ll check everything is installed, then pick
                an AI model that suits your graphics card.
              </p>
            </>
          )}

          {/* ---------------------------------------------- 1: checks */}
          {step === 1 && (
            <>
              <h2 className="text-2xl font-bold">Checking your computer</h2>
              {error && (
                <div className="mt-4 bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded-lg px-4 py-2.5">
                  {error}
                </div>
              )}
              {!pre && !error && (
                <p className="text-sm text-muted mt-4">Checking…</p>
              )}
              {pre && (
                <>
                  <div className="mt-4">
                    {pre.checks.map((c) => (
                      <CheckRow key={c.name} check={c} />
                    ))}
                  </div>
                  {!ollamaOk && (
                    <div className="mt-4 bg-accent/10 border border-accent/30 rounded-lg px-4 py-3">
                      <p className="text-sm font-medium">Ollama needs installing</p>
                      <p className="text-xs text-muted mt-1 leading-relaxed">
                        Ollama runs the AI on your computer. It&apos;s free, and it&apos;s a
                        separate program because it manages your graphics card and updates
                        itself. Install it, leave it running, then press Check again.
                      </p>
                      <div className="flex items-center gap-2 mt-3">
                        <button
                          className="btn-accent !py-1.5"
                          onClick={() => void window.studio.openExternal('https://ollama.com/download')}
                        >
                          Get Ollama
                        </button>
                        <button className="btn-ghost !py-1.5" disabled={checking} onClick={refresh}>
                          {checking ? 'Checking…' : 'Check again'}
                        </button>
                      </div>
                    </div>
                  )}
                  {ollamaOk && (
                    <button
                      className="btn-ghost !py-1.5 mt-4"
                      disabled={checking}
                      onClick={refresh}
                    >
                      {checking ? 'Checking…' : 'Check again'}
                    </button>
                  )}
                </>
              )}
            </>
          )}

          {/* ---------------------------------------------- 2: model */}
          {step === 2 && (
            <>
              <h2 className="text-2xl font-bold">Pick an AI model</h2>
              <p className="text-sm text-muted mt-3 leading-relaxed">
                This is the brain that decides which moments are worth clipping and writes the
                titles. It downloads once and then works offline.
              </p>

              <div className="mt-4 bg-raised/40 border border-raised rounded-lg px-4 py-3">
                <p className="text-xs text-muted">
                  {stats?.gpu ? `Detected: ${stats.gpu.name}` : 'No graphics card detected'}
                </p>
                <p className="text-sm font-semibold mt-1">
                  Recommended: {rec ? rec.model : '…'}
                </p>
                <p className="text-xs text-muted mt-1 leading-relaxed">{rec?.reason ?? ''}</p>
              </div>

              {modelOk ? (
                <p className="text-sm text-green-400 mt-4">
                  ✓ {check('model')?.detail} — nothing to download.
                </p>
              ) : !ollamaOk ? (
                <p className="text-sm text-muted mt-4">
                  Ollama has to be running before a model can be downloaded. Go back a step.
                </p>
              ) : (
                <div className="mt-4">
                  <button
                    className="btn-accent"
                    disabled={!rec || (pullStatus !== null && pullPct !== 100)}
                    onClick={async () => {
                      if (!rec) return
                      setPullStatus('starting…')
                      setPullPct(null)
                      try {
                        await api.pullModel(rec.model)
                      } catch (e) {
                        setPullStatus(`Could not start: ${e instanceof Error ? e.message : e}`)
                      }
                    }}
                  >
                    Download {rec ? rec.model : 'model'}
                  </button>
                  {pullStatus && (
                    <div className="mt-3">
                      <p className="text-xs text-muted tabular-nums">
                        {pullStatus}
                        {pullPct !== null ? ` · ${pullPct}%` : ''}
                      </p>
                      {pullPct !== null && (
                        <div className="h-2 mt-1.5 rounded-full bg-raised overflow-hidden">
                          <div
                            className="h-full bg-accent rounded-full transition-[width] duration-500"
                            style={{ width: `${Math.max(2, pullPct)}%` }}
                          />
                        </div>
                      )}
                      <p className="text-[11px] text-muted mt-1.5">
                        These are several gigabytes — it can take a while on a slow connection.
                        You can leave this window open.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {/* ---------------------------------------------- 3: done */}
          {step === 3 && (
            <>
              <h2 className="text-2xl font-bold">
                {pre?.ready ? "You're ready" : 'Nearly there'}
              </h2>
              {pre?.ready ? (
                <>
                  <p className="text-sm text-muted mt-3 leading-relaxed">
                    Everything is installed. Paste a video link on the Clip Studio page and
                    press <b>Generate clips</b>.
                  </p>
                  <p className="text-sm text-muted mt-3 leading-relaxed">
                    A first video takes a while — it downloads, transcribes, then studies the
                    whole thing before it renders anything. Later videos from the same creator
                    get faster as the app learns them.
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm text-muted mt-3 leading-relaxed">
                    Some things are still missing. You can carry on and sort them out later —
                    the app will tell you what it needs when you try to make a clip.
                  </p>
                  <div className="mt-4">
                    {(pre?.checks ?? []).filter((c) => !c.ok).map((c) => (
                      <CheckRow key={c.name} check={c} />
                    ))}
                  </div>
                </>
              )}
              <p className="text-xs text-muted mt-4">
                You can run this again any time from Settings.
              </p>
            </>
          )}
        </div>

        {/* ---------------------------------------------- footer */}
        <div className="flex items-center gap-2 px-7 py-4 border-t border-raised/60 bg-base/40">
          {step > 0 && (
            <button className="btn-ghost" onClick={() => setStep((n) => n - 1)}>
              Back
            </button>
          )}
          <button className="text-xs text-muted hover:text-ink ml-auto" onClick={finish}>
            Skip setup
          </button>
          {step < steps.length - 1 ? (
            <button
              className="btn-accent"
              // Only the Ollama step is a hard gate: without it there is no AI
              // at all, and every later screen is meaningless.
              disabled={step === 1 && !ollamaOk}
              title={step === 1 && !ollamaOk ? 'Install Ollama first, then press Check again' : ''}
              onClick={() => setStep((n) => n + 1)}
            >
              Continue
            </button>
          ) : (
            <button className="btn-accent" onClick={finish}>
              Start clipping
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

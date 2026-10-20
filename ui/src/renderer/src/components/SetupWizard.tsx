import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../lib/api'
import { isChecking, pullPercent, pullStep } from '../lib/modelPull'
import { useEvents } from '../lib/useEvents'
import type { ModelPull, Preflight, PreflightCheck, SystemStats } from '../lib/types'
import OpenRouterPrompt from './OpenRouterPrompt'

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
  // Not "Ollama" any more. It ships inside the app and starts itself, so its
  // brand name is an implementation detail — naming it here only invites
  // someone to go and install a second copy.
  ollama: 'AI runtime',
  model: 'AI model',
  ai: 'AI model',
  whisper: 'Transcription',
  transcription: 'Transcription',
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
 *  make a clip".
 *
 *  This used to be a checkpoint: it stopped, told people Ollama was a separate
 *  program, and sent them to a website to install it. The runtime now ships
 *  inside the app, so the only thing left that cannot be bundled is the
 *  language model — several gigabytes, under a licence that has to be accepted
 *  by the person downloading it rather than on their behalf. So the wizard
 *  asks for nothing and installs nothing. It starts that one download by
 *  itself and shows how far along it is. */
export default function SetupWizard({ onClose }: { onClose: () => void }): JSX.Element {
  const [step, setStep] = useState(0)
  const [pre, setPre] = useState<Preflight | null>(null)
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [rec, setRec] = useState<{ model: string; reason: string } | null>(null)
  const [checking, setChecking] = useState(false)
  const [pullStatus, setPullStatus] = useState<string | null>(null)
  const [pullPct, setPullPct] = useState<number | null>(null)
  // While true the window keeps asking the engine how the download is going,
  // in case the events miss its end (see catchUp).
  const [pullRunning, setPullRunning] = useState(false)
  const [canRetry, setCanRetry] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async (): Promise<Preflight | null> => {
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
      return p
    } catch (e) {
      setError(
        'Could not reach the Clips Kitty engine. If you just installed, give it a few seconds and try again.'
      )
      return null
    } finally {
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const check = (name: string): PreflightCheck | undefined =>
    pre?.checks.find((c) => c.name === name)
  const ollamaOk = check('ollama')?.ok ?? false
  const modelOk = check('model')?.ok ?? false

  // A pull is server-side and survives this component, so "have we already
  // asked for one" is not something the render state can be trusted for —
  // re-entering the step would queue a second download of the same model.
  const pullStarted = useRef(false)
  // True once the engine has said it is running the download: its POST
  // returned, or its record listed it. Only then does a record gone missing
  // mean the download stopped; before, a look can simply have beaten the POST.
  const pullKnown = useRef(false)

  const failed = useCallback((text: string): void => {
    setPullStatus(text)
    setPullPct(null)
    setPullRunning(false)
    setCanRetry(true)
    pullStarted.current = false // so Try again actually tries again
    pullKnown.current = false
  }, [])

  // One download's state, from an event or from the engine's own record. Only
  // the recommended model's: this window shows one download, and the Models
  // page may be fetching another.
  const showPull = useCallback(
    (p: { status?: string; completed?: number | null; total?: number | null; error?: string | null }): void => {
      if (p.status === 'done') {
        setPullStatus('Downloaded')
        setPullPct(100)
        setPullRunning(false)
        pullStarted.current = false
        pullKnown.current = false
        // "done" means Ollama has the model, so setup should now find one it
        // can run. If it still doesn't, the download didn't really finish,
        // and saying "Downloaded" over a missing model is the bug this is
        // here for. No model check at all means a cloud AI is chosen.
        void refresh().then((latest) => {
          const model = latest?.checks.find((c) => c.name === 'model')
          if (model && !model.ok) failed("The download didn't finish. Try again; it carries on where it stopped.")
        })
      } else if (p.status === 'error') {
        failed(`Download failed: ${p.error || 'unknown error'}`)
      } else {
        const checkingIt = isChecking(p.status)
        setPullStatus(pullStep(p.status))
        setPullPct(checkingIt ? null : pullPercent(p.completed, p.total))
        setPullRunning(true)
        setCanRetry(false)
        pullStarted.current = true
      }
    },
    [refresh, failed]
  )

  const recModel = rec?.model
  useEvents((e) => {
    if (e.type !== 'model_pull' || !recModel || e.tag !== recModel) return
    showPull(e)
  })

  // The events can miss the one that matters (a reconnect, or a window that
  // fell behind), which left the bar at its last percentage for good. So the
  // engine's own record is read when the window opens and every few seconds
  // while the download runs.
  const catchUp = useCallback(async (): Promise<void> => {
    if (!recModel) return
    let mine: ModelPull | undefined
    try {
      const { pulls } = await api.pullStatus()
      mine = pulls.find((p) => p.tag === recModel)
    } catch {
      return // engine not answering: the next look, or an event, will say
    }
    if (mine) {
      if (!mine.done) pullKnown.current = true
      showPull(mine)
      return
    }
    if (!pullKnown.current) return
    // The engine has no record of a download it said it was running: it was
    // restarted, and its downloads went with it. The Models page drops such a
    // download; here the bar sat at its last percentage, asking every few
    // seconds, for good. Unless the model got in first, it stopped.
    const latest = await refresh()
    if (!latest) return // not answering now: the next look tries again
    const model = latest.checks.find((c) => c.name === 'model')
    if (model && !model.ok) {
      failed('The download stopped. Try again; it carries on where it stopped.')
    } else {
      setPullRunning(false)
      pullStarted.current = false
      pullKnown.current = false
    }
  }, [recModel, showPull, refresh, failed])

  useEffect(() => {
    void catchUp()
  }, [catchUp])

  useEffect(() => {
    if (!pullRunning) return
    const timer = setInterval(() => void catchUp(), 3000)
    return () => clearInterval(timer)
  }, [pullRunning, catchUp])

  const startPull = useCallback(async (): Promise<void> => {
    if (!rec || pullStarted.current) return
    pullStarted.current = true
    setCanRetry(false)
    setPullRunning(true)
    setPullStatus(pullStep('starting'))
    setPullPct(null)
    pullKnown.current = false
    try {
      await api.pullModel(rec.model)
    } catch (e) {
      failed(`Could not start: ${errorText(e)}`)
      return
    }
    // Unless it has already ended: a "done" or "error" in before the POST
    // came back has settled it, and a missing record then means nothing.
    if (pullStarted.current) pullKnown.current = true
  }, [rec, failed])

  // Start the download the moment the setup step opens. Nobody installs a
  // clipping app in order to choose a language model, and the one worth having
  // is decided by their graphics card — which the server already knows.
  // Once only: after a failure the next try is the Try again button, or a
  // download that keeps failing would start itself again on every check.
  const autoStarted = useRef(false)
  useEffect(() => {
    if (step !== 1 || !ollamaOk || !rec || modelOk || autoStarted.current) return
    autoStarted.current = true
    void startPull()
  }, [step, ollamaOk, rec, modelOk, startPull])

  const finish = (): void => {
    localStorage.setItem(DONE_KEY, '1')
    onClose()
  }

  const steps = ['Welcome', 'Setting up', 'Ready']

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
              <h2 className="text-2xl font-bold">Welcome to Clips Kitty</h2>
              <p className="text-sm text-muted mt-3 leading-relaxed">
                Paste a link to a YouTube video, Twitch VOD or Kick VOD and Clips Kitty finds
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

          {/* ------------------------------- 1: checks + the one download */}
          {step === 1 && (
            <>
              <h2 className="text-2xl font-bold">Setting up</h2>
              <p className="text-sm text-muted mt-3 leading-relaxed">
                Everything Clips Kitty needs came with it, apart from the AI model that picks
                the moments worth clipping. We&apos;re fetching the one that suits your graphics
                card now — it downloads once and then works offline.
              </p>
              {/* For the old laptop or low-spec mini PC that cannot run the
                  model: OpenRouter on their own key instead. Prominent when
                  the card is under the "Under 6 GB VRAM, or an older PC" tier
                  the model guide uses (llm/manager.recommend_for), or there is
                  no card at all. The local download carries on either way, so
                  a machine that can run it still ends up ready. */}
              {(() => {
                const vram = stats?.gpu?.vram_total ?? 0
                const lowSpec = !!stats && (!stats.gpu || vram < 5.5 * 1024 ** 3)
                return (
                  <OpenRouterPrompt
                    className="mt-3"
                    prominent={lowSpec}
                    title={lowSpec ? 'Local AI may be slow on this computer.' : 'PC too old to run AI?'}
                    body={
                      lowSpec
                        ? 'Recommended: OpenRouter. Bring your own OpenRouter key to use a cloud model instead (billed by OpenRouter to your account).'
                        : 'Use OpenRouter with your own key instead, billed by OpenRouter to your account.'
                    }
                    onSetup={finish}
                  />
                )
              })()}
              {error && (
                <div className="mt-4 bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded-lg px-4 py-2.5">
                  {error}
                </div>
              )}
              {!pre && !error && <p className="text-sm text-muted mt-4">Checking…</p>}

              {pre && (
                <>
                  <div className="mt-4 bg-raised/40 border border-raised rounded-lg px-4 py-3">
                    <p className="text-xs text-muted">
                      {stats?.gpu ? `Detected: ${stats.gpu.name}` : 'No graphics card detected'}
                    </p>
                    <p className="text-sm font-semibold mt-1">
                      {modelOk
                        ? `✓ ${check('model')?.detail}`
                        : `Downloading ${rec ? rec.model : '…'}`}
                    </p>
                    <p className="text-xs text-muted mt-1 leading-relaxed">{rec?.reason ?? ''}</p>

                    {!modelOk && pullStatus && (
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
                          Several gigabytes — it can take a while on a slow connection. You can
                          leave this window open, or carry on and let it finish in the
                          background.
                        </p>
                        {canRetry && (
                          <button className="btn-ghost !py-1.5 mt-2" onClick={() => void startPull()}>
                            Try again
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="mt-4">
                    {pre.checks.map((c) => (
                      <CheckRow key={c.name} check={c} />
                    ))}
                  </div>
                  <button className="btn-ghost !py-1.5 mt-4" disabled={checking} onClick={refresh}>
                    {checking ? 'Checking…' : 'Check again'}
                  </button>
                </>
              )}
            </>
          )}

          {/* ---------------------------------------------- 2: done */}
          {step === 2 && (
            <>
              <h2 className="text-2xl font-bold">
                {pre?.ready ? "You're ready" : 'Nearly there'}
              </h2>
              {pre?.ready ? (
                <>
                  <p className="text-sm text-muted mt-3 leading-relaxed">
                    Everything is installed. Paste a video link on the Clip Editor page and
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
            // No gate any more. There is nothing left for a creator to go and
            // install, so nothing they could be blocked on fixing — and the
            // model download continues server-side whether this window is on
            // this step or not.
            <button className="btn-accent" onClick={() => setStep((n) => n + 1)}>
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

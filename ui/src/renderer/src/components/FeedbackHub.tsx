import { useState } from 'react'
import { api } from '../lib/api'

/** Feedback Hub: report a bug / request a feature / suggest an improvement
 *  in plain language. Diagnostics (versions, hardware, AI model, log tail)
 *  are auto-attached — previewable before sending — and the report goes to
 *  the developers with ONE click: no accounts, no sign-ups. (Under the
 *  hood it becomes a GitHub issue via the feedback relay, but users never
 *  need to know or care what GitHub is.) */

type Kind = 'bug' | 'feature' | 'improvement'

const AREAS = [
  ['ui', 'The app’s look & controls'],
  ['video-editor', 'The clip editor'],
  ['ai', 'AI results (clips picked, captions, titles)'],
  ['performance', 'Speed / freezes'],
  ['youtube', 'YouTube videos'],
  ['twitch', 'Twitch streams'],
  ['kick', 'Kick streams'],
  ['accessibility', 'Accessibility']
] as const

const KINDS: { id: Kind; icon: string; title: string; blurb: string }[] = [
  { id: 'bug', icon: '🐞', title: 'Report a bug', blurb: 'Something broke or looks wrong' },
  { id: 'feature', icon: '💡', title: 'Request a feature', blurb: 'Something new you wish the app did' },
  { id: 'improvement', icon: '🚀', title: 'Suggest an improvement', blurb: 'Make an existing part better' }
]

export default function FeedbackHub(): JSX.Element {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<Kind | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [areas, setAreas] = useState<string[]>([])
  const [images, setImages] = useState<string[]>([])
  const [includeDiag, setIncludeDiag] = useState(true)
  const [diagPreview, setDiagPreview] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const set = (k: string, v: string): void => setAnswers((a) => ({ ...a, [k]: v }))
  const reset = (): void => {
    setKind(null)
    setAnswers({})
    setAreas([])
    setImages([])
    setDone(null)
    setError(null)
    setDiagPreview(null)
  }

  const field = (
    key: string,
    label: string,
    placeholder: string,
    rows = 2
  ): JSX.Element => (
    <div key={key}>
      <label htmlFor={`fb-${key}`} className="label">
        {label}
      </label>
      <textarea
        id={`fb-${key}`}
        className="input mt-1 resize-none"
        rows={rows}
        placeholder={placeholder}
        value={answers[key] ?? ''}
        onChange={(e) => set(key, e.target.value)}
      />
    </div>
  )

  const titleFor = (): string => {
    const src =
      kind === 'bug' ? (answers.happened ?? '') : (answers.what ?? '')
    return src.split('\n')[0].slice(0, 120)
  }

  const canSend =
    titleFor().trim().length >= 8 &&
    (kind === 'bug'
      ? (answers.trying ?? '').trim().length > 0
      : (answers.why ?? '').trim().length > 0)

  const submit = async (): Promise<void> => {
    if (!kind) return
    setBusy(true)
    setError(null)
    try {
      const res = await api.feedbackSubmit({
        kind,
        title: titleFor(),
        answers,
        areas,
        severity: answers.severity ?? '',
        include_diagnostics: includeDiag,
        images: images.map((p) => ({ path: p }))
      })
      if (res.ok) {
        setDone('Sent — thank you! Your report went straight to the developers.')
      } else {
        // Relay unreachable/not configured: save the report locally instead.
        const blob = new Blob([res.markdown], { type: 'text/markdown' })
        const a = document.createElement('a')
        a.href = URL.createObjectURL(blob)
        a.download = `clips-studio-${kind}-report.md`
        a.click()
        URL.revokeObjectURL(a.href)
        setDone(
          'Sending isn’t available right now, so the report was saved to your Downloads as a file — ' +
            'you can share it on the project page or community instead.'
        )
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        className="btn-ghost shrink-0"
        onClick={() => {
          reset()
          setOpen(true)
        }}
        title="Report a bug, request a feature, or suggest an improvement"
      >
        💬 Feedback
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6"
          onClick={() => setOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Feedback Hub"
        >
          <div
            className="bg-surface border border-raised/60 rounded-2xl p-5 w-full max-w-xl max-h-[85vh] overflow-y-auto space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <p className="font-semibold text-lg">
                {kind === null ? 'Feedback Hub' : KINDS.find((k) => k.id === kind)?.title}
              </p>
              <button
                className="text-muted hover:text-ink text-lg leading-none px-1"
                onClick={() => setOpen(false)}
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            {done ? (
              <div className="space-y-4">
                <p className="text-sm">{done}</p>
                <button className="btn-accent" onClick={() => setOpen(false)}>
                  Close
                </button>
              </div>
            ) : kind === null ? (
              <div className="space-y-2">
                <p className="text-sm text-muted">
                  Found a problem or have an idea? Tell us in plain words — technical details are
                  collected automatically, and you don’t need an account for anything.
                </p>
                {KINDS.map((k) => (
                  <button
                    key={k.id}
                    className="w-full text-left bg-raised hover:bg-raised/70 rounded-xl px-4 py-3 flex items-center gap-3"
                    onClick={() => setKind(k.id)}
                  >
                    <span className="text-2xl" aria-hidden>
                      {k.icon}
                    </span>
                    <span>
                      <span className="font-medium block">{k.title}</span>
                      <span className="text-xs text-muted">{k.blurb}</span>
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                {kind === 'bug' && (
                  <>
                    {field('trying', 'What were you trying to do?', 'e.g. Export a clip of my Twitch stream')}
                    {field('happened', 'What happened?', 'e.g. The export button froze the whole app')}
                    {field('expected', 'What did you expect to happen?', 'e.g. The clip saves to my Downloads')}
                    <div className="flex gap-3 flex-wrap">
                      <div>
                        <label className="label" htmlFor="fb-repro">
                          Can you make it happen again?
                        </label>
                        <select
                          id="fb-repro"
                          className="input mt-1 !w-40"
                          value={answers.repro ?? ''}
                          onChange={(e) => set('repro', e.target.value)}
                        >
                          <option value="">Not sure</option>
                          <option>Always</option>
                          <option>Sometimes</option>
                          <option>Only once</option>
                        </select>
                      </div>
                      <div>
                        <label className="label" htmlFor="fb-sev">
                          How serious is it?
                        </label>
                        <select
                          id="fb-sev"
                          className="input mt-1 !w-44"
                          value={answers.severity ?? ''}
                          onChange={(e) => set('severity', e.target.value)}
                        >
                          <option value="">Annoying</option>
                          <option value="low">Low — cosmetic</option>
                          <option value="medium">Medium</option>
                          <option value="high">High — blocks my work</option>
                          <option value="critical">Critical — app unusable</option>
                        </select>
                      </div>
                    </div>
                    {field('notes', 'Anything else? (optional)', 'Anything that seems related', 2)}
                  </>
                )}
                {kind === 'feature' && (
                  <>
                    {field('what', 'What feature would you like?', 'e.g. Auto-post finished clips to TikTok')}
                    {field('why', 'Why would it be useful?', 'What problem would it solve for you?')}
                    {field('workflow', 'How would it fit your workflow? (optional)', 'e.g. After a stream I always…')}
                    <div>
                      <label className="label" htmlFor="fb-imp">
                        How important is this to you?
                      </label>
                      <select
                        id="fb-imp"
                        className="input mt-1 !w-52"
                        value={answers.importance ?? ''}
                        onChange={(e) => set('importance', e.target.value)}
                      >
                        <option value="">Nice to have</option>
                        <option>Would use it weekly</option>
                        <option>Would use it every video</option>
                        <option>Can’t use the app well without it</option>
                      </select>
                    </div>
                  </>
                )}
                {kind === 'improvement' && (
                  <>
                    {field('what', 'What would you like improved?', 'e.g. The timeline is hard to use with a trackpad')}
                    {field('why', 'Why would it improve Clips Studio?', 'What gets easier or faster?')}
                    {field('inspiration', 'Which app inspired this? (optional)', 'e.g. CapCut’s keyframe editor')}
                    {field('links', 'Links / screenshots of that feature (optional)', 'A YouTube video, docs page…')}
                  </>
                )}

                <div>
                  <p className="label mb-1">Which part of the app is this about? (optional)</p>
                  <div className="flex gap-1.5 flex-wrap">
                    {AREAS.map(([id, label]) => (
                      <button
                        key={id}
                        className={`px-2.5 py-1 rounded-full text-xs border ${
                          areas.includes(id)
                            ? 'border-accent text-accent bg-accent/10'
                            : 'border-raised text-muted hover:text-ink'
                        }`}
                        onClick={() =>
                          setAreas((a) => (a.includes(id) ? a.filter((x) => x !== id) : [...a, id].slice(0, 4)))
                        }
                        title={label}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex items-center gap-2 flex-wrap text-sm">
                  <button
                    className="btn-ghost !py-1.5 text-xs"
                    onClick={async () => {
                      const p = await window.studio.pickImageFile()
                      if (p) setImages((im) => [...im, p].slice(0, 3))
                    }}
                    disabled={images.length >= 3}
                  >
                    📷 Attach screenshot ({images.length}/3)
                  </button>
                  {images.map((p, i) => (
                    <span key={i} className="text-xs text-muted bg-raised rounded px-2 py-1">
                      {p.split(/[\\/]/).pop()}{' '}
                      <button
                        className="hover:text-error"
                        onClick={() => setImages((im) => im.filter((_, j) => j !== i))}
                        aria-label="Remove screenshot"
                      >
                        ✕
                      </button>
                    </span>
                  ))}
                </div>

                <div className="border-t border-raised/60 pt-3 space-y-2">
                  <label className="flex items-center gap-2 cursor-pointer text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-[#38BDF8]"
                      checked={includeDiag}
                      onChange={(e) => setIncludeDiag(e.target.checked)}
                    />
                    Include technical details (PC specs, app version, AI model, recent log) — this is
                    what lets someone actually fix it
                  </label>
                  {includeDiag && (
                    <button
                      className="text-xs text-accent hover:underline"
                      onClick={async () => {
                        if (diagPreview) {
                          setDiagPreview(null)
                          return
                        }
                        const d = await api.feedbackDiagnostics()
                        setDiagPreview(JSON.stringify(d, null, 2))
                      }}
                    >
                      {diagPreview ? 'Hide what will be shared' : 'See exactly what will be shared'}
                    </button>
                  )}
                  {diagPreview && (
                    <pre className="bg-base rounded-lg p-2 text-[10px] max-h-48 overflow-auto whitespace-pre-wrap">
                      {diagPreview}
                    </pre>
                  )}
                </div>

                {error && <p className="text-sm text-error">{error}</p>}

                <div className="flex gap-2 justify-end">
                  <button className="btn-ghost" onClick={reset} disabled={busy}>
                    Back
                  </button>
                  <button
                    className="btn-accent"
                    onClick={submit}
                    disabled={!canSend || busy}
                    title={!canSend ? 'Fill in the first two questions so the report is understandable' : undefined}
                  >
                    {busy ? 'Sending…' : 'Send feedback'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}

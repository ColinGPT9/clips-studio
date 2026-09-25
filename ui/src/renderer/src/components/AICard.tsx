import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../lib/api'
import { clearAISetupHint, peekAISetupHint } from '../lib/aiSetup'
import { t } from '../lib/i18n'
import type { AIModel, AIProvider, AIStatus } from '../lib/types'
import { forgetAIStatus } from '../lib/useAIStatus'

/** Settings → AI: where the AI work and the transcription run.
 *
 *  Local first, cloud when you need it, and OpenRouter the easiest cloud path:
 *
 *    1. This PC (Ollama, Whisper): the default. No key, nothing sent.
 *    2. OpenRouter: the recommended cloud option. One key reaches many models
 *       and providers, so switching models needs no new account.
 *    3. A direct provider API (OpenAI, Claude, Gemini, Grok, Meta): always
 *       available, offered as the advanced option.
 *
 *  Every cloud option is bring-your-own-key: the user's key and account, the
 *  provider's bill. Clips Kitty has no key of its own and proxies nothing.
 *  Nothing switches on until the user has pasted their own key and picked a
 *  model. The tiers come from the engine (GET /ai), so a new provider lands
 *  in the right place without touching this file. The key is write-only: the
 *  card only learns whether one is saved and its last four characters.
 */

type Job = 'ai' | 'stt'
type Tier = 'local' | 'recommended' | 'direct'
type Run = (fn: () => Promise<AIStatus | null>, done?: string) => Promise<boolean>

const LOCAL_ID: Record<Job, string> = { ai: 'ollama', stt: 'local' }

export default function AICard({ onOpenModels }: { onOpenModels?: () => void }): JSX.Element {
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [hint] = useState(peekAISetupHint)
  const cardRef = useRef<HTMLDivElement>(null)
  useEffect(clearAISetupHint, [])

  useEffect(() => {
    api
      .ai()
      .then(setStatus)
      .catch(() => {
        /* engine not up yet */
      })
  }, [])

  // Arriving from "Set up OpenRouter" elsewhere in the app: bring the card
  // into view. It opens on that setup; nothing is switched on.
  useEffect(() => {
    if (hint && status) cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [hint, status])

  const run: Run = async (fn, done = '') => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const next = await fn()
      if (next) setStatus(next)
      if (done) setNotice(done)
      forgetAIStatus() // the OpenRouter suggestions elsewhere follow the new choice
      return true
    } catch (e) {
      setError(errorText(e))
      return false
    } finally {
      setBusy(false)
    }
  }

  if (!status) {
    return (
      <div className="card text-sm text-muted" aria-label="AI">
        {t('Loading…')}
      </div>
    )
  }

  return (
    <div ref={cardRef} className="card space-y-5" aria-label="AI">
      <div>
        <h3 className="font-semibold">{t('AI')}</h3>
        <p className="text-xs text-muted mt-0.5">
          {t('Local first. Cloud when you need it, on your own API key. OpenRouter is the easiest cloud path.')}
        </p>
      </div>

      <JobChoice
        job="ai"
        title={t('Clip picking, titles and the assistant')}
        status={status}
        hint={hint}
        busy={busy}
        run={run}
        onOpenModels={onOpenModels}
      />
      <div className="border-t border-raised/60" />
      <JobChoice job="stt" title={t('Transcription')} status={status} hint={hint} busy={busy} run={run} />

      {notice && <p className="text-xs text-success">{notice}</p>}
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  )
}

/** One job's three tiers. Only the selected one opens up. */
function JobChoice({
  job,
  title,
  status,
  hint,
  busy,
  run,
  onOpenModels
}: {
  job: Job
  title: string
  status: AIStatus
  hint: string
  busy: boolean
  run: Run
  onOpenModels?: () => void
}): JSX.Element {
  const usable = status.providers.filter((p) => !p.local && (job === 'ai' || p.stt))
  const recommended = usable.filter((p) => p.tier === 2)
  const direct = usable.filter((p) => p.tier === 3)
  const activeId = job === 'ai' ? status.active.provider : status.transcription.backend
  const activeModel = job === 'ai' ? status.active.model : status.transcription.model

  const initial = (): { tier: Tier; direct: string } => {
    const wanted = job === 'ai' ? hint : ''
    const pick = usable.find((p) => p.id === (wanted || activeId))
    if (!pick) return { tier: 'local', direct: direct[0]?.id ?? '' }
    return pick.tier === 2
      ? { tier: 'recommended', direct: direct[0]?.id ?? '' }
      : { tier: 'direct', direct: pick.id }
  }
  const [tier, setTier] = useState<Tier>(() => initial().tier)
  const [recommendedId, setRecommendedId] = useState(() => {
    const pick = recommended.find((p) => p.id === (hint || activeId))
    return pick?.id ?? recommended[0]?.id ?? ''
  })
  const [directId, setDirectId] = useState(() => initial().direct)

  const chooseLocal = (): void => {
    setTier('local')
    if (activeId !== LOCAL_ID[job]) {
      void (job === 'ai'
        ? run(() => api.activateAI(LOCAL_ID.ai), t('AI runs on this PC again.'))
        : run(() => api.setTranscription(LOCAL_ID.stt), t('Transcription runs on this PC again.')))
    }
  }

  const provider = (id: string): AIProvider | undefined => usable.find((p) => p.id === id)
  const inUse = (id: string): string => (activeId === id ? activeModel : '')

  return (
    <div className="space-y-2" role="radiogroup" aria-label={title}>
      <p className="label">{title}</p>

      <TierRow
        name={`${job}-tier`}
        checked={tier === 'local'}
        onSelect={chooseLocal}
        busy={busy}
        title={job === 'ai' ? `⭐ ${t('Ollama')}` : `⭐ ${t('Whisper')}`}
        badge={t('Local AI · recommended')}
        tone="local"
        tagline={
          job === 'ai'
            ? t('Runs AI on your computer. No API key needed, nothing sent anywhere. The first choice for Clips Kitty.')
            : t('Transcribes on your computer. No API key needed, nothing sent anywhere.')
        }
      >
        {job === 'ai' && onOpenModels && (
          <button className="text-xs text-accent hover:underline" onClick={onOpenModels}>
            {t('Manage local models')} →
          </button>
        )}
      </TierRow>

      {recommended.map((p) => (
        <TierRow
          key={p.id}
          name={`${job}-tier`}
          checked={tier === 'recommended' && recommendedId === p.id}
          onSelect={() => {
            setTier('recommended')
            setRecommendedId(p.id)
          }}
          busy={busy}
          title={`🚀 ${p.label}`}
          badge={t('Recommended cloud AI')}
          tone="recommended"
          tagline={p.tagline}
        >
          <ProviderPanel provider={p} job={job} inUse={inUse(p.id)} busy={busy} run={run} recommended />
        </TierRow>
      ))}

      {direct.length > 0 && (
        <TierRow
          name={`${job}-tier`}
          checked={tier === 'direct'}
          onSelect={() => setTier('direct')}
          busy={busy}
          title={t('Direct provider API')}
          badge={t('Advanced')}
          tone="direct"
          tagline={t('Connect straight to one provider with your own key.')}
        >
          <select
            className="input !py-1 text-sm mb-2"
            value={directId}
            onChange={(e) => setDirectId(e.target.value)}
            aria-label={t('Provider')}
          >
            {direct.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {activeId === p.id ? ` · ${t('in use')}` : ''}
              </option>
            ))}
          </select>
          {provider(directId) && (
            <ProviderPanel
              key={directId}
              provider={provider(directId) as AIProvider}
              job={job}
              inUse={inUse(directId)}
              busy={busy}
              run={run}
            />
          )}
        </TierRow>
      )}
    </div>
  )
}

function TierRow({
  name,
  checked,
  onSelect,
  busy,
  title,
  badge,
  tone,
  tagline,
  children
}: {
  name: string
  checked: boolean
  onSelect: () => void
  busy: boolean
  title: string
  badge: string
  tone: 'local' | 'recommended' | 'direct'
  tagline: string
  children?: React.ReactNode
}): JSX.Element {
  const badgeTone = {
    local: 'bg-success/15 text-success',
    recommended: 'bg-accent/20 text-accent',
    direct: 'bg-raised text-muted'
  }[tone]
  return (
    <div
      className={`rounded-lg border p-3 transition-colors ${
        checked ? 'border-accent/60 bg-accent/5' : 'border-raised/60 hover:border-raised'
      }`}
    >
      <label className="flex gap-3 cursor-pointer">
        <input
          type="radio"
          name={name}
          checked={checked}
          disabled={busy}
          onChange={onSelect}
          className="mt-1 size-4 accent-[#38BDF8] shrink-0"
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold">{title}</span>
            <span className={`text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 ${badgeTone}`}>
              {badge}
            </span>
          </span>
          <span className="block text-xs text-muted mt-0.5">{tagline}</span>
        </span>
      </label>
      {checked && children && <div className="mt-3 pl-7 space-y-2">{children}</div>}
    </div>
  )
}

/** One provider's setup for one job: key, model, connection test, and what
 *  it costs and sends. OpenRouter also gets its three steps, the "why", and a
 *  model list grouped by who makes each model. */
function ProviderPanel({
  provider,
  job,
  inUse,
  busy,
  run,
  recommended = false
}: {
  provider: AIProvider
  job: Job
  inUse: string
  busy: boolean
  run: Run
  recommended?: boolean
}): JSX.Element {
  const [models, setModels] = useState<AIModel[]>([])
  const [filter, setFilter] = useState('')
  const [loadingModels, setLoadingModels] = useState(false)
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null)

  useEffect(() => {
    if (job !== 'ai' || !provider.has_key) return
    setLoadingModels(true)
    api
      .aiModels(provider.id)
      .then((got) => setModels(got.models))
      .catch(() => setModels([]))
      .finally(() => setLoadingModels(false))
  }, [provider.id, provider.has_key, job])

  const choose = (model: string): void => {
    if (!model) return
    void (job === 'ai'
      ? run(() => api.activateAI(provider.id, model), `${t('Now using')} ${provider.label} · ${model}.`)
      : run(() => api.setTranscription(provider.id, model), `${t('Transcribing online with')} ${model}.`))
  }

  const current = models.find((m) => m.id === inUse)

  return (
    <div className="space-y-2">
      {!recommended && (
        <p className="text-xs text-muted">
          {t('Direct provider integration: connect straight to')} {provider.label}{' '}
          {t('with your own API key. Useful if you want your')} {provider.label}{' '}
          {t('account, limits and billing.')}
        </p>
      )}

      {recommended && !provider.has_key && (
        <ol className="text-xs text-muted list-decimal pl-4 space-y-0.5">
          <li>
            <button
              className="text-accent hover:underline"
              onClick={() => void window.studio.openExternal(provider.key_url)}
            >
              {t('Open')} {provider.label} ↗
            </button>{' '}
            {t('and sign in or create an account.')}
          </li>
          <li>{t('Create an API key. Add credit, or start with the free models.')}</li>
          <li>{t('Paste the key below.')}</li>
        </ol>
      )}

      <KeyField provider={provider} busy={busy} run={run} />

      {provider.has_key && (
        <div className="space-y-1.5">
          {job === 'ai' ? (
            <ModelPicker
              models={models}
              filter={filter}
              setFilter={setFilter}
              loading={loadingModels}
              inUse={inUse}
              busy={busy}
              onChoose={choose}
              grouped={models.some((m) => m.vendor)}
            />
          ) : (
            <select
              className="input !py-1 text-sm"
              value={inUse}
              disabled={busy}
              onChange={(e) => choose(e.target.value)}
              aria-label={t('Transcription model')}
            >
              <option value="">{t('Choose a model')}</option>
              {(provider.stt_models ?? []).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          )}
          <div className="flex items-center gap-2 flex-wrap text-xs">
            <span className={inUse ? 'text-success' : 'text-muted'}>
              {inUse ? `● ${t('In use')}: ${inUse}` : t('Not in use yet: choose a model.')}
            </span>
            <button
              className="btn-ghost !py-0.5 !px-2 text-xs ml-auto"
              disabled={busy}
              onClick={() =>
                void api
                  .testAI(provider.id, job === 'ai' ? inUse : '')
                  .then(setTest)
                  .catch((e) => setTest({ ok: false, message: errorText(e) }))
              }
            >
              {t('Test connection')}
            </button>
          </div>
          {current?.note && <p className="text-[11px] text-amber-400">{current.note}</p>}
          {test && (
            <p className={`text-[11px] ${test.ok ? 'text-success' : 'text-red-400'}`}>
              {test.ok ? '✓ ' : ''}
              {test.message}
            </p>
          )}
        </div>
      )}

      {recommended && job === 'ai' && (
        <details className="text-xs">
          <summary className="cursor-pointer text-accent">{t('Why OpenRouter?')}</summary>
          <ul className="mt-1.5 list-disc pl-4 space-y-1 text-muted">
            <li>
              {t(
                'One cloud connection to many AI models and providers: Claude, GPT, Gemini, Muse Spark, Qwen and more, without a separate account and key for each.'
              )}
            </li>
            <li>{t('Switch models here at any time. You are not locked into one.')}</li>
            <li>
              {t(
                'If a provider serving your model is down, OpenRouter can send the request to another provider of the same model.'
              )}
            </li>
            <li>
              {t(
                "Compare models and prices in one place. OpenRouter charges the providers' own prices and a fee when you buy credits."
              )}
            </li>
            <li>
              {t(
                'Good for a low-spec PC or an always-on mini PC running Watched channels: the AI runs in the cloud while this PC does the rest.'
              )}
            </li>
          </ul>
        </details>
      )}

      <p className="text-[11px] text-muted leading-snug">
        {t('Billed by')} {provider.label} {t("to your account. Clips Kitty doesn't provide or pay for API use.")}{' '}
        <button
          className="text-accent hover:underline"
          onClick={() => void window.studio.openExternal(provider.pricing_url)}
        >
          {t('Pricing')} ↗
        </button>
        <br />
        {provider.privacy}
      </p>
    </div>
  )
}

// OpenRouter's list, grouped by who makes each model. Matched by the id's
// prefix, so no model names are written down here to go stale.
const VENDOR_GROUPS: [string, string[]][] = [
  ['Claude (Anthropic)', ['anthropic']],
  ['GPT (OpenAI)', ['openai']],
  ['Gemini (Google)', ['google']],
  ['Muse Spark and Llama (Meta)', ['meta', 'meta-llama']],
  ['Qwen', ['qwen']],
  ['Grok (xAI)', ['x-ai']],
  ['DeepSeek', ['deepseek']],
  ['Mistral', ['mistralai']]
]

function contextLabel(tokens: number): string {
  if (!tokens) return ''
  return tokens >= 1_000_000 ? `${Math.round(tokens / 100_000) / 10}M` : `${Math.round(tokens / 1000)}K`
}

function priceLabel(price: AIModel['price']): string {
  if (!price) return ''
  if (price.input === 0 && price.output === 0) return t('free')
  const f = (n: number): string => `$${Number(n.toPrecision(3))}`
  return `${f(price.input)} / ${f(price.output)} ${t('per 1M')}`
}

function optionLabel(m: AIModel): string {
  return [m.name, contextLabel(m.context), priceLabel(m.price)].filter(Boolean).join(' · ')
}

function ModelPicker({
  models,
  filter,
  setFilter,
  loading,
  inUse,
  busy,
  onChoose,
  grouped
}: {
  models: AIModel[]
  filter: string
  setFilter: (v: string) => void
  loading: boolean
  inUse: string
  busy: boolean
  onChoose: (id: string) => void
  grouped: boolean
}): JSX.Element {
  const needle = filter.trim().toLowerCase()
  const shown = models.filter((m) => !needle || `${m.id} ${m.name}`.toLowerCase().includes(needle))

  const groups: [string, AIModel[]][] = []
  if (grouped) {
    const claimed = new Set<string>()
    for (const [label, prefixes] of VENDOR_GROUPS) {
      const inGroup = shown.filter((m) => prefixes.includes(m.vendor))
      inGroup.forEach((m) => claimed.add(m.id))
      if (inGroup.length) groups.push([label, inGroup])
    }
    const rest = shown.filter((m) => !claimed.has(m.id))
    if (rest.length) groups.push([t('Other models'), rest])
  }

  return (
    <div className="space-y-1">
      <div className="flex gap-2 flex-wrap">
        <input
          className="input !py-1 text-sm flex-1 min-w-32"
          placeholder={loading ? t('Loading models…') : t('Search models')}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          spellCheck={false}
        />
        <select
          className="input !py-1 text-sm flex-[2] min-w-48"
          value={inUse}
          disabled={busy || loading}
          onChange={(e) => onChoose(e.target.value)}
          aria-label={t('Model')}
        >
          <option value="">{inUse || t('Choose a model')}</option>
          {grouped
            ? groups.map(([label, list]) => (
                <optgroup key={label} label={label}>
                  {list.map((m) => (
                    <option key={m.id} value={m.id}>
                      {optionLabel(m)}
                    </option>
                  ))}
                </optgroup>
              ))
            : shown.slice(0, 300).map((m) => (
                <option key={m.id} value={m.id}>
                  {optionLabel(m)}
                </option>
              ))}
        </select>
      </div>
      {models.length > 0 && (
        <p className="text-[11px] text-muted">
          {models.length} {t('models that can do this job.')}{' '}
          {grouped && t('You can switch models here at any time.')}
        </p>
      )}
    </div>
  )
}

/** The provider's key: saved (last four only), or a box to paste one into.
 *  Checked with the provider before it is kept. */
function KeyField({ provider, busy, run }: { provider: AIProvider; busy: boolean; run: Run }): JSX.Element {
  const [draft, setDraft] = useState('')
  const [show, setShow] = useState(false)
  const [replacing, setReplacing] = useState(false)

  if (provider.has_key && !replacing) {
    return (
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <span className="text-success">
          ● {t('Key saved')} <span className="text-muted">····{provider.key_tail}</span>
        </span>
        <button className="btn-ghost !py-0.5 !px-2 text-xs" onClick={() => setReplacing(true)}>
          {t('Replace')}
        </button>
        <button
          className="btn-ghost !py-0.5 !px-2 text-xs"
          disabled={busy}
          onClick={() => void run(() => api.deleteAIKey(provider.id), t('Key removed.'))}
        >
          {t('Remove')}
        </button>
      </div>
    )
  }

  const save = async (): Promise<void> => {
    const key = draft.trim()
    if (!key) return
    const ok = await run(() => api.putAIKey(provider.id, key), `${provider.label} ${t('accepted your key.')}`)
    if (ok) {
      setDraft('')
      setShow(false)
      setReplacing(false)
    }
  }

  return (
    <div className="flex gap-2 flex-wrap items-center">
      <input
        className="input flex-1 !py-1 text-sm min-w-48"
        type={show ? 'text' : 'password'}
        value={draft}
        placeholder={`${t('Paste your')} ${provider.key_label}`}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && void save()}
        autoComplete="off"
        spellCheck={false}
        aria-label={provider.key_label}
      />
      <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => setShow((v) => !v)}>
        {show ? t('Hide') : t('Show')}
      </button>
      <button className="btn-accent !py-1 !px-3 text-xs" disabled={busy || !draft.trim()} onClick={() => void save()}>
        {t('Save')}
      </button>
      <button
        className="text-xs text-accent hover:underline"
        onClick={() => void window.studio.openExternal(provider.key_url)}
      >
        {t('Get a key')} ↗
      </button>
      {replacing && (
        <button className="btn-ghost !py-1 !px-2 text-xs" onClick={() => setReplacing(false)}>
          {t('Cancel')}
        </button>
      )}
    </div>
  )
}

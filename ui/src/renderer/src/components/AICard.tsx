import { useEffect, useState } from 'react'
import { api, errorText } from '../lib/api'
import { t } from '../lib/i18n'
import type { AIModel, AIProvider, AIStatus } from '../lib/types'

/** Settings → AI: where the AI work and the transcription run.
 *
 *  This PC first, always: Ollama and Whisper are the default and cost
 *  nothing. The cloud providers are for PCs that cannot run the models, and
 *  every one is bring-your-own-key: the user's key, the user's account, billed
 *  by the provider. Clips Kitty has no key of its own and proxies nothing.
 *
 *  Compact on purpose: one dropdown per job, and only the chosen provider's
 *  key and model below it. Keys are per provider, so switching back and forth
 *  never asks for one twice, but only one key field is ever on screen. The
 *  provider list comes from the engine (GET /ai), so a new provider needs no
 *  change here. The key is write-only: the card is only told whether one is
 *  saved and its last four characters.
 */

const LOCAL_AI = 'ollama'
const LOCAL_STT = 'local'

export default function AICard({ onOpenModels }: { onOpenModels?: () => void }): JSX.Element {
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [aiChoice, setAiChoice] = useState('')
  const [sttChoice, setSttChoice] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .ai()
      .then((s) => {
        setStatus(s)
        setAiChoice(s.active.provider)
        setSttChoice(s.transcription.backend)
      })
      .catch(() => {
        /* engine not up yet */
      })
  }, [])

  const run = async (fn: () => Promise<AIStatus | null>, done = ''): Promise<boolean> => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const next = await fn()
      if (next) setStatus(next)
      if (done) setNotice(done)
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

  const cloud = status.providers.filter((p) => !p.local)
  const provider = (id: string): AIProvider | undefined => status.providers.find((p) => p.id === id)
  const aiProvider = provider(aiChoice)
  const sttProvider = provider(sttChoice)

  const pickAI = (id: string): void => {
    setAiChoice(id)
    if (id === LOCAL_AI && !status.active.local) {
      void run(() => api.activateAI(LOCAL_AI), t('AI runs on this PC again.'))
    }
  }

  const pickStt = (id: string): void => {
    setSttChoice(id)
    if (id === LOCAL_STT && status.transcription.backend !== LOCAL_STT) {
      void run(() => api.setTranscription(LOCAL_STT), t('Transcription runs on this PC again.'))
    } else if (id !== LOCAL_STT && provider(id)?.has_key) {
      void run(() => api.setTranscription(id), t('Transcription now runs online.'))
    }
  }

  return (
    <div className="card space-y-4" aria-label="AI">
      <div>
        <h3 className="font-semibold">{t('AI')}</h3>
        <p className="text-xs text-muted mt-0.5">
          {t(
            "Runs on this PC by default, free and private. If your PC can't run it, bring your own API key and a provider runs it instead, billed to your account."
          )}
        </p>
      </div>

      <div className="space-y-2">
        <label className="label block" htmlFor="ai-backend">
          {t('Clip picking, titles and the assistant')}
        </label>
        <select
          id="ai-backend"
          className="input text-sm"
          value={aiChoice}
          disabled={busy}
          onChange={(e) => pickAI(e.target.value)}
        >
          <option value={LOCAL_AI}>{t('This PC — Ollama (free, private)')}</option>
          <optgroup label={t('Bring your own key')}>
            {cloud.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </optgroup>
        </select>
        {aiChoice === LOCAL_AI || !aiProvider ? (
          <p className="text-xs text-muted">
            {t('Runs on this PC.')}{' '}
            {onOpenModels && (
              <button className="text-accent hover:underline" onClick={onOpenModels}>
                {t('Manage local models')} →
              </button>
            )}
          </p>
        ) : (
          <ProviderPanel
            key={`ai-${aiProvider.id}`}
            provider={aiProvider}
            kind="text"
            inUse={status.active.provider === aiProvider.id ? status.active.model : ''}
            busy={busy}
            run={run}
          />
        )}
      </div>

      <div className="space-y-2 border-t border-raised/60 pt-3">
        <label className="label block" htmlFor="ai-transcription">
          {t('Transcription')}
        </label>
        <select
          id="ai-transcription"
          className="input text-sm"
          value={sttChoice}
          disabled={busy}
          onChange={(e) => pickStt(e.target.value)}
        >
          <option value={LOCAL_STT}>{t('This PC — Whisper (free, private)')}</option>
          <optgroup label={t('Online with your own key')}>
            {cloud
              .filter((p) => p.stt)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
          </optgroup>
        </select>
        {sttChoice === LOCAL_STT || !sttProvider ? (
          <p className="text-xs text-muted">{t('Whisper runs on this PC.')}</p>
        ) : (
          <ProviderPanel
            key={`stt-${sttProvider.id}`}
            provider={sttProvider}
            kind="stt"
            inUse={status.transcription.backend === sttProvider.id ? status.transcription.model : ''}
            busy={busy}
            run={run}
          />
        )}
      </div>

      {notice && <p className="text-xs text-success">{notice}</p>}
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  )
}

/** One provider's key, model and connection test, for one job. */
function ProviderPanel({
  provider,
  kind,
  inUse,
  busy,
  run
}: {
  provider: AIProvider
  kind: 'text' | 'stt'
  inUse: string
  busy: boolean
  run: (fn: () => Promise<AIStatus | null>, done?: string) => Promise<boolean>
}): JSX.Element {
  const [models, setModels] = useState<AIModel[]>([])
  const [filter, setFilter] = useState('')
  const [loadingModels, setLoadingModels] = useState(false)
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null)

  const sttModels = provider.stt_models ?? []

  useEffect(() => {
    if (kind !== 'text' || !provider.has_key) return
    setLoadingModels(true)
    api
      .aiModels(provider.id)
      .then((got) => setModels(got.models))
      .catch(() => setModels([]))
      .finally(() => setLoadingModels(false))
  }, [provider.id, provider.has_key, kind])

  const choose = (model: string): void => {
    if (!model) return
    void (kind === 'text'
      ? run(() => api.activateAI(provider.id, model), `${t('Now using')} ${provider.label} · ${model}.`)
      : run(() => api.setTranscription(provider.id, model), `${t('Transcribing with')} ${model}.`))
  }

  const shown = models
    .filter((m) => !filter || `${m.id} ${m.name}`.toLowerCase().includes(filter.toLowerCase()))
    .slice(0, 200)
  const current = models.find((m) => m.id === inUse)

  return (
    <div className="space-y-2 rounded-lg bg-raised/30 p-3">
      <KeyField provider={provider} busy={busy} run={run} />

      {provider.has_key && (
        <div className="space-y-1.5">
          {kind === 'text' ? (
            <div className="flex gap-2 flex-wrap">
              <input
                className="input !py-1 text-sm flex-1 min-w-32"
                placeholder={loadingModels ? t('Loading models…') : t('Search models')}
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                spellCheck={false}
              />
              <select
                className="input !py-1 text-sm flex-[2] min-w-48"
                value={inUse}
                disabled={busy || loadingModels}
                onChange={(e) => choose(e.target.value)}
                aria-label={t('Model')}
              >
                <option value="">{inUse ? inUse : t('Choose a model')}</option>
                {shown.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <select
              className="input !py-1 text-sm"
              value={inUse}
              disabled={busy}
              onChange={(e) => choose(e.target.value)}
              aria-label={t('Transcription model')}
            >
              <option value="">{t('Choose a model')}</option>
              {sttModels.map((m) => (
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
                  .testAI(provider.id, kind === 'text' ? inUse : '')
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

/** The provider's key: saved (last four only), or a box to paste one into.
 *  Checked with the provider before it is kept. */
function KeyField({
  provider,
  busy,
  run
}: {
  provider: AIProvider
  busy: boolean
  run: (fn: () => Promise<AIStatus | null>, done?: string) => Promise<boolean>
}): JSX.Element {
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

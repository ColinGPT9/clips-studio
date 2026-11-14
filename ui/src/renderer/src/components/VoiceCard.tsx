import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { activeLocale, languageLabel, t } from '../lib/i18n'
import type { VoiceEngine, VoiceList, VoiceOption, VoiceStatus } from '../lib/types'

/** Settings → Voice: who speaks when the app speaks (a dub today, the
 *  voice-over tool next).
 *
 *  The built-in voice is the default and needs nothing. Voicebox, VoiceStudio
 *  or another voice app on this PC are for people who already run one: the
 *  app never installs, starts or changes them. Everything shown here comes
 *  from the engine (GET /voice, GET /voice/voices), which is also the only
 *  thing that ever talks to a voice app; this window never does.
 *
 *  Explanations are tooltips. The one line of text is whether the chosen
 *  voice app can be reached, in the engine's own words.
 *
 *  Keyboard: no control here is disabled while a change is being saved. A
 *  browser drops the focus of a control that becomes disabled, which would
 *  stop the arrow keys after one step and send a screen reader to the top of
 *  the page. A second change made while one is still being saved is ignored
 *  instead (the card says it is busy with aria-busy).
 */

const TIP: Record<VoiceEngine['id'], string> = {
  builtin: 'Speaks on this PC. Nothing to install or open.',
  voicebox: 'Uses the voices you made in your own Voicebox app. Voicebox has to be open.',
  voicestudio: 'Uses the voices of your own VoiceStudio app. VoiceStudio has to be open.',
  custom: 'Uses another voice app that runs on this PC. Enter its address below.'
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

function optionLabel(v: VoiceOption): string {
  return [
    v.name,
    v.country,
    v.quality,
    v.commercial_ok === false && t('not for monetised videos'),
    v.ready === false && t('not downloaded in Voicebox')
  ]
    .filter(Boolean)
    .join(' · ')
}

export default function VoiceCard(): JSX.Element {
  const [status, setStatus] = useState<VoiceStatus | null>(null)
  const [list, setList] = useState<VoiceList | null>(null)
  const [language, setLanguage] = useState(() => activeLocale())
  const [address, setAddress] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [player, setPlayer] = useState('')
  // Bumped after every change, so the voice list follows it.
  const [version, setVersion] = useState(0)
  const card = useRef<HTMLDivElement>(null)
  // The same as `busy`, readable at once by a second key press.
  const working = useRef(false)

  /** Start a change, unless one is running. */
  const begin = (): boolean => {
    if (working.current) return false
    working.current = true
    setBusy(true)
    setError('')
    return true
  }

  /** Finish a change. When the control that had the focus is gone ("Check
   *  again" leaves with its line), the focus goes to the chosen voice app
   *  rather than to the top of the page. */
  const end = (focused: Element | null): void => {
    working.current = false
    setBusy(false)
    requestAnimationFrame(() => {
      const box = card.current
      if (!box || !focused || focused.isConnected || document.activeElement !== document.body) return
      box.querySelector<HTMLInputElement>('input[type="radio"]:checked')?.focus()
    })
  }

  const run = async (fn: () => Promise<VoiceStatus>): Promise<void> => {
    if (!begin()) return
    const focused = card.current?.contains(document.activeElement) ? document.activeElement : null
    setError('')
    try {
      const next = await fn()
      setStatus(next)
      setAddress(next.settings.custom_address)
      setVersion((n) => n + 1)
    } catch (e) {
      setError(message(e))
    } finally {
      end(focused)
    }
  }

  useEffect(() => {
    void run(() => api.voice())
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const builtin = status?.engines.find((e) => e.id === 'builtin')
  // Every language a voice here can speak. A voice app does not say which
  // languages its voices speak, so it is offered the same list.
  const languages = [
    ...new Set(['en', ...(builtin?.kokoro?.languages ?? []), ...(builtin?.piper?.languages ?? [])])
  ].sort((a, b) => languageLabel(a).localeCompare(languageLabel(b)))
  const shown = languages.includes(language) ? language : 'en'

  useEffect(() => {
    if (!status) return
    let stale = false
    api
      .voiceList(shown)
      .then((got) => !stale && setList(got))
      .catch((e) => {
        if (stale) return
        setList(null)
        setError(message(e))
      })
    return () => {
      stale = true
    }
  }, [status === null, shown, version]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!status) {
    return (
      <div className="card text-sm text-muted" aria-label={t('Voice')}>
        {error ? t(error) : t('Loading…')}
      </div>
    )
  }

  const chosen = status.engines.find((e) => e.id === status.engine)
  const voices = list?.voices ?? []
  const selected = voices.find((v) => v.id === list?.default)
  // Whether the chosen voice can speak right now, in the engine's words
  // ("Open Voicebox to use it."). Empty when it can.
  const line = list?.message || chosen?.message || ''

  const unchanged = address.trim() === status.settings.custom_address
  const saveAddress = (): void => {
    if (!unchanged) void run(() => api.setVoice({ custom_address: address.trim() }))
  }

  const hear = async (): Promise<void> => {
    if (!begin()) return
    try {
      setPlayer(await api.voiceSample(shown, list?.default))
    } catch (e) {
      setPlayer('')
      setError(message(e))
    } finally {
      end(null)
    }
  }

  return (
    <div ref={card} className="card space-y-3" aria-label={t('Voice')} aria-busy={busy}>
      <h3 className="font-semibold">{t('Voice')}</h3>

      <div className="space-y-1.5" role="radiogroup" aria-label={t('Voice')}>
        {status.engines.map((e) => (
          <label
            key={e.id}
            className="flex items-center gap-2 text-sm cursor-pointer"
            title={[t(TIP[e.id]), e.licence_note && t(e.licence_note)].filter(Boolean).join(' ')}
          >
            <input
              type="radio"
              name="voice-engine"
              className="size-4 accent-[#38BDF8] shrink-0"
              checked={status.engine === e.id}
              onChange={() => {
                if (working.current) return
                setPlayer('')
                void run(() => api.setVoice({ engine: e.id }))
              }}
            />
            <span>{t(e.name)}</span>
            {e.optional && e.ready && <span className="text-[11px] text-success">● {t('Ready')}</span>}
          </label>
        ))}
      </div>

      {status.engine === 'custom' && (
        <div className="flex items-center gap-2">
          <input
            className="input flex-1 !py-1 text-sm min-w-0"
            value={address}
            placeholder="http://127.0.0.1:8880"
            onChange={(e) => setAddress(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && saveAddress()}
            spellCheck={false}
            autoComplete="off"
            aria-label={t("Your voice app's address")}
            title={t('The address your voice app shows. It has to be on this PC.')}
          />
          <button
            className="btn-ghost !py-1 !px-3 text-xs aria-disabled:opacity-40 aria-disabled:cursor-not-allowed"
            aria-disabled={unchanged}
            onClick={saveAddress}
          >
            {t('Save')}
          </button>
        </div>
      )}

      {line && (
        <p className="text-xs text-warn flex items-center gap-2 flex-wrap">
          <span>{t(line)}</span>
          {chosen?.optional && (
            <button className="text-accent hover:underline" onClick={() => void run(() => api.voice())}>
              {t('Check again')}
            </button>
          )}
        </p>
      )}

      {chosen?.optional && (
        <label
          className="flex items-center gap-2 text-xs cursor-pointer"
          title={t(
            'Off: the job stops and says why, so you never get a different voice than the one you picked. On: the built-in voice speaks instead.'
          )}
        >
          <input
            type="checkbox"
            checked={status.settings.fallback_to_builtin}
            onChange={(e) => void run(() => api.setVoice({ fallback_to_builtin: e.target.checked }))}
          />
          <span>{t('Use the built-in voice when it is closed')}</span>
        </label>
      )}

      <div className="space-y-1.5">
        <p className="label" title={t('The voice that speaks when you have not picked another one. One for each language.')}>
          {t('Default voice')}
        </p>
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <select
            className="input !w-auto !py-1 text-xs"
            value={shown}
            onChange={(e) => {
              setPlayer('')
              setList(null)
              setLanguage(e.target.value)
            }}
            aria-label={t('Language')}
          >
            {languages.map((code) => (
              <option key={code} value={code}>
                {languageLabel(code)}
              </option>
            ))}
          </select>
          <select
            className="input flex-1 min-w-40 !py-1 text-xs"
            value={list?.default ?? ''}
            disabled={voices.length === 0}
            onChange={(e) => {
              if (working.current) return
              setPlayer('')
              void run(() => api.setVoice({ voices: { [shown]: e.target.value } }))
            }}
            aria-label={t('Default voice')}
          >
            {voices.length === 0 && <option value="">{list ? t('No voices') : t('Loading…')}</option>}
            {voices.map((v) => (
              <option key={v.id} value={v.id} disabled={v.ready === false}>
                {optionLabel(v)}
              </option>
            ))}
          </select>
          <button
            className="px-2 py-1 rounded-md bg-raised text-muted hover:text-ink disabled:opacity-50"
            disabled={voices.length === 0}
            onClick={() => void hear()}
          >
            ▶ {t('Hear this voice')}
          </button>
        </div>
        {selected?.commercial_ok === false && (
          <span
            className="inline-block text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 bg-raised text-muted"
            title={t("This voice's licence does not allow videos that earn money.")}
          >
            {t('not for monetised videos')}
          </span>
        )}
        {player && (
          <audio
            key={player}
            src={player}
            controls
            autoPlay
            className="h-8 w-full"
            onError={() => setError(t('That voice could not be played.'))}
          />
        )}
      </div>

      {error && <p className="text-xs text-red-400">{t(error)}</p>}
    </div>
  )
}

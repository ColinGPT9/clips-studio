import { useEffect, useState } from 'react'
import { t } from '../lib/i18n'
import {
  chosenSettings,
  executionBadge,
  jobSettings,
  settingValue,
  type InstalledPlugin,
  type SettingSpec
} from '../lib/marketplace'
import { openMarketplace } from '../lib/plugins'
import type { PipelineChoice } from '../lib/types'

/** One video's community pipeline: which one, and the settings it offers.
 *  Shown under the video's switches when Pipeline is ticked, like the Sports
 *  row. A pipeline that sends anything off this PC says so right here, where
 *  the video is chosen for it. The engine checks the settings again when the
 *  video is added (plugins/store.clean_choice). */
export default function PipelineFields({
  value,
  pipelines,
  name,
  onChange,
  onProblem
}: {
  value: PipelineChoice
  /** Null while the engine hasn't said which are installed. */
  pipelines: InstalledPlugin[] | null
  name: string
  onChange: (next: PipelineChoice) => void
  /** Whether a box holds a value that can't be sent, so Generate can wait for it. */
  onProblem?: (bad: boolean) => void
}): JSX.Element {
  const loading = pipelines === null
  const plugin = pipelines?.find((p) => p.id === value.id)
  const fields = jobSettings(plugin?.settings)
  // What is typed, kept as typed so a half-written number isn't thrown away.
  const [raw, setRaw] = useState<Record<string, string>>({})
  const [problems, setProblems] = useState<Record<string, string>>({})

  const current = (n: string, spec: SettingSpec): unknown =>
    value.settings && n in value.settings ? value.settings[n] : spec.default

  const bad = Object.keys(problems).length > 0
  useEffect(() => {
    onProblem?.(bad)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bad])

  const set = (n: string, spec: SettingSpec, input: string | boolean): void => {
    if (typeof input === 'string') setRaw((r) => ({ ...r, [n]: input }))
    // An emptied box goes back to the plugin's default.
    const r = typeof input === 'string' && input.trim() === '' ? { value: spec.default } : settingValue(spec, input)
    setProblems((p) => {
      const next = { ...p }
      if (r.problem) next[n] = r.problem
      else delete next[n]
      return next
    })
    if (r.problem) return
    const values: Record<string, unknown> = {}
    for (const [k, s] of fields) values[k] = k === n ? r.value : current(k, s)
    const settings = chosenSettings(plugin?.settings, values)
    const { settings: _old, ...rest } = value
    onChange(Object.keys(settings).length ? { ...rest, settings } : rest)
  }

  const exec = plugin ? executionBadge(plugin.details.execution, plugin.details.execution_text) : null
  return (
    <div className="w-full space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="label shrink-0">{t('Pipeline')}</span>
        <select
          className="input !w-72"
          value={value.id}
          aria-label={`${t('Pipeline')} ${name}`}
          onChange={(e) => {
            setRaw({})
            setProblems({})
            onChange({ id: e.target.value })
          }}
        >
          {!plugin && (
            <option value={value.id}>
              {value.id} ({loading ? t('checking…') : t('not available')})
            </option>
          )}
          {(pipelines ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} {p.version}
            </option>
          ))}
        </select>
        {exec && (
          <span className={`text-xs ${exec.tone === 'ok' ? 'text-muted' : 'text-warn'}`} title={exec.title}>
            {t(exec.label)}
          </span>
        )}
        <button
          className="text-xs text-accent hover:underline"
          onClick={() => openMarketplace(plugin ? 'installed' : 'browse')}
        >
          {t('Marketplace')} ↗
        </button>
      </div>
      {!plugin && !loading && (
        <p className="text-xs text-error">
          {t('This pipeline isn’t installed and turned on any more. Choose another, or untick Pipeline.')}
        </p>
      )}
      {plugin?.details.data_warnings.map((w) => (
        <p key={w} className="text-xs text-warn font-medium">
          {w}
        </p>
      ))}
      {plugin && plugin.details.secrets.some((s) => !plugin.secrets_set.includes(s)) && (
        <p className="text-xs text-warn">{t('It needs a key you haven’t added. Add it in Marketplace → Installed.')}</p>
      )}
      {fields.length > 0 && (
        <div className="flex items-start gap-4 flex-wrap">
          {fields.map(([n, spec]) => {
            const v = current(n, spec)
            const label = spec.title ?? n
            return (
              <label key={n} className="flex flex-col gap-1 text-sm" title={spec.description}>
                <span className="text-xs text-muted">{label}</span>
                {spec.type === 'boolean' ? (
                  <input
                    type="checkbox"
                    className="size-4 accent-[#38BDF8]"
                    checked={Boolean(v)}
                    onChange={(e) => set(n, spec, e.target.checked)}
                  />
                ) : spec.type === 'choice' ? (
                  <select className="input !w-48" value={String(v ?? '')} onChange={(e) => set(n, spec, e.target.value)}>
                    {v === undefined && <option value="">{t('Choose…')}</option>}
                    {(spec.options ?? []).map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="input !w-48"
                    inputMode={spec.type === 'string' ? 'text' : 'decimal'}
                    value={raw[n] ?? (v === undefined || v === null ? '' : String(v))}
                    onChange={(e) => set(n, spec, e.target.value)}
                  />
                )}
                {problems[n] && <span className="text-xs text-error">{problems[n]}</span>}
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}

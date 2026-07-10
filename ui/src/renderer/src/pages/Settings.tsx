import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import {
  DEFAULT_APPEARANCE,
  FONTS,
  loadAppearance,
  saveAppearance,
  type Appearance
} from '../lib/appearance'
import type { Settings as SettingsT } from '../lib/types'

function AppearanceCard(): JSX.Element {
  const [appearance, setAppearance] = useState<Appearance>(loadAppearance)

  const update = (patch: Partial<Appearance>): void => {
    const next = { ...appearance, ...patch }
    setAppearance(next)
    saveAppearance(next)
  }

  return (
    <div className="card space-y-4" aria-label="Appearance and accessibility">
      <h3 className="font-semibold">Appearance &amp; accessibility</h3>
      <div>
        <label htmlFor="app-font" className="label">
          Font
        </label>
        <select
          id="app-font"
          className="input mt-1"
          value={appearance.font}
          onChange={(e) => update({ font: e.target.value })}
        >
          {FONTS.map((f) => (
            <option key={f.label} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="app-scale" className="label">
          Text size — {appearance.scale}%
        </label>
        <input
          id="app-scale"
          type="range"
          min={87.5}
          max={150}
          step={12.5}
          value={appearance.scale}
          onChange={(e) => update({ scale: Number(e.target.value) })}
          className="w-full mt-1 accent-[#38BDF8]"
        />
      </div>
      <div className="flex items-end gap-3">
        <div>
          <label htmlFor="app-color" className="label">
            Text colour
          </label>
          <input
            id="app-color"
            type="color"
            value={appearance.textColor}
            onChange={(e) => update({ textColor: e.target.value })}
            className="mt-1 h-10 w-16 bg-raised rounded-lg cursor-pointer border border-raised"
          />
        </div>
        <button className="btn-ghost" onClick={() => update(DEFAULT_APPEARANCE)}>
          Reset to defaults
        </button>
      </div>
      <p className="text-xs text-muted">
        Changes apply instantly and are remembered. Keyboard focus outlines and reduced-motion
        preferences are always honored.
      </p>
    </div>
  )
}

export default function Settings(): JSX.Element {
  const [settings, setSettings] = useState<SettingsT | null>(null)
  const [channel, setChannel] = useState('')
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setSettings(s)
        setChannel(s.channel)
      })
      .catch(() => setSettings(null))
  }, [])

  const save = async (): Promise<void> => {
    try {
      await api.patchSettings({ channel })
      setNotice('Saved. Pipeline-level changes apply on next backend start.')
    } catch (e) {
      setNotice(`Error: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  if (!settings)
    return (
      <div className="p-6 space-y-5 max-w-xl">
        <h2 className="text-2xl font-bold">Settings</h2>
        <AppearanceCard />
        <div className="card text-muted">Connecting to backend…</div>
      </div>
    )

  return (
    <div className="p-6 space-y-5 max-w-xl">
      <h2 className="text-2xl font-bold">Settings</h2>

      <AppearanceCard />

      <div className="card space-y-4">
        <div>
          <label className="label">Your channel (@handle or URL)</label>
          <input
            className="input mt-1"
            value={channel}
            placeholder="@YourHandle"
            onChange={(e) => setChannel(e.target.value)}
          />
        </div>
        <button className="btn-accent" onClick={save}>
          Save settings
        </button>
        {notice && <p className="text-sm text-accent">{notice}</p>}
      </div>

      <div className="card text-sm text-muted">
        The active AI model is managed on the <span className="text-ink">Models</span> page. Advanced
        options (scoring weights, tracking, captions) live in <code>config/settings.yaml</code>.
      </div>
    </div>
  )
}

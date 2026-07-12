import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { BrandingProfile, WatermarkConfig } from '../lib/types'
import WatermarkControls, { DEFAULT_WATERMARK } from './WatermarkControls'

const ENABLED_KEY = 'watermark-enabled'
const PROFILE_KEY = 'watermark-profile-id'

/** Whether branding is on + which profile is active (read by the Generate
 *  bar to attach watermark_profile_id to new jobs). */
export function watermarkSelection(): { enabled: boolean; profileId: number | null } {
  const id = Number(localStorage.getItem(PROFILE_KEY))
  return {
    enabled: localStorage.getItem(ENABLED_KEY) === 'true',
    profileId: id > 0 ? id : null
  }
}

/** Dashboard "Watermark & Branding" section. Create/edit/switch saved
 *  branding profiles; the active one is applied to newly generated clips. */
export default function WatermarkCard(): JSX.Element {
  const [enabled, setEnabled] = useState(localStorage.getItem(ENABLED_KEY) === 'true')
  const [profiles, setProfiles] = useState<BrandingProfile[]>([])
  const [activeId, setActiveId] = useState<number | null>(watermarkSelection().profileId)
  const [name, setName] = useState('')
  const [config, setConfig] = useState<WatermarkConfig>(DEFAULT_WATERMARK)
  const [open, setOpen] = useState(false)
  const [notice, setNotice] = useState('')

  const load = async (selectId?: number): Promise<void> => {
    const list = await api.branding().catch(() => [])
    setProfiles(list)
    const pick = list.find((p) => p.id === (selectId ?? activeId)) ?? list[0]
    if (pick) {
      setActiveId(pick.id)
      localStorage.setItem(PROFILE_KEY, String(pick.id))
      setName(pick.name)
      setConfig(pick.config)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const flash = (m: string): void => {
    setNotice(m)
    setTimeout(() => setNotice(''), 3000)
  }

  const selectProfile = (id: number): void => {
    const p = profiles.find((x) => x.id === id)
    if (!p) return
    setActiveId(id)
    localStorage.setItem(PROFILE_KEY, String(id))
    setName(p.name)
    setConfig(p.config)
  }

  const save = async (): Promise<void> => {
    try {
      if (activeId) {
        await api.updateBranding(activeId, name || 'Branding', config)
        flash('Saved')
        await load(activeId)
      } else {
        const { id } = await api.createBranding(name || 'Branding', config)
        flash('Profile created')
        await load(id)
      }
    } catch (e) {
      flash(String(e))
    }
  }

  const newProfile = (): void => {
    setActiveId(null)
    setName('')
    setConfig(DEFAULT_WATERMARK)
  }

  const remove = async (): Promise<void> => {
    if (!activeId || !window.confirm(`Delete branding profile "${name}"?`)) return
    await api.deleteBranding(activeId)
    setActiveId(null)
    await load()
    if (profiles.length <= 1) newProfile()
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <button
          className="font-semibold flex items-center gap-2"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          <span>🅱 Watermark &amp; Branding</span>
          <span className="text-muted text-xs">{open ? '▾' : '▸'}</span>
        </button>
        <label className="flex items-center gap-2 cursor-pointer text-sm">
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={enabled}
            onChange={(e) => {
              setEnabled(e.target.checked)
              localStorage.setItem(ENABLED_KEY, String(e.target.checked))
            }}
          />
          Apply to new clips
        </label>
      </div>

      {!open && enabled && (
        <p className="text-xs text-muted">
          Branding on — “{profiles.find((p) => p.id === activeId)?.name ?? 'none'}” is applied to
          newly generated clips.
        </p>
      )}

      {open && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="label">Profile</span>
            <select
              className="input !w-48 !py-1 text-sm"
              value={activeId ?? ''}
              onChange={(e) => selectProfile(Number(e.target.value))}
            >
              {profiles.length === 0 && <option value="">(none yet)</option>}
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <button className="btn-ghost !py-1 text-xs" onClick={newProfile}>
              + New
            </button>
            {activeId && (
              <button className="text-xs text-muted hover:text-red-400" onClick={remove}>
                Delete
              </button>
            )}
          </div>

          <input
            className="input !py-1.5 text-sm w-full max-w-xs"
            value={name}
            placeholder="Profile name (e.g. YouTube Channel)"
            onChange={(e) => setName(e.target.value)}
          />

          <WatermarkControls
            config={config}
            onChange={(patch) => setConfig((c) => ({ ...c, ...patch }))}
          />

          <div className="flex items-center gap-3">
            <button className="btn-accent" onClick={save}>
              {activeId ? 'Save profile' : 'Create profile'}
            </button>
            {notice && <span className="text-xs text-accent">{notice}</span>}
          </div>
          <p className="text-[11px] text-muted/70">
            The active profile is burned into every clip you generate while “Apply to new clips” is
            on. You can also set a default branding per creator in the Creators tab.
          </p>
        </div>
      )}
    </div>
  )
}

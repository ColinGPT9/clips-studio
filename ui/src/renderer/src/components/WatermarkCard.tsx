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

export function setWatermarkEnabled(on: boolean): void {
  localStorage.setItem(ENABLED_KEY, String(on))
}

/** Branding-profile management: create/switch/edit/save/delete the saved
 *  profiles. Shown in the Generate bar (below the Watermark toggle), so it
 *  matches the Caption-style and Longform expandable rows. The enable toggle
 *  itself lives in the Generate bar. */
export default function BrandingEditor(): JSX.Element {
  const [profiles, setProfiles] = useState<BrandingProfile[]>([])
  const [activeId, setActiveId] = useState<number | null>(watermarkSelection().profileId)
  const [name, setName] = useState('')
  const [config, setConfig] = useState<WatermarkConfig>(DEFAULT_WATERMARK)
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
    <div className="w-full space-y-3 border-t border-raised/60 pt-3">
      <div className="flex items-center gap-2 flex-wrap">
        <p className="label shrink-0">Branding profile</p>
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
        <input
          className="input !py-1 text-sm !w-52"
          value={name}
          placeholder="Profile name (e.g. YouTube Channel)"
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      <WatermarkControls config={config} onChange={(patch) => setConfig((c) => ({ ...c, ...patch }))} />

      <div className="flex items-center gap-3">
        <button className="btn-accent !py-1.5" onClick={save}>
          {activeId ? 'Save profile' : 'Create profile'}
        </button>
        {notice && <span className="text-xs text-accent">{notice}</span>}
        <span className="text-[11px] text-muted/70 ml-auto">
          Also settable per creator in the Creators tab.
        </span>
      </div>
    </div>
  )
}

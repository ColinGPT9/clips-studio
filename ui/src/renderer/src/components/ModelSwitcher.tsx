import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { InstalledModel } from '../lib/types'

/** Always-visible model selector in the sidebar: switch the active LLM
 *  from anywhere in the app without opening the Models page. */
export default function ModelSwitcher(): JSX.Element {
  const [installed, setInstalled] = useState<InstalledModel[]>([])
  const [active, setActive] = useState('')
  const [switching, setSwitching] = useState(false)

  const refresh = async (): Promise<void> => {
    try {
      const info = await api.models()
      setInstalled(info.installed)
      setActive(info.active.replace(/^ollama\//, ''))
    } catch {
      setInstalled([])
    }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30000)
    return () => clearInterval(id)
  }, [])

  const onChange = async (tag: string): Promise<void> => {
    setSwitching(true)
    try {
      await api.activateModel(tag)
      setActive(tag)
    } catch {
      await refresh()
    } finally {
      setSwitching(false)
    }
  }

  if (installed.length === 0) return <></>

  return (
    <div className="px-3 pb-2">
      <label className="label px-2">AI model</label>
      <select
        className="input mt-1 text-sm"
        value={active}
        disabled={switching}
        onChange={(e) => onChange(e.target.value)}
      >
        {installed.map((m) => (
          <option key={m.name} value={m.name}>
            {m.name} ({m.size_gb.toFixed(1)} GB)
          </option>
        ))}
      </select>
    </div>
  )
}

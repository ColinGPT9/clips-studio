import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { SystemStats as Stats } from '../lib/types'

function gb(bytes: number): string {
  return `${(bytes / 1e9).toFixed(1)} GB`
}

function Widget({ label, value, sub }: { label: string; value: string; sub?: string }): JSX.Element {
  return (
    <div className="card flex-1 min-w-36">
      <p className="label">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
      {sub && <p className="text-xs text-muted mt-0.5">{sub}</p>}
    </div>
  )
}

export default function SystemStats(): JSX.Element {
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    let alive = true
    const poll = async (): Promise<void> => {
      try {
        const s = await api.systemStats()
        if (alive) setStats(s)
      } catch {
        if (alive) setStats(null)
      }
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  if (!stats) return <div className="card text-muted">Connecting to backend…</div>

  return (
    <div className="flex gap-3 flex-wrap">
      <Widget label="CPU" value={`${Math.round(stats.cpu_percent)}%`} />
      <Widget label="RAM" value={`${Math.round(stats.ram_percent)}%`} />
      {stats.gpu ? (
        <Widget
          label="GPU"
          value={`${stats.gpu.gpu_percent}%`}
          sub={`${stats.gpu.name} · ${gb(stats.gpu.vram_used)} / ${gb(stats.gpu.vram_total)} VRAM`}
        />
      ) : (
        <Widget label="GPU" value="—" sub="CPU-only mode" />
      )}
      <Widget label="Storage" value={gb(stats.data_dir_bytes)} sub={`${gb(stats.disk_free_bytes)} free`} />
    </div>
  )
}

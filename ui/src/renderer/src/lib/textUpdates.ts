import { API_BASE } from './api'
import { sessionSecret } from './plugins'

export interface ClipUpdatesStatus {
  enabled: boolean
  away_from_desk: boolean
  show_dmca: boolean
  events: string[]
  model: string
  model_status: string
  channel_id: string
  dm: boolean
  test_ok: boolean
  owner_connected: boolean
  has_token: boolean
  connected: boolean
  typed_enabled: boolean
  invite_url: string
  last_error: string
}
export interface PendingApproval {
  item_id: number
  video_id: string
  title: string
  line: string
  platforms: string[]
  first_slot: string
  thumbnail_url: string
  answer: string
  answered_where: string
  state_text?: string
  show_dmca: boolean
}

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (method !== 'GET') {
    const secret = await sessionSecret()
    if (!secret) throw new Error('Clip updates settings need the desktop app.')
    headers['X-Clips-Kitty-Session'] = secret
  }
  const response = await fetch(`${API_BASE}/text-updates${path}`, {
    method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) })
  })
  if (!response.ok) {
    const value = await response.json().catch(() => ({}))
    throw new Error(typeof value.detail === 'string' ? value.detail : 'Clip updates could not be refreshed.')
  }
  return response.json() as Promise<T>
}

export const textUpdates = {
  status: () => call<ClipUpdatesStatus>('/status'),
  pending: () => call<PendingApproval[]>('/pending'),
  settings: (patch: Partial<ClipUpdatesStatus>) => call<ClipUpdatesStatus>('/settings', 'PATCH', patch),
  bot: (token: string) => call<ClipUpdatesStatus>('/discord-bot', 'PUT', { token }),
  channels: () => call<{ id: string; name: string; guild: string }[]>('/channels'),
  test: () => call<ClipUpdatesStatus & { ok: boolean }>('/test', 'POST'),
  disconnect: () => call<ClipUpdatesStatus>('/credentials', 'DELETE')
}

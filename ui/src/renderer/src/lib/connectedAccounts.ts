import { API_BASE } from './api'

/** Account metadata only. OAuth credentials remain in the trusted engine. */
export interface ConnectedAccount {
  id: string
  provider: string
  user_id: string
  login: string
  display_name: string
  scopes: string[]
  status: string
  error?: string
}

export interface AccountProvider {
  id: string
  name: string
  configured: boolean
  client_id: string
  storage: string
  storage_available: boolean
}

export interface ConnectedAccountsStatus {
  providers: AccountProvider[]
  accounts: ConnectedAccount[]
}

export interface AccountFlow {
  flow_id: string
  status: 'pending'
  user_code: string
  verification_uri: string
  expires_in: number
  interval: number
}

export interface AccountPoll {
  status: 'pending' | 'connected' | 'expired' | 'denied' | 'cancelled' | 'error'
  interval?: number
  account?: ConnectedAccount
  error?: string
}

export interface RecordedNominationStream {
  stream_id: string
  broadcaster_id: string
  stream_start: string | null
  ended_at: string | null
  nomination_count: number
}

export interface ViewerNominationStatus {
  monitoring: boolean
  streams: RecordedNominationStream[]
}

export const ACCOUNTS_CHANGED = 'connected-accounts-changed'

async function authenticatedRequest<T>(
  prefix: '/connected-accounts' | '/stream-workflows',
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const session = await window.studio?.pluginSession?.()
  if (!session) throw new Error('Managing connected accounts requires the Clips Kitty desktop app.')
  let response: Response
  try {
    response = await fetch(`${API_BASE}${prefix}${path}`, {
      ...init,
      redirect: 'error',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        'X-Clips-Kitty-Session': session
      }
    })
  } catch {
    throw new Error('The account operation failed. Check that Clips Kitty is running and try again.')
  }
  if (!response.ok) {
    // Only the API's deliberately sanitized detail is shown. Never include a
    // whole response body or a request URL in an OAuth error message.
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    const safeMessage = typeof message === 'string' &&
      !/(?:https?:\/\/|bearer\s+|(?:access_token|refresh_token|client_secret|password|authorization)\s*[:=])/i.test(message)
      ? message.replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 300)
      : 'The account operation failed.'
    throw new Error(safeMessage)
  }
  if (response.status === 204) return undefined as T
  try {
    return await response.json() as T
  } catch {
    throw new Error('The account operation returned an unreadable response. Try again.')
  }
}

function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return authenticatedRequest('/connected-accounts', path, init)
}

function changed<T>(value: T): T {
  window.dispatchEvent(new Event(ACCOUNTS_CHANGED))
  return value
}

const accountPath = (id: string): string => `/${encodeURIComponent(id)}`

export const connectedAccounts = {
  status: () => request<ConnectedAccountsStatus>(''),
  configureTwitch: (clientId: string) =>
    request<unknown>('/twitch/config', { method: 'PUT', body: JSON.stringify({ client_id: clientId }) }).then(changed),
  connectTwitch: (scopes: string[]) =>
    request<AccountFlow>('/twitch/connect', { method: 'POST', body: JSON.stringify({ scopes }) }),
  poll: (flowId: string) =>
    request<AccountPoll>(`/flows/${encodeURIComponent(flowId)}/poll`, { method: 'POST' }),
  cancel: (flowId: string) =>
    request<unknown>(`/flows/${encodeURIComponent(flowId)}`, { method: 'DELETE' }),
  test: (id: string) => request<unknown>(`${accountPath(id)}/test`, { method: 'POST' }).then(changed),
  disconnect: (id: string) => request<unknown>(accountPath(id), { method: 'DELETE' }).then(changed),
  watch: (id: string) => request<unknown>(`${accountPath(id)}/watch`, { method: 'POST' })
}

/** Explicit desktop controls; loading status never starts a chat listener. */
export const streamWorkflows = {
  streams: (id: string) =>
    authenticatedRequest<ViewerNominationStatus>('/stream-workflows', `${accountPath(id)}/streams`),
  startMonitor: (id: string) =>
    authenticatedRequest<{ monitoring: boolean }>('/stream-workflows', `${accountPath(id)}/monitor`, { method: 'POST' }),
  stopMonitor: (id: string) =>
    authenticatedRequest<{ monitoring: boolean }>('/stream-workflows', `${accountPath(id)}/monitor`, { method: 'DELETE' })
}

/** Twitch's device grant opens its official activation page in the system browser. */
export function twitchActivationUrl(value: string): string | null {
  try {
    const url = new URL(value)
    if (
      url.protocol !== 'https:' || url.hostname !== 'www.twitch.tv' || url.port ||
      url.username || url.password || url.hash || !/^\/activate\/?$/.test(url.pathname)
    ) return null
    return url.href
  } catch {
    return null
  }
}

export function accountPollDelay(seconds: number | undefined): number {
  return Math.max(1, Number.isFinite(seconds) ? seconds! : 5) * 1000
}

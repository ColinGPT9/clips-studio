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

/** A refusal is an HTTP error (AccountRequestError), never a poll status. */
export interface AccountPoll {
  status: 'pending' | 'connected'
  interval?: number
  account?: ConnectedAccount
}

export interface DisconnectResult {
  disconnected: boolean
  /** False when Twitch did not confirm it removed the authorization. */
  revoked: boolean
}

export interface RecordedNominationStream {
  stream_id: string
  broadcaster_id: string
  stream_start: string | null
  ended_at: string | null
  nomination_count: number
  commands?: Record<string, number>
  timeline?: { offset_seconds: number; count: number; commands: Record<string, number> }[]
  timeline_truncated?: boolean
  bucket_seconds?: number
  mapping?: { vod_id: string; offset_seconds: number; source_id: string | null } | null
  recording?: { status: 'pending' | 'ready' | 'expired'; vod_id: string | null; attempts: number; next_check: number } | null
}

export interface RecordingMapping {
  broadcaster_id: string
  vod_id: string
  stream_id: string
  stream_start: string
  offset_seconds: number
  verified: true
}

export interface ViewerNominationStatus {
  monitoring: boolean
  broadcaster_id?: string | null
  broadcaster_login?: string | null
  streams: RecordedNominationStream[]
}

export interface TwitchStreamContext {
  broadcaster: { id: string; login: string; display_name: string }
  stream: { id: string; title: string; game_name: string; started_at: string } | null
}

export interface TwitchEventBatch {
  status: string
  broadcaster_id?: string
  cursor: number
  truncated?: boolean
  events: { sequence: number; type: string; broadcaster_id: string }[]
}

export const ACCOUNTS_CHANGED = 'connected-accounts-changed'

/** The engine's HTTP status (0 when it could not be reached) and its fixed
 * error code, so a caller can tell a passing failure from a final answer. */
export class AccountRequestError extends Error {
  status: number
  code: string
  constructor(message: string, status = 0, code = '') {
    super(message)
    this.name = 'AccountRequestError'
    this.status = status
    this.code = code
  }
}

/** Worth asking again: Twitch or the engine did not answer, or asked to slow down. */
export function isTransientAccountError(error: unknown): boolean {
  if (!(error instanceof AccountRequestError)) return false
  if (error.status === 0) return error.code === 'unreachable'
  return error.status === 429 || error.status === 502 || error.status === 503 || error.status === 504
}

async function authenticatedRequest<T>(
  prefix: '/connected-accounts' | '/stream-workflows',
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const session = await window.studio?.pluginSession?.()
  if (!session) {
    throw new AccountRequestError('Managing connected accounts requires the Clips Kitty desktop app.', 0, 'no_session')
  }
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
    throw new AccountRequestError(
      'The account operation failed. Check that Clips Kitty is running and try again.', 0, 'unreachable')
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
    const code = typeof detail?.code === 'string' && /^[a-z_]{1,40}$/.test(detail.code) ? detail.code : ''
    throw new AccountRequestError(safeMessage, response.status, code)
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

/** A failed test can still change the account (to Reconnect required), so
 * the cards re-read the list whichever way it went. */
function settled<T>(pending: Promise<T>): Promise<T> {
  return pending.finally(() => window.dispatchEvent(new Event(ACCOUNTS_CHANGED)))
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
  test: (id: string) => settled(request<unknown>(`${accountPath(id)}/test`, { method: 'POST' })),
  disconnect: (id: string) => request<DisconnectResult>(accountPath(id), { method: 'DELETE' }).then(changed),
  watch: (id: string) => settled(request<unknown>(`${accountPath(id)}/watch`, { method: 'POST' })),
  events: (id: string, after = 0) =>
    request<TwitchEventBatch>(`${accountPath(id)}/events?after=${after}&limit=100`),
  streamContext: (id: string, login: string) =>
    request<TwitchStreamContext>(`${accountPath(id)}/twitch`, {
      method: 'POST', body: JSON.stringify({ operation: 'stream_context', params: { broadcaster_login: login } })
    })
}

/** Explicit desktop controls; loading status never starts a chat listener. */
export const streamWorkflows = {
  streams: (id: string) =>
    authenticatedRequest<ViewerNominationStatus>('/stream-workflows', `${accountPath(id)}/streams`),
  startMonitor: (id: string, broadcasterId?: string) =>
    authenticatedRequest<{ monitoring: boolean }>('/stream-workflows', `${accountPath(id)}/monitor`, {
      method: 'POST', ...(broadcasterId ? { body: JSON.stringify({ broadcaster_id: broadcasterId }) } : {})
    }),
  stopMonitor: (id: string) =>
    authenticatedRequest<{ monitoring: boolean }>('/stream-workflows', `${accountPath(id)}/monitor`, { method: 'DELETE' }),
  bindRecording: (id: string, mapping: RecordingMapping) =>
    authenticatedRequest<unknown>('/stream-workflows', `${accountPath(id)}/vod-mapping`, {
      method: 'PUT', body: JSON.stringify(mapping)
    }),
  retryRecording: (id: string, streamId: string) =>
    authenticatedRequest<{ status: 'pending' }>('/stream-workflows',
      `${accountPath(id)}/recordings/${encodeURIComponent(streamId)}/retry`, { method: 'POST' })
}

/** Parse a channel name locally; pasted URLs are never opened or fetched. */
export function twitchChannelLogin(value: string): string | null {
  let input = value.trim()
  if (input.includes('://')) {
    try {
      const url = new URL(input)
      if (url.protocol !== 'https:' || !['twitch.tv', 'www.twitch.tv'].includes(url.hostname) ||
        url.username || url.password || url.port || url.search || url.hash) return null
      input = url.pathname.replace(/^\//, '').replace(/\/$/, '')
    } catch { return null }
  } else input = input.replace(/^@/, '')
  return /^[A-Za-z0-9_]{1,25}$/.test(input) ? input.toLowerCase() : null
}

/** Read a recording ID without opening an arbitrary pasted address. */
export function twitchVodId(value: string): string | null {
  const input = value.trim()
  if (/^[0-9]{1,30}$/.test(input)) return input
  try {
    const url = new URL(input)
    if (url.protocol !== 'https:' || !['twitch.tv', 'www.twitch.tv'].includes(url.hostname) ||
      url.username || url.password || url.port || url.hash) return null
    return /^\/videos\/([0-9]{1,30})\/?$/.exec(url.pathname)?.[1] ?? null
  } catch {
    return null
  }
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

import { API_BASE } from './api'
import { sessionSecret } from './plugins'

/** Clipping campaigns: the engine's /discord routes (server/discord_api.py)
 *  for now; the /campaigns routes join them later. Routes that change
 *  something need the desktop app's session secret (the plugin routes' one),
 *  so in a plain browser these screens can read but not change. A webhook
 *  URL only ever goes in a request body, never in a query string, which the
 *  engine's access log would print. Discord ids stay strings: they overflow
 *  JavaScript numbers. */

export interface DiscordUser {
  id: string
  username: string
  global_name: string
}

export type DestinationState = 'connected' | 'gone' | 'denied'

/** A webhook Clips Kitty may post to. Never its token: `has_token` says
 *  whether one is stored on this PC. */
export interface Destination {
  id: number
  label: string
  source: 'pasted' | 'oauth'
  guild_id: string
  guild_name: string
  channel_id: string
  webhook_name: string
  is_forum: boolean
  thread_id: string
  state: DestinationState
  last_error: string
  has_token: boolean
  /** How many campaigns post to it. */
  campaigns: number
}

export interface DiscordStatus {
  /** Whether this version can sign in to Discord at all (an application id). */
  configured: boolean
  client_id: string
  client_id_source: 'override' | 'shipped' | 'none'
  connected: boolean
  expired: boolean
  user: DiscordUser
  scopes: string[]
  connected_at: string
  /** Where Discord sends the browser back; it works only on port 8765. */
  redirect_uri: string
  destinations: Destination[]
  storage: string
}

export interface DiscordGuild {
  id: string
  name: string
  owner: boolean
  can_manage_webhooks: boolean
}

export interface ParsedLink {
  kind: 'channel' | 'message' | 'guild' | 'webhook' | 'invite' | 'none'
  guild_id: string
  channel_id: string
  message_id: string
  problem: string
}

export interface DestinationTest {
  ok: boolean
  message_url: string
  error: string
}

export interface DestinationPatch {
  label?: string
  is_forum?: boolean
  thread_id?: string
  webhook_url?: string
}

/** The engine's own words when it refuses (FastAPI's `detail`). */
async function failure(res: Response): Promise<Error> {
  const text = await res.text().catch(() => '')
  try {
    const detail = JSON.parse(text).detail
    if (typeof detail === 'string') return new Error(detail)
  } catch {
    // not JSON: fall through to the raw text
  }
  return new Error(text.slice(0, 300) || `The engine answered ${res.status}`)
}

async function call<T>(path: string, init: RequestInit = {}, guarded = false): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (guarded) {
    const s = await sessionSecret()
    if (!s) throw new Error('Changing Discord settings needs the Clips Kitty desktop app.')
    headers['X-Clips-Kitty-Session'] = s
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) throw await failure(res)
  return res.json() as Promise<T>
}

const send = (method: string, body?: unknown): RequestInit =>
  body === undefined ? { method } : { method, body: JSON.stringify(body) }

export const discord = {
  status: () => call<DiscordStatus>('/discord/status'),
  /** The Advanced application id override; '' goes back to Clips Kitty's own. */
  setClientId: (clientId: string) =>
    call<DiscordStatus>('/discord/settings', send('PATCH', { client_id: clientId }), true),
  /** The sign-in page to open in the user's browser. */
  connect: () => call<{ url: string }>('/discord/connect', send('POST'), true),
  disconnect: () =>
    call<DiscordStatus & { revoked: boolean }>('/discord/disconnect', send('POST'), true),
  guilds: (refresh = false) =>
    call<{ guilds: DiscordGuild[]; fetched_at: number }>(
      `/discord/guilds${refresh ? '?refresh=true' : ''}`
    ),
  /** What a pasted Discord link points at. In the body: it may be a webhook's. */
  parseLink: (link: string) => call<ParsedLink>('/discord/parse-link', send('POST', { link })),
  destinations: () => call<{ destinations: Destination[] }>('/discord/destinations'),
  sendPublished: (clipId: number, body: { destination_id: number; platform: string; provider: string }) =>
    call<{ submission_id: number; status: string; already_sent: boolean; check_discord: boolean }>(
      `/discord/clips/${clipId}/send`, send('POST', body), true
    ),
  addDestination: (d: {
    webhook_url: string
    label?: string
    is_forum?: boolean
    thread_id?: string
  }) => call<{ destination: Destination }>('/discord/destinations', send('POST', d), true),
  /** Create with Discord: a server admin picks the channel on Discord's page. */
  authorizeDestination: (label = '') =>
    call<{ url: string }>('/discord/destinations/authorize', send('POST', { label }), true),
  updateDestination: (id: number, patch: DestinationPatch) =>
    call<{ destination: Destination }>(`/discord/destinations/${id}`, send('PATCH', patch), true),
  removeDestination: (id: number) =>
    call<{ removed: boolean; campaigns_affected: number }>(
      `/discord/destinations/${id}`,
      send('DELETE'),
      true
    ),
  /** Posts "Clips Kitty test message. You can delete it." (confirm first). */
  testDestination: (id: number) =>
    call<DestinationTest>(`/discord/destinations/${id}/test`, send('POST'), true)
}

// ---- pure helpers (Node-tested) --------------------------------------------------------

export type DiscordCardState = 'not_set_up' | 'not_connected' | 'connected' | 'expired'

/** Which of the card's four states the status is in. */
export function discordCardState(
  status: Pick<DiscordStatus, 'configured' | 'connected' | 'expired'>
): DiscordCardState {
  if (status.connected) return 'connected'
  if (!status.configured) return 'not_set_up'
  return status.expired ? 'expired' : 'not_connected'
}

/** The name to show for a Discord user: their display name, else their username. */
export function discordName(user: DiscordUser): string {
  return user.global_name || user.username || ''
}

/** Up to two letters for the avatar circle (the window may load nothing from Discord). */
export function initials(name: string): string {
  const words = name.trim().split(/[\s._-]+/).filter(Boolean)
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? '').slice(0, 2)
  return letters.toUpperCase() || '?'
}

/** What the card's Edit form holds for a destination. `webhook_url` starts
 *  empty: the stored link is never sent to the window. */
export interface DestinationDraft {
  label: string
  is_forum: boolean
  thread_id: string
  webhook_url: string
}

export function destinationDraft(d: Destination): DestinationDraft {
  return { label: d.label, is_forum: d.is_forum, thread_id: d.thread_id, webhook_url: '' }
}

/** The PATCH an edit makes: only the fields that changed, and a new webhook
 *  link only when one was pasted (the same link again re-stores it). */
export function destinationPatch(d: Destination, draft: DestinationDraft): DestinationPatch {
  const patch: DestinationPatch = {}
  const label = draft.label.trim()
  if (label !== d.label) patch.label = label
  if (draft.is_forum !== d.is_forum) patch.is_forum = draft.is_forum
  const thread = draft.thread_id.trim()
  if (thread !== d.thread_id) patch.thread_id = thread
  const url = draft.webhook_url.trim()
  if (url) patch.webhook_url = url
  return patch
}

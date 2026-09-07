/** YouTube publishing: types, the on/off gate, and timezone handling.
 *
 *  The gate matters more than it looks. Publishing is bring-your-own-key and
 *  most people will never turn it on, so an install that has not enabled it
 *  must show no YouTube anything — not a greyed-out tab, not an upsell. The
 *  localStorage mirror exists so the very first paint of the editor already
 *  knows the answer; asking the API first would flash a tab in and out.
 */

export interface YouTubeChannel {
  id: string
  title: string
  handle: string
}

export interface YouTubeSettings {
  enabled: boolean
  privacy: string
  category_id: string
  made_for_kids: boolean
  playlists_enabled: boolean
  notify_subscribers: boolean
  region: string
}

/** One connected channel. A creator running a main channel and a clips channel
 *  has two of these; each is a separate Google consent with its own token. */
export interface YouTubeAccount {
  id: string
  title: string
  handle: string
  scopes?: string[]
  default?: boolean
}

export interface YouTubeStatus {
  enabled: boolean
  backend?: string
  has_client?: boolean
  connected?: boolean
  scopes?: string[]
  playlists_available?: boolean
  accounts?: YouTubeAccount[]
  channel?: YouTubeChannel | null
  settings?: YouTubeSettings
  quota?: {
    uploads_used: number
    uploads_limit: number
    remaining: number
    resets_at: string
  }
}

export interface PublishRecord {
  clip_id: number
  youtube_id: string
  uploaded_at: string
  title: string
  privacy: string
  actual_privacy: string
  publish_at: string
  channel_title: string
  thumbnail_set: number
  playlist_id: string
  state: string
  error: string
}

export interface PublishJobRow {
  id: number
  clip_id: number
  status: string
  error: string
  youtube_id: string
  after_job_id: number
}

export interface Playlist {
  id: string
  title: string
  privacy: string
  count: number
}

export interface VideoCategory {
  id: string
  title: string
}

const GATE_KEY = 'clips-kitty-youtube-enabled'

/** Synchronous best guess, for the first render. Defaults to off. */
export function youtubeEnabledSync(): boolean {
  try {
    return localStorage.getItem(GATE_KEY) === '1'
  } catch {
    // A blocked or full localStorage must never stop the editor loading.
    return false
  }
}

export function rememberYoutubeEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(GATE_KEY, enabled ? '1' : '0')
  } catch {
    /* nothing to do — the API is still the authority */
  }
}

/** YouTube's own limits, mirrored so the counters can warn before the API does. */
export const TITLE_MAX = 100
export const DESCRIPTION_MAX = 5000
export const TAGS_BUDGET = 500

/** Total characters tags cost. A tag with a space is quoted, costing two more. */
export function tagsCost(tags: string[]): number {
  return tags.reduce(
    (total, tag, i) => total + (i ? 1 : 0) + tag.length + (tag.includes(' ') ? 2 : 0),
    0
  )
}

export function parseTags(raw: string): string[] {
  return raw
    .split(',')
    .map((t) => t.trim().replace(/^#/, ''))
    .filter(Boolean)
}

// ---- timezones -------------------------------------------------------------
//
// All of this happens here rather than in Python on purpose: the browser has a
// full timezone database and knows that 1:30am happens twice on a fall-back
// Sunday. Python on Windows has no tz database at all, so the backend only ever
// sees an instant that already carries an offset.

export function localTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** A `datetime-local` value is local wall-clock. `new Date()` parses it in the
 *  browser's zone, so the offset in force ON THAT DATE is applied — which is
 *  what makes this daylight-saving correct without any rules of our own. */
export function localInputToUtc(value: string): string | null {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed.toISOString().replace(/\.\d{3}Z$/, 'Z')
}

/** "Wednesday, 10 September 2026 at 19:00 EDT" */
export function describeInstant(iso: string): string {
  if (!iso) return ''
  const moment = new Date(iso)
  if (Number.isNaN(moment.getTime())) return iso
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'full',
      timeStyle: 'short',
      timeZoneName: 'short'
    }).format(moment)
  } catch {
    return moment.toISOString()
  }
}

/** The soonest time YouTube will accept, as a `datetime-local` value. */
export function earliestSchedule(leadMinutes = 15): string {
  const moment = new Date(Date.now() + leadMinutes * 60_000)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}` +
    `T${pad(moment.getHours())}:${pad(moment.getMinutes())}`
  )
}

export function studioUrl(videoId: string): string {
  return `https://studio.youtube.com/video/${videoId}/edit`
}

export function watchUrl(videoId: string): string {
  return `https://www.youtube.com/watch?v=${videoId}`
}

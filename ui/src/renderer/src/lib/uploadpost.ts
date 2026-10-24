/** Types and helpers for publishing to several platforms through Upload-Post.
 *
 * Kept apart from lib/youtube.ts on purpose: the two providers share no
 * settings and no state, and the direct-YouTube path has to keep working
 * exactly as it does whether or not this one is ever switched on.
 */

export type UploadPostStatus = {
  enabled: boolean
  has_key: boolean
  /** Last four characters, so the card can say WHICH key is stored. The key
   *  itself is never sent to the renderer. */
  key_tail: string
  profile: string
  platforms: string[]
  common_description: string
  first_comment: string
  /** Empty until an approved referral URL is configured. While it is empty no
   *  affiliate claim is shown anywhere — just a plain signup link. */
  affiliate_url: string
  storage: string
}

/** sending | queued | processing | published | failed | skipped. 'sending' is
 *  written before the request goes out, by background publishing only.
 *  scheduled | checking | private come only from a direct YouTube upload
 *  (GET /clips/{id}/posts): not live yet, past its time but not yet read back
 *  as public, and still not public after the last check. */
export type PublishState =
  | 'sending'
  | 'queued'
  | 'processing'
  | 'published'
  | 'failed'
  | 'skipped'
  | 'scheduled'
  | 'checking'
  | 'private'

export type PlatformRow = {
  clip_id: number
  platform: string
  /** As the engine writes it: 'upload_post', 'woopsocial', or 'youtube' for a
   *  direct upload. rowProvider() turns it into the panel's name. */
  provider: string
  state: PublishState
  post_id: string
  post_url: string
  error: string
  request_id: string
  /** When the post is due (UTC), '' for straight away. */
  scheduled_for: string
  /** Published with a link to open. Only on GET /clips/{id}/posts rows. */
  live?: boolean
  created_at: string
  updated_at: string
}

export type FanOut = {
  clip_id: number
  request_id: string
  platforms: PlatformRow[]
  /** True once nothing is queued or processing, so polling can stop. */
  done: boolean
}

/** What Upload-Post documents for video, in the order the picker shows them.
 *
 * Mirrors VIDEO_PLATFORMS in publish/uploadpost.py. Reddit is deliberately
 * absent: it is documented but currently answers 503, and offering a
 * destination that cannot work is worse than not offering it.
 */
export const PLATFORMS: { id: string; label: string; note?: string }[] = [
  { id: 'youtube', label: 'YouTube' },
  { id: 'tiktok', label: 'TikTok', note: 'Not available on Upload-Post’s free plan' },
  { id: 'instagram', label: 'Instagram' },
  { id: 'facebook', label: 'Facebook' },
  { id: 'x', label: 'X' },
  { id: 'threads', label: 'Threads' },
  { id: 'linkedin', label: 'LinkedIn' },
  { id: 'pinterest', label: 'Pinterest' },
  { id: 'bluesky', label: 'Bluesky' }
]

export function platformLabel(id: string): string {
  return PLATFORMS.find((p) => p.id === id)?.label ?? id
}

/** What one platform accepts, as reported by /uploadpost/capabilities.
 *
 *  Served from the same table the publish path uses, so a control can never
 *  be offered for a field that would be dropped on the way out.
 */
export type Capability = {
  description: boolean
  first_comment: boolean
  ai_disclosure: boolean
  thumbnail: boolean
  /** field name -> human label. Facebook needs a page id, Pinterest a board
   *  id, and without them that platform fails after the upload. */
  requires: Record<string, string>
  extra: string[]
}

export type Capabilities = Record<string, Capability>

/** Per-platform overrides, keyed the way the publish route expects. */
export type Overrides = Record<string, Record<string, string>>

/** The browser's own zone, which is the one the user means by "7pm". */
export function localZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** A `datetime-local` value is wall-clock with no zone. Paired with the IANA
 *  zone name it is unambiguous; sent alone, Upload-Post reads it as UTC. */
export function localInputToIso(value: string): string {
  if (!value) return ''
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '' : d.toISOString()
}

/** Which required fields are still blank across the chosen platforms. */
export function missingRequired(
  platforms: string[],
  caps: Capabilities | null,
  overrides: Overrides
): string[] {
  if (!caps) return []
  const wanted: string[] = []
  for (const platform of platforms) {
    const needs = caps[platform]?.requires ?? {}
    for (const [field, label] of Object.entries(needs)) {
      if (!(overrides[platform]?.[field] ?? '').trim()) wanted.push(label)
    }
  }
  return [...new Set(wanted)]
}

/** How long to wait before polling again.
 *
 * Upload-Post's own guidance scales with file size; this follows the shape of
 * it without pretending to know the size — quick at first while short clips
 * finish, then backing off so a long encode is not hammered.
 */
export function nextPollDelay(attempt: number): number {
  if (attempt < 5) return 5000
  if (attempt < 15) return 10000
  return 15000
}

/** Stop polling rather than spinning forever on a stuck upload. */
export const MAX_POLLS = 80

export function describeState(row: PlatformRow): string {
  switch (row.state) {
    case 'published':
      return 'Published'
    case 'failed':
      return row.error || 'Failed'
    case 'skipped':
      return row.error || 'Not connected'
    case 'processing':
      return 'Processing'
    case 'sending':
      return 'Sending'
    case 'scheduled':
      return 'Scheduled'
    case 'checking':
      return 'Checking it went live'
    case 'private':
      return row.error || 'Private'
    default:
      return 'Queued'
  }
}

/** Which service delivers a publish. Both are bring-your-own-key and both
 *  reach the same platforms; a creator uses whichever they have an account
 *  with. Upload-Post covers more destinations; WoopSocial's free tier
 *  connects two accounts, with about five YouTube posts a day. */
export type Provider = 'uploadpost' | 'woopsocial'

export type ProviderStatus = {
  enabled: boolean
  has_key: boolean
  key_tail: string
  platforms: string[]
  common_description: string
  affiliate_url: string
  storage: string
}

/** WoopSocial reaches eight of the nine; it has no Bluesky. */
export const WOOPSOCIAL_PLATFORMS = [
  'youtube',
  'tiktok',
  'instagram',
  'facebook',
  'x',
  'threads',
  'linkedin',
  'pinterest'
]

export function platformsFor(provider: Provider): typeof PLATFORMS {
  return provider === 'woopsocial'
    ? PLATFORMS.filter((p) => WOOPSOCIAL_PLATFORMS.includes(p.id))
    : PLATFORMS
}

export const PROVIDER_LABEL: Record<Provider, string> = {
  uploadpost: 'Upload-Post',
  woopsocial: 'WoopSocial'
}

/** Which way a row's post went, in the panel's names: the engine writes
 *  'upload_post' where the panel says 'uploadpost'. 'youtube' is a direct
 *  upload; '' a row that does not say. */
export function rowProvider(row: Pick<PlatformRow, 'provider'>): Provider | 'youtube' | '' {
  switch (row.provider) {
    case 'upload_post':
      return 'uploadpost'
    case 'woopsocial':
      return 'woopsocial'
    case 'youtube':
      return 'youtube'
    default:
      return ''
  }
}

/** Rows a publish, refresh or retry answered with, put in what the panel
 *  shows. Those answers carry only this clip's provider rows, so the direct
 *  YouTube upload (another table) and posts found by the clip's window stay.
 *  A provider row is one per clip and platform, so a fresh one replaces the
 *  shown row of its clip and platform whichever provider that was, and the
 *  same provider's row for that platform. */
export function mergeRows(shown: PlatformRow[], fresh: PlatformRow[]): PlatformRow[] {
  const stale = (r: PlatformRow): boolean =>
    r.provider !== 'youtube' &&
    fresh.some((f) => f.platform === r.platform && (f.provider === r.provider || f.clip_id === r.clip_id))
  return [...fresh, ...shown.filter((r) => !stale(r))].sort(
    (a, b) => a.platform.localeCompare(b.platform) || a.provider.localeCompare(b.provider)
  )
}

/** A post still on its way and already due, so worth asking about now. One
 *  scheduled ahead has nothing to tell yet. */
export function stillOnItsWay(row: PlatformRow, now: number = Date.now()): boolean {
  if (row.state !== 'queued' && row.state !== 'processing') return false
  const due = row.scheduled_for ? Date.parse(row.scheduled_for) : NaN
  return Number.isNaN(due) || due <= now
}

/** Whether a row keeps the Publish button locked, so the clip is not sent
 *  twice while a send is under way: one going out right now, one of a request
 *  this panel sent (`sentHere`) still on its way, or one already due. A post
 *  found on reopening that is scheduled days ahead is listed but does not
 *  lock: nothing is going out yet, and the button would read "Publishing…"
 *  until its day. */
export function holdsPublish(
  row: PlatformRow,
  sentHere: ReadonlySet<string>,
  now: number = Date.now()
): boolean {
  if (row.state === 'sending') return true
  if (row.state !== 'queued' && row.state !== 'processing') return false
  return (row.request_id !== '' && sentHere.has(row.request_id)) || stillOnItsWay(row, now)
}

/** The request of this panel's provider to pick back up on reopening: one
 *  still on its way, else one with a platform that failed (what "Retry the
 *  ones that failed" re-sends), else any. */
export function pickRequest(
  rows: PlatformRow[],
  provider: Provider,
  now: number = Date.now()
): PlatformRow | undefined {
  const mine = rows.filter((r) => r.request_id && rowProvider(r) === provider)
  return (
    mine.find((r) => stillOnItsWay(r, now)) ?? mine.find((r) => r.state === 'failed') ?? mine[0]
  )
}

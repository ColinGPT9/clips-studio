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
 *  written before the request goes out, by background publishing only. */
export type PublishState =
  | 'sending'
  | 'queued'
  | 'processing'
  | 'published'
  | 'failed'
  | 'skipped'

export type PlatformRow = {
  clip_id: number
  platform: string
  provider: string
  state: PublishState
  post_id: string
  post_url: string
  error: string
  request_id: string
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

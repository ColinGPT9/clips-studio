import { platformLabel } from '../../lib/uploadpost'
import { t } from '../../lib/i18n'

/** Per-platform values, as WoopSocial's post body takes them (see build_post
 *  in publish/woopsocial.py). Strings and booleans, keyed by platform. */
export type PlatformOverrides = Record<string, Record<string, string | boolean>>

type Choice = { value: string; label: string }

const YOUTUBE_PRIVACY: Choice[] = [
  { value: 'public', label: 'Public' },
  { value: 'unlisted', label: 'Unlisted' },
  { value: 'private', label: 'Private' }
]

// WoopSocial's own enums, checked against their create-post schema. Only the
// kinds a vertical video can be are offered.
const TIKTOK_PRIVACY: Choice[] = [
  { value: 'PUBLIC_TO_EVERYONE', label: 'Everyone' },
  { value: 'FOLLOWER_OF_CREATOR', label: 'Followers' },
  { value: 'MUTUAL_FOLLOW_FRIENDS', label: 'Friends' },
  { value: 'SELF_ONLY', label: 'Only me' }
]
const INSTAGRAM_TYPE: Choice[] = [
  { value: 'REEL', label: 'Reel' },
  { value: 'STORY', label: 'Story' }
]
const FACEBOOK_TYPE: Choice[] = [
  { value: 'REEL', label: 'Reel' },
  { value: 'VIDEO', label: 'Video' },
  { value: 'STORY', label: 'Story' }
]

/** The settings each chosen platform takes, set once for every post from a
 *  watched channel. Only the platforms ticked are shown, and every field
 *  starts at what WoopSocial would use anyway, so leaving this alone changes
 *  nothing. */
export default function WatchPlatformOptions({
  platforms,
  value,
  onChange
}: {
  platforms: string[]
  value: PlatformOverrides
  onChange: (next: PlatformOverrides) => void
}): JSX.Element | null {
  const set = (platform: string, key: string, v: string | boolean): void =>
    onChange({ ...value, [platform]: { ...(value[platform] ?? {}), [key]: v } })
  const str = (platform: string, key: string, fallback: string): string => {
    const v = value[platform]?.[key]
    return typeof v === 'string' && v ? v : fallback
  }
  const flag = (platform: string, key: string, fallback: boolean): boolean => {
    const v = value[platform]?.[key]
    return typeof v === 'boolean' ? v : fallback
  }

  const select = (platform: string, key: string, choices: Choice[], label: string): JSX.Element => (
    <label className="text-sm flex items-center gap-2">
      <span className="text-muted">{t(label)}</span>
      <select
        className="input !w-40 !py-1"
        value={str(platform, key, choices[0].value)}
        onChange={(e) => set(platform, key, e.target.value)}
      >
        {choices.map((c) => (
          <option key={c.value} value={c.value}>
            {t(c.label)}
          </option>
        ))}
      </select>
    </label>
  )
  const tick = (platform: string, key: string, fallback: boolean, label: string): JSX.Element => (
    <label className="text-sm flex items-center gap-2 cursor-pointer">
      <input
        type="checkbox"
        className="size-4 accent-[#38BDF8]"
        checked={flag(platform, key, fallback)}
        onChange={(e) => set(platform, key, e.target.checked)}
      />
      {t(label)}
    </label>
  )

  const shown = platforms.filter((p) =>
    ['youtube', 'tiktok', 'instagram', 'facebook', 'pinterest'].includes(p)
  )
  if (shown.length === 0) return null

  return (
    <div className="space-y-2">
      <p className="label">{t('Per platform')}</p>
      {shown.map((p) => (
        <div key={p} className="flex items-center gap-x-5 gap-y-2 flex-wrap">
          <span className="text-sm font-medium w-20 shrink-0">{platformLabel(p)}</span>
          {p === 'youtube' && select(p, 'privacy', YOUTUBE_PRIVACY, 'Visibility')}
          {p === 'tiktok' && (
            <>
              {select(p, 'privacyLevel', TIKTOK_PRIVACY, 'Who can watch')}
              {tick(p, 'allowComment', true, 'Comments')}
              {tick(p, 'allowDuet', true, 'Duets')}
              {tick(p, 'allowStitch', true, 'Stitches')}
              {/* Disclosures, not preferences: off unless it is true. */}
              {tick(p, 'isYourBrand', false, 'Promotes my own brand')}
              {tick(p, 'isBrandedContent', false, 'Paid partnership')}
            </>
          )}
          {p === 'instagram' && select(p, 'postType', INSTAGRAM_TYPE, 'Post as')}
          {p === 'facebook' && select(p, 'postType', FACEBOOK_TYPE, 'Post as')}
          {p === 'pinterest' && (
            <label className="text-sm flex items-center gap-2">
              <span className="text-muted">{t('Board ID')}</span>
              <input
                className="input !w-56 !py-1"
                value={str(p, 'pinterestBoardId', '')}
                placeholder={t('Needed for Pinterest')}
                onChange={(e) => set(p, 'pinterestBoardId', e.target.value.trim())}
              />
            </label>
          )}
        </div>
      ))}
    </div>
  )
}

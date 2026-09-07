import { t } from '../lib/i18n'
import { describeInstant, earliestSchedule, localInputToUtc, localTimeZone } from '../lib/youtube'

/** Visibility, and the scheduling that YouTube itself owns.
 *
 *  The wording under "Schedule" is the most important text in this feature.
 *  People assume a scheduler means the app has to stay open — this one does
 *  not, because the video is uploaded immediately and YouTube holds it. Saying
 *  so on screen is what stops someone leaving their PC on all night for
 *  nothing.
 */

interface Props {
  privacy: string
  scheduledAt: string // a datetime-local value, '' when publishing now
  onChange: (patch: { privacy?: string; scheduledAt?: string }) => void
  disabled?: boolean
}

export default function YouTubeSchedule({
  privacy,
  scheduledAt,
  onChange,
  disabled
}: Props): JSX.Element {
  const scheduling = scheduledAt !== ''
  const utc = scheduling ? localInputToUtc(scheduledAt) : null
  const zone = localTimeZone()

  return (
    <fieldset className="border border-raised/60 rounded-lg p-3 space-y-3">
      <legend className="label px-1">{t('Visibility')}</legend>

      <div className="flex flex-wrap gap-3 text-sm">
        {(['public', 'unlisted', 'private'] as const).map((option) => (
          <label key={option} className="inline-flex items-center gap-2">
            <input
              type="radio"
              name="yt-visibility"
              checked={!scheduling && privacy === option}
              disabled={disabled}
              onChange={() => onChange({ privacy: option, scheduledAt: '' })}
            />
            {t(option === 'public' ? 'Public' : option === 'unlisted' ? 'Unlisted' : 'Private')}
          </label>
        ))}
        <label className="inline-flex items-center gap-2">
          <input
            type="radio"
            name="yt-visibility"
            checked={scheduling}
            disabled={disabled}
            onChange={() => onChange({ scheduledAt: earliestSchedule(30) })}
          />
          {t('Schedule')}
        </label>
      </div>

      {scheduling && (
        <div className="space-y-2">
          <input
            type="datetime-local"
            className="input"
            value={scheduledAt}
            min={earliestSchedule()}
            disabled={disabled}
            aria-label={t('Publish date and time')}
            onChange={(e) => onChange({ scheduledAt: e.target.value })}
          />

          <p className="text-[11px] text-muted">
            {t('Times are in your timezone')} ({zone}).
          </p>

          {utc && (
            <p className="text-xs text-accent" aria-live="polite">
              {t('Goes live')} {describeInstant(utc)}
            </p>
          )}

          {/* The whole justification for using YouTube's publishAt instead of
              a timer in this app. It has to be on screen, not in the docs. */}
          <p className="text-xs bg-raised/40 border border-raised/60 rounded-md p-2">
            {t(
              'Clips Kitty uploads the video to YouTube now and asks YouTube to publish it at that time. You can close Clips Kitty and turn off your computer — YouTube handles the rest.'
            )}
          </p>
        </div>
      )}
    </fieldset>
  )
}

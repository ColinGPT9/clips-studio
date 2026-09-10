import { t } from '../lib/i18n'
import type { RunOutcome } from '../lib/types'

/** Why a finished run produced no clips.
 *
 *  This replaces "No clips for this video yet", which reads like a fault and
 *  sends people straight to the bug reporter. The commonest cause by far is
 *  footage the scoring was never meant for — gameplay, or anything with nobody
 *  on screen — and the run knows that: part of a clip's score measures whether
 *  a person is visible, so those score zero on that term rather than merely low.
 *
 *  The tone matters as much as the facts. Someone reading this has just spent
 *  twenty minutes on a video and got nothing back; it has to explain without
 *  reading as "you picked the wrong video".
 */

interface Props {
  outcome: RunOutcome | null | undefined
  compact?: boolean
}

export default function NoClipsExplanation({ outcome, compact }: Props): JSX.Element {
  const size = compact ? 'text-xs' : 'text-sm'

  // Nothing recorded: an older video processed before runs were summarised.
  // Say the plain thing rather than invent a reason.
  if (!outcome || !outcome.candidates) {
    return (
      <p className={`text-muted ${size}`}>
        {t('No clips for this video — or pick another video above.')}
      </p>
    )
  }

  const { candidates, best_score, min_score, cause, measured, nothing_detected } = outcome

  const numbers =
    best_score === null || best_score === undefined
      ? `${candidates} ${t('moments were considered.')}`
      : `${candidates} ${t('moments were considered; the best scored')} ${best_score} ${t(
          'against a threshold of'
        )} ${min_score}.`

  return (
    <div
      className={`border border-raised/60 rounded-lg p-3 space-y-2 ${size} max-w-2xl`}
      role="note"
    >
      <p className="font-medium text-ink">{t('No clips from this video, and nothing went wrong.')}</p>
      <p className="text-muted">{numbers}</p>

      {cause === 'no_people' && (
        <>
          <p className="text-muted">
            {t('Nothing person-shaped was detected in')} {nothing_detected} {t('of')} {measured}{' '}
            {t(
              'of the moments it looked at. Part of a clip’s score is whether someone is on screen, so gameplay and top-down footage score zero there rather than just low — which puts them under the threshold.'
            )}
          </p>
          <p className="text-muted">
            {t(
              'Clips Kitty is tuned for IRL, just chatting, podcasts, vlogs and interviews. It is what it was built and tested on.'
            )}
          </p>
        </>
      )}

      {cause === 'duplicates' && (
        <p className="text-muted">
          {t('Most candidates repeated a moment already covered, so they were dropped.')}
        </p>
      )}

      {cause === 'no_candidates' && (
        <p className="text-muted">
          {t(
            'Nothing was proposed at all, which usually means there was no speech to work from.'
          )}
        </p>
      )}

      <p className="text-muted">
        {cause === 'no_people'
          ? t(
              'Lowering Minimum score in Settings will start producing clips, but they will be picked without the visual half of the signal — expect them to be arbitrary.'
            )
          : t('Lowering Minimum score in Settings would let more of these through.')}
      </p>
    </div>
  )
}

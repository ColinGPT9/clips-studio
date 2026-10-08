import { t } from '../../lib/i18n'
import type { InstalledPlugin } from '../../lib/marketplace'
import { MAX_PER_STEP, usableFor, type JobStep } from '../../lib/steps'
import type { JobOptions, PipelineChoice, SportChoice } from '../../lib/types'
import PipelineFields from '../PipelineFields'

/** The words of each step's rows: the first row's label, the label of the
 *  rows after it, and what Clips Kitty does when no plugin is chosen. */
const ROWS: Record<JobStep, { first: string; more: string; none: string }> = {
  understand: { first: 'Understand them with', more: 'and with', none: 'Clips Kitty (from what’s said)' },
  rate: { first: 'Rate them with', more: 'then with', none: 'Clips Kitty’s own scores' },
  edit: { first: 'Suggest edits with', more: 'and with', none: 'No suggestions' }
}

/** One video's Rate & understand choices, under the switch, read as a
 *  sentence: who finds the moments, then the plugins that say what happens in
 *  them, then the plugins that score them, each in the order they run. Or,
 *  with `steps` ['edit'], its Suggest edits choices: the plugins that
 *  suggest edits for each clip, in the order they run. Each chosen plugin is
 *  a PipelineFields row (where it runs, what it sends off this PC, a missing
 *  key, its settings). The engine checks everything again when the video is
 *  added (plugins/steps.py clean and check_installed). */
export default function StepFields({
  steps = ['understand', 'rate'],
  options,
  plugins,
  pipelines,
  sports,
  name,
  onChange,
  onProblem
}: {
  /** Which steps' rows to show: Rate & understand's two (the default), or ['edit']. */
  steps?: JobStep[]
  options: JobOptions
  /** The plugins that can do these steps (useStepPlugins, useEditPlugins); null while the engine hasn't said. */
  plugins: InstalledPlugin[] | null
  /** The installed pipelines, for the name of the one that finds the moments. */
  pipelines: InstalledPlugin[] | null
  sports: SportChoice[]
  name: string
  /** The step's new list; an empty one means Clips Kitty does that step. */
  onChange: (step: JobStep, next: PipelineChoice[]) => void
  /** Whether a row's settings hold a value that can't be sent. */
  onProblem: (step: JobStep, id: string, bad: boolean) => void
}): JSX.Element {
  const finder = options.pipeline
    ? (pipelines?.find((p) => p.id === options.pipeline?.id)?.name ?? options.pipeline.id)
    : options.sport
      ? `${t('Sports')} (${t(sports.find((s) => s.id === options.sport?.name)?.label ?? options.sport.name)})`
      : options.gaming || options.gaming_scoring
        ? t('Gaming scoring')
        : t('Clips Kitty')

  const step = (which: JobStep): JSX.Element => {
    const chosen = options[which] ?? []
    // The job's own pipeline already describes and scores what it finds: the
    // engine refuses it under Rate & understand. It may suggest edits: a find
    // run is never asked to.
    const usable = usableFor(plugins ?? [], which).filter((p) => which === 'edit' || p.id !== options.pipeline?.id)
    const free = usable.filter((p) => !chosen.some((c) => c.id === p.id))
    const words = ROWS[which]
    const add = free.length > 0 && chosen.length < MAX_PER_STEP
    if (chosen.length === 0)
      return (
        <div className="flex items-center gap-3 flex-wrap">
          <span className="label shrink-0">{t(words.first)}</span>
          <span className="text-sm text-muted">{t(words.none)}</span>
          {add && (
            <button
              className="text-xs text-accent hover:underline"
              onClick={() => onChange(which, [{ id: free[0].id }])}
            >
              + {t('Choose a plugin')}
            </button>
          )}
        </div>
      )
    return (
      <div className="space-y-2">
        {chosen.map((choice, i) => (
          <PipelineFields
            key={`${which}:${choice.id}`}
            value={choice}
            // Only the plugins that can do this step, less the ones in its other rows.
            pipelines={
              plugins === null
                ? null
                : usable.filter((p) => p.id === choice.id || !chosen.some((c) => c.id === p.id))
            }
            name={name}
            label={t(i === 0 ? words.first : words.more)}
            missingText={t('This plugin isn’t installed and turned on any more. Choose another, or remove it.')}
            onChange={(next) => onChange(which, chosen.map((c, j) => (j === i ? next : c)))}
            onProblem={(bad) => onProblem(which, choice.id, bad)}
            onRemove={() => onChange(which, chosen.filter((_, j) => j !== i))}
          />
        ))}
        {add && (
          <button
            className="text-xs text-accent hover:underline"
            title={t('Up to 3 plugins for each step')}
            onClick={() => onChange(which, [...chosen, { id: free[0].id }])}
          >
            + {t('Add another')}
          </button>
        )}
      </div>
    )
  }

  if (!steps.includes('understand') && !steps.includes('rate')) {
    const editors = options.edit?.length ?? 0
    return (
      <div className="w-full space-y-2">
        {step('edit')}
        {editors >= 2 && (
          <p className="text-xs text-muted">
            {t('Each plugin’s suggestion is shown on its own. You choose which to use.')}
          </p>
        )}
      </div>
    )
  }

  const understanders = options.understand?.length ?? 0
  const raters = options.rate?.length ?? 0
  return (
    <div className="w-full space-y-2">
      <p className="text-sm">
        <span className="label">{t('Moments found by')}</span> {finder}
      </p>
      {step('understand')}
      {understanders >= 2 && (
        <p className="text-xs text-muted">
          {t('Clips Kitty can use what each of them says (up to 8 notes for each moment).')}
        </p>
      )}
      {step('rate')}
      {raters >= 2 && (
        <p className="text-xs text-muted">
          {t(
            'They rate in this order: each one sees the score the one before it gave. The last one’s score is the one that counts.'
          )}
        </p>
      )}
      {raters > 0 && (
        <p className="text-xs text-warn">
          {t('Rating decides which clips are made and their order, and which are posted when only the best few are.')}
        </p>
      )}
    </div>
  )
}

/** Rate & understand (plugins/steps.py): Marketplace plugins that look at a
 *  video's moments once they're found. Which installed plugins a job can name
 *  for each step, the keys a video's step rows report setting problems under,
 *  what the watch editor sends back, and the lines that say what the steps
 *  change on a watched channel and on the video page.
 *
 *  Standalone, with type-only imports, so tests run it under Node
 *  (tests/test_ui_steps.py) against the engine's own helpers. Words that are
 *  shown go through `tr`, the screen's t(); without it they are English. */

import type { InstalledPlugin } from './marketplace'
import type { JobOptions, RunOutcome, StepRun } from './types'

export type Step = 'find' | 'understand' | 'rate'
/** The steps chosen under Rate & understand, in the order they run. */
export type MomentStep = 'understand' | 'rate'
export const MOMENT_STEPS: MomentStep[] = ['understand', 'rate']

/** Plugins for one step, in a job (plugins/steps.py MAX_PER_STEP). */
export const MAX_PER_STEP = 3

const STEPS: Step[] = ['find', 'understand', 'rate']
/** What each step answers with (manifest.STEP_OUTPUTS). */
const STEP_OUTPUTS: Record<Step, string> = { find: 'ranges', understand: 'context', rate: 'ratings' }

type Words = (s: string) => string
const english: Words = (s) => s

function words(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** The steps a job may name a plugin for, as the engine's manifest.offers
 *  says: find for `ranges` in its outputs; understand for `context` and rate
 *  for `ratings`, each only when it also takes `moments` in. */
export function offers(m: { inputs?: unknown; outputs?: unknown } | null | undefined): Step[] {
  const outputs = words(m?.outputs)
  const given = words(m?.inputs).includes('moments')
  return STEPS.filter((step) => outputs.includes(STEP_OUTPUTS[step]) && (step === 'find' || given))
}

/** The installed plugins a job can name for this step: turned on, able to
 *  run here, not blocked, and able to do it. */
export function usableFor(plugins: InstalledPlugin[], step: MomentStep): InstalledPlugin[] {
  return plugins.filter(
    (p) =>
      (p.kind ?? 'pipeline') === 'pipeline' &&
      p.enabled &&
      !p.problem &&
      p.flag?.severity !== 'blocked' &&
      offers(p).includes(step)
  )
}

/** The installed plugins a job can name to understand or to rate (useStepPlugins). */
export function stepPlugins(plugins: InstalledPlugin[]): InstalledPlugin[] {
  return plugins.filter((p) => MOMENT_STEPS.some((step) => usableFor([p], step).length > 0))
}

/** Whether a video's options name a step. */
export function hasSteps(o: Pick<JobOptions, 'rate' | 'understand'> | null | undefined): boolean {
  return Boolean(o?.rate?.length || o?.understand?.length)
}

/** The keys a video's step rows report setting problems under, for the rows
 *  it has now: `{slot}:understand:{id}` and `{slot}:rate:{id}`. Named by
 *  plugin, so removing a row never moves a problem onto another; a row that
 *  is gone has no key here, so it can't hold Generate. The Pipeline row's
 *  key is the bare slot key, never one of these. */
export function stepProblemKeys(slotKey: string, o: Pick<JobOptions, 'rate' | 'understand'>): string[] {
  return MOMENT_STEPS.flatMap((step) => (o[step] ?? []).map((c) => `${slotKey}:${step}:${c.id}`))
}

/** What the queued-video or watched-channel panel sends for Rate &
 *  understand, which it can only keep or turn off. Off clears both lists.
 *  Kept, a watched channel (`replaces`: its options are replaced on save)
 *  gets the lists back as they were, or an autosave would drop them; a queued
 *  video's options are merged, so they are left out, as Pipeline is. A video
 *  or channel that had none sends nothing. */
export function stepsPatch(
  original: Pick<JobOptions, 'rate' | 'understand'>,
  kept: boolean,
  replaces: boolean
): { patch: Pick<JobOptions, 'rate' | 'understand'>; clear: string[] } {
  const patch: Pick<JobOptions, 'rate' | 'understand'> = {}
  if (!hasSteps(original)) return { patch, clear: [] }
  if (!kept) return { patch, clear: ['rate', 'understand'] }
  if (replaces) {
    if (original.rate?.length) patch.rate = original.rate
    if (original.understand?.length) patch.understand = original.understand
  }
  return { patch, clear: [] }
}

/** A watched channel's publishing, as the posting lines read it. */
export interface ChannelPosting {
  mode: 'off' | 'ask' | 'auto'
  /** Best clips of each video to post; 0 posts every clip. */
  max_posts: number
}

/** What the kept steps change about a watched channel's posts: a rater's
 *  scores decide which clips go out and in what order, and an understander's
 *  notes can reach a title nobody checked. None when the channel doesn't post. */
export function stepPostingLines(
  channel: ChannelPosting,
  kept: Pick<JobOptions, 'rate' | 'understand'>,
  tr: Words = english
): string[] {
  if (channel.mode !== 'auto' && channel.mode !== 'ask') return []
  const out: string[] = []
  const auto = channel.mode === 'auto'
  const n = channel.max_posts
  if (kept.rate?.length) {
    if (n > 1)
      out.push(
        auto
          ? `${tr('This channel posts only the')} ${n} ${tr('clips these plugins rate highest, without asking you.')}`
          : `${tr('Publish sends the')} ${n} ${tr('clips these plugins rate highest.')}`
      )
    else if (n === 1)
      out.push(
        auto
          ? tr('This channel posts only the clip these plugins rate highest, without asking you.')
          : tr('Publish sends the clip these plugins rate highest.')
      )
    else out.push(tr('Clips go out in the order these plugins rate them.'))
  }
  if (kept.understand?.length && auto)
    out.push(
      tr(
        'This channel posts automatically, so what these plugins say about a moment can end up in the posted title, description and hashtags without you checking them.'
      )
    )
  return out
}

/** A plugin's name in a run's report; the id for an older entry without one. */
function nameOf(run: Pick<StepRun, 'name' | 'plugin'>): string {
  return run.name || run.plugin
}

/** One line of the video page's "Marketplace plugins on this video": what a
 *  plugin's run on the moments did, or why Clips Kitty made the clips without it. */
export function stepRunText(run: StepRun, minScore: number, tr: Words = english): string {
  const name = nameOf(run)
  if (!run.ok) {
    const why = run.error ? ` ${tr(run.error)}` : ''
    return `${tr('Clips Kitty made these clips without')} ${name}.${why}`
  }
  const understood = run.steps.includes('understand')
  const rated = run.steps.includes('rate')
  if (!(understood && run.noted > 0) && !(rated && run.rated > 0))
    return `${name} ${tr('looked at the moments and changed nothing.')}`
  const said = `${name} ${tr('said what happens in')} ${run.noted} ${tr('of')} ${run.given}`
  let text: string
  if (understood && rated) text = `${said} ${tr('moments and rated')} ${run.rated}.`
  else if (understood) text = `${said} ${tr('moments.')}`
  else text = `${name} ${tr('rated')} ${run.rated} ${tr('of')} ${run.given} ${tr('moments.')}`
  if (rated && run.set_aside > 0)
    text += ` ${run.set_aside} ${tr('rated under your minimum score')} (${minScore}) ${tr('were set aside.')}`
  return text
}

/** The plugins that set moments of this video aside by rating them under the
 *  minimum score (core/outcome.py rated_out_names), each once. */
export function ratedOutNames(outcome: Pick<RunOutcome, 'steps'> | null | undefined): string[] {
  const names: string[] = []
  for (const run of outcome?.steps ?? []) {
    const name = run && run.set_aside > 0 ? nameOf(run) : ''
    if (name && !names.includes(name)) names.push(name)
  }
  return names
}

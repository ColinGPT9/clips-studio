/** Make clips again (#190): a video's clips made again with other settings,
 *  without adding the video again. What the Library's action and its dialog
 *  decide: the options the dialog starts from and posts, why the action is
 *  off, the words of its count, and what a finished run says it did. The
 *  engine's side is docs/editor-suite/api/step2-remake.md.
 *
 *  Standalone, with type-only imports, so tests run it under Node
 *  (tests/test_ui_remake.py). Words that are shown go through `tr`, the
 *  screen's t(); without it they are English. */

import type { JobOptions, RemakeOptions, RemakePreview, RemakeReason, RemakeResult } from './types'

type Words = (s: string) => string
const english: Words = (s) => s

/** What the settings panel (components/queue/QueueItemSettings.tsx) saves:
 *  the fields it sets, and in `clear` the ones it turns off. */
export type SettingsPatch = Partial<JobOptions> & { clear?: string[] }

/** Never sent: the video's own link or file is used, the run is forced, and
 *  Longform is refused (this makes the 9:16 clips again). */
const NOT_SENT = ['url', 'force', 'longform', 'clear']

function sendable(options: object): RemakeOptions {
  const out: Record<string, unknown> = { ...options }
  for (const key of NOT_SENT) delete out[key]
  return out as RemakeOptions
}

/** The options the dialog starts from: the video's last run's, or, when no
 *  run is left to read them from (Clear completed on the Queue page deletes
 *  them), the Generate bar's (`usual`, AddVideos' seedOptions). */
export function remakeStart(
  preview: Pick<RemakePreview, 'options' | 'from_job'>,
  usual: () => JobOptions
): RemakeOptions {
  return sendable(preview.from_job === null ? usual() : preview.options)
}

/** What Make clips again posts: the options the dialog started from, with
 *  the panel's patch over them. Each field the patch has is set, then each
 *  key in its `clear` goes, which is how the panel turns a switch off.
 *  Every other key goes back as it was, the ones the panel doesn't show
 *  (hashtags, the plugins, the split, whole video, ...) too: a key left out
 *  means the setting's own value, not the last run's. */
export function remakeOptions(start: RemakeOptions, patch: SettingsPatch): RemakeOptions {
  const { clear, ...fields } = patch
  const out: Record<string, unknown> = { ...start }
  for (const [key, value] of Object.entries(fields)) if (value !== undefined) out[key] = value
  for (const key of clear ?? []) delete out[key]
  return sendable(out)
}

/** Why Make clips again can't be used now, for its tooltip; null when it can. */
export function remakeBlocked(
  preview: Pick<RemakePreview, 'blocked' | 'source'>,
  tr: Words = english
): string | null {
  switch (preview.blocked) {
    case 'running':
      return tr('This video’s clips are being made right now. You can make them again once that’s done.')
    case 'queued':
      return tr(
        'This video is waiting in the queue. You can make its clips again once it’s done, or after you remove it from the queue.'
      )
    case 'source_gone':
      return preview.source.kind === 'file'
        ? tr('This video’s file isn’t in Clips Kitty any more, so its clips can’t be made again. Add the file again.')
        : tr(
            'Clips Kitty no longer has this video’s download or a link to it, so its clips can’t be made again. Add the video again.'
          )
    case 'busy':
      return tr(
        'Clips Kitty is working with one of this video’s clips right now (publishing, exporting or editing it). You can make its clips again once that’s done.'
      )
    case 'queue_full':
      return tr('The queue is full. Let a video finish, or remove one from the queue, first.')
    default:
      return null
  }
}

/** Why a clip is kept, in the engine's order (core/remake.py REASONS). */
const KEPT_AS: Record<RemakeReason, string> = {
  longform: '16:9 Longform',
  story_reel: 'match reels',
  edited: 'edited',
  published: 'published or sent to publish',
  scheduled: 'scheduled',
  exported: 'exported or starred',
  translated: 'translated',
  submitted: 'sent to a clipping campaign',
  chosen: 'chosen by a watched channel',
  busy: 'still being edited, exported or published',
  older: 'made before this update or in another version',
  unsure: 'not sure if used'
}

/** The tooltip of the dialog's "N will be replaced, M kept": what replaced
 *  means, and why the kept ones are kept, each reason with its count. Clips
 *  made before this update, or in another version of Clips Kitty, get a
 *  line of their own saying why, since nothing on them shows it. */
export function remakeCountTitle(counts: RemakePreview['counts'], tr: Words = english): string {
  const lines = [
    tr(
      'Replaced: the clips nobody used. Once the new clips are ready, each is made again with the new settings or deleted.'
    )
  ]
  const kept = (Object.keys(KEPT_AS) as RemakeReason[])
    .filter((reason) => (counts.kept_because[reason] ?? 0) > 0)
    .map((reason) => `${tr(KEPT_AS[reason])} (${counts.kept_because[reason]})`)
  if (kept.length > 0) lines.push(`${tr('Kept:')} ${kept.join(', ')}`)
  if ((counts.kept_because.older ?? 0) > 0)
    lines.push(
      tr(
        'Clips made before this update, or in another version of Clips Kitty, are kept: Clips Kitty can’t tell whether you changed them. One the new settings find again at exactly the same start and end is made again with them, and keeps its title. Delete the ones you don’t want yourself.'
      )
    )
  return lines.join('\n')
}

/** What a finished Make clips again did, in one line, for its queue row and
 *  the Library: "15 new · 7 removed · 2 kept as they were". A run that
 *  changed nothing says so, since the clips from before would otherwise
 *  look as if nothing had run. A clip the run couldn't make (then nothing
 *  was replaced), or replacing that stopped partway, is said too. */
export function remakeResultText(result: RemakeResult, tr: Words = english): string {
  const count = (n: number | undefined): number => (typeof n === 'number' && n > 0 ? n : 0)
  const failed = count(result.renders_failed) > 0
  const changed = count(result.new) + count(result.again) + count(result.removed)
  if (changed === 0 && !failed && !result.error)
    return count(result.unchanged) > 0
      ? tr('No new clips this time, so your clips are kept as they were.')
      : tr('No new clips this time.')
  const parts: string[] = []
  if (failed) parts.push(tr('Some clips couldn’t be made, so nothing was replaced'))
  if (count(result.new) > 0) parts.push(`${result.new} ${tr('new')}`)
  if (count(result.again) > 0) parts.push(`${result.again} ${tr('made again with the new settings')}`)
  if (count(result.removed) > 0) parts.push(`${result.removed} ${tr('removed')}`)
  if (count(result.unchanged) > 0) parts.push(`${result.unchanged} ${tr('kept as they were')}`)
  if (result.error) parts.push(tr('the rest kept, as they couldn’t be replaced'))
  return parts.join(' · ')
}

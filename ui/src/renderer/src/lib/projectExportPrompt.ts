/** Today's editor and a clip the new editor exported (step 7's 7-D5).
 *
 *  After the new editor exports a project made from a clip, the clip's file
 *  is that export, while the clip's own settings are still the clip as it was
 *  before. Applying a change in today's editor (or its chat edit) would make
 *  the clip again from those settings and lose the new editor's changes, so
 *  the engine asks first (409, reason "project_export"). The app then asks
 *  the person: open it in the new editor (when that is switched on), apply
 *  anyway (the same request with replace_project_edits), or leave it.
 *
 *  ProjectExportPrompt (mounted once in App.tsx) answers the question; with
 *  nothing mounted the answer is "leave it". A clip the new editor never
 *  exported never meets any of this. */
import { t } from './i18n'

export type ProjectExportChoice = 'apply' | 'make' | 'open' | 'cancel'
export interface ProjectExportQuestion {
  message: string
  project_id: string
  /** A new file made beside the clip (a translation, a dub, subtitle files;
   *  8-D13), not a render that replaces the clip's file: the person is told
   *  it is made from the clip as the AI made it, and chooses "Make it
   *  anyway" or "Cancel". */
  separate?: boolean
  /** How many of the request's clips were changed in the new editor, of
   *  how many it names (a batch is asked about once). */
  count?: number
  total?: number
}

type Asker = (q: ProjectExportQuestion) => Promise<ProjectExportChoice>
let asker: Asker | null = null

export function setProjectExportAsker(fn: Asker | null): void {
  asker = fn
}

/** The engine's refusal, when it is this question; else null. */
export function projectExportQuestion(body: unknown): ProjectExportQuestion | null {
  const detail = (body as { detail?: unknown } | null)?.detail as {
    reason?: unknown
    message?: unknown
    project_id?: unknown
    separate_file?: unknown
    count?: unknown
    total?: unknown
  } | null
  if (!detail || detail.reason !== 'project_export') return null
  const question: ProjectExportQuestion = { message: String(detail.message ?? ''), project_id: String(detail.project_id ?? '') }
  if (detail.separate_file === true) {
    question.separate = true
    question.count = Number(detail.count) || 1
    question.total = Number(detail.total) || question.count
  }
  return question
}

/** Send a request that makes NEW files from a clip's own settings (Export in
 *  other languages: burned subtitles, a dub, subtitle files). When a clip's
 *  file is a new-editor export the engine asks first (409, reason
 *  "project_export", separate_file); the person is told the new files are
 *  made without the new editor's changes, and the request is sent again with
 *  `goAhead` only when they choose "Make it anyway" (8-D13). */
export async function sendAskingFirstForNewFiles(send: (goAhead: boolean) => Promise<Response>): Promise<Response> {
  const res = await send(false)
  if (res.status !== 409) return res
  const question = projectExportQuestion(await res.clone().json().catch(() => null))
  if (!question) return res
  const choice = asker ? await asker({ ...question, separate: true }) : 'cancel'
  if (choice === 'make') return send(true)
  throw new NothingMade(choice === 'open' ? t('Opened in the new editor. Nothing was made.') : t('Nothing was made.'))
}

/** The person chose not to make the files: said as it is, not as an error. */
export class NothingMade extends Error {}

/** Send a render or chat-edit request; ask the person when the engine says
 *  the clip's file is a new-editor export, and send it again with
 *  replace_project_edits only when they choose "Apply anyway". */
export async function sendAskingFirst(send: (replace: boolean) => Promise<Response>): Promise<Response> {
  const res = await send(false)
  if (res.status !== 409) return res
  const question = projectExportQuestion(await res.clone().json().catch(() => null))
  if (!question) return res
  const choice = asker ? await asker(question) : 'cancel'
  if (choice === 'apply') return send(true)
  throw new Error(
    choice === 'open' ? t('Opened in the new editor. Nothing was applied here.') : t('Nothing was applied.')
  )
}

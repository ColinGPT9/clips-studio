/** Quick edit and a clip Clips Kitty Editor exported (editor suite K2, E5).
 *
 *  After the editor exports a project made from a clip, the clip's file is
 *  that export (it lives in data/clips/projects/), while the clip's own
 *  settings still describe the clip as the AI made it. Applying a change in
 *  Quick edit (Apply, the chat edit, the caption save, "Apply edits &
 *  upload") would make the clip again from those settings and lose the
 *  editor's changes, so the engine asks first (409, reason
 *  "project_export"). The app then asks the person: open it in the editor
 *  (when the editor is found and its project is there), apply anyway (the
 *  same request with replace_project_edits), or leave it.
 *
 *  Ported from the step 8 editor (commit b43d905) with new words.
 *  ProjectExportPrompt (components/editor/, mounted once in App.tsx) answers
 *  the question; with nothing mounted the answer is "leave it". A clip the
 *  editor never exported never meets any of this. */
import { t } from './i18n'

export type ProjectExportChoice = 'apply' | 'make' | 'open' | 'cancel'
export interface ProjectExportQuestion {
  message: string
  project_id: string
  /** New files made beside the clip (a translation, a dub, subtitle files),
   *  not a render that replaces the clip's file: the person is told they
   *  are made from the clip as the AI made it, and chooses "Make it anyway"
   *  or "Cancel". */
  separate?: boolean
  /** How many of the request's clips were changed in the editor, of how
   *  many it names (a batch is asked about once). */
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
  const question: ProjectExportQuestion = {
    message: String(detail.message ?? ''),
    project_id: typeof detail.project_id === 'string' ? detail.project_id : ''
  }
  if (detail.separate_file === true) {
    question.separate = true
    question.count = Number(detail.count) || 1
    question.total = Number(detail.total) || question.count
  }
  return question
}

/** The person chose not to go ahead: said as it is, not as an error. Its
 *  name is empty, so String(e) is the sentence alone. */
export class NothingMade extends Error {
  constructor(message: string) {
    super(message)
    this.name = ''
  }
}

/** Send a request that makes NEW files from a clip's own settings (Export in
 *  other languages: burned subtitles, a dub, subtitle files). When a clip's
 *  file is an editor export the engine asks first (409, separate_file); the
 *  request is sent again with `goAhead` only on "Make it anyway". */
export async function sendAskingFirstForNewFiles(send: (goAhead: boolean) => Promise<Response>): Promise<Response> {
  const res = await send(false)
  if (res.status !== 409) return res
  const question = projectExportQuestion(await res.clone().json().catch(() => null))
  if (!question) return res
  const choice = asker ? await asker({ ...question, separate: true }) : 'cancel'
  if (choice === 'make') return send(true)
  throw new NothingMade(t('Nothing was made.'))
}

/** Send a render or chat-edit request; ask the person when the engine says
 *  the clip's file is an editor export, and send it again with
 *  replace_project_edits only when they choose "Apply anyway". */
export async function sendAskingFirst(send: (replace: boolean) => Promise<Response>): Promise<Response> {
  const res = await send(false)
  if (res.status !== 409) return res
  const question = projectExportQuestion(await res.clone().json().catch(() => null))
  if (!question) return res
  const choice = asker ? await asker(question) : 'cancel'
  if (choice === 'apply') return send(true)
  throw new NothingMade(t('Nothing was made.'))
}

/** An error's text for a notice line: a NothingMade as its sentence alone,
 *  anything else with the caller's prefix. */
export function noticeText(e: unknown, prefix: string): string {
  if (e instanceof NothingMade) return e.message
  return `${prefix}${e instanceof Error ? e.message : String(e)}`
}

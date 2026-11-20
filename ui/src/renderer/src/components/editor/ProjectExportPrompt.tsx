/** The question Quick edit asks before it makes a clip again over Clips Kitty
 *  Editor's export of it (lib/projectExportPrompt.ts): the sentence, then
 *  "Open in editor" (only when the editor is found and the project is still
 *  there), "Apply anyway" and "Cancel". The same dialog asks before NEW files
 *  are made from such a clip's own settings (Export in other languages): "Open
 *  in editor", "Make it anyway" and "Cancel". First focus is never on "Apply
 *  anyway" or "Make it anyway", so Enter by reflex never replaces the export.
 *
 *  Ported from commit b43d905 (step 8) with new words. Mounted once in
 *  App.tsx; it holds the editor's cover while open (lib/editorCover.ts), so
 *  it is never drawn under the editor. */
import { useEffect, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { openEditorProject } from '../../lib/editor'
import { useEditorCover } from '../../lib/editorCover'
import { useEditorFound } from '../../lib/editorStatus'
import { setProjectExportAsker, type ProjectExportChoice, type ProjectExportQuestion } from '../../lib/projectExportPrompt'

export default function ProjectExportPrompt(): JSX.Element | null {
  const found = useEditorFound()
  const [question, setQuestion] = useState<ProjectExportQuestion | null>(null)
  const answer = useRef<((c: ProjectExportChoice) => void) | null>(null)
  const first = useRef<HTMLButtonElement>(null)
  useEditorCover(question !== null)

  useEffect(() => {
    setProjectExportAsker(
      (q) =>
        new Promise<ProjectExportChoice>((resolve) => {
          answer.current?.('cancel')
          answer.current = resolve
          setQuestion(q)
        })
    )
    return () => setProjectExportAsker(null)
  }, [])
  useEffect(() => {
    if (question) first.current?.focus()
  }, [question])

  if (!question) return null
  const canOpen = found && !!question.project_id
  const choose = (c: ProjectExportChoice): void => {
    if (c === 'open' && question.project_id) void openEditorProject(question.project_id)
    answer.current?.(c)
    answer.current = null
    setQuestion(null)
  }
  const separate = question.separate === true
  const many = separate && (question.total ?? 1) > 1
  const heading = many ? t('Some of these clips were changed in the editor') : t('This clip was changed in the editor')
  return (
    <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label={heading}
        className="card w-[28rem] max-w-full space-y-3"
        onKeyDown={(e) => {
          if (e.key === 'Escape') choose('cancel')
        }}
      >
        <h3 className="font-semibold">{heading}</h3>
        <p className="text-sm text-muted">
          {separate
            ? t('The new files are made from the clip as the AI made it, without the editor’s changes.')
            : t(
                'This clip was changed in the editor. Applying changes here starts again from the clip as it was before, so those changes would be lost.'
              )}
        </p>
        {many && (
          <p className="text-sm text-muted">
            {t('Clips changed in the editor:')} {question.count} / {question.total}
          </p>
        )}
        <div className="flex flex-wrap gap-2 justify-end">
          {canOpen && (
            <button ref={first} type="button" className="btn-accent !py-1.5 text-sm" onClick={() => choose('open')}>
              {t('Open in editor')}
            </button>
          )}
          <button
            type="button"
            className="btn-ghost !py-1.5 text-sm"
            onClick={() => choose(separate ? 'make' : 'apply')}
            title={
              separate
                ? t('Makes the new files from the clip as the AI made it. The clip and its editor project stay as they are.')
                : t('Makes the clip again from its own settings here. The editor’s changes to its video are lost.')
            }
          >
            {separate ? t('Make it anyway') : t('Apply anyway')}
          </button>
          {/* Without "Open in editor", first focus is on Cancel. */}
          <button
            ref={canOpen ? undefined : first}
            type="button"
            className="btn-ghost !py-1.5 text-sm"
            onClick={() => choose('cancel')}
          >
            {t('Cancel')}
          </button>
        </div>
      </div>
    </div>
  )
}

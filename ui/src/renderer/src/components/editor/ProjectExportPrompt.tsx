/** The question today's editor asks before it makes a clip again over the new
 *  editor's export of it (lib/projectExportPrompt.ts): the engine's own
 *  sentence, then "Open in new editor" (only while the new editor is
 *  switched on), "Apply anyway" and "Cancel". When the project that made the
 *  export was deleted (the engine sends no project_id, 8-D9) there is
 *  nothing to open: a sentence of its own, then only "Apply anyway" and
 *  "Cancel". Mounted once in App.tsx; it shows nothing until such a clip is
 *  applied.
 *
 *  The same dialog asks before NEW files are made from such a clip's own
 *  settings (Export in other languages, 8-D13; question.separate): they are
 *  made without the new editor's changes, so "Open in new editor" (when its
 *  project is there and the switch is on), "Make it anyway" and "Cancel". A
 *  batch is asked about once, with how many of its clips are affected. */
import { useEffect, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { openProject, useNewEditor } from '../../lib/newEditor'
import { setProjectExportAsker, type ProjectExportChoice, type ProjectExportQuestion } from '../../lib/projectExportPrompt'

export default function ProjectExportPrompt(): JSX.Element | null {
  const newEditor = useNewEditor()
  const [question, setQuestion] = useState<ProjectExportQuestion | null>(null)
  const answer = useRef<((c: ProjectExportChoice) => void) | null>(null)
  const first = useRef<HTMLButtonElement>(null)

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
  const canOpen = newEditor && !!question.project_id
  const choose = (c: ProjectExportChoice): void => {
    if (c === 'open' && question.project_id) openProject(question.project_id)
    answer.current?.(c)
    answer.current = null
    setQuestion(null)
  }
  if (question.separate) {
    // New files made beside the clip (Export in other languages, 8-D13):
    // nothing of the clip is replaced; the person is told the new files leave
    // out the new editor's changes.
    const many = (question.total ?? 1) > 1
    return (
      <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4">
        <div
          role="alertdialog"
          aria-modal="true"
          aria-label={many ? t('Some of these clips were changed in the new editor') : t('This clip was changed in the new editor')}
          className="card w-[28rem] max-w-full space-y-3"
          onKeyDown={(e) => {
            if (e.key === 'Escape') choose('cancel')
          }}
        >
          <h3 className="font-semibold">
            {many ? t('Some of these clips were changed in the new editor') : t('This clip was changed in the new editor')}
          </h3>
          <p className="text-sm text-muted">
            {many
              ? t("The new files are made from those clips as the AI made them, without the new editor's changes.")
              : t("The new files are made from the clip as the AI made it, without the new editor's changes.")}
          </p>
          {many && (
            <p className="text-sm text-muted">
              {t('Clips changed in the new editor:')} {question.count} / {question.total}
            </p>
          )}
          <div className="flex flex-wrap gap-2 justify-end">
            {canOpen && (
              <button ref={first} type="button" className="btn-accent !py-1.5 text-sm" onClick={() => choose('open')}>
                {t('Open in new editor')}
              </button>
            )}
            <button
              type="button"
              className="btn-ghost !py-1.5 text-sm"
              onClick={() => choose('make')}
              title={t("Makes the new files from the clip as the AI made it. The clip and the new editor's project stay as they are.")}
            >
              {t('Make it anyway')}
            </button>
            {/* As below: without "Open in new editor", first focus is on
                Cancel, so Enter by reflex makes nothing. */}
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
  return (
    <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label={t('This clip was changed in the new editor')}
        className="card w-[28rem] max-w-full space-y-3"
        onKeyDown={(e) => {
          if (e.key === 'Escape') choose('cancel')
        }}
      >
        <h3 className="font-semibold">{t('This clip was changed in the new editor')}</h3>
        <p className="text-sm text-muted">
          {question.project_id
            ? question.message
            : t('Its project was deleted, so it can no longer be opened in the new editor. Applying changes here makes the clip again from how it was before, and the video you exported from the new editor is replaced.')}
        </p>
        <div className="flex flex-wrap gap-2 justify-end">
          {canOpen && (
            <button ref={first} type="button" className="btn-accent !py-1.5 text-sm" onClick={() => choose('open')}>
              {t('Open in new editor')}
            </button>
          )}
          <button
            type="button"
            className="btn-ghost !py-1.5 text-sm"
            onClick={() => choose('apply')}
            title={
              question.project_id
                ? t("Makes the clip again from its own settings here. The new editor's changes to its video are lost; the project stays.")
                : t("Makes the clip again from its own settings here. The new editor's changes to its video are lost.")
            }
          >
            {t('Apply anyway')}
          </button>
          {/* Without "Open in new editor", first focus is on Cancel, never on
              "Apply anyway": Enter by reflex must not replace the export. */}
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

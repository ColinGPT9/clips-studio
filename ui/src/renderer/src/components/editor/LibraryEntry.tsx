/** The ways into the new editor from the Library (Clip Editor page), shown
 *  only while Settings → "New editor (beta)" is on:
 *
 *  - "Edit in new editor" on a clip: the clip's project (made the first time,
 *    the same one after);
 *  - "Edit whole video" on a video: a project of the whole video, in the shape
 *    chosen (9:16, 16:9, 1:1 or 4:5), framed to fit; no AI run;
 *  - "Import a video to edit": a file from this PC, the same way;
 *  - the Projects list. */
import { useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { useModalFocus } from './useModalFocus'
import { editorApi, errorText } from '../../lib/editorApi'
import { CANVAS_CHOICES, type CanvasChoice, type ImportedAsset, type MediaJob } from '../../lib/editorTypes'
import { openProject } from '../../lib/newEditor'
import ProjectsList from './ProjectsList'

const CANVAS_HINT: Record<CanvasChoice, string> = {
  '9:16': 'Tall: TikTok, Reels, Shorts',
  '16:9': 'Wide: YouTube',
  '1:1': 'Square',
  '4:5': 'Portrait: Instagram and Facebook feeds'
}

/** The shape box: the four canvases as small outlines, like CapCut's ratio picker. */
function CanvasPicker({ value, onChange }: { value: CanvasChoice; onChange: (c: CanvasChoice) => void }): JSX.Element {
  return (
    <div className="flex gap-2" role="radiogroup" aria-label={t('Shape')}>
      {CANVAS_CHOICES.map((c) => {
        const [w, h] = c.split(':').map(Number)
        const k = 34 / Math.max(w, h)
        return (
          <button
            key={c}
            type="button"
            role="radio"
            aria-checked={value === c}
            onClick={() => onChange(c)}
            title={t(CANVAS_HINT[c])}
            className={`flex flex-col items-center gap-1 rounded-lg px-3 py-2 border text-xs ${
              value === c ? 'border-accent bg-accent/10 text-accent' : 'border-raised text-muted hover:text-ink'
            }`}
          >
            <span className="flex items-center justify-center" style={{ width: 36, height: 36 }} aria-hidden>
              <span className="border-2 border-current rounded-sm" style={{ width: w * k, height: h * k }} />
            </span>
            {c}
          </button>
        )
      })}
    </div>
  )
}

/** Choose the shape, then make the project. `pick` (import) chooses a file first. */
function NewProjectDialog({
  title,
  action,
  onCreate,
  onClose
}: {
  title: string
  action: string
  onCreate: (canvas: CanvasChoice, progress: (line: string) => void) => Promise<void>
  onClose: () => void
}): JSX.Element {
  const [canvas, setCanvas] = useState<CanvasChoice>('9:16')
  const [busy, setBusy] = useState(false)
  const [line, setLine] = useState('')
  const [error, setError] = useState('')
  const root = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLDivElement>(null)
  // The keyboard stays in the box; Esc closes it from anywhere, not while working.
  useModalFocus(root, box, busy ? null : onClose)
  return (
    <div ref={root} className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div ref={box} className="card w-full max-w-md space-y-4">
        <h3 className="font-semibold">{title}</h3>
        <div className="space-y-1.5">
          <p className="label">{t('Shape')}</p>
          <CanvasPicker value={canvas} onChange={setCanvas} />
        </div>
        {line && <p className="text-xs text-muted" role="status">{line}</p>}
        {error && <p className="text-xs text-red-300">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>
            {t('Cancel')}
          </button>
          <button
            type="button"
            className="btn-accent"
            disabled={busy}
            autoFocus
            onClick={async () => {
              setBusy(true)
              setError('')
              try {
                await onCreate(canvas, setLine)
              } catch (e) {
                setError(errorText(e))
                setLine('')
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? t('Working…') : action}
          </button>
        </div>
      </div>
    </div>
  )
}

/** Wait for a big file's import (a lane job) to finish. */
export async function importFinished(job: MediaJob, progress: (line: string) => void): Promise<ImportedAsset> {
  let current = job
  for (;;) {
    if (current.status === 'done') return current.result as ImportedAsset
    if (current.status === 'failed' || current.status === 'cancelled')
      throw new Error(current.error || t('The video could not be imported.'))
    progress(`${t('Copying the video…')} ${Math.round((current.fraction ?? 0) * 100)}%`)
    await new Promise((r) => window.setTimeout(r, 700))
    current = await editorApi.importJob(job.job_id)
  }
}

/** The question when a clip was changed in the clip editor after its
 *  project was made, and the project has changes of its own (8-D6). */
function ClipChangedDialog({
  onStartAgain,
  onKeep,
  onCancel
}: {
  onStartAgain: () => void
  onKeep: () => void
  onCancel: () => void
}): JSX.Element {
  return (
    <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4" onClick={(e) => e.stopPropagation()}>
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label={t('This clip was changed since')}
        className="card w-[28rem] max-w-full space-y-3"
        onKeyDown={(e) => {
          if (e.key === 'Escape') onCancel()
        }}
      >
        <h3 className="font-semibold">{t('This clip was changed since')}</h3>
        <p className="text-sm text-muted">
          {t("You changed this clip in the clip editor after you last worked on it in the new editor. The new editor still has it as it was.")}
        </p>
        <div className="flex flex-wrap gap-2 justify-end">
          <button
            type="button"
            className="btn-accent !py-1.5 text-sm"
            autoFocus
            onClick={onStartAgain}
            title={t('Puts the clip as it is now in the new editor. Undo brings your earlier version back.')}
          >
            {t('Start again from the clip as it is now')}
          </button>
          <button
            type="button"
            className="btn-ghost !py-1.5 text-sm"
            onClick={onKeep}
            title={t("Opens your version from the new editor. Exporting it replaces the clip's changes.")}
          >
            {t('Keep the project')}
          </button>
          <button type="button" className="btn-ghost !py-1.5 text-sm" onClick={onCancel}>
            {t('Cancel')}
          </button>
        </div>
      </div>
    </div>
  )
}

/** The line the editor shows when the engine started a clip's project again
 *  from the clip as it is now (8-D12): never without a word. */
function startedAgainNotice(p: { started_again?: boolean }): string | undefined {
  return p.started_again ? t('Started again from the clip as it is now. Undo brings back your changes.') : undefined
}

/** "Edit in new editor" for one clip. */
export function EditClipInNewEditor({ clipId, className }: { clipId: number; className?: string }): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState<{ id: string; revision: number } | null>(null)
  const startAgain = async (revision: number): Promise<void> => {
    setAsking(null)
    setBusy(true)
    try {
      const p = await editorApi.restartClipProject(clipId, revision)
      openProject(p.id, startedAgainNotice(p))
    } catch (err) {
      window.alert(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <button
        type="button"
        className={className ?? 'btn-ghost w-full'}
        disabled={busy}
        title={t('Open this clip in the new editor (beta). Your changes there are saved as you make them.')}
        onClick={async (e) => {
          e.stopPropagation()
          setBusy(true)
          try {
            const p = await editorApi.clipProject(clipId)
            if (p.clip_changed_since) setAsking({ id: p.id, revision: p.revision })
            else openProject(p.id, startedAgainNotice(p))
          } catch (err) {
            window.alert(errorText(err))
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? t('Opening…') : t('Edit in new editor')}
      </button>
      {asking && (
        <ClipChangedDialog
          onStartAgain={() => void startAgain(asking.revision)}
          onKeep={() => {
            const id = asking.id
            setAsking(null)
            openProject(id)
          }}
          onCancel={() => setAsking(null)}
        />
      )}
    </>
  )
}

/** "Edit whole video" for the video open in the Library (#144). */
export function EditWholeVideo({ videoId }: { videoId: string }): JSX.Element {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        className="btn-ghost !py-1 !px-3 text-xs"
        onClick={() => setOpen(true)}
        title={t('Edit the whole video yourself in the new editor (beta): choose your own parts, no AI')}
      >
        {t('Edit whole video')}
      </button>
      {open && (
        <NewProjectDialog
          title={t('Edit whole video')}
          action={t('Open in the new editor')}
          onClose={() => setOpen(false)}
          onCreate={async (canvas) => {
            const p = await editorApi.createProject({ media: `video:${videoId}`, canvas })
            setOpen(false)
            openProject(p.id)
          }}
        />
      )}
    </>
  )
}

/** The Library's row for the new editor: the Projects list and importing a
 *  video of your own. */
export function NewEditorBar(): JSX.Element {
  const [showProjects, setShowProjects] = useState(false)
  const [importing, setImporting] = useState(false)
  const [refresh, setRefresh] = useState(0)
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={`px-3 py-1.5 rounded-lg text-sm ${showProjects ? 'bg-accent/15 text-accent' : 'bg-raised text-muted hover:text-ink'}`}
          aria-expanded={showProjects}
          onClick={() => {
            setShowProjects((s) => !s)
            setRefresh((r) => r + 1)
          }}
          title={t('Your projects in the new editor (beta)')}
        >
          {t('Projects')} {showProjects ? '▾' : '▸'}
        </button>
        <button
          type="button"
          className="btn-ghost !py-1.5 !px-3 text-sm"
          onClick={() => setImporting(true)}
          title={t('Bring in a video from this PC and edit it yourself in the new editor (beta). No AI runs.')}
        >
          {t('Import a video to edit')}
        </button>
      </div>
      {showProjects && (
        <div className="card !p-3">
          <ProjectsList refreshKey={refresh} />
        </div>
      )}
      {importing && (
        <NewProjectDialog
          title={t('Import a video to edit')}
          action={t('Choose a video…')}
          onClose={() => setImporting(false)}
          onCreate={async (canvas, progress) => {
            const path = await window.studio?.pickVideoFile?.()
            if (!path) return
            progress(t('Copying the video…'))
            const answer = await editorApi.importMedia(path)
            const asset = 'job' in answer ? await importFinished(answer.job, progress) : answer
            progress(t('Opening…'))
            const p = await editorApi.createProject({ media: asset.id, canvas })
            setImporting(false)
            openProject(p.id)
          }}
        />
      )}
    </div>
  )
}

/** The new editor's projects, last changed first: name, shape, length and
 *  when it was last changed. A project whose clip was deleted says so and
 *  still opens and exports. A project that can't be read is listed, to be
 *  deleted, never opened. */
import { useEffect, useState } from 'react'
import { t } from '../../lib/i18n'
import { editorApi, errorText } from '../../lib/editorApi'
import { formatLength } from '../../lib/editorProgram'
import type { Canvas, ProjectSummary } from '../../lib/editorTypes'
import { openProject } from '../../lib/newEditor'

/** "9:16" for the four choices, else the size. */
export function canvasLabel(c: Canvas | null): string {
  if (!c) return ''
  for (const [w, h] of [[9, 16], [16, 9], [1, 1], [4, 5]] as const) {
    if (Math.abs(c.width / c.height - w / h) < 0.01) return `${w}:${h}`
  }
  return `${c.width}×${c.height}`
}

function when(stamp: string): string {
  const d = new Date(stamp)
  return Number.isNaN(d.getTime()) ? stamp : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export default function ProjectsList({ refreshKey }: { refreshKey: number }): JSX.Element {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [error, setError] = useState('')
  const load = (): void => {
    editorApi
      .projects()
      .then((r) => {
        setProjects(r.projects)
        setError('')
      })
      .catch((e) => setError(errorText(e)))
  }
  useEffect(load, [refreshKey])

  const remove = async (p: ProjectSummary): Promise<void> => {
    if (
      !window.confirm(
        `${t('Delete this project?')}\n\n"${p.name}"\n\n${t('The clip, your videos and the files you exported stay. This can’t be undone.')}`
      )
    )
      return
    try {
      await editorApi.deleteProject(p.id)
      load()
    } catch (e) {
      setError(errorText(e))
    }
  }

  if (error) return <p className="text-xs text-red-300">{error}</p>
  if (!projects) return <p className="text-xs text-muted">{t('Checking…')}</p>
  if (projects.length === 0)
    return <p className="text-xs text-muted">{t('No projects yet. Open a clip or a whole video in the new editor to start one.')}</p>

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-muted">
          <th className="font-medium py-1 pr-3">{t('Name')}</th>
          <th className="font-medium py-1 pr-3">{t('Shape')}</th>
          <th className="font-medium py-1 pr-3">{t('Length')}</th>
          <th className="font-medium py-1 pr-3">{t('Last change')}</th>
          <th className="py-1">
            <span className="sr-only">{t('Actions')}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {projects.map((p) => (
          <tr key={p.id} className="border-t border-raised/60">
            <td className="py-1.5 pr-3 max-w-72">
              {p.damaged ? (
                <span className="text-muted">{p.name || p.id}</span>
              ) : (
                <button
                  type="button"
                  className="text-left text-ink hover:text-accent truncate max-w-full"
                  onClick={() => openProject(p.id)}
                  title={t('Open in the new editor')}
                >
                  {p.name || t('Untitled project')}
                </button>
              )}
              {p.clip_deleted && (
                <span
                  className="ml-2 text-[10px] bg-raised text-muted rounded px-1.5 py-0.5"
                  title={t('The clip this project was made from was deleted. The project still opens and exports.')}
                >
                  {t('Clip deleted')}
                </span>
              )}
              {p.damaged && (
                <span className="ml-2 text-[10px] bg-red-500/15 text-red-300 rounded px-1.5 py-0.5" title={t('This project can’t be read, so it can’t be opened. You can delete it.')}>
                  {t('Can’t be opened')}
                </span>
              )}
              {p.read_only && !p.damaged && (
                <span className="ml-2 text-[10px] bg-raised text-muted rounded px-1.5 py-0.5" title={t('This project was saved by a newer version of Clips Kitty, so it can only be viewed here.')}>
                  {t('View only')}
                </span>
              )}
            </td>
            <td className="py-1.5 pr-3 tabular-nums">{canvasLabel(p.canvas)}</td>
            <td className="py-1.5 pr-3 tabular-nums">{p.damaged ? '' : formatLength(p.duration_us)}</td>
            <td className="py-1.5 pr-3 text-muted">{when(p.updated_at)}</td>
            <td className="py-1.5 text-right whitespace-nowrap">
              <button type="button" className="text-muted hover:text-red-400 px-1" onClick={() => void remove(p)} aria-label={`${t('Delete project')} ${p.name}`} title={t('Delete project')}>
                ×
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

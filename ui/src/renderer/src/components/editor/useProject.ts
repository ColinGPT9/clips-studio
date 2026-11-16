/** One open project: loading it, changing it through step 5's operations, undo
 *  and redo.
 *
 *  Every change is a POST of operations with the revision the editor last saw
 *  (`expected_revision`). Changes are sent one after another, never two at a
 *  time, so each carries the revision the one before produced. A 409 means
 *  something else (another window, the AI, a plugin) changed the project
 *  first: the project is loaded again and the person is told in plain words.
 *  Nothing needs saving: every change is saved as it is made. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { EditorError, editorApi, errorText } from '../../lib/editorApi'
import type { Op, OpsAnswer, Project } from '../../lib/editorTypes'

export interface ProjectState {
  project: Project | null
  /** Loading failed: the engine's sentence. */
  loadError: string
  /** A plain line to show for a while: a warning, a refusal, a reload. */
  notice: string
  clearNotice: () => void
  /** Apply operations; resolves with the answer, or null when refused. */
  apply: (ops: Op[], label?: string) => Promise<OpsAnswer | null>
  undo: () => Promise<void>
  redo: () => Promise<void>
  reload: () => Promise<void>
}

export function useProject(projectId: string, openNotice?: string): ProjectState {
  const [project, setProject] = useState<Project | null>(null)
  const [loadError, setLoadError] = useState('')
  const [notice, setNotice] = useState(() => openNotice ?? '')
  const current = useRef<Project | null>(null)
  // The chain every change waits its turn on.
  const queue = useRef<Promise<unknown>>(Promise.resolve())

  const keep = (p: Project | null): void => {
    current.current = p
    setProject(p)
  }

  const reload = useCallback(async (): Promise<void> => {
    try {
      keep(await editorApi.project(projectId))
      setLoadError('')
    } catch (e) {
      setLoadError(errorText(e))
    }
  }, [projectId])

  useEffect(() => {
    keep(null)
    setLoadError('')
    void reload()
  }, [reload])

  /** An answer from ops, undo or redo, folded into the project. */
  const take = (answer: OpsAnswer): void => {
    const p = current.current
    if (!p) return
    keep({
      ...p,
      revision: answer.revision,
      document: answer.document,
      name: answer.document.name,
      can_undo: answer.can_undo,
      can_redo: answer.can_redo
    })
    if (answer.warnings?.length) setNotice(answer.warnings.join(' '))
  }

  const refused = async (e: unknown): Promise<void> => {
    if (e instanceof EditorError && e.conflict) {
      await reload()
      setNotice(
        t('This project was changed somewhere else, so it was loaded again. Your last change was not made: please make it again.')
      )
    } else if (e instanceof EditorError && e.status === 423) {
      setNotice(t('This project was saved by a newer version of Clips Kitty, so it can only be viewed here.'))
    } else {
      setNotice(errorText(e))
    }
  }

  const inTurn = <T,>(work: () => Promise<T>): Promise<T> => {
    const next = queue.current.then(work, work)
    queue.current = next.catch(() => undefined)
    return next
  }

  const apply = useCallback(
    (ops: Op[], label?: string): Promise<OpsAnswer | null> =>
      inTurn(async () => {
        const p = current.current
        if (!p) return null
        try {
          const answer = await editorApi.ops(p.id, p.revision, ops, label)
          take(answer)
          return answer
        } catch (e) {
          await refused(e)
          return null
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [reload]
  )

  const step = (which: 'undo' | 'redo') => (): Promise<void> =>
    inTurn(async () => {
      const p = current.current
      if (!p) return
      if (which === 'undo' ? !p.can_undo : !p.can_redo) return
      try {
        take(await editorApi[which](p.id, p.revision))
      } catch (e) {
        await refused(e)
      }
    })

  return {
    project,
    loadError,
    notice,
    clearNotice: () => setNotice(''),
    apply,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    undo: useCallback(step('undo'), [reload]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    redo: useCallback(step('redo'), [reload]),
    reload
  }
}

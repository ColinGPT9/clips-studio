/** The project's captions and text as the engine draws them, at the
 *  project's revision (step 9): the caption lines in program time (GET
 *  /projects/{id}/captions, for the viewer's caption layer, the caption lane
 *  and the transcript list) and the export's own ASS file (its
 *  urls.captions_ass, for JASSUB). Asked for again after every change; an
 *  older answer never replaces a newer one. */
import { useEffect, useState } from 'react'
import { EditorError, editorApi, errorText } from '../../lib/editorApi'
import type { CaptionLane } from '../../lib/editorTypes'

export interface CaptionLayerState {
  /** The lines at `lane.revision`, or null before the first answer. */
  lane: CaptionLane | null
  /** The ASS file of the same revision, or null before the first answer. */
  ass: string | null
  /** The engine's sentence when they couldn't be had. */
  error: string
}

export function useCaptionLayer(projectId: string, revision: number): CaptionLayerState {
  const [state, setState] = useState<CaptionLayerState>({ lane: null, ass: null, error: '' })
  useEffect(() => {
    if (revision < 0) return // the project isn't loaded yet
    const ask = new AbortController()
    // A short wait, so a burst of changes (arrow keys) asks once.
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const lane = await editorApi.captionLines(projectId, revision, ask.signal)
          const ass = await editorApi.captionsAss(lane.urls.captions_ass, ask.signal)
          if (!ask.signal.aborted) setState({ lane, ass, error: '' })
        } catch (e) {
          if (ask.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
          // The project moved on: the newer revision's answer is on its way.
          if (e instanceof EditorError && e.status === 409) return
          setState((s) => ({ ...s, error: errorText(e) }))
        }
      })()
    }, 60)
    return () => {
      window.clearTimeout(timer)
      ask.abort()
    }
  }, [projectId, revision])
  return state
}

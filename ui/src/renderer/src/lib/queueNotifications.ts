import type { StudioEvent } from './types'
import { useEvents } from './useEvents'

/** Desktop notifications for a queue running unattended.
 *
 *  The whole reason the queue exists is that a batch takes hours and the user
 *  walks away, so this must be mounted at the app shell — not on the queue
 *  page, which is precisely the screen they will not be looking at.
 *
 *  Suppressed while the window is focused: a toast telling you what you are
 *  already watching is just noise. */

const FINISH_KEY = 'notify-on-finish'
const EMPTY_KEY = 'notify-on-queue-empty'

export function notifyOnFinish(): boolean {
  return localStorage.getItem(FINISH_KEY) !== 'false'
}

export function notifyOnQueueEmpty(): boolean {
  return localStorage.getItem(EMPTY_KEY) !== 'false'
}

export function setNotifyOnFinish(on: boolean): void {
  localStorage.setItem(FINISH_KEY, String(on))
}

export function setNotifyOnQueueEmpty(on: boolean): void {
  localStorage.setItem(EMPTY_KEY, String(on))
}

function send(title: string, body: string): void {
  if (document.hasFocus()) return
  void window.studio?.notify?.(title, body)
}

export function useQueueNotifications(): void {
  useEvents((e: StudioEvent) => {
    if (e.type !== 'job') return
    // Re-renders and subtitle exports finish in seconds while the user is
    // sitting there; only a video worth walking away from is worth a toast.
    if (e.job_type && e.job_type !== 'process') return
    if (e.status !== 'done' && e.status !== 'failed') return

    const name = e.title || 'A video'
    const remaining = typeof e.remaining === 'number' ? e.remaining : null

    if (notifyOnFinish()) {
      if (e.status === 'failed') {
        send('Clips Kitty', `${name} failed — ${e.error || 'see the queue for details'}`)
      } else {
        const left =
          remaining === null
            ? ''
            : remaining === 0
              ? ''
              : ` ${remaining} ${remaining === 1 ? 'video' : 'videos'} remaining.`
        send('Clips Kitty', `${name} has finished processing.${left}`)
      }
    }

    // The batch is done. Sent separately from the per-video toast so turning
    // per-video notifications off still leaves the one that matters most.
    if (remaining === 0 && notifyOnQueueEmpty()) {
      send('Clips Kitty', 'All queued videos have finished processing.')
    }
  })
}

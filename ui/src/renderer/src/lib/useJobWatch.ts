import { useEffect, useRef } from 'react'
import { api } from './api'

/** Catching up when the event stream misses something.
 *
 *  Everything that reacts to a job finishing used to trust one WebSocket
 *  event, and that event is not guaranteed. Two ways it goes missing:
 *
 *  - the socket is down at the moment the job ends — a reconnect, a reload,
 *    the backend restarting — so nothing is delivered;
 *  - the server drops it on purpose. Each client gets a 200-event queue and
 *    `except asyncio.QueueFull: pass` (server/events.py), because one stalled
 *    client must not block the broadcaster for everyone. A render emits
 *    progress for minutes, and nothing protects the terminal event from being
 *    the one discarded.
 *
 *  ProcessingBar learned this first and grew the fix inline: when events go
 *  quiet, stop believing them and ask the server what is actually running.
 *  The Dashboard and the clip list never got it, so a finished run could leave
 *  them showing an empty page until someone reloaded — which reads as "no
 *  clips were created" and turns into a bug report.
 *
 *  The server is the authority; the event stream is just faster.
 */

// Long enough that a slow stage — a big download, a long analysis chunk —
// never triggers a pointless check.
export const SILENCE_BEFORE_RECHECK_MS = 45_000
export const RECHECK_EVERY_MS = 10_000

interface Options {
  /** Only watch while this is true, so an idle page never polls. */
  active: boolean
  /** Called when the stream went quiet and the server says nothing is running
   *  any more — i.e. something finished and the event was missed. */
  onSettled: () => void
  /** Bump this whenever an event arrives, to restart the quiet timer. */
  lastEventAt: React.MutableRefObject<number>
}

export function useJobWatch({ active, onSettled, lastEventAt }: Options): void {
  // Held in a ref so a caller can pass an inline closure without tearing the
  // interval down and rebuilding it on every render.
  const settled = useRef(onSettled)
  useEffect(() => {
    settled.current = onSettled
  })

  useEffect(() => {
    if (!active) return
    const id = setInterval(() => {
      if (Date.now() - lastEventAt.current < SILENCE_BEFORE_RECHECK_MS) return
      void api
        .jobs()
        .then((jobs) => {
          const live = jobs.some((j) => j.status === 'running' || j.status === 'queued')
          if (!live) {
            settled.current()
          } else {
            // Something IS running — the socket just missed events. Reset the
            // clock so we re-check on the next quiet stretch, not every tick.
            lastEventAt.current = Date.now()
          }
        })
        .catch(() => {
          /* backend unreachable — say nothing rather than wrongly clear */
        })
    }, RECHECK_EVERY_MS)
    return () => clearInterval(id)
  }, [active, lastEventAt])
}

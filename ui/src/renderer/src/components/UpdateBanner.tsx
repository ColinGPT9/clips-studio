import { useEffect, useState } from 'react'

/** "813 MB" or "6.2 GB": update downloads span both. */
function size(n?: number): string {
  if (!n) return ''
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(0)} MB`
}

/** Strips HTML tags from release notes, which arrive as markup from the
 *  update feed and are shown here as plain text.
 *
 *  A single `.replace(/<[^>]+>/g, '')` is not enough: removing one layer of
 *  tags can reveal another that the first pass stepped over, so
 *  `<<script>script>` comes out as `<script>`. Repeating until the string
 *  stops changing is the fix.
 *
 *  React escapes text children, so this was never an injection route on its
 *  own — nothing here is passed to dangerouslySetInnerHTML. It is a display
 *  cleanup, and it should still be a correct one. The iteration cap stops a
 *  pathological string turning it into a long loop. */
function stripTags(html: string): string {
  let out = html
  for (let i = 0; i < 20; i++) {
    const next = out.replace(/<[^>]*>/g, '')
    if (next === out) return out
    out = next
  }
  return out.replace(/[<>]/g, '')
}

/** Tells the user a new version exists, and installs it when they say so.
 *
 *  Deliberately a strip at the top rather than a modal: an update is never
 *  more urgent than the render someone is in the middle of. Nothing
 *  downloads or installs without being asked — the payload is gigabytes, and
 *  a surprise restart mid-render loses work. */
export default function UpdateBanner(): JSX.Element | null {
  const [s, setS] = useState<UpdateState | null>(null)
  const [notesOpen, setNotesOpen] = useState(false)

  // The preload is only rebuilt when the window is recreated, while the
  // renderer hot-reloads on every save — so during development this can run
  // against a preload that predates window.studio.update. Throwing here
  // would unmount the entire app and leave a blank window, which is a
  // catastrophic result for a feature that is only ever a notification.
  useEffect(() => {
    const updater = window.studio?.update
    if (!updater) return
    return updater.onState(setS)
  }, [])

  // Nothing to say when there's no update, we're mid-check, we're in dev, or
  // the Store is handling updates and there is nothing for us to offer.
  if (!s || s.state === 'none' || s.state === 'checking' || s.state === 'dev' || s.state === 'store')
    return null
  // A failed background check is not the user's problem; Settings shows it.
  if (s.state === 'error') return null

  const bar = 'w-full px-4 py-2.5 flex items-center gap-3 text-sm border-b'

  if (s.state === 'downloading') {
    // Two downloads: the small setup, then the app files. Between them the
    // updater is briefly silent, and a bar parked at 100% reads as frozen, so
    // that gap gets its own words instead of a finished-looking number.
    const packagePhase = s.phase === 'package'
    const pct = s.percent ?? 0
    const betweenDownloads = !packagePhase && pct >= 100
    const label = packagePhase
      ? 'Downloading the app files…'
      : betweenDownloads
        ? 'Preparing the app files download…'
        : 'Downloading update…'
    const detail = betweenDownloads
      ? 'This can take a while'
      : packagePhase && !s.total
        ? `${size(s.transferred)} downloaded`
        : `${pct}%${s.total ? ` · ${size(s.transferred)} of ${size(s.total)}` : ''}`
    return (
      <div className={`${bar} bg-surface border-raised/60`}>
        <span className="shrink-0 font-medium">{label}</span>
        <div className="flex-1 h-2 rounded-full bg-raised overflow-hidden max-w-md">
          <div
            className={`h-full bg-accent rounded-full transition-[width] duration-500${
              betweenDownloads ? ' animate-pulse' : ''
            }`}
            style={{ width: `${betweenDownloads ? 100 : Math.max(2, pct)}%` }}
          />
        </div>
        <span className="text-xs text-muted tabular-nums shrink-0">{detail}</span>
      </div>
    )
  }

  if (s.state === 'ready') {
    return (
      <div className={`${bar} bg-accent/10 border-accent/30`}>
        <span className="font-medium">Version {s.version} is ready to install.</span>
        <span className="text-xs text-muted">
          Clips Kitty will close and reopen. Finish anything that&apos;s rendering first.
        </span>
        <button
          className="btn-accent !py-1 ml-auto shrink-0"
          onClick={() => void window.studio.update.install()}
        >
          Restart &amp; install
        </button>
      </div>
    )
  }

  // state === 'available'
  return (
    <div className="bg-accent/10 border-b border-accent/30">
      <div className={`${bar} border-transparent`}>
        <span className="font-medium">Version {s.version} is available.</span>
        {s.notes && (
          <button
            className="text-xs text-accent hover:underline"
            onClick={() => setNotesOpen((v) => !v)}
          >
            {notesOpen ? "Hide what's new" : "What's new"}
          </button>
        )}
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <button
            className="text-xs text-muted hover:text-ink"
            onClick={() => void window.studio.update.skip(s.version ?? '')}
          >
            Skip this version
          </button>
          <button
            className="btn-accent !py-1"
            onClick={() => void window.studio.update.download()}
          >
            Download
          </button>
        </div>
      </div>
      {notesOpen && s.notes && (
        <div className="px-4 pb-3 -mt-1">
          <div className="max-h-48 overflow-y-auto text-xs text-muted whitespace-pre-wrap leading-relaxed bg-base/40 rounded-lg p-3">
            {stripTags(s.notes)}
          </div>
        </div>
      )}
    </div>
  )
}

import { Component, type ErrorInfo, type ReactNode } from 'react'

/** Last line of defence for the whole window.
 *
 *  React unmounts the entire tree on an unhandled render error, so without
 *  this the app becomes a blank white window with nothing to click and
 *  nothing to read — the worst possible failure, because it looks identical
 *  to a hang and tells the user nothing.
 *
 *  A real cause of exactly that: the preload script is only rebuilt when the
 *  window is recreated, while the renderer hot-reloads on every save. Editing
 *  both leaves new UI calling a `window.studio` method the running preload
 *  does not have yet.
 *
 *  Nothing here can be lost by crashing: videos, clips and edits all live in
 *  the database and on disk, not in this process.
 */
export default class AppBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Clips Studio crashed:', error, info.componentStack)
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children

    const message = String(this.state.error?.message ?? this.state.error)
    // A stale preload is common enough in development to name directly,
    // rather than leaving someone to decode "undefined is not an object".
    const stalePreload = /window\.studio|Cannot read (properties|property)/i.test(message)

    return (
      <div className="h-screen grid place-items-center p-8 bg-base text-ink">
        <div className="max-w-lg space-y-4">
          <h1 className="text-xl font-bold">Clips Studio hit a problem</h1>
          <p className="text-sm text-muted leading-relaxed">
            The window failed to draw. Your videos, clips and edits are safe — they live on
            disk and in the database, not in this window.
          </p>
          {stalePreload && (
            <p className="text-sm text-muted leading-relaxed">
              This usually means the app was updated while running. Fully closing and
              reopening it should fix it.
            </p>
          )}
          <pre className="text-xs text-muted/70 bg-surface border border-raised/60 rounded-lg p-3 whitespace-pre-wrap break-words">
            {message}
          </pre>
          <div className="flex gap-2">
            <button className="btn-accent" onClick={() => window.location.reload()}>
              Reload
            </button>
            <button className="btn-ghost" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
          </div>
          <p className="text-xs text-muted">
            If it keeps happening, the Feedback button in the app sends this with the
            diagnostics attached.
          </p>
        </div>
      </div>
    )
  }
}

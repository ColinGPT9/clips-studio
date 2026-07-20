import { Component, type ErrorInfo, type ReactNode } from 'react'

/** Keeps an optional feature from taking the editor down with it.
 *
 *  Clipping is the app; multilingual publishing is an extra. A crash inside
 *  an extra must cost you that panel and nothing else — not the timeline,
 *  not your unsaved trims, not the clip. React unmounts the whole tree on an
 *  unhandled render error, so without a boundary here a bug in a side
 *  feature would blank the entire editor.
 */
export default class FeatureBoundary extends Component<
  { name: string; children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[${this.props.name}] crashed and was isolated:`, error, info.componentStack)
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children
    return (
      <div className="border border-warn/40 bg-warn/5 rounded-lg p-3 space-y-2 text-xs">
        <p className="font-medium">{this.props.name} hit a problem.</p>
        <p className="text-muted">
          Your clip and your edits are untouched — this panel only reads the clip and writes new
          files beside it. Everything else in the editor still works.
        </p>
        <p className="text-muted/70 font-mono break-words">{String(this.state.error.message)}</p>
        <button
          className="btn-ghost !py-1 text-xs"
          onClick={() => this.setState({ error: null })}
        >
          Try again
        </button>
      </div>
    )
  }
}

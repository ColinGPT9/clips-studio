import type { SuggestionCard } from '../lib/editSuggestions'

/** The edits Marketplace plugins suggested for this clip, above the
 *  timeline: one card per suggestion in the order the plugins ran, with
 *  what it does, the plugin's reason and what the creator should know
 *  before pressing anything. The words come from lib/editSuggestions.ts;
 *  like the rest of the editor they are English.
 *
 *  Use draws a suggestion on the timeline and renders nothing; it goes into
 *  the clip when the creator applies their edits. */
export default function EditSuggestions({
  cards,
  hidden,
  busy,
  makeAgainOff,
  onUse,
  onHide,
  onTakeBack,
  onMakeAgain,
  onShowHidden
}: {
  cards: SuggestionCard[]
  /** How many suggestions are hidden. */
  hidden: number
  busy: boolean
  /** Why Make it again with my edits is off, when it is ('' when it isn't). */
  makeAgainOff: string
  onUse: (id: string) => void
  onHide: (id: string) => void
  onTakeBack: (id: string) => void
  onMakeAgain: () => void
  onShowHidden: () => void
}): JSX.Element | null {
  if (cards.length === 0 && hidden === 0) return null
  const button = 'bg-raised px-2.5 py-1 rounded-md hover:bg-raised/70 disabled:opacity-40'
  return (
    <section className="space-y-2" aria-label="Suggested edits">
      {cards.map((card) => (
        <div key={card.id} className="border border-raised/60 rounded-lg p-2.5 space-y-1 text-xs">
          <p className="font-medium text-ink">{card.title}</p>
          {card.parts.map((part) => (
            <p key={part} className="text-muted">
              {part}
            </p>
          ))}
          {card.reason && <p className="text-muted italic">{card.reason}</p>}
          {card.notes.map((note) => (
            <p key={note} className="text-warn">
              {note}
            </p>
          ))}
          {card.status && <p className="text-accent">{card.status}</p>}
          {card.useOff && <p className="text-warn">{card.useOff}</p>}
          <div className="flex gap-2 flex-wrap pt-1">
            {card.actions.includes('use') && (
              <button
                className={`${button} text-accent font-medium`}
                disabled={busy || Boolean(card.useOff)}
                onClick={() => onUse(card.id)}
                title="Draw this suggestion on the timeline. Nothing renders until you apply your edits."
              >
                Use
              </button>
            )}
            {card.actions.includes('take_back') && (
              <button
                className={button}
                disabled={busy}
                onClick={() => onTakeBack(card.id)}
                title="Take out only what this suggestion put in. Your own changes stay."
              >
                Take it back
              </button>
            )}
            {card.actions.includes('make_again') && (
              <button
                className={button}
                disabled={busy || Boolean(makeAgainOff)}
                onClick={onMakeAgain}
                title={makeAgainOff || 'Make this clip again with the edits saved for it'}
              >
                Make it again with my edits
              </button>
            )}
            {card.actions.includes('hide') && (
              <button
                className="text-muted hover:text-ink px-1 disabled:opacity-40"
                disabled={busy}
                onClick={() => onHide(card.id)}
                title="Put this suggestion away. A later run that suggests the same keeps it hidden."
              >
                Hide
              </button>
            )}
          </div>
        </div>
      ))}
      {hidden > 0 && (
        <p className="text-xs text-muted">
          {hidden} {hidden === 1 ? 'hidden suggestion' : 'hidden suggestions'} ·{' '}
          <button className="text-accent hover:underline disabled:opacity-40" disabled={busy} onClick={onShowHidden}>
            Show
          </button>
        </p>
      )}
    </section>
  )
}

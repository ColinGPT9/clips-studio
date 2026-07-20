import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'

/** Words the translator must leave alone.
 *
 *  The app already guesses these from what it learned about the creator —
 *  channel name, games, collaborators — but guessing gets it wrong in both
 *  directions: it protects an ordinary word from a Title Case heading, and
 *  it has never heard of this week's sponsor. This is the manual override.
 *
 *  Changes apply to the NEXT translation; text already translated keeps
 *  whatever it says until that language is translated again.
 */
export default function GlossaryEditor({ clipId }: { clipId: number }): JSX.Element {
  const [kept, setKept] = useState<string[]>([])
  const [ignored, setIgnored] = useState<string[]>([])
  const [adding, setAdding] = useState('')
  const [busy, setBusy] = useState(false)

  const load = (): void => {
    api
      .glossary(clipId)
      .then((g) => {
        setKept(g.protected)
        setIgnored(g.ignored)
      })
      .catch(() => {})
  }
  useEffect(load, [clipId])

  const rule = async (term: string, r: 'protect' | 'ignore' | 'auto'): Promise<void> => {
    setBusy(true)
    try {
      await api.ruleTerm(clipId, term, r)
      load()
    } catch {
      /* leave the list as it was */
    } finally {
      setBusy(false)
    }
  }

  const add = async (): Promise<void> => {
    const term = adding.trim()
    if (!term) return
    setAdding('')
    await rule(term, 'protect')
  }

  return (
    <details className="border border-raised/60 rounded-lg">
      <summary className="px-3 py-2 text-xs cursor-pointer hover:bg-raised/40 rounded-lg">
        {t('Words to keep as they are')}
        <span className="text-muted ml-2">{kept.length}</span>
      </summary>
      <div className="p-3 pt-0 space-y-2">
        <p className="text-[11px] text-muted/80">
          {t(
            'Names, sponsors and in-jokes the translator should not touch. Click one to let it be translated normally. Applies to the next translation.'
          )}
        </p>

        <div className="flex gap-1 flex-wrap">
          {kept.map((term) => (
            <button
              key={term}
              disabled={busy}
              onClick={() => rule(term, 'ignore')}
              className="px-2 py-0.5 rounded-full text-[11px] border border-accent/50 text-accent hover:line-through disabled:opacity-50"
              title="Click to let this word be translated normally"
            >
              {term}
            </button>
          ))}
          {kept.length === 0 && (
            <span className="text-[11px] text-muted/70">{t('Nothing protected yet.')}</span>
          )}
        </div>

        <div className="flex gap-1.5">
          <input
            className="input !py-1 text-xs"
            value={adding}
            placeholder={t('Add a word or name…')}
            onChange={(e) => setAdding(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                add()
              }
            }}
          />
          <button className="btn-ghost !py-1 text-xs shrink-0" disabled={busy || !adding.trim()} onClick={add}>
            {t('Keep it')}
          </button>
        </div>

        {ignored.length > 0 && (
          <div className="space-y-1">
            <p className="text-[11px] text-muted/70">{t('Being translated normally')}</p>
            <div className="flex gap-1 flex-wrap">
              {ignored.map((term) => (
                <button
                  key={term}
                  disabled={busy}
                  onClick={() => rule(term, 'auto')}
                  className="px-2 py-0.5 rounded-full text-[11px] border border-raised text-muted hover:text-ink disabled:opacity-50"
                  title="Undo — let the app decide about this word again"
                >
                  {term} ✕
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </details>
  )
}

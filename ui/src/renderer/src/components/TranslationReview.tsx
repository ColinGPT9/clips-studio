import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { CaptionLine, Translation } from '../lib/types'
import { t } from '../lib/i18n'

/** Read and correct the machine translation before it is written anywhere.
 *
 *  Burning captions into a video and dubbing it are slow and permanent, so
 *  the text is shown next to the English it came from and every line is
 *  editable. Saved corrections are marked `edited` server-side, which both
 *  protects them from a later re-translation and means Export reuses them
 *  instead of calling the model again.
 *
 *  Timings are not editable here: a translated line inherits the timing of
 *  the source line it replaces, so the caption stays in sync with the
 *  speech no matter how the wording changes. */
export default function TranslationReview({
  clipId,
  languages,
  nameOf,
  reloadKey,
  onLoaded,
  onPreview,
  previewing
}: {
  clipId: number
  languages: string[]
  nameOf: (code: string) => string
  reloadKey: number
  onLoaded: (languages: string[]) => void
  /** Show this language's captions over the editor's video, or clear it. */
  onPreview?: (p: { language: string; lines: CaptionLine[]; source: CaptionLine[] } | null) => void
  previewing?: string | null
}): JSX.Element | null {
  const [source, setSource] = useState<CaptionLine[]>([])
  const [items, setItems] = useState<Translation[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, string[]>>({})
  const [saving, setSaving] = useState('')
  const [note, setNote] = useState('')

  useEffect(() => {
    api
      .translations(clipId)
      .then((r) => {
        setSource(r.source)
        setItems(r.translations)
        onLoaded(r.translations.map((i) => i.language))
      })
      .catch(() => onLoaded([]))
  }, [clipId, reloadKey])

  // Keep the on-video preview in step with what is being typed, so a fix can
  // be checked against the video immediately rather than after saving.
  useEffect(() => {
    if (!onPreview || !previewing) return
    const item = items.find((i) => i.language === previewing)
    if (!item) return
    const texts = draft[item.language] ?? item.lines.map((l) => l.text)
    onPreview({
      language: item.language,
      lines: item.lines.map((l, i) => ({ ...l, text: texts[i] ?? l.text })),
      source
    })
  }, [draft, items, previewing, source])

  // Never leave captions floating over the video after this panel goes away.
  useEffect(() => () => onPreview?.(null), [clipId])

  // Only the languages currently picked, in the order they were picked.
  const shown = languages
    .map((code) => items.find((i) => i.language === code))
    .filter((i): i is Translation => Boolean(i))

  if (shown.length === 0) return null

  const textsFor = (item: Translation): string[] =>
    draft[item.language] ?? item.lines.map((l) => l.text)

  const edit = (language: string, i: number, value: string, item: Translation): void => {
    const next = [...textsFor(item)]
    next[i] = value
    setDraft((d) => ({ ...d, [language]: next }))
  }

  const save = async (item: Translation): Promise<void> => {
    setSaving(item.language)
    setNote('')
    try {
      const texts = textsFor(item)
      const lines = item.lines.map((l, i) => ({ ...l, text: texts[i] ?? l.text }))
      await api.saveTranslation(clipId, item.language, lines)
      setItems((prev) =>
        prev.map((p) => (p.language === item.language ? { ...p, lines, edited: true } : p))
      )
      setDraft((d) => {
        const { [item.language]: _dropped, ...rest } = d
        return rest
      })
      setNote(`${nameOf(item.language)} ${t('saved — Export will use your version.')}`)
    } catch (e) {
      setNote(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving('')
    }
  }

  const discard = async (item: Translation): Promise<void> => {
    setNote('')
    try {
      await api.discardTranslation(clipId, item.language)
      const rest = items.filter((p) => p.language !== item.language)
      setItems(rest)
      onLoaded(rest.map((i) => i.language))
      setDraft((d) => {
        const { [item.language]: _dropped, ...keep } = d
        return keep
      })
      setOpen(null)
      setNote(`${nameOf(item.language)} ${t('discarded — translate again for a fresh version.')}`)
    } catch (e) {
      setNote(`Error: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <div className="border-t border-raised/60 pt-2 space-y-1.5">
      <p className="label">{t('Review the translation')}</p>
      <p className="text-xs text-muted">
        {t('Fix anything the model got wrong. Nothing is written until you export.')}
      </p>

      {shown.map((item) => {
        const isOpen = open === item.language
        const dirty = Boolean(draft[item.language])
        return (
          <div key={item.language} className="border border-raised/60 rounded-md">
            <button
              className="w-full text-left px-2 py-1.5 text-xs flex items-center gap-2 hover:bg-raised/40 rounded-md"
              onClick={() => setOpen(isOpen ? null : item.language)}
              aria-expanded={isOpen}
            >
              <span className="font-medium">{nameOf(item.language)}</span>
              <span className="text-muted">
                {item.lines.length} {t('lines')}
              </span>
              {item.edited && <span className="text-accent">✓ {t('edited by you')}</span>}
              {dirty && <span className="text-warn">{t('unsaved')}</span>}
              <span className="ml-auto" aria-hidden>
                {isOpen ? '▾' : '▸'}
              </span>
            </button>

            {/* Watch the translation over the actual video before committing
                to a burn. Uses the live text you have typed, not the saved
                copy, so a fix can be checked before it is even saved. */}
            {onPreview && (
              <div className="px-2 pb-1.5 -mt-0.5">
                <button
                  className={`text-xs px-2 py-0.5 rounded-md border ${
                    previewing === item.language
                      ? 'border-accent text-accent bg-accent/10'
                      : 'border-raised text-muted hover:text-ink'
                  }`}
                  onClick={() =>
                    previewing === item.language
                      ? onPreview(null)
                      : onPreview({
                          language: item.language,
                          lines: item.lines.map((l, i) => ({
                            ...l,
                            text: textsFor(item)[i] ?? l.text
                          })),
                          source
                        })
                  }
                  title="Play the clip with these captions drawn over it, exactly where a burn would put them"
                >
                  {previewing === item.language
                    ? `◉ ${t('Showing on video')}`
                    : `▶ ${t('Show on video')}`}
                </button>
              </div>
            )}

            {isOpen && (
              <div className="p-2 space-y-1.5">
                <div className="max-h-64 overflow-y-auto pr-1 space-y-1">
                  {item.lines.map((line, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-[10px] text-muted tabular-nums w-10 shrink-0 pt-1.5">
                        {line.start.toFixed(1)}s
                      </span>
                      <span
                        className="text-[11px] text-muted w-40 shrink-0 pt-1.5 leading-tight"
                        title={source[i]?.text ?? ''}
                      >
                        {source[i]?.text ?? ''}
                      </span>
                      <input
                        className="input !py-1 text-xs"
                        aria-label={`${nameOf(item.language)} caption at ${line.start.toFixed(1)} seconds`}
                        value={textsFor(item)[i] ?? ''}
                        onChange={(e) => edit(item.language, i, e.target.value, item)}
                      />
                    </div>
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    className="btn-accent !py-1 text-xs flex-1"
                    disabled={!dirty || saving === item.language}
                    onClick={() => save(item)}
                  >
                    {saving === item.language
                      ? t('Saving…')
                      : `${t('Save corrections')} — ${nameOf(item.language)}`}
                  </button>
                  <button
                    className="text-xs text-muted hover:text-ink px-2 py-1 shrink-0"
                    onClick={() => discard(item)}
                    title="Throw this translation away so the next Translate run redoes it from scratch"
                  >
                    {t('Start over')}
                  </button>
                </div>
              </div>
            )}
          </div>
        )
      })}
      {note && <p className="text-xs text-muted">{note}</p>}
    </div>
  )
}

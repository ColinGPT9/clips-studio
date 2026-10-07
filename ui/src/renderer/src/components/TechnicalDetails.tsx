import { Fragment } from 'react'
import { t } from '../lib/i18n'

/** The exact facts behind a plain sentence (where a pipeline's files come
 *  from, the setting behind a switch, what the engine noticed in a
 *  pipeline's files), for a developer or a bug report. Folded away until
 *  opened, so a creator reads the sentence and can still check the source.
 *  Labels and values are shown as they are, untranslated. Rows without a
 *  value are left out; with none left, nothing is shown. */
export default function TechnicalDetails({
  rows
}: {
  rows: [label: string, value: string | string[] | null | undefined][]
}): JSX.Element | null {
  const shown = rows.filter(([, v]) => (Array.isArray(v) ? v.length > 0 : Boolean(v)))
  if (shown.length === 0) return null
  return (
    <details className="text-xs text-muted">
      <summary className="cursor-pointer hover:text-ink w-fit">{t('Technical details')}</summary>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {shown.map(([label, value]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd className="min-w-0 break-all select-all">
              {Array.isArray(value)
                ? value.map((line, i) => (
                    <span key={i} className="block">
                      {String(line)}
                    </span>
                  ))
                : String(value)}
            </dd>
          </Fragment>
        ))}
      </dl>
    </details>
  )
}

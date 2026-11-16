/** Settings → Storage's line for the new editor's cache (step 6's waveforms,
 *  thumbnails, smaller copies for playing and previews), with "Clear editor
 *  cache". Shown only while "New editor (beta)" is on; a file a project or
 *  an open video is using right now is kept, and everything cleared is made
 *  again when it is next needed. */
import { useEffect, useState } from 'react'
import { t } from '../../lib/i18n'
import { editorApi, errorText } from '../../lib/editorApi'
import { formatBytes } from '../../lib/exportJobs'

export default function EditorCacheRow(): JSX.Element | null {
  const [size, setSize] = useState<{ files: number; bytes: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const load = (): void => {
    editorApi
      .cacheSize()
      .then((s) => setSize({ files: s.files, bytes: s.bytes }))
      .catch(() => setSize(null))
  }
  useEffect(load, [])
  if (!size) return null
  return (
    <div className="flex items-center gap-3 text-xs border-t border-raised/60 pt-2">
      <span
        className="text-muted flex-1 truncate"
        title={t("The new editor's waveforms, thumbnails, smaller copies for playing and previews. They are made again when needed.")}
      >
        {t('New editor files')}: {size.files} · {formatBytes(size.bytes)}
      </span>
      {note && <span className="text-muted">{note}</span>}
      <button
        type="button"
        className="btn-ghost !py-1 text-xs"
        disabled={busy || size.files === 0}
        title={t('Delete them now. Files a project is using right now are kept.')}
        onClick={async () => {
          setBusy(true)
          setNote('')
          try {
            const r = await editorApi.clearCache()
            setNote(`${t('Freed')} ${formatBytes(r.bytes_freed)}`)
            load()
          } catch (e) {
            setNote(errorText(e))
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? t('Cleaning…') : t('Clear editor cache')}
      </button>
    </div>
  )
}

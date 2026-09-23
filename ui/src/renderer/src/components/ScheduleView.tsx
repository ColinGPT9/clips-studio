import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { platformLabel } from '../lib/uploadpost'

/** What is due, and what actually happened.
 *
 *  This exists because of a real run: 37 clips went out at once, five posted
 *  and thirty-two failed, and the app could say nothing about any of it. The
 *  rows sat at "processing" forever because nothing ever asked again, so a
 *  queue moving slowly looked exactly like a batch that had died. The answer
 *  was counting Shorts on YouTube by hand.
 *
 *  Refresh asks the provider what became of everything still in the air. It
 *  is a button rather than a poll because these runs stretch over days: a
 *  timer would spend its life asking about posts due on Thursday.
 */
const STATE_STYLE: Record<string, string> = {
  published: 'text-success',
  failed: 'text-error',
  skipped: 'text-muted',
  processing: 'text-warn',
  queued: 'text-warn'
}

export default function ScheduleView({ onClose }: { onClose: () => void }): JSX.Element {
  const [posts, setPosts] = useState<
    {
      clip_id: number
      platform: string
      state: string
      scheduled_for: string
      post_url: string
      error: string
      title: string
    }[]
  >([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = async (): Promise<void> => {
    try {
      const got = await api.woopSocialSchedule()
      setPosts(got.posts)
    } catch {
      /* provider off or no key: an empty schedule is the honest answer */
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const refresh = async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    setNote('')
    try {
      const got = await api.woopSocialRefresh()
      setNote(
        got.checked === 0
          ? t('Nothing is waiting.')
          : `${t('Checked')} ${got.checked}, ${t('updated')} ${got.updated}, ${got.still_waiting} ${t('still waiting')}.`
      )
      await load()
    } catch (e) {
      setNote(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const done = posts.filter((p) => p.state === 'published').length
  const failed = posts.filter((p) => p.state === 'failed').length
  const waiting = posts.filter(
    (p) => p.state === 'queued' || p.state === 'processing' || p.state === 'sending'
  ).length

  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Posting schedule"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-2xl space-y-3 max-h-[85vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-semibold text-lg">{t('Posting schedule')}</h3>
            <p className="text-xs text-muted mt-1">
              {posts.length
                ? `${done} ${t('posted')}, ${waiting} ${t('waiting')}, ${failed} ${t('failed')}.`
                : t('Nothing scheduled yet.')}
            </p>
          </div>
          <button className="btn-ghost !py-1 !px-3 text-sm shrink-0" onClick={() => void refresh()}>
            {busy ? t('Checking…') : t('Refresh')}
          </button>
        </div>

        {note && <p className="text-xs text-muted">{note}</p>}

        <div className="flex-1 overflow-y-auto border border-raised rounded-lg divide-y divide-raised">
          {loading && <p className="p-3 text-sm text-muted">{t('Loading…')}</p>}
          {!loading && posts.length === 0 && (
            <p className="p-3 text-sm text-muted">
              {t('Publish some clips with a daily budget and they will appear here.')}
            </p>
          )}
          {posts.map((p) => (
            <div
              key={`${p.clip_id}-${p.platform}`}
              className="flex items-center gap-3 px-3 py-2 text-sm"
            >
              <span className="tabular-nums text-xs text-muted w-32 shrink-0">
                {p.scheduled_for ? new Date(p.scheduled_for).toLocaleString() : '-'}
              </span>
              <span className="flex-1 min-w-0 truncate" title={p.title}>
                {p.title}
              </span>
              <span className="text-xs text-muted shrink-0">{platformLabel(p.platform)}</span>
              <span className={`text-xs shrink-0 w-20 ${STATE_STYLE[p.state] || 'text-muted'}`}>
                {p.state}
              </span>
              {p.post_url ? (
                <button
                  className="text-xs text-accent shrink-0"
                  onClick={() => void window.studio.openExternal(p.post_url)}
                >
                  {t('Open')}
                </button>
              ) : (
                <span className="w-10 shrink-0" />
              )}
            </div>
          ))}
        </div>

        {failed > 0 && (
          <p className="text-xs text-warn">
            {t(
              'Failed posts are usually the daily limit: WoopSocial allows five YouTube posts a day. Publish the rest with a daily budget rather than all at once.'
            )}
          </p>
        )}

        <button className="btn-accent w-full !py-2" onClick={onClose}>
          {t('Done')}
        </button>
      </div>
    </div>
  )
}

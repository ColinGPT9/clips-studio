import { useEffect, useState } from 'react'
import { api, errorText } from '../../lib/api'
import type { Watch, WatchPublish } from '../../lib/types'
import { platformLabel, WOOPSOCIAL_PLATFORMS } from '../../lib/uploadpost'
import { t } from '../../lib/i18n'
import WatchPlatformOptions, { type PlatformOverrides } from './WatchPlatformOptions'
import WatchSchedule, { type ScheduleValue } from './WatchSchedule'

/** Whether WoopSocial can publish, and which accounts it has. Null while
 *  loading. Shared by the add form and each channel's settings, so both offer
 *  the same platforms. */
export function useWoopAccounts(): { ready: boolean; connected: string[] } | null {
  const [woop, setWoop] = useState<{ ready: boolean; connected: string[] } | null>(null)
  useEffect(() => {
    let live = true
    const load = async (): Promise<void> => {
      try {
        const status = await api.woopSocialStatus()
        const ready = status.enabled && status.has_key
        const connected = ready ? (await api.woopSocialConnections()).connected : []
        if (live) setWoop({ ready, connected })
      } catch {
        if (live) setWoop({ ready: false, connected: [] })
      }
    }
    void load()
    return () => {
      live = false
    }
  }, [])
  return woop
}

const MODES: { id: WatchPublish['mode']; label: string; hint: string }[] = [
  { id: 'off', label: 'Off', hint: 'Only make the clips.' },
  { id: 'ask', label: 'Ask first', hint: 'Make the clips, then wait for you to press Publish.' },
  {
    id: 'auto',
    label: 'Automatic (hands-off)',
    hint: 'Publish as soon as the clips are made. Anything that fails is tried again, so it keeps going with nobody at the PC.'
  }
]

const BACKLOG: { id: Watch['backlog']; label: string }[] = [
  { id: 'newest', label: 'Only the newest one' },
  { id: 'all', label: 'All of them' },
  { id: 'day', label: 'The ones from the last 24 hours' },
  { id: 'none', label: 'None, I will pick' }
]

/** What happens to a watched channel's clips, and to videos it finds late.
 *
 *  Configured once per channel, so nobody fills in a publish form for every
 *  video. The platforms offered are the ones actually connected to
 *  WoopSocial, not a fixed list: a box for an account that is not there would
 *  only produce a "skipped" row later. */
export default function WatchPublishSettings({
  watch,
  onSaved
}: {
  watch: Watch
  onSaved: () => void
}): JSX.Element {
  const p = watch.publish
  const [mode, setMode] = useState(p.mode)
  const [platforms, setPlatforms] = useState<string[]>(p.platforms)
  const [schedule, setSchedule] = useState<ScheduleValue>({
    max_posts: p.max_posts ?? 0,
    per_day: p.per_day,
    gap_hours: p.gap_hours,
    day_start: p.day_start
  })
  const [hashtags, setHashtags] = useState(p.hashtags.map((h) => `#${h.replace(/^#/, '')}`).join(' '))
  const [aiHashtags, setAiHashtags] = useState(p.ai_hashtags)
  const [footer, setFooter] = useState(p.footer)
  const [overrides, setOverrides] = useState<PlatformOverrides>(
    (p.overrides ?? {}) as PlatformOverrides
  )
  const [backlog, setBacklog] = useState(watch.backlog)
  const [minMinutes, setMinMinutes] = useState(watch.min_minutes)
  const woop = useWoopAccounts()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  // Connected accounts, plus anything already chosen that has since been
  // disconnected, so a stale choice stays visible and can be unticked.
  const offered = WOOPSOCIAL_PLATFORMS.filter(
    (id) => woop?.connected.includes(id) || platforms.includes(id)
  )

  const save = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      await api.patchWatch(watch.id, {
        publish: {
          mode,
          platforms,
          ...schedule,
          hashtags: hashtags
            .split(/[\s,]+/)
            .map((h) => h.replace(/^#/, '').trim())
            .filter(Boolean),
          ai_hashtags: aiHashtags,
          footer,
          overrides
        },
        backlog,
        min_minutes: minMinutes
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
      onSaved()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 pt-3 border-t border-raised/60 space-y-4">
      <div className="space-y-2">
        <p className="label">{t('When the clips are made')}</p>
        <div className="flex gap-x-5 gap-y-2 flex-wrap">
          {MODES.map((m) => (
            <label key={m.id} className="flex items-center gap-2 cursor-pointer text-sm" title={t(m.hint)}>
              <input
                type="radio"
                name={`publish-mode-${watch.id}`}
                className="size-4 accent-[#38BDF8]"
                checked={mode === m.id}
                onChange={() => setMode(m.id)}
              />
              {t(m.label)}
            </label>
          ))}
        </div>
        <p className="text-xs text-muted">{t(MODES.find((m) => m.id === mode)?.hint ?? '')}</p>
      </div>

      {mode !== 'off' && (
        <>
          {woop && !woop.ready ? (
            <div className="text-sm text-warn flex items-center gap-3 flex-wrap">
              {t('Publishing goes through WoopSocial, which is not set up yet.')}
              <button
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={() => window.dispatchEvent(new CustomEvent('open-settings'))}
              >
                {t('Open Settings')}
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="label">{t('Publish to')}</p>
              {woop === null ? (
                <p className="text-sm text-muted">{t('Loading…')}</p>
              ) : offered.length === 0 ? (
                <p className="text-sm text-muted">
                  {t('No accounts are connected to WoopSocial yet. Connect them in Settings.')}
                </p>
              ) : (
                <div className="flex gap-x-5 gap-y-2 flex-wrap">
                  {offered.map((id) => (
                    <label key={id} className="flex items-center gap-2 cursor-pointer text-sm">
                      <input
                        type="checkbox"
                        className="size-4 accent-[#38BDF8]"
                        checked={platforms.includes(id)}
                        onChange={(e) =>
                          setPlatforms((prev) =>
                            e.target.checked ? [...prev, id] : prev.filter((x) => x !== id)
                          )
                        }
                      />
                      {platformLabel(id)}
                      {!woop.connected.includes(id) && (
                        <span className="text-xs text-warn">{t('(not connected)')}</span>
                      )}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          <WatchSchedule value={schedule} onChange={setSchedule} />

          <WatchPlatformOptions platforms={platforms} value={overrides} onChange={setOverrides} />

          <div className="text-sm space-y-1">
            <label className="label block" htmlFor={`watch-tags-${watch.id}`}>
              {t('Hashtags on every post')}
            </label>
            <input
              id={`watch-tags-${watch.id}`}
              className="input"
              value={hashtags}
              placeholder="#creatorname #twitch"
              onChange={(e) => setHashtags(e.target.value)}
            />
            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="checkbox"
                className="size-4 accent-[#38BDF8]"
                checked={!aiHashtags}
                onChange={(e) => setAiHashtags(!e.target.checked)}
              />
              {t('Only use my hashtags')}
              <span className="text-muted text-xs">{t('(leave out the ones the AI picks)')}</span>
            </label>
            <span className="text-xs text-muted block">
              {t('These go first on every caption, so they are always kept.')}
            </span>
          </div>

          <label className="text-sm space-y-1 block">
            <span className="label block">{t('Text under every caption (optional)')}</span>
            <textarea
              className="input min-h-16"
              value={footer}
              maxLength={1000}
              onChange={(e) => setFooter(e.target.value)}
            />
            <span className="text-xs text-muted block">
              {t(
                "Leave out links and 'clipped from' wording: TikTok can flag those posts as unoriginal content."
              )}
            </span>
          </label>
        </>
      )}

      <div className="flex gap-x-6 gap-y-3 flex-wrap items-end">
        <label className="text-sm space-y-1">
          <span className="label block">
            {t('If several videos were posted while Clips Kitty was not watching, clip')}
          </span>
          <select
            className="input !w-72"
            value={backlog}
            onChange={(e) => setBacklog(e.target.value as Watch['backlog'])}
          >
            {BACKLOG.map((b) => (
              <option key={b.id} value={b.id}>
                {t(b.label)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm space-y-1">
          <span className="label block">{t('Skip videos shorter than (minutes)')}</span>
          <input
            type="number"
            min={0}
            max={600}
            className="input !w-24"
            value={minMinutes}
            onChange={(e) => setMinMinutes(Math.max(0, Number(e.target.value) || 0))}
          />
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button className="btn-accent" onClick={save} disabled={busy}>
          {busy ? t('Saving…') : t('Save settings')}
        </button>
        {saved && <span className="text-sm text-accent">{t('Saved')}</span>}
        {error && <span className="text-sm text-error">{error}</span>}
      </div>
    </div>
  )
}

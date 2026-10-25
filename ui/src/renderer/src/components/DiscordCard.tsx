import { useEffect, useRef, useState } from 'react'
import {
  destinationDraft,
  destinationPatch,
  discord,
  discordCardState,
  discordName,
  initials
} from '../lib/campaigns'
import type { Destination, DestinationDraft, DiscordGuild, DiscordStatus } from '../lib/campaigns'
import { t } from '../lib/i18n'
import { canManage } from '../lib/plugins'

/** Settings → Discord, for clipping campaigns.
 *
 *  Two separate things live here. Signing in (optional) lets Clips Kitty
 *  show who is connected and list their servers; it never posts as them, and
 *  nothing in Discord's sign-in could. Destinations are webhooks a server
 *  admin made for one channel: the only way Clips Kitty posts by itself.
 *  They work with no sign-in at all, as do campaigns submitted by hand.
 *
 *  The card never sees a token. While a sign-in is open in the browser it
 *  asks the engine every 2 s how it went (as the AI card does). */

const WAIT_MS = 15 * 60 * 1000

const BADGE: Record<string, string> = {
  connected: 'border-success/50 text-success',
  gone: 'border-error/50 text-error',
  denied: 'border-error/50 text-error'
}

const STATE_LABEL: Record<string, string> = {
  connected: 'Connected',
  gone: 'Gone',
  denied: 'Not allowed'
}

export default function DiscordCard(): JSX.Element {
  const [status, setStatus] = useState<DiscordStatus | null>(null)
  const [waiting, setWaiting] = useState<'' | 'connect' | 'webhook'>('')
  const [guilds, setGuilds] = useState<DiscordGuild[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [allowed, setAllowed] = useState(true)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [hookUrl, setHookUrl] = useState('')
  const [showUrl, setShowUrl] = useState(false)
  const [hookLabel, setHookLabel] = useState('')
  const [hookForum, setHookForum] = useState(false)
  const [hookThread, setHookThread] = useState('')
  const [clientDraft, setClientDraft] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<DestinationDraft>({
    label: '',
    is_forum: false,
    thread_id: '',
    webhook_url: ''
  })
  const mounted = useRef(true)
  const destinationsBefore = useRef(0)
  const clientSeeded = useRef(false)

  const say = (m: string): void => {
    setError('')
    setNotice(m)
  }
  const failed = (e: unknown): void => {
    setNotice('')
    setError(String(e).replace(/^Error:\s*/, ''))
  }

  // The Advanced field is filled from the status once, and again after Save,
  // so the status polls and reloads never wipe what is being typed there.
  const show = (next: DiscordStatus, seedClient = false): void => {
    if (!mounted.current) return
    setStatus(next)
    if (seedClient || !clientSeeded.current) {
      clientSeeded.current = true
      setClientDraft(next.client_id_source === 'override' ? next.client_id : '')
    }
  }

  const load = (): void => {
    discord
      .status()
      .then(show)
      .catch(() => {
        /* backend not up yet */
      })
  }

  useEffect(() => {
    mounted.current = true
    load()
    void canManage().then((ok) => mounted.current && setAllowed(ok))
    return () => {
      mounted.current = false
    }
  }, [])

  // While the sign-in page is open in the browser, ask how it is going.
  useEffect(() => {
    if (!waiting) return
    const started = Date.now()
    const timer = window.setInterval(() => {
      discord
        .status()
        .then((next) => {
          show(next)
          if (waiting === 'connect' && next.connected) {
            setWaiting('')
            say(`${t('Connected to Discord as')} ${discordName(next.user)}.`)
          } else if (waiting === 'webhook' && next.destinations.length > destinationsBefore.current) {
            setWaiting('')
            say(t('Webhook added.'))
          } else if (Date.now() - started > WAIT_MS) {
            setWaiting('')
          }
        })
        .catch(() => {
          /* the next tick tries again */
        })
    }, 2000)
    return () => window.clearInterval(timer)
  }, [waiting]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = status ? discordCardState(status) : 'not_connected'

  // The server list, once connected (kept by the engine for 10 minutes).
  useEffect(() => {
    if (state !== 'connected') {
      setGuilds(null)
      return
    }
    discord
      .guilds()
      .then((got) => mounted.current && setGuilds(got.guilds))
      .catch(() => {
        // Refresh servers says why; the status shows a sign-in found expired.
        load()
      })
  }, [state]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (work: () => Promise<void>): Promise<void> => {
    if (busy) return
    setBusy(true)
    try {
      await work()
    } catch (e) {
      failed(e)
      load()
    } finally {
      setBusy(false)
    }
  }

  const openInBrowser = async (url: string, purpose: 'connect' | 'webhook'): Promise<void> => {
    if (!(await window.studio.openExternal(url))) {
      failed(t('Could not open your browser.'))
      return
    }
    destinationsBefore.current = status?.destinations.length ?? 0
    setWaiting(purpose)
    say(t('Finish in your browser. This card updates when you come back.'))
  }

  const connect = (): Promise<void> =>
    run(async () => {
      const { url } = await discord.connect()
      await openInBrowser(url, 'connect')
    })

  const disconnect = (): Promise<void> =>
    run(async () => {
      const got = await discord.disconnect()
      show(got)
      setGuilds(null)
      say(
        got.revoked
          ? t('Disconnected.')
          : t(
              "Disconnected here, but Discord didn't confirm it. To be sure, remove Clips Kitty in Discord: User Settings → Authorized Apps."
            )
      )
    })

  const refreshServers = (): Promise<void> =>
    run(async () => {
      const got = await discord.guilds(true)
      setGuilds(got.guilds)
      say(t('Server list updated.'))
    })

  const addWebhook = (): Promise<void> =>
    run(async () => {
      await discord.addDestination({
        webhook_url: hookUrl.trim(),
        label: hookLabel.trim(),
        is_forum: hookForum,
        thread_id: hookThread.trim()
      })
      setHookUrl('')
      setShowUrl(false)
      setHookLabel('')
      setHookForum(false)
      setHookThread('')
      load()
      say(t('Webhook added.'))
    })

  const createWithDiscord = (): Promise<void> =>
    run(async () => {
      const { url } = await discord.authorizeDestination(hookLabel.trim())
      await openInBrowser(url, 'webhook')
    })

  const testOne = (d: Destination): Promise<void> => {
    if (
      !window.confirm(
        `${t('Post a test message to')} “${d.label}”?\n\n${t('Clips Kitty test message. You can delete it.')}`
      )
    )
      return Promise.resolve()
    return run(async () => {
      const got = await discord.testDestination(d.id)
      load()
      if (got.ok) say(t('Test message posted. You can delete it in Discord.'))
      else failed(got.error)
    })
  }

  const removeOne = (d: Destination): Promise<void> => {
    const used =
      d.campaigns > 0
        ? `\n\n${t('Removing it stops pending link deliveries to this destination.')}`
        : ''
    if (!window.confirm(`${t('Remove the webhook')} “${d.label}”?${used}`)) return Promise.resolve()
    return run(async () => {
      await discord.removeDestination(d.id)
      load()
      say(t('Webhook removed.'))
    })
  }

  const startEdit = (d: Destination): void => {
    setDraft(destinationDraft(d))
    setEditing(d.id)
  }

  // Label, Forum, thread, or a new link for the same destination (its id,
  // and so its campaigns, stay): what the engine's messages send people to.
  const saveEdit = (d: Destination): Promise<void> =>
    run(async () => {
      const patch = destinationPatch(d, draft)
      if (Object.keys(patch).length > 0) await discord.updateDestination(d.id, patch)
      setEditing(null)
      load()
      say(patch.webhook_url ? t('Webhook link saved.') : t('Destination saved.'))
    })

  const saveClientId = (): Promise<void> =>
    run(async () => {
      show(await discord.setClientId(clientDraft.trim()), true)
      say(clientDraft.trim() ? t('Application ID saved.') : t('Using Clips Kitty’s own application.'))
    })

  const user = status?.user
  const name = user ? discordName(user) : ''
  const since = status?.connected_at ? new Date(status.connected_at).toLocaleDateString() : ''
  const destinations = status?.destinations ?? []
  const locked = busy || !allowed

  return (
    <section className="card space-y-3" aria-label="Discord">
      <div>
        <h3 className="font-semibold">{t('Discord')}</h3>
        <p className="text-xs text-muted mt-1 max-w-xl">
          {t(
            'Send the public links of your published clips to a Discord channel. Add a webhook below, then select it when publishing or in Watched channels. Signing in lets Clips Kitty list your servers. It never posts as you.'
          )}
        </p>
      </div>

      {!allowed && (
        <p className="text-xs text-warn">
          {t('Changing Discord settings needs the Clips Kitty desktop app.')}
        </p>
      )}

      {state === 'not_set_up' && (
        <p className="text-xs text-muted">
          {t(
            "Discord sign-in isn't available in this version. You can still add a webhook below."
          )}
        </p>
      )}

      {state === 'not_connected' && (
        <div className="flex items-center gap-2 flex-wrap">
          <button
            className="btn-accent !py-1 !px-3 text-xs"
            disabled={locked}
            onClick={() => void connect()}
          >
            {waiting === 'connect' ? t('Waiting for Discord…') : t('Connect Discord')}
          </button>
          <span className="text-[11px] text-muted">
            {t('Optional: pasted webhooks work without signing in.')}
          </span>
        </div>
      )}

      {state === 'expired' && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-warn">
            {t('Your Discord sign-in has expired')}
            {name && <span className="text-muted"> ({name})</span>}
          </span>
          <button
            className="btn-ghost !py-1 !px-3 text-xs text-warn border-warn/50"
            disabled={locked}
            onClick={() => void connect()}
          >
            {waiting === 'connect' ? t('Waiting for Discord…') : t('Reconnect')}
          </button>
        </div>
      )}

      {state === 'connected' && (
        <div className="space-y-2">
          <div className="flex items-center gap-3 flex-wrap">
            <span
              className="w-8 h-8 rounded-full bg-accent/20 text-accent text-xs font-semibold flex items-center justify-center"
              aria-hidden
            >
              {initials(name)}
            </span>
            <div className="min-w-0">
              <p className="text-sm text-ink">{name}</p>
              {since && (
                <p className="text-[11px] text-muted">
                  {t('Connected')} {since}
                </p>
              )}
            </div>
            <div className="flex gap-2 ml-auto">
              <button
                className="btn-ghost !py-1 !px-3 text-xs"
                disabled={busy}
                onClick={() => void refreshServers()}
              >
                {t('Refresh servers')}
              </button>
              <button
                className="btn-ghost !py-1 !px-3 text-xs"
                disabled={locked}
                onClick={() => void disconnect()}
              >
                {t('Disconnect')}
              </button>
            </div>
          </div>
          {guilds && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted">
                {t('Your servers')} ({guilds.length})
              </summary>
              <ul className="mt-1.5 space-y-1">
                {guilds.map((g) => (
                  <li key={g.id} className="flex items-center gap-2">
                    <span
                      className="w-5 h-5 rounded bg-raised text-[10px] flex items-center justify-center"
                      aria-hidden
                    >
                      {initials(g.name)}
                    </span>
                    <span className="text-ink">{g.name}</span>
                    {g.can_manage_webhooks && (
                      <span className="text-[10px] text-muted">{t('can add webhooks')}</span>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      <div className="space-y-2 border-t border-raised/60 pt-3">
        <p className="label">{t('Destinations')}</p>
        <p className="text-[11px] text-muted">
          {t(
            'A webhook lets Clips Kitty post a clip’s link to one channel, only where a server admin made it. Webhook posts aren’t credited to your Discord account.'
          )}
        </p>
        {destinations.length === 0 && <p className="text-xs text-muted">{t('No webhooks yet.')}</p>}
        <ul className="space-y-1.5">
          {destinations.map((d) => (
            <li key={d.id} className="border border-raised rounded-lg p-2 space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-ink">{d.label}</span>
                <span className="text-[11px] text-muted">
                  {d.guild_name || (d.guild_id ? `${t('Server')} ${d.guild_id}` : '')}
                  {d.is_forum ? ` · ${t('Forum')}` : ''}
                </span>
                <span
                  className={`px-1.5 py-0.5 rounded text-[10px] border ${
                    BADGE[d.state] ?? 'border-raised text-muted'
                  }`}
                >
                  {t(STATE_LABEL[d.state] ?? d.state)}
                </span>
                {!d.has_token && (
                  <span className="text-[10px] text-warn">
                    {t('no link on this PC: paste one under Edit')}
                  </span>
                )}
                <div className="flex gap-2 ml-auto">
                  <button
                    className="btn-ghost !py-0.5 !px-2 text-xs"
                    disabled={locked || !d.has_token}
                    onClick={() => void testOne(d)}
                  >
                    {t('Test')}
                  </button>
                  <button
                    className="btn-ghost !py-0.5 !px-2 text-xs"
                    disabled={locked || editing === d.id}
                    onClick={() => startEdit(d)}
                  >
                    {t('Edit')}
                  </button>
                  <button
                    className="btn-ghost !py-0.5 !px-2 text-xs"
                    disabled={locked}
                    onClick={() => void removeOne(d)}
                  >
                    {t('Remove')}
                  </button>
                </div>
              </div>
              {d.last_error && <p className="text-[11px] text-error">{d.last_error}</p>}
              {editing === d.id && (
                <div className="pt-1.5 space-y-2 text-xs">
                  <input
                    className="input w-full !py-1 text-sm"
                    value={draft.label}
                    placeholder={d.webhook_name || t('Label')}
                    onChange={(e) => setDraft({ ...draft, label: e.target.value })}
                    aria-label={t('Label')}
                  />
                  <label className="inline-flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={draft.is_forum}
                      onChange={(e) => setDraft({ ...draft, is_forum: e.target.checked })}
                    />
                    {t('Forum channel (each post starts a thread)')}
                  </label>
                  <input
                    className="input w-full !py-1 text-sm"
                    value={draft.thread_id}
                    placeholder={t('Thread ID, to post into one thread (optional)')}
                    onChange={(e) => setDraft({ ...draft, thread_id: e.target.value })}
                    spellCheck={false}
                    aria-label={t('Thread ID')}
                  />
                  <input
                    className="input w-full !py-1 text-sm"
                    type="password"
                    value={draft.webhook_url}
                    placeholder={t(
                      'New webhook URL (optional): paste it to replace the stored link'
                    )}
                    onChange={(e) => setDraft({ ...draft, webhook_url: e.target.value })}
                    autoComplete="off"
                    spellCheck={false}
                    aria-label={t('New webhook URL')}
                  />
                  <div className="flex gap-2">
                    <button
                      className="btn-accent !py-1 !px-3 text-xs"
                      disabled={locked}
                      onClick={() => void saveEdit(d)}
                    >
                      {busy ? t('Checking…') : t('Save')}
                    </button>
                    <button
                      className="btn-ghost !py-1 !px-3 text-xs"
                      disabled={busy}
                      onClick={() => setEditing(null)}
                    >
                      {t('Cancel')}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>

        <details className="text-xs">
          <summary className="cursor-pointer">{t('Add a webhook')}</summary>
          <div className="mt-2 space-y-2">
            <p className="text-[11px] text-muted">
              {t(
                'In Discord: Server Settings → Integrations → Webhooks → New Webhook → Copy Webhook URL.'
              )}
            </p>
            <div className="flex gap-2 flex-wrap">
              <input
                className="input flex-1 !py-1 text-sm min-w-52"
                type={showUrl ? 'text' : 'password'}
                value={hookUrl}
                placeholder={t('Paste the webhook URL')}
                onChange={(e) => setHookUrl(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                aria-label={t('Webhook URL')}
              />
              <button className="btn-ghost !py-1 !px-3 text-xs" onClick={() => setShowUrl((v) => !v)}>
                {showUrl ? t('Hide') : t('Show')}
              </button>
            </div>
            <input
              className="input w-full !py-1 text-sm"
              value={hookLabel}
              placeholder={t('Label, like #clips (optional)')}
              onChange={(e) => setHookLabel(e.target.value)}
              aria-label={t('Label')}
            />
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={hookForum}
                onChange={(e) => setHookForum(e.target.checked)}
              />
              {t('Forum channel (each post starts a thread)')}
            </label>
            <input
              className="input w-full !py-1 text-sm"
              value={hookThread}
              placeholder={t('Thread ID, to post into one thread (optional)')}
              onChange={(e) => setHookThread(e.target.value)}
              spellCheck={false}
              aria-label={t('Thread ID')}
            />
            <div className="flex gap-2 flex-wrap items-center">
              <button
                className="btn-accent !py-1 !px-3 text-xs"
                disabled={locked || !hookUrl.trim()}
                onClick={() => void addWebhook()}
              >
                {busy ? t('Checking…') : t('Add webhook')}
              </button>
              {status?.configured && (
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  disabled={locked}
                  onClick={() => void createWithDiscord()}
                >
                  {waiting === 'webhook' ? t('Waiting for Discord…') : t('Create with Discord')}
                </button>
              )}
            </div>
            {status?.configured && (
              <p className="text-[11px] text-muted">
                {t(
                  'Create with Discord lets a server admin pick the channel on Discord’s own page. If Discord doesn’t hand a webhook back, paste one instead.'
                )}
              </p>
            )}
          </div>
        </details>
      </div>

      {waiting && (
        <button className="btn-ghost !py-0.5 !px-2 text-[11px]" onClick={() => setWaiting('')}>
          {t('Stop waiting')}
        </button>
      )}

      <details className="text-[11px] text-muted">
        <summary className="cursor-pointer">{t('Advanced')}</summary>
        <div className="mt-1.5 space-y-1.5">
          <label className="label block" htmlFor="discord-client-id">
            {t('Discord application ID')}
          </label>
          <div className="flex gap-2 flex-wrap">
            <input
              id="discord-client-id"
              className="input flex-1 !py-1 text-xs min-w-52"
              value={clientDraft}
              placeholder={status?.client_id_source === 'shipped' ? status.client_id : '…'}
              onChange={(e) => setClientDraft(e.target.value)}
              spellCheck={false}
            />
            <button
              className="btn-ghost !py-1 !px-3 text-xs"
              disabled={locked}
              onClick={() => void saveClientId()}
            >
              {t('Save')}
            </button>
          </div>
          <p>
            {t(
              'For testing with your own Discord application. Leave it empty to use Clips Kitty’s own.'
            )}
          </p>
          {status?.redirect_uri && (
            <p>
              {t('Discord sends you back to')} <code>{status.redirect_uri}</code>.{' '}
              {t('Signing in works only while Clips Kitty runs on port 8765.')}
            </p>
          )}
          {status?.storage && (
            <p>
              {t('Stored with')} {status.storage}
            </p>
          )}
        </div>
      </details>

      {notice && <p className="text-xs text-success">{notice}</p>}
      {error && <p className="text-xs text-error">{error}</p>}
    </section>
  )
}

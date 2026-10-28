import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { api } from '../lib/api'
import {
  ACCOUNTS_CHANGED,
  AccountRequestError,
  accountPollDelay,
  connectedAccounts,
  isTransientAccountError,
  streamWorkflows,
  twitchActivationUrl,
  twitchChannelLogin,
  type AccountFlow,
  type ConnectedAccount,
  type ConnectedAccountsStatus,
  type ViewerNominationStatus
} from '../lib/connectedAccounts'
import { t } from '../lib/i18n'
import type { JobOptions, WatchPublish } from '../lib/types'
import { seedOptions } from './queue/AddVideos'
import RecordingBindingCard from './RecordingBindingCard'
import TwitchActivity from './TwitchActivity'

const TWITCH_APPS = 'https://dev.twitch.tv/console/apps'
// What Twitch's console shows; anything else (a full stop, a quote, a hyphen)
// is a copying slip, refused here with a message that says so.
const CLIENT_ID = /^[A-Za-z0-9]+$/
// The longest wait between polls when Twitch or the engine did not answer.
const MAX_RETRY_DELAY = 30000

// Defined through t() so the labels are found and translated like every
// other string.
const PERMISSION_LABELS: Record<string, () => string> = {
  'clips:edit': () => t('Create live Twitch Clips'),
  'channel:manage:clips': () => t('Manage Twitch Clips for your channel, including VOD clips'),
  'editor:manage:clips': () => t('Manage Twitch Clips as a channel editor'),
  'user:read:chat': () => t('Read Twitch chat'),
  'user:write:chat': () => t('Send Twitch chat messages')
}

/** How a watch made from this card is set up: the Watch page passes its Add a
 * channel choices, so the button follows them like the Add button does. */
export interface WatchChoices {
  publish?: Partial<WatchPublish>
  options?: Partial<JobOptions>
}

interface Problem {
  text: string
  /** A warning (it worked, with a caveat) rather than a failure. */
  warn?: boolean
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : t('The account operation failed.')
}

function statusLabel(account: ConnectedAccount): string {
  if (account.status === 'connected') return t('Connected')
  if (account.status === 'reconnect_required') return t('Reconnect required')
  return t('Check connection')
}

function permissionLabel(scope: string): string {
  return PERMISSION_LABELS[scope]?.() ?? scope
}

/** The permissions an account holds that this card knows how to ask for. */
function knownScopes(account: ConnectedAccount): string[] {
  return Object.keys(PERMISSION_LABELS).filter((scope) => account.scopes.includes(scope))
}

/** A connection belongs to Clips Kitty, so all permitted integrations reuse it. */
export default function ConnectedAccountsCard({
  compact = false,
  onWatchAdded,
  watchChoices
}: {
  compact?: boolean
  onWatchAdded?: (watchId: number) => void
  watchChoices?: () => WatchChoices
}): JSX.Element {
  const [status, setStatus] = useState<ConnectedAccountsStatus | null>(null)
  const [clientId, setClientId] = useState('')
  // Nothing is ticked to start with: a plain connection asks Twitch for no
  // permission at all, and Watch my channel needs none.
  const [clips, setClips] = useState(false)
  const [vodClips, setVodClips] = useState(false)
  const [readChat, setReadChat] = useState(false)
  const [sendChat, setSendChat] = useState(false)
  const [flow, setFlow] = useState<AccountFlow | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [problem, setProblem] = useState<Problem | null>(null)
  const [loadError, setLoadError] = useState('')
  const [nominationStatus, setNominationStatus] = useState<Record<string, ViewerNominationStatus>>({})
  const [nominationErrors, setNominationErrors] = useState<Record<string, string>>({})
  const [channelNames, setChannelNames] = useState<Record<string, string>>({})
  const [collectionChannels, setCollectionChannels] = useState<Record<string, string>>({})
  const mounted = useRef(false)
  const activeFlow = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Until the person changes a checkbox, the boxes show what the connected
  // account already holds, so reconnecting never quietly drops a permission.
  const ticksTouched = useRef(false)

  const provider = status?.providers.find((item) => item.id === 'twitch')
  const accounts = status?.accounts.filter((account) => account.provider === 'twitch') ?? []
  const usableStorage = provider?.storage_available === true

  const setTicks = useCallback((scopes: string[]): void => {
    setClips(scopes.includes('clips:edit'))
    setVodClips(scopes.includes('channel:manage:clips') || scopes.includes('editor:manage:clips'))
    setReadChat(scopes.includes('user:read:chat'))
    setSendChat(scopes.includes('user:write:chat'))
  }, [])

  const say = useCallback((text: string): void => {
    setProblem(null)
    setNotice(text)
  }, [])

  const fail = useCallback((text: string, warn = false): void => {
    setNotice('')
    setProblem({ text, warn })
  }, [])

  const load = useCallback(async (): Promise<void> => {
    try {
      const value = await connectedAccounts.status()
      if (!mounted.current) return
      setStatus(value)
      setLoadError('')
      const twitch = value.accounts.filter((account) => account.provider === 'twitch')
      if (!ticksTouched.current && twitch.length) setTicks(knownScopes(twitch[0]))
      // The Watch page's card shows no nomination counts, so it never asks.
      const eligible = compact ? [] : twitch.filter((account) =>
        account.status === 'connected' && account.scopes.includes('user:read:chat'))
      const results = await Promise.all(eligible.map(async (account) => {
        try {
          return { id: account.id, value: await streamWorkflows.streams(account.id), error: '' }
        } catch (error) {
          return { id: account.id, value: undefined, error: errorMessage(error) }
        }
      }))
      if (!mounted.current) return
      const nextStatus: Record<string, ViewerNominationStatus> = {}
      const nextErrors: Record<string, string> = {}
      for (const result of results) {
        if (result.value) nextStatus[result.id] = result.value
        if (result.error) nextErrors[result.id] = result.error
      }
      setNominationStatus(nextStatus)
      setNominationErrors(nextErrors)
    } catch (error) {
      if (mounted.current) setLoadError(errorMessage(error))
    }
  }, [compact, setTicks])

  useEffect(() => {
    mounted.current = true
    void load()
    const changed = (): void => { void load() }
    window.addEventListener(ACCOUNTS_CHANGED, changed)
    return () => {
      mounted.current = false
      window.removeEventListener(ACCOUNTS_CHANGED, changed)
      if (timer.current) clearTimeout(timer.current)
      const id = activeFlow.current
      activeFlow.current = null
      if (id) void connectedAccounts.cancel(id).catch(() => {})
    }
  }, [load])

  const needsNominationRefresh = Object.values(nominationStatus).some((status) =>
    status.monitoring || status.streams.some((stream) => stream.recording?.status === 'pending'))
  useEffect(() => {
    if (!needsNominationRefresh) return
    const refresh = setInterval(() => { void load() }, 15000)
    return () => clearInterval(refresh)
  }, [load, needsNominationRefresh])

  const stopFlow = useCallback((): void => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    activeFlow.current = null
    if (mounted.current) setFlow(null)
  }, [])

  useEffect(() => {
    if (!flow) return
    const id = flow.flow_id
    const expiresAt = Date.now() + Math.max(0, flow.expires_in) * 1000
    let stopped = false
    let interval = accountPollDelay(flow.interval)
    let retrying = false
    const expired = (): void => {
      stopFlow()
      fail(t('The sign-in code expired. Connect again to get a new code.'))
      void connectedAccounts.cancel(id).catch(() => {})
    }
    const poll = async (): Promise<void> => {
      if (stopped || activeFlow.current !== id) return
      if (Date.now() >= expiresAt) {
        expired()
        return
      }
      try {
        const result = await connectedAccounts.poll(id)
        if (stopped || !mounted.current || activeFlow.current !== id) return
        if (result.status === 'pending') {
          if (retrying) {
            retrying = false
            setProblem(null)
          }
          if (result.interval !== undefined) interval = accountPollDelay(result.interval)
          timer.current = setTimeout(() => { void poll() }, interval)
          return
        }
        stopFlow()
        say(t('Twitch connected. Your permitted integrations can now use this account.'))
        window.dispatchEvent(new Event(ACCOUNTS_CHANGED))
      } catch (error) {
        if (stopped || !mounted.current || activeFlow.current !== id) return
        if (isTransientAccountError(error)) {
          // Twitch or the engine did not answer this time. The code is still
          // good and the person may already have approved it, so keep asking,
          // more slowly, until the code expires.
          retrying = true
          interval = Math.min(interval * 2, MAX_RETRY_DELAY)
          fail(t('Twitch did not answer. Still waiting: your code stays valid until it expires.'), true)
          timer.current = setTimeout(() => { void poll() }, interval)
          return
        }
        const code = error instanceof AccountRequestError ? error.code : ''
        if (code === 'expired_token') {
          expired()
          return
        }
        // A final answer (declined, or the sign-in request is gone): no
        // invisible polling loop is left running.
        stopFlow()
        fail(code === 'access_denied' ? t('Twitch sign-in was declined.') : errorMessage(error))
        void connectedAccounts.cancel(id).catch(() => {})
      }
    }
    timer.current = setTimeout(() => { void poll() }, interval)
    return () => {
      stopped = true
      if (timer.current) clearTimeout(timer.current)
    }
  }, [flow, stopFlow, say, fail])

  const act = async (operation: () => Promise<void>): Promise<void> => {
    setBusy(true)
    setNotice('')
    setProblem(null)
    try {
      await operation()
    } catch (error) {
      if (mounted.current) fail(errorMessage(error))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const tickedScopes = (): string[] => [
    ...(clips ? ['clips:edit'] : []),
    ...(vodClips ? ['channel:manage:clips'] : []),
    ...(readChat ? ['user:read:chat'] : []),
    ...(sendChat ? ['user:write:chat'] : [])
  ]

  const tick = (set: (value: boolean) => void) => (event: ChangeEvent<HTMLInputElement>): void => {
    ticksTouched.current = true
    set(event.target.checked)
  }

  const connect = async (scopes: string[]): Promise<void> => {
    const next = await connectedAccounts.connectTwitch(scopes)
    if (!mounted.current) {
      await connectedAccounts.cancel(next.flow_id)
      return
    }
    const url = twitchActivationUrl(next.verification_uri)
    if (!url) {
      await connectedAccounts.cancel(next.flow_id)
      throw new Error(t('Twitch returned an unexpected sign-in address. Please try again.'))
    }
    activeFlow.current = next.flow_id
    setFlow(next)
    const opened = await window.studio?.openExternal(url)
    if (mounted.current && !opened) {
      say(t('Open the Twitch activation page below and enter your code.'))
    }
  }

  // Reconnecting an account asks for exactly what it held before.
  const reconnect = (account: ConnectedAccount): Promise<void> => act(async () => {
    const scopes = knownScopes(account)
    setTicks(scopes)
    await connect(scopes)
  })

  // The channel goes in through the same Add a channel route the Watch page
  // uses, with the same choices, once the connection has proved it is this
  // account. Settings has no Add form: there the Generate bar's settings apply
  // and its clips wait for the person before anything is published.
  const watch = (account: ConnectedAccount): Promise<void> => act(async () => {
    await connectedAccounts.test(account.id)
    const choices = watchChoices?.() ?? { options: seedOptions() }
    const added = await api.addWatch('twitch', account.login, choices.publish, choices.options)
    const automation = await api.automation().catch(() => null)
    if (!mounted.current) return
    if (!added.created) {
      say(t('Your Twitch channel is already in Watched channels.'))
    } else if (automation?.enabled) {
      say(t('Your Twitch channel is in Watched channels, and Clips Kitty is watching it now.'))
    } else {
      say(t('Your Twitch channel is in Watched channels. Turn on Watch channels on the Watch page when you are ready.'))
    }
    onWatchAdded?.(added.id)
  })

  const monitor = (account: ConnectedAccount, start: boolean): Promise<void> => act(async () => {
    if (start) {
      const login = twitchChannelLogin(
        channelNames[account.id] ?? nominationStatus[account.id]?.broadcaster_login ?? account.login)
      if (!login) throw new Error(t('Enter a Twitch channel name or channel URL.'))
      const context = await connectedAccounts.streamContext(account.id, login)
      await streamWorkflows.startMonitor(account.id, context.broadcaster.id)
      if (mounted.current) setCollectionChannels((current) => ({ ...current, [account.id]: context.broadcaster.login }))
    } else await streamWorkflows.stopMonitor(account.id)
    if (!mounted.current) return
    say(start
      ? t('Viewer nomination collection started for the selected channel. Earlier chat is not recovered.')
      : t('Viewer nomination collection stopped. Recorded nomination counts remain available.'))
    await load()
  })

  const saveClientId = (): Promise<void> => act(async () => {
    const value = clientId.trim()
    if (!CLIENT_ID.test(value)) {
      throw new Error(t('A Twitch Client ID is letters and numbers only. Copy it again from the Twitch developer console.'))
    }
    if (accounts.length && provider?.configured && !window.confirm(
      t('Replace the Twitch Client ID? Every connected Twitch account will need reconnecting.')
    )) return
    await connectedAccounts.configureTwitch(value)
    setClientId('')
    say(t('Twitch application saved. You can now connect an account.'))
  })

  const disconnect = (account: ConnectedAccount): Promise<void> => act(async () => {
    const result = await connectedAccounts.disconnect(account.id)
    if (!mounted.current) return
    if (result?.revoked === false) {
      fail(t('Disconnected from Clips Kitty, but Twitch did not confirm it removed access. To be sure, remove the app under Connections in your Twitch settings.'), true)
    } else {
      say(t('Account disconnected.'))
    }
  })

  const messages = (
    <>
      {notice && <p role="status" className="text-xs">{notice}</p>}
      {problem && (
        <p role="alert" className={`text-xs ${problem.warn ? 'text-warn' : 'text-error'}`}>{problem.text}</p>
      )}
    </>
  )

  if (compact) {
    return (
      <section className="card space-y-3" aria-label={t('Connected Twitch accounts')}>
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-semibold">{t('Connected Twitch accounts')}</h2>
          <button className="btn-ghost !py-1 text-xs" onClick={() => window.dispatchEvent(new Event('open-settings'))}>
            {t('Manage in Settings')}
          </button>
        </div>
        <p className="text-xs text-muted">
          {t('Connect Twitch once in Settings, then add your own channel here. Watching another channel does not grant permission to manage it.')}
        </p>
        {accounts.map((account) => (
          <div key={account.id} className="flex items-center justify-between gap-3 text-sm">
            <span>
              {account.display_name || account.login}{' '}
              <span className={account.status === 'connected' ? 'text-muted' : 'text-warn'}>· {statusLabel(account)}</span>
            </span>
            <button className="btn-ghost !py-1 text-xs" disabled={busy || account.status !== 'connected'}
              aria-label={`${t('Watch my channel')}: ${account.display_name || account.login}`}
              onClick={() => void watch(account)}>
              {t('Watch my channel')}
            </button>
          </div>
        ))}
        {status && !accounts.length && <p className="text-xs text-muted">{t('No Twitch account connected.')}</p>}
        {loadError && <p role="alert" className="text-xs text-warn">{loadError}</p>}
        {messages}
      </section>
    )
  }

  return (
    <section className="card space-y-4" aria-label={t('Connected accounts')}>
      <div>
        <h3 className="font-semibold">{t('Connected accounts')}</h3>
        <p className="text-xs text-muted mt-1">
          {t('Connect Twitch once for Clips Kitty and the integrations you permit. Sign in in your browser; no Clips Kitty cloud account is needed.')}
        </p>
      </div>

      {loadError && <p role="alert" className="text-sm text-warn">{loadError}</p>}
      {!status && !loadError && <p className="text-xs text-muted">{t('Loading connections…')}</p>}

      {provider && (
        <div className="space-y-3">
          <h4 className="text-sm font-semibold">Twitch</h4>
          {!usableStorage && (
            <p role="alert" className="text-xs text-warn">
              {t('Secure account storage is unavailable. Enable your operating system credential store before connecting Twitch.')}
            </p>
          )}
          <details className="text-sm" open={!provider.configured}>
            <summary className="cursor-pointer text-muted">{t('Twitch application setup')}</summary>
            <div className="space-y-2 mt-3">
              <ol className="text-xs text-muted list-decimal pl-5 space-y-1">
                <li>{t('Turn on two-factor authentication for your Twitch account. Twitch asks for it before you can register an application.')}</li>
                <li>{t('In the Twitch developer console, choose Register Your Application and give it any name.')}</li>
                <li>{t('Under OAuth Redirect URLs enter https://localhost. Twitch requires one, though signing in here never uses it.')}</li>
                <li>{t('Pick any Category, and set Client Type to Public.')}</li>
                <li>{t('Create it, then copy its Client ID and paste it below. A client secret is not needed.')}</li>
              </ol>
              <button className="text-xs text-accent hover:underline" onClick={() => void window.studio?.openExternal(TWITCH_APPS)}>
                {t('Open Twitch developer console')}
              </button>
              <label className="block text-xs" htmlFor="twitch-client-id">{t('Twitch Client ID')}</label>
              <input id="twitch-client-id" className="input w-full" autoComplete="off" spellCheck={false}
                value={clientId} placeholder={provider.client_id || t('Public application Client ID')}
                disabled={busy || Boolean(flow)} onChange={(event) => setClientId(event.target.value)} />
              <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow) || !clientId.trim()}
                onClick={() => void saveClientId()}>
                {t('Save Client ID')}
              </button>
            </div>
          </details>

          {accounts.map((account) => (
            <div key={account.id} className="rounded border border-raised p-3 space-y-2">
              <div className="text-sm font-medium">{account.display_name || account.login}</div>
              <p className={`text-xs ${account.status === 'connected' ? 'text-muted' : 'text-warn'}`}>{statusLabel(account)}</p>
              <p className="text-xs text-muted">
                {t('Permissions')}: {account.scopes.length ? account.scopes.map(permissionLabel).join(', ') : t('Stream and VOD information only')}
              </p>
              <div className="flex flex-wrap gap-2">
                {account.status !== 'connected' && (
                  <button className="btn-accent !py-1 text-xs"
                    disabled={busy || Boolean(flow) || !provider.configured || !usableStorage}
                    onClick={() => void reconnect(account)}>
                    {t('Reconnect')}
                  </button>
                )}
                <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow)} onClick={() => void act(async () => {
                  await connectedAccounts.test(account.id)
                  say(t('Connection tested. The account status has been refreshed.'))
                })}>{t('Test connection')}</button>
                <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow) || account.status !== 'connected'}
                  aria-label={`${t('Watch my channel')}: ${account.display_name || account.login}`}
                  onClick={() => void watch(account)}>
                  {t('Watch my channel')}
                </button>
                <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow)}
                  onClick={() => void disconnect(account)}>{t('Disconnect')}</button>
              </div>
              {account.scopes.includes('user:read:chat') && account.status === 'connected' && (
                <div className="space-y-2 border-t border-raised pt-2">
                  <h5 className="text-xs font-semibold">{t('Viewer highlight nominations')}</h5>
                  <p className="text-xs text-muted">
                    {t('Collect !clip, !chaos and !funny nominations from a channel you watch. Viewer access is enough to read public chat. Collection begins once Twitch confirms a live stream; earlier chat is not recovered.')}
                  </p>
                  <label className="block space-y-1 text-xs">
                    <span>{t('Twitch channel to collect from')}</span>
                    <input className="input w-full" type="text" placeholder={account.login}
                      aria-label={`${t('Twitch channel to collect from')}: ${account.login}`}
                      value={nominationStatus[account.id]?.monitoring
                        ? (collectionChannels[account.id] || nominationStatus[account.id].broadcaster_login || '')
                        : (channelNames[account.id] ?? nominationStatus[account.id]?.broadcaster_login ?? account.login)}
                      disabled={busy || Boolean(flow) || nominationStatus[account.id]?.monitoring === true}
                      onChange={(event) => setChannelNames((current) => ({ ...current, [account.id]: event.target.value }))} />
                  </label>
                  <p className="text-xs text-muted">
                    {t('After the stream ends, bind its Twitch recording and check the timing to use these nominations when finding highlights.')}
                  </p>
                  {nominationStatus[account.id] && (
                    <p className="text-xs" role="status">
                      {nominationStatus[account.id].monitoring
                        ? `${t('Collection enabled for channel')}: ${collectionChannels[account.id] || nominationStatus[account.id].broadcaster_login || t('Selected channel')}`
                        : t('Collection stopped.')}
                    </p>
                  )}
                  {nominationStatus[account.id]?.monitoring && nominationStatus[account.id]?.broadcaster_id && (
                    <TwitchActivity key={`${account.id}:${nominationStatus[account.id].broadcaster_id}`}
                      accountId={account.id} broadcasterId={nominationStatus[account.id].broadcaster_id!} />
                  )}
                  {nominationErrors[account.id] && <p className="text-xs text-warn">{nominationErrors[account.id]}</p>}
                  <div className="flex flex-wrap gap-2">
                    <button className="btn-ghost !py-1 text-xs"
                      disabled={busy || Boolean(flow) || nominationStatus[account.id]?.monitoring === true}
                      onClick={() => void monitor(account, true)}>{t('Start collecting nominations')}</button>
                    <button className="btn-ghost !py-1 text-xs"
                      disabled={busy || Boolean(flow) || nominationStatus[account.id]?.monitoring === false}
                      onClick={() => void monitor(account, false)}>{t('Stop collecting nominations')}</button>
                    <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow)}
                      onClick={() => void act(load)}>{t('Refresh nomination counts')}</button>
                  </div>
                  {nominationStatus[account.id]?.streams.slice(0, 5).map((stream) => (
                    <RecordingBindingCard key={stream.stream_id} accountId={account.id} stream={stream}
                      disabled={busy || Boolean(flow)} onSaved={load} />
                  ))}
                  {nominationStatus[account.id]?.streams.length === 0 && (
                    <p className="text-xs text-muted">{t('No stream has been recorded for nomination collection yet.')}</p>
                  )}
                </div>
              )}
            </div>
          ))}

          {!flow && (
            <div className="space-y-2 border-t border-raised pt-3">
              <p className="text-xs text-muted">{t('Choose the permissions for this connection. Changing them requires signing in again. Watching your own channel needs none.')}</p>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={clips} disabled={busy} onChange={tick(setClips)} />{t('Create native Twitch Clips during a live stream')}</label>
              <p className="text-xs text-muted">{t("Only apps that use Clips Kitty's local API can ask for a live clip. Pipelines and watched channels never clip a live stream.")}</p>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={vodClips} disabled={busy} onChange={tick(setVodClips)} />{t('Manage Twitch Clips for my channel, including public VOD clips')}</label>
              <p className="text-xs text-muted">{t("Lets pipelines you allow make public Twitch clips from your channel's VODs. Collecting nominations never publishes a clip.")}</p>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={readChat} disabled={busy} onChange={tick(setReadChat)} />{t('Read Twitch chat and chat events')}</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={sendChat} disabled={busy} onChange={tick(setSendChat)} />{t('Send Twitch chat messages')}</label>
              <button className="btn-accent !py-1.5 text-sm" disabled={busy || !provider.configured || !usableStorage}
                onClick={() => void act(() => connect(tickedScopes()))}>
                {accounts.length ? t('Connect or reconnect Twitch') : t('Connect Twitch')}
              </button>
            </div>
          )}

          {flow && (
            <div className="rounded border border-accent/40 p-3 space-y-3" role="status">
              <p className="text-sm">{t('Enter this code on the Twitch activation page, then approve the permissions:')}</p>
              <p className="text-xl font-mono font-semibold tracking-widest select-all">{flow.user_code}</p>
              <p className="text-xs text-muted">{t('Waiting for Twitch. Keep this page open; the code expires shortly.')}</p>
              <div className="flex gap-2 flex-wrap">
                <button className="btn-ghost !py-1 text-xs" onClick={() => {
                  const url = twitchActivationUrl(flow.verification_uri)
                  if (url) void window.studio?.openExternal(url)
                }}>{t('Open Twitch activation page')}</button>
                <button className="btn-ghost !py-1 text-xs" onClick={() => void act(async () => {
                  const id = activeFlow.current
                  stopFlow()
                  if (id) await connectedAccounts.cancel(id)
                  say(t('Twitch sign-in cancelled.'))
                })}>{t('Cancel')}</button>
              </div>
            </div>
          )}
        </div>
      )}
      {messages}
    </section>
  )
}

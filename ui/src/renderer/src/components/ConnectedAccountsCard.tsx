import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ACCOUNTS_CHANGED,
  accountPollDelay,
  connectedAccounts,
  streamWorkflows,
  twitchActivationUrl,
  type AccountFlow,
  type ConnectedAccount,
  type ConnectedAccountsStatus,
  type ViewerNominationStatus
} from '../lib/connectedAccounts'
import { t } from '../lib/i18n'

const TWITCH_APPS = 'https://dev.twitch.tv/console/apps'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : t('The account operation failed.')
}

function statusLabel(account: ConnectedAccount): string {
  if (account.status === 'connected') return t('Connected')
  if (account.status === 'reconnect_required' || account.status === 'reauthorization_required') {
    return t('Reconnect required')
  }
  if (account.status === 'revoked' || account.status === 'disconnected') return t('Disconnected')
  return t('Check connection')
}

function permissionLabel(scope: string): string {
  const labels: Record<string, string> = {
    'clips:edit': 'Create live Twitch Clips',
    'channel:manage:clips': 'Manage Twitch Clips for your channel, including VOD clips',
    'editor:manage:clips': 'Manage Twitch Clips as a channel editor',
    'user:read:chat': 'Read Twitch chat',
    'user:write:chat': 'Send Twitch chat messages'
  }
  return labels[scope] ? t(labels[scope]) : scope
}

/** A connection belongs to Clips Kitty, so all permitted integrations reuse it. */
export default function ConnectedAccountsCard({
  compact = false,
  onWatchAdded
}: {
  compact?: boolean
  onWatchAdded?: () => void
}): JSX.Element {
  const [status, setStatus] = useState<ConnectedAccountsStatus | null>(null)
  const [clientId, setClientId] = useState('')
  const [clips, setClips] = useState(true)
  const [vodClips, setVodClips] = useState(false)
  const [readChat, setReadChat] = useState(false)
  const [sendChat, setSendChat] = useState(false)
  const [flow, setFlow] = useState<AccountFlow | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [loadError, setLoadError] = useState('')
  const [nominationStatus, setNominationStatus] = useState<Record<string, ViewerNominationStatus>>({})
  const [nominationErrors, setNominationErrors] = useState<Record<string, string>>({})
  const mounted = useRef(false)
  const activeFlow = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async (): Promise<void> => {
    try {
      const value = await connectedAccounts.status()
      if (!mounted.current) return
      setStatus(value)
      setLoadError('')
      const eligible = value.accounts.filter((account) =>
        account.provider === 'twitch' && account.status === 'connected' && account.scopes.includes('user:read:chat'))
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
  }, [])

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
    const poll = async (): Promise<void> => {
      if (stopped || activeFlow.current !== id) return
      if (Date.now() >= expiresAt) {
        stopFlow()
        setNotice(t('The sign-in code expired. Connect again to get a new code.'))
        void connectedAccounts.cancel(id).catch(() => {})
        return
      }
      try {
        const result = await connectedAccounts.poll(id)
        if (stopped || !mounted.current || activeFlow.current !== id) return
        if (result.status === 'pending') {
          if (result.interval !== undefined) interval = accountPollDelay(result.interval)
          timer.current = setTimeout(() => { void poll() }, interval)
          return
        }
        stopFlow()
        if (result.status === 'connected') {
          setNotice(t('Twitch connected. Your permitted integrations can now use this account.'))
          window.dispatchEvent(new Event(ACCOUNTS_CHANGED))
        } else if (result.status === 'denied') {
          setNotice(t('Twitch sign-in was declined.'))
        } else if (result.status === 'expired') {
          setNotice(t('The sign-in code expired. Connect again to get a new code.'))
        } else {
          setNotice(result.error || t('Twitch sign-in stopped. Connect again to try another code.'))
        }
      } catch (error) {
        if (stopped || !mounted.current || activeFlow.current !== id) return
        // A failed request must not leave an invisible polling loop running.
        stopFlow()
        setNotice(errorMessage(error))
        void connectedAccounts.cancel(id).catch(() => {})
      }
    }
    timer.current = setTimeout(() => { void poll() }, interval)
    return () => {
      stopped = true
      if (timer.current) clearTimeout(timer.current)
    }
  }, [flow, stopFlow])

  const act = async (operation: () => Promise<void>): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      await operation()
    } catch (error) {
      if (mounted.current) setNotice(errorMessage(error))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const connect = async (): Promise<void> => {
    const scopes = [
      ...(clips ? ['clips:edit'] : []),
      ...(vodClips ? ['channel:manage:clips'] : []),
      ...(readChat ? ['user:read:chat'] : []),
      ...(sendChat ? ['user:write:chat'] : [])
    ]
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
      setNotice(t('Open the Twitch activation page below and enter your code.'))
    }
  }

  const watch = (account: ConnectedAccount): Promise<void> => act(async () => {
    await connectedAccounts.watch(account.id)
    if (!mounted.current) return
    setNotice(t('Your Twitch channel is in Watched channels. Turn watching on there when you are ready.'))
    onWatchAdded?.()
  })

  const monitor = (account: ConnectedAccount, start: boolean): Promise<void> => act(async () => {
    if (start) await streamWorkflows.startMonitor(account.id)
    else await streamWorkflows.stopMonitor(account.id)
    if (!mounted.current) return
    setNotice(start
      ? t('Viewer nomination collection started for your live or upcoming stream. Earlier chat is not recovered.')
      : t('Viewer nomination collection stopped. Recorded nomination counts remain available.'))
    await load()
  })

  const provider = status?.providers.find((item) => item.id === 'twitch')
  const accounts = status?.accounts.filter((account) => account.provider === 'twitch') ?? []
  const usableStorage = provider?.storage_available === true

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
            <span>{account.display_name || account.login} <span className="text-muted">· {statusLabel(account)}</span></span>
            <button className="btn-ghost !py-1 text-xs" disabled={busy || account.status !== 'connected'} onClick={() => void watch(account)}>
              {t('Watch my channel')}
            </button>
          </div>
        ))}
        {!accounts.length && <p className="text-xs text-muted">{loadError || t('No Twitch account connected.')}</p>}
        {notice && <p role="status" className="text-xs">{notice}</p>}
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

      {loadError && <p role="alert" className="text-sm text-amber-400">{loadError}</p>}
      {!status && !loadError && <p className="text-xs text-muted">{t('Loading connections…')}</p>}

      {provider && (
        <div className="space-y-3">
          <h4 className="text-sm font-semibold">Twitch</h4>
          {!usableStorage && (
            <p role="alert" className="text-xs text-amber-400">
              {t('Secure account storage is unavailable. Enable your operating system credential store before connecting Twitch.')}
            </p>
          )}
          <details className="text-sm" open={!provider.configured}>
            <summary className="cursor-pointer text-muted">{t('Twitch application setup')}</summary>
            <div className="space-y-2 mt-3">
              <p className="text-xs text-muted">
                {t('Register a Twitch application with the Public client type, then paste its Client ID. A client secret is not needed.')}
              </p>
              <button className="text-xs text-accent hover:underline" onClick={() => void window.studio?.openExternal(TWITCH_APPS)}>
                {t('Open Twitch developer console')}
              </button>
              <label className="block text-xs" htmlFor="twitch-client-id">{t('Twitch Client ID')}</label>
              <input id="twitch-client-id" className="input w-full" autoComplete="off" spellCheck={false}
                value={clientId} placeholder={provider.client_id || t('Public application Client ID')}
                disabled={busy || Boolean(flow)} onChange={(event) => setClientId(event.target.value)} />
              <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow) || !clientId.trim()}
                onClick={() => void act(async () => {
                  await connectedAccounts.configureTwitch(clientId.trim())
                  setClientId('')
                  setNotice(t('Twitch application saved. You can now connect an account.'))
                  await load()
                })}>
                {t('Save Client ID')}
              </button>
            </div>
          </details>

          {accounts.map((account) => (
            <div key={account.id} className="rounded border border-raised p-3 space-y-2">
              <div className="text-sm font-medium">{account.display_name || account.login}</div>
              <p className="text-xs text-muted">{statusLabel(account)}</p>
              {account.error && <p className="text-xs text-amber-400">{account.error}</p>}
              <p className="text-xs text-muted">
                {t('Permissions')}: {account.scopes.length ? account.scopes.map(permissionLabel).join(', ') : t('Stream and VOD information only')}
              </p>
              <div className="flex flex-wrap gap-2">
                <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow)} onClick={() => void act(async () => {
                  await connectedAccounts.test(account.id)
                  setNotice(t('Connection tested. The account status has been refreshed.'))
                  await load()
                })}>{t('Test connection')}</button>
                <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow) || account.status !== 'connected'} onClick={() => void watch(account)}>
                  {t('Watch my channel')}
                </button>
                <button className="btn-ghost !py-1 text-xs" disabled={busy || Boolean(flow)} onClick={() => void act(async () => {
                  await connectedAccounts.disconnect(account.id)
                  setNotice(t('Account disconnected.'))
                  await load()
                })}>{t('Disconnect')}</button>
              </div>
              {account.scopes.includes('user:read:chat') && account.status === 'connected' && (
                <div className="space-y-2 border-t border-raised pt-2">
                  <h5 className="text-xs font-semibold">{t('Viewer highlight nominations')}</h5>
                  <p className="text-xs text-muted">
                    {t('Viewers can use !clip, !chaos and !funny to support AI highlight selection. Start collection explicitly for your live or upcoming stream; it begins once Twitch confirms the stream. Earlier chat is not recovered.')}
                  </p>
                  <p className="text-xs text-muted">
                    {t('Nominations support source evidence. Using them with a recording requires a verified match between stream and recording times.')}
                  </p>
                  {nominationStatus[account.id] && (
                    <p className="text-xs" role="status">
                      {nominationStatus[account.id].monitoring
                        ? t('Collection enabled for your live or upcoming stream.')
                        : t('Collection stopped.')}
                    </p>
                  )}
                  {nominationErrors[account.id] && <p className="text-xs text-amber-400">{nominationErrors[account.id]}</p>}
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
                    <p key={stream.stream_id} className="text-xs text-muted">
                      {t('Stream')} {stream.stream_id}
                      {stream.stream_start ? ` · ${new Date(stream.stream_start).toLocaleString()}` : ''}
                      {' · '}{stream.ended_at ? t('Ended') : t('No end recorded')}
                      {' · '}{stream.nomination_count} {t('nominations')}
                    </p>
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
              <p className="text-xs text-muted">{t('Choose the permissions for this connection. Changing them requires signing in again.')}</p>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={clips} disabled={busy} onChange={(event) => setClips(event.target.checked)} />{t('Create native Twitch Clips during a live stream')}</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={vodClips} disabled={busy} onChange={(event) => setVodClips(event.target.checked)} />{t('Manage Twitch Clips for my channel, including public VOD clips')}</label>
              <p className="text-xs text-muted">{t('VOD clip permission is optional and off by default. It lets approved workflows create public Twitch clips; collection alone does not publish clips.')}</p>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={readChat} disabled={busy} onChange={(event) => setReadChat(event.target.checked)} />{t('Read Twitch chat and chat events')}</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={sendChat} disabled={busy} onChange={(event) => setSendChat(event.target.checked)} />{t('Send Twitch chat messages')}</label>
              <button className="btn-accent !py-1.5 text-sm" disabled={busy || !provider.configured || !usableStorage} onClick={() => void act(connect)}>
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
                  setNotice(t('Twitch sign-in cancelled.'))
                })}>{t('Cancel')}</button>
              </div>
            </div>
          )}
        </div>
      )}
      {notice && <p role="status" className="text-xs">{notice}</p>}
    </section>
  )
}

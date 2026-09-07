import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { rememberYoutubeEnabled, type YouTubeStatus } from '../lib/youtube'

/** Settings for publishing to YouTube.
 *
 *  This card is the ONLY YouTube surface in the app until it is switched on.
 *  The editor renders nothing at all while `enabled` is false — no greyed-out
 *  tab, no placeholder — because most people will never bring a Google Cloud
 *  key and the editor should not carry a permanent advertisement for a feature
 *  they cannot use.
 */

const AUDIT_FORM = 'https://support.google.com/youtube/contact/yt_api_form'
const CLOUD_CONSOLE = 'https://console.cloud.google.com/projectcreate'

export default function YouTubeCard(): JSX.Element {
  const [status, setStatus] = useState<YouTubeStatus>({ enabled: false })
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [showSecret, setShowSecret] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [connecting, setConnecting] = useState(false)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = (): void => {
    api
      .youtubeStatus()
      .then((s) => {
        setStatus(s)
        rememberYoutubeEnabled(Boolean(s.enabled))
      })
      .catch(() => setStatus({ enabled: false }))
  }

  useEffect(load, [])
  useEffect(
    () => () => {
      if (poll.current) clearInterval(poll.current)
    },
    []
  )

  const setEnabled = async (enabled: boolean): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const r = await api.patchYoutubeSettings({ enabled })
      setStatus(r.status)
      rememberYoutubeEnabled(Boolean(r.status.enabled))
    } catch (e) {
      setNotice(String(e))
    } finally {
      setBusy(false)
    }
  }

  const saveKey = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      await api.putYoutubeCredentials(clientId, clientSecret)
      setClientId('')
      setClientSecret('')
      setNotice(t('Saved. Now connect your channel.'))
      load()
    } catch (e) {
      setNotice(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const connect = async (playlists: boolean, add = false): Promise<void> => {
    setConnecting(true)
    setNotice(
      add
        ? t('A browser window has opened — pick the OTHER channel there, not the one already connected.')
        : t('A browser window has opened — finish signing in there.')
    )
    try {
      await api.startYoutubeConnect(playlists, add)
    } catch (e) {
      setConnecting(false)
      setNotice(String(e).replace(/^Error:\s*/, ''))
      return
    }
    poll.current = setInterval(async () => {
      try {
        const r = await api.pollYoutubeConnect()
        if (r.state === 'waiting') return
        if (poll.current) clearInterval(poll.current)
        setConnecting(false)
        if (r.state === 'error') {
          setNotice(r.error || t('Connecting failed.'))
        } else {
          setNotice(t('Connected.'))
          if (r.status) setStatus(r.status)
          else load()
        }
      } catch {
        /* keep polling; the backend may just be busy */
      }
    }, 1500)
  }

  const disconnect = async (channelId?: string): Promise<void> => {
    setBusy(true)
    try {
      await api.youtubeDisconnect(channelId)
      setNotice(t('Disconnected.'))
      load()
    } catch (e) {
      setNotice(String(e))
    } finally {
      setBusy(false)
    }
  }

  const makeDefault = async (channelId: string): Promise<void> => {
    setBusy(true)
    try {
      const r = await api.setDefaultYoutubeAccount(channelId)
      setStatus(r.status)
    } catch (e) {
      setNotice(String(e))
    } finally {
      setBusy(false)
    }
  }

  const removeKey = async (): Promise<void> => {
    setBusy(true)
    try {
      await api.deleteYoutubeCredentials()
      setNotice(t('Removed.'))
      load()
    } catch (e) {
      setNotice(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card space-y-3" aria-label={t('Publish to YouTube')}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">{t('Publish to YouTube')}</h3>
          <p className="text-xs text-muted mt-0.5">
            {t(
              'Upload finished clips straight from the editor. Needs your own Google API key — free, about ten minutes to set up.'
            )}
          </p>
        </div>
        <label className="inline-flex items-center gap-2 text-sm shrink-0">
          <input
            type="checkbox"
            checked={Boolean(status.enabled)}
            disabled={busy}
            onChange={(e) => setEnabled(e.target.checked)}
            aria-label={t('Enable YouTube publishing')}
          />
          {status.enabled ? t('On') : t('Off')}
        </label>
      </div>

      {status.enabled && (
        <div className="space-y-3 border-t border-raised/60 pt-3">
          {status.connected ? (
            <div className="space-y-2">
              <ul className="space-y-1">
                {(status.accounts ?? []).map((account) => (
                  <li
                    key={account.id}
                    className="flex items-center justify-between gap-2 text-sm border border-raised/60 rounded-lg px-3 py-2"
                  >
                    <span className="truncate">
                      {account.handle || account.title}
                      {account.default && (
                        <span className="text-[11px] text-accent ml-2">{t('default')}</span>
                      )}
                    </span>
                    <span className="flex gap-2 shrink-0">
                      {!account.default && (
                        <button
                          className="text-[11px] text-accent hover:underline"
                          disabled={busy}
                          onClick={() => makeDefault(account.id)}
                        >
                          {t('Make default')}
                        </button>
                      )}
                      <button
                        className="text-[11px] text-muted hover:text-ink"
                        disabled={busy}
                        onClick={() => disconnect(account.id)}
                      >
                        {t('Disconnect')}
                      </button>
                    </span>
                  </li>
                ))}
                {(status.accounts ?? []).length === 0 && (
                  <li className="text-sm">
                    {t('Publishing as')}{' '}
                    <span className="font-medium">
                      {status.channel?.handle || status.channel?.title || t('your channel')}
                    </span>
                  </li>
                )}
              </ul>

              <button
                className="btn-ghost w-full !py-1.5 text-xs"
                disabled={busy || connecting}
                onClick={() => connect(Boolean(status.playlists_available), true)}
              >
                {connecting ? t('Waiting for Google…') : t('Add another channel')}
              </button>
              <p className="text-[11px] text-muted">
                {t(
                  'Google asks which channel to grant access to. To add a second one, sign in again and pick a different channel on that screen — picking the same one just updates it.'
                )}
              </p>
              {status.quota && (
                <p className="text-xs text-muted tabular-nums">
                  {status.quota.remaining} {t('of')} {status.quota.uploads_limit}{' '}
                  {t('uploads left today')}
                </p>
              )}
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={Boolean(status.playlists_available)}
                  disabled={busy || connecting}
                  onChange={(e) => {
                    if (e.target.checked) connect(true)
                  }}
                />
                <span>
                  {t('Add videos to playlists')}
                  <span className="block text-[11px] text-muted">
                    {t(
                      'Needs one extra permission from Google, because YouTube will not let an upload-only app touch playlists. Ticking this reopens the sign-in.'
                    )}
                  </span>
                </span>
              </label>
              <div className="flex gap-2 pt-1">
                <button className="btn-ghost !px-3 !py-1 text-xs" disabled={busy} onClick={removeKey}>
                  {t('Remove API key')}
                </button>
              </div>
            </div>
          ) : (
            <SetupWizard
              hasClient={Boolean(status.has_client)}
              clientId={clientId}
              clientSecret={clientSecret}
              showSecret={showSecret}
              busy={busy}
              connecting={connecting}
              backend={status.backend ?? 'file'}
              onClientId={setClientId}
              onClientSecret={setClientSecret}
              onToggleSecret={() => setShowSecret((v) => !v)}
              onSave={saveKey}
              onConnect={() => connect(false)}
            />
          )}

          {notice && <p className="text-xs text-accent">{notice}</p>}
        </div>
      )}
    </div>
  )
}

interface WizardProps {
  hasClient: boolean
  clientId: string
  clientSecret: string
  showSecret: boolean
  busy: boolean
  connecting: boolean
  backend: string
  onClientId: (v: string) => void
  onClientSecret: (v: string) => void
  onToggleSecret: () => void
  onSave: () => void
  onConnect: () => void
}

function SetupWizard(props: WizardProps): JSX.Element {
  const open = (url: string) => () => window.studio.openExternal(url)

  return (
    <div className="space-y-3 text-sm">
      {/* Stated before anything else, because it is the one thing that can
          make this whole feature useless and no code can work around it. */}
      <div className="border border-warn/60 bg-warn/10 rounded-lg p-3 space-y-1">
        <p className="text-xs font-medium text-warn">{t('Read this first')}</p>
        <p className="text-[11px]">
          {t(
            'Until your Google Cloud project passes YouTube’s free audit, YouTube locks every video uploaded through it to private — permanently. You cannot make it public afterwards in Studio; the only fix is uploading it again from an audited project. Clips Kitty checks after each upload and tells you if it happened.'
          )}
        </p>
        <button className="text-[11px] text-accent hover:underline" onClick={open(AUDIT_FORM)}>
          {t('Apply for the audit')}
        </button>
      </div>

      <ol className="space-y-2 text-xs list-decimal list-inside">
        <li>
          {t('Create a Google Cloud project.')}{' '}
          <button className="text-accent hover:underline" onClick={open(CLOUD_CONSOLE)}>
            {t('Open Cloud Console')}
          </button>
        </li>
        <li>{t('In APIs & Services → Library, enable "YouTube Data API v3".')}</li>
        <li>
          <span className="font-medium">
            {t('Fill in the OAuth consent screen, then press Publish app.')}
          </span>
          <span className="block text-muted mt-0.5">
            {t(
              'This one matters: leaving it on Testing makes Google expire your sign-in every 7 days, so you would have to reconnect weekly. "In production" does not mean verified and costs nothing — you will see a "Google hasn’t verified this app" warning when you connect, and Advanced → Go to (unsafe) gets past it. It is your own app warning you about yourself.'
            )}
          </span>
        </li>
        <li>
          {t('Credentials → Create credentials → OAuth client ID → application type')}{' '}
          <span className="font-medium">{t('Desktop app')}</span>.
        </li>
        <li>{t('Paste the two values below.')}</li>
      </ol>

      <div className="space-y-2">
        <div>
          <label className="label" htmlFor="yt-client-id">
            {t('Client ID')}
          </label>
          <input
            id="yt-client-id"
            className="input"
            value={props.clientId}
            onChange={(e) => props.onClientId(e.target.value)}
            placeholder="123456789-abc.apps.googleusercontent.com"
            spellCheck={false}
          />
        </div>
        <div>
          <label className="label" htmlFor="yt-client-secret">
            {t('Client secret')}
          </label>
          <div className="flex gap-2">
            <input
              id="yt-client-secret"
              className="input"
              type={props.showSecret ? 'text' : 'password'}
              value={props.clientSecret}
              onChange={(e) => props.onClientSecret(e.target.value)}
              spellCheck={false}
            />
            <button className="btn-ghost !px-3 !py-1 text-xs shrink-0" onClick={props.onToggleSecret}>
              {props.showSecret ? t('Hide') : t('Show')}
            </button>
          </div>
        </div>
        <p className="text-[11px] text-muted">
          {props.backend === 'windows-dpapi'
            ? t(
                'Stored encrypted on this PC with Windows DPAPI, tied to your Windows account. It never leaves your machine.'
              )
            : t('Stored in a private file on this computer. It never leaves your machine.')}
        </p>
        <button
          className="btn-accent w-full !py-1.5 text-xs"
          disabled={props.busy || !props.clientId.trim() || !props.clientSecret.trim()}
          onClick={props.onSave}
        >
          {t('Save API key')}
        </button>
      </div>

      {props.hasClient && (
        <button
          className="btn-accent w-full disabled:opacity-40"
          disabled={props.busy || props.connecting}
          onClick={props.onConnect}
        >
          {props.connecting ? t('Waiting for Google…') : t('Connect your YouTube channel')}
        </button>
      )}
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { PLATFORMS, WOOPSOCIAL_PLATFORMS, platformLabel } from '../lib/uploadpost'

/** Settings for publishing through WoopSocial.
 *
 *  The provider Clips Kitty leads with, and the default in the editor.
 *  Bring-your-own-key like the other one: the creator's own account, their
 *  own connected socials, their own allowance. Clips Kitty holds no shared
 *  key and pays for nothing.
 *
 *  Why this one first: their free plan allows unlimited posts on two
 *  connected accounts with API access, which covers most creators outright.
 *  Upload-Post reaches more destinations and does thumbnails, first comments
 *  and a posting queue, so it stays available below as the alternative.
 *
 *  Two rules this card must never break: display the key back, or claim an
 *  affiliate relationship that does not exist. The key is write-only over
 *  the API, and the referral CTA appears only once a URL is configured.
 */

const SIGNUP = 'https://woopsocial.com/'
const DASHBOARD = 'https://app.woopsocial.com/api-access'

type Status = {
  enabled: boolean
  has_key: boolean
  key_tail: string
  platforms: string[]
  common_description: string
  affiliate_url: string
  storage: string
}

const empty: Status = {
  enabled: false,
  has_key: false,
  key_tail: '',
  platforms: [],
  common_description: '',
  affiliate_url: '',
  storage: ''
}

export default function WoopSocialCard(): JSX.Element {
  const [status, setStatus] = useState<Status>(empty)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [connected, setConnected] = useState<string[]>([])
  const [offered, setOffered] = useState('')
  const [affiliateDraft, setAffiliateDraft] = useState('')
  const mounted = useRef(true)

  const load = (): void => {
    api
      .woopSocialStatus()
      .then((s) => {
        if (!mounted.current) return
        setStatus(s as unknown as Status)
        setAffiliateDraft(s.affiliate_url)
      })
      .catch(() => {
        /* backend not up yet */
      })
    api
      .woopSocialConnections()
      .then((got) => mounted.current && setConnected(got.connected))
      .catch(() => {
        /* off, or no key yet */
      })
  }

  useEffect(() => {
    mounted.current = true
    load()
    return () => {
      mounted.current = false
    }
  }, [])

  const say = (m: string): void => {
    setError('')
    setNotice(m)
  }
  const failed = (e: unknown): void => {
    setNotice('')
    setError(String(e).replace(/^Error:\s*/, ''))
  }

  const toggle = async (enabled: boolean): Promise<void> => {
    try {
      setStatus((await api.patchWoopSocialSettings({ enabled })) as unknown as Status)
    } catch (e) {
      failed(e)
    }
  }

  const saveKey = async (value?: string): Promise<void> => {
    const key = (value ?? apiKey).trim()
    if (!key || busy) return
    setBusy(true)
    setOffered('')
    try {
      const got = await api.putWoopSocialKey(key)
      setStatus(got as unknown as Status)
      setApiKey('')
      setShowKey(false)
      say(t('Connected.'))
      load()
    } catch (e) {
      failed(e)
    } finally {
      setBusy(false)
    }
  }

  const removeKey = async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    try {
      setStatus((await api.deleteWoopSocialKey()) as unknown as Status)
      setConnected([])
      say(t('API key removed.'))
    } catch (e) {
      failed(e)
    } finally {
      setBusy(false)
    }
  }

  /** WoopSocial authorises one platform at a time rather than offering one
   *  hosted page for all of them, so each gets its own button. */
  const connect = async (platform: string): Promise<void> => {
    if (busy) return
    setBusy(true)
    try {
      const got = await api.woopSocialConnect(platform)
      const opened = await window.studio.openExternal(got.url)
      if (opened) {
        say(
          `${t('Opened')} ${platformLabel(platform)} ${t('in your browser. Come back when it is linked.')}`
        )
        window.addEventListener(
          'focus',
          () => {
            api
              .woopSocialConnections()
              .then((c) => mounted.current && setConnected(c.connected))
              .catch(() => {})
          },
          { once: true }
        )
      } else {
        failed(t('Could not open your browser.'))
      }
    } catch (e) {
      failed(e)
    } finally {
      setBusy(false)
    }
  }

  const saveAffiliate = async (): Promise<void> => {
    const url = affiliateDraft.trim()
    if (url === status.affiliate_url) return
    if (url && !/^https:\/\//i.test(url)) {
      failed(t('That needs to be a full https:// link.'))
      return
    }
    try {
      setStatus(
        (await api.patchWoopSocialSettings({ affiliate_url: url })) as unknown as Status
      )
      say(url ? t('Referral link saved.') : t('Referral link cleared.'))
    } catch (e) {
      failed(e)
    }
  }

  const watchForKey = (): void => {
    window.addEventListener(
      'focus',
      () => {
        window.studio
          .readClipboardKey()
          .then((found) => mounted.current && found && setOffered(found))
          .catch(() => {})
      },
      { once: true }
    )
  }

  const open = (url: string) => () => {
    void window.studio.openExternal(url)
    if (!status.has_key) watchForKey()
  }

  const affiliate = status.affiliate_url.trim()
  const mine = PLATFORMS.filter((p) => WOOPSOCIAL_PLATFORMS.includes(p.id))

  return (
    <section className="card space-y-3" aria-label="WoopSocial publishing">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-semibold">{t('Publish through WoopSocial')}</h3>
          <p className="text-xs text-muted mt-1 max-w-xl">
            {t(
              'Send a clip to YouTube, TikTok, Instagram and more in one go, through your own WoopSocial account. Their free plan allows unlimited posts on two connected accounts. Your accounts are connected on WoopSocial, not here, so Clips Kitty never asks for a social password.'
            )}
          </p>
        </div>
        <label className="inline-flex items-center gap-2 text-sm shrink-0">
          <input
            type="checkbox"
            checked={status.enabled}
            disabled={busy}
            onChange={(e) => void toggle(e.target.checked)}
            aria-label={t('Enable publishing through WoopSocial')}
          />
          {status.enabled ? t('On') : t('Off')}
        </label>
      </div>

      {status.enabled && (
        <>
          <p className="text-[11px] text-muted border-l-2 border-raised pl-2">
            {t(
              'Publishing this way sends the clip and its details to WoopSocial, which delivers them to the platforms you pick. Everything else in Clips Kitty still runs on your PC.'
            )}
          </p>

          <div className="space-y-2">
            <label className="label block" htmlFor="woopsocial-key">
              {t('API key')}
            </label>
            {status.has_key ? (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-success">
                  ● {t('Key saved')}
                  {status.key_tail && <span className="text-muted"> ····{status.key_tail}</span>}
                </span>
                <button className="btn-ghost !py-1 !px-3 text-xs" onClick={() => void removeKey()}>
                  {t('Remove')}
                </button>
                {status.storage && (
                  <span className="text-[11px] text-muted">
                    {t('Stored with')} {status.storage}
                  </span>
                )}
              </div>
            ) : (
              <>
                {offered && (
                  <div className="flex items-center gap-2 flex-wrap border border-accent/40 bg-accent/10 rounded-lg p-2 mb-2">
                    <span className="text-xs">
                      {t('Found a key on your clipboard')}{' '}
                      <span className="text-muted">····{offered.slice(-4)}</span>
                    </span>
                    <button
                      className="btn-accent !py-1 !px-3 text-xs"
                      disabled={busy}
                      onClick={() => void saveKey(offered)}
                    >
                      {t('Use it')}
                    </button>
                    <button
                      className="btn-ghost !py-1 !px-3 text-xs"
                      onClick={() => setOffered('')}
                    >
                      {t('No thanks')}
                    </button>
                  </div>
                )}
                <div className="flex gap-2 flex-wrap">
                  <input
                    id="woopsocial-key"
                    className="input flex-1 !py-1 text-sm min-w-52"
                    type={showKey ? 'text' : 'password'}
                    value={apiKey}
                    placeholder={t('Paste your WoopSocial API key')}
                    onChange={(e) => setApiKey(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button
                    className="btn-ghost !py-1 !px-3 text-xs"
                    onClick={() => setShowKey((v) => !v)}
                  >
                    {showKey ? t('Hide') : t('Show')}
                  </button>
                  <button
                    className="btn-accent !py-1 !px-3 text-xs"
                    disabled={busy || !apiKey.trim()}
                    onClick={() => void saveKey()}
                  >
                    {busy ? t('Checking…') : t('Save and check')}
                  </button>
                </div>
              </>
            )}
          </div>

          {status.has_key && (
            <div className="space-y-2">
              <p className="label">{t('Connected accounts')}</p>
              <div className="flex flex-wrap gap-2">
                {mine.map((p) => {
                  const on = connected.includes(p.id)
                  return (
                    <button
                      key={p.id}
                      className={`px-2.5 py-1 rounded-lg text-xs border ${
                        on
                          ? 'border-success/50 text-success'
                          : 'border-raised text-muted hover:text-ink'
                      }`}
                      disabled={busy}
                      onClick={() => void connect(p.id)}
                      title={on ? t('Connected - click to reconnect') : t('Click to connect')}
                    >
                      {on ? '● ' : '○ '}
                      {p.label}
                    </button>
                  )
                })}
              </div>
              <p className="text-[11px] text-muted">
                {t(
                  'Each opens WoopSocial in your browser. Their free plan allows two connected accounts.'
                )}
              </p>
            </div>
          )}

          {!status.has_key && (
            <div className="border border-raised rounded-lg p-3 space-y-2">
              <p className="text-sm font-medium">{t('Don’t have a WoopSocial account?')}</p>
              <p className="text-xs text-muted">
                {t(
                  'The free plan includes unlimited posts on two social accounts and API access. Copy your API key from their dashboard.'
                )}
              </p>
              <div className="flex gap-2 flex-wrap">
                <button
                  className="btn-accent !py-1 !px-3 text-xs"
                  onClick={open(affiliate || SIGNUP)}
                >
                  {t('Create a WoopSocial account ↗')}
                </button>
                <button className="btn-ghost !py-1 !px-3 text-xs" onClick={open(DASHBOARD)}>
                  {t('I already have one ↗')}
                </button>
              </div>
              {affiliate && (
                <p className="text-[11px] text-muted">
                  {t(
                    'Affiliate link - Clips Kitty may earn a commission if you sign up through it, at no extra cost to you.'
                  )}
                </p>
              )}
            </div>
          )}

          {status.has_key && (
            <details className="text-[11px] text-muted">
              <summary className="cursor-pointer">{t('Affiliate referral link')}</summary>
              <input
                className="input !py-1 text-xs w-full mt-1.5"
                value={affiliateDraft}
                placeholder="https://…"
                onChange={(e) => setAffiliateDraft(e.target.value)}
                onBlur={() => void saveAffiliate()}
                aria-label="WoopSocial affiliate referral URL"
              />
            </details>
          )}

          <div className="text-[11px] text-muted">
            {t('Can publish to:')} {mine.map((p) => p.label).join(', ')}.{' '}
            {t('Bluesky needs Upload-Post instead.')}
          </div>
        </>
      )}

      {notice && <p className="text-xs text-success">{notice}</p>}
      {error && <p className="text-xs text-error">{error}</p>}
    </section>
  )
}

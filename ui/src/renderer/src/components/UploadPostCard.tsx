import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { PLATFORMS, type UploadPostStatus } from '../lib/uploadpost'

/** Settings for publishing to several platforms through Upload-Post.
 *
 *  Bring your own key. Clips Kitty never pays for anyone's Upload-Post usage,
 *  holds no shared key and proxies nothing: the user's own account, their own
 *  connected socials, their own allowance. With no key entered this card is
 *  the only Upload-Post surface in the app — the editor shows nothing, the
 *  same rule YouTubeCard follows.
 *
 *  Two things this card must never do: display the API key back, and claim an
 *  affiliate relationship that does not exist. The key is write-only over the
 *  API (the backend returns has_key and a four-character tail), and the
 *  referral CTA appears only when an approved URL has been configured.
 */

const SIGNUP = 'https://www.upload-post.com/'
const DASHBOARD = 'https://app.upload-post.com/'

const empty: UploadPostStatus = {
  enabled: false,
  has_key: false,
  key_tail: '',
  profile: '',
  platforms: [],
  common_description: '',
  first_comment: '',
  affiliate_url: '',
  storage: ''
}

export default function UploadPostCard(): JSX.Element {
  const [status, setStatus] = useState<UploadPostStatus>(empty)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [profile, setProfile] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  // Held locally while typed, saved on blur — a PATCH per keystroke would be
  // absurd for a paragraph.
  const [common, setCommon] = useState('')
  const [commonSaved, setCommonSaved] = useState('')
  const [connected, setConnected] = useState<string[]>([])
  // A key-shaped thing spotted on the clipboard after they went to fetch one.
  const [offered, setOffered] = useState('')
  const [affiliateDraft, setAffiliateDraft] = useState('')
  const mounted = useRef(true)
  const watcher = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = (): void => {
    api
      .uploadPostStatus()
      .then((s) => {
        if (!mounted.current) return
        setStatus(s)
        setProfile(s.profile)
        setCommon(s.common_description)
        setCommonSaved(s.common_description)
        setAffiliateDraft(s.affiliate_url)
      })
      .catch(() => {
        /* backend not up yet; the card just shows its off state */
      })
  }

  useEffect(() => {
    mounted.current = true
    load()
    api
      .uploadPostConnections()
      .then((got) => mounted.current && setConnected(got.connected))
      .catch(() => {
        /* off, or no key yet */
      })
    return () => {
      mounted.current = false
      if (watcher.current) clearInterval(watcher.current)
    }
  }, [])

  const say = (message: string): void => {
    setError('')
    setNotice(message)
  }

  const failed = (e: unknown): void => {
    setNotice('')
    setError(String(e).replace(/^Error:\s*/, ''))
  }

  const toggle = async (enabled: boolean): Promise<void> => {
    try {
      setStatus(await api.patchUploadPostSettings({ enabled }))
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
      const got = await api.putUploadPostKey(key)
      setStatus(got)
      // Clear the field the moment it is stored. Leaving a key sitting in an
      // input is how it ends up in a screenshot on a bug report.
      setApiKey('')
      setShowKey(false)
      say(got.plan ? t('Connected. Plan:') + ` ${got.plan}` : t('Connected.'))
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
      setStatus(await api.deleteUploadPostKey())
      say(t('API key removed.'))
    } catch (e) {
      failed(e)
    } finally {
      setBusy(false)
    }
  }

  /** Poll until the linked accounts change, so setup completes by itself.
   *
   *  The connection happens on Upload-Post's page in the real browser —
   *  Google refuses OAuth inside an embedded webview, so there is no
   *  in-app version of this to build. What the app CAN do is notice when
   *  the user comes back, rather than making them press refresh. */
  const watchForConnections = (before: string[]): void => {
    if (watcher.current) clearInterval(watcher.current)
    let ticks = 0
    watcher.current = setInterval(() => {
      ticks += 1
      if (ticks > 40 || !mounted.current) {
        if (watcher.current) clearInterval(watcher.current)
        return
      }
      api
        .uploadPostConnections()
        .then((got) => {
          if (!mounted.current) return
          setConnected(got.connected)
          if (got.connected.length > before.length) {
            if (watcher.current) clearInterval(watcher.current)
            say(`${t('Connected')}: ${got.connected.join(', ')}.`)
          }
        })
        .catch(() => {
          /* still mid-connection, keep waiting */
        })
    }, 3000)
  }

  const manageAccounts = async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    try {
      const got = await api.uploadPostConnect(profile.trim())
      const opened = await window.studio.openExternal(got.url)
      if (opened) {
        say(
          t(
            'Opened Upload-Post in your browser. Link your accounts there - this page updates on its own when you are done.'
          )
        )
        setStatus({ ...status, profile: got.profile })
        watchForConnections(connected)
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
    // Refuse anything that is not an https link rather than storing a typo
    // that would make the signup button silently do nothing.
    if (url && !/^https:\/\//i.test(url)) {
      failed(t('That needs to be a full https:// link.'))
      return
    }
    try {
      setStatus(await api.patchUploadPostSettings({ affiliate_url: url }))
      say(url ? t('Referral link saved.') : t('Referral link cleared.'))
    } catch (e) {
      failed(e)
    }
  }

  const saveCommon = async (): Promise<void> => {
    if (common === commonSaved) return
    try {
      setStatus(await api.patchUploadPostSettings({ common_description: common }))
      setCommonSaved(common)
    } catch (e) {
      failed(e)
    }
  }

  /** Opening the signup or dashboard means they have gone to fetch a key.
   *
   *  Watch for them coming back, and if a key-shaped thing is on the
   *  clipboard, offer it. This is the one genuinely fiddly step of BYOK —
   *  copy a credential off a website, find the right box, paste — and it is
   *  the part worth removing. Offered, never applied on its own, and the
   *  main process only hands back something that looks like a key, so
   *  nothing else on the clipboard is readable from here.
   */
  const watchForKey = (): void => {
    const check = (): void => {
      window.studio
        .readClipboardKey()
        .then((found) => {
          if (!mounted.current || !found) return
          setOffered(found)
        })
        .catch(() => {
          /* no clipboard access, no offer */
        })
    }
    window.addEventListener('focus', check, { once: true })
  }

  const open = (url: string) => () => {
    void window.studio.openExternal(url)
    if (!status.has_key) watchForKey()
  }
  const affiliate = status.affiliate_url.trim()

  return (
    <section className="card space-y-3" aria-label="Upload-Post publishing">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-semibold">{t('Upload-Post')}</h3>
          <p className="text-xs text-muted mt-1 max-w-xl">
            {t(
              'Another way to post to your socials, if you already have an Upload-Post account or want Bluesky, custom thumbnails, first comments or a posting queue. Otherwise WoopSocial above covers most of this on a free plan.'
            )}
          </p>
        </div>
        {/* On/Off rather than a bare "Enable", to read the same as the
            YouTube card directly above it. */}
        <label className="inline-flex items-center gap-2 text-sm shrink-0">
          <input
            type="checkbox"
            checked={status.enabled}
            disabled={busy}
            onChange={(e) => void toggle(e.target.checked)}
            aria-label={t('Enable publishing to social media platforms')}
          />
          {status.enabled ? t('On') : t('Off')}
        </label>
      </div>

      {status.enabled && (
        <>
          {/* Honest about what leaves the machine. Everything else in Clips
              Kitty runs locally; this one step does not, and saying so here
              is better than someone discovering it afterwards. */}
          <p className="text-[11px] text-muted border-l-2 border-raised pl-2">
            {t(
              'Publishing this way sends the clip and its details to Upload-Post, which delivers them to the platforms you pick. Everything else in Clips Kitty still runs on your PC.'
            )}
          </p>

          {/* Three steps, numbered, because the account lives somewhere
              else and a creator needs to know how far through they are. */}
          {!status.has_key && (
            <ol className="text-xs text-muted space-y-1 list-decimal list-inside">
              <li>{t('Create an Upload-Post account and connect your socials there')}</li>
              <li>{t('Copy your API key from their dashboard')}</li>
              <li>{t('Paste it below - that is the whole setup')}</li>
            </ol>
          )}

          <div className="space-y-2">
            <label className="label block" htmlFor="uploadpost-key">
              {status.has_key ? t('API key') : `${t('API key')} · ${t('step 3')}`}
            </label>
            {status.has_key ? (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-success">
                  ● {t('Key saved')}
                  {status.key_tail && (
                    <span className="text-muted"> ····{status.key_tail}</span>
                  )}
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
                {/* Spotted on the clipboard after they went to get one.
                    Shown, not applied — a one-click end to the copy-paste. */}
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
                  id="uploadpost-key"
                  className="input flex-1 !py-1 text-sm min-w-52"
                  type={showKey ? 'text' : 'password'}
                  value={apiKey}
                  placeholder={t('Paste your Upload-Post API key')}
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
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  className={
                    connected.length
                      ? 'btn-ghost !py-1 !px-3 text-xs'
                      : 'btn-accent !py-1 !px-3 text-xs'
                  }
                  disabled={busy}
                  onClick={() => void manageAccounts()}
                >
                  {connected.length
                    ? t('Manage social accounts ↗')
                    : t('Connect your social accounts ↗')}
                </button>
                {connected.length > 0 && (
                  <span className="text-xs text-success">
                    ● {t('Connected')}: {connected.join(', ')}
                  </span>
                )}
              </div>
              <p className="text-[11px] text-muted">
                {t(
                  'Opens Upload-Post in your browser to link your accounts. This page updates on its own when you come back.'
                )}
              </p>
              {/* Where the approved referral URL goes once it exists. Set
                  here it applies to this install only, which is what you
                  want for trying it; the link that ships to everyone is
                  AFFILIATE_URL in server/uploadpost_service.py. */}
              <details className="text-[11px] text-muted">
                <summary className="cursor-pointer">{t('Affiliate referral link')}</summary>
                <input
                  className="input !py-1 text-xs w-full mt-1.5"
                  value={affiliateDraft}
                  placeholder="https://…"
                  onChange={(e) => setAffiliateDraft(e.target.value)}
                  onBlur={() => void saveAffiliate()}
                  aria-label="Affiliate referral URL"
                />
                <p className="mt-1">
                  {affiliate
                    ? t(
                        'Set. The signup button uses this and shows an affiliate disclosure beside it.'
                      )
                    : t(
                        'Empty. The signup button goes to Upload-Post directly and claims no commission.'
                      )}
                </p>
              </details>

              {/* Demoted to a detail. Every upload has to name a profile,
                  but a creator with one set of accounts should never think
                  about it, so it comes pre-filled and folded away. */}
              <details className="text-[11px] text-muted">
                <summary className="cursor-pointer">{t('Profile name')}</summary>
                <input
                  id="uploadpost-profile"
                  className="input !py-1 text-xs !w-52 mt-1.5"
                  value={profile}
                  onChange={(e) => setProfile(e.target.value)}
                  onBlur={() => void api.patchUploadPostSettings({ profile: profile.trim() })}
                  aria-label="Upload-Post profile name"
                />
                <p className="mt-1">
                  {t('Only matters if you keep separate sets of social accounts.')}
                </p>
              </details>
            </div>
          )}

          {status.has_key && (
            <div className="space-y-1">
              <label className="label block" htmlFor="uploadpost-common">
                {t('Added to every description')}
              </label>
              <textarea
                id="uploadpost-common"
                className="input text-sm !py-2"
                rows={3}
                value={common}
                placeholder={t('Your Twitch, your Discord, anything that goes on every post')}
                onChange={(e) => setCommon(e.target.value)}
                onBlur={() => void saveCommon()}
              />
            </div>
          )}

          {!status.has_key && (
            <div className="border border-raised rounded-lg p-3 space-y-2">
              <p className="text-sm font-medium">{t('Don’t have an Upload-Post account?')}</p>
              <p className="text-xs text-muted">
                {t(
                  'Create one, connect your social accounts, and copy your API key from their dashboard. Their free plan includes 10 uploads a month; TikTok needs a paid plan.'
                )}
              </p>
              <div className="flex gap-2 flex-wrap">
                <button
                  className="btn-accent !py-1 !px-3 text-xs"
                  onClick={open(affiliate || SIGNUP)}
                >
                  {t('Create an Upload-Post account ↗')}
                </button>
                <button className="btn-ghost !py-1 !px-3 text-xs" onClick={open(DASHBOARD)}>
                  {t('I already have one ↗')}
                </button>
              </div>
              {/* Shown only when a real approved referral URL is configured.
                  With none set the button above goes to the plain official
                  site and no commission is claimed, because none is earned. */}
              {affiliate && (
                <p className="text-[11px] text-muted">
                  {t(
                    'Affiliate link - Clips Kitty may earn a commission if you sign up through it, at no extra cost to you.'
                  )}
                </p>
              )}
            </div>
          )}

          <div className="text-[11px] text-muted">
            {t('Can publish to:')} {PLATFORMS.map((p) => p.label).join(', ')}
          </div>
        </>
      )}

      {notice && <p className="text-xs text-success">{notice}</p>}
      {error && <p className="text-xs text-error">{error}</p>}
    </section>
  )
}

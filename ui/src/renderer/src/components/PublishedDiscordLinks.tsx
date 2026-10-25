import { useEffect, useState } from 'react'
import { api, errorText } from '../lib/api'
import { discord, type Destination } from '../lib/campaigns'
import { t } from '../lib/i18n'
import { platformLabel, type PlatformRow } from '../lib/uploadpost'

/** Send an existing post's link from the editor without uploading again. */
export default function PublishedDiscordLinks({ clipId }: { clipId: number }): JSX.Element | null {
  const [destinations, setDestinations] = useState<Destination[]>([])
  const [posts, setPosts] = useState<PlatformRow[]>([])
  const [destination, setDestination] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [messages, setMessages] = useState<Record<string, string>>({})
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    const load = async (): Promise<void> => {
      try {
        const choices = (await discord.destinations()).destinations
          .filter((d) => d.state === 'connected' && d.has_token)
        if (!live) return
        setDestinations(choices)
        if (!choices.length) return
        const got = await api.clipPosts(clipId)
        if (live) setPosts(got.posts.filter((p) => p.live && p.post_url))
      } catch {
        // An optional Discord section must not disturb editing.
      }
    }
    void load()
    const timer = setInterval(() => void load(), 20000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [clipId])

  const keyFor = (post: PlatformRow): string => `${destination}:${post.provider}:${post.platform}`
  const send = async (post: PlatformRow): Promise<void> => {
    if (destination === null || busy) return
    setBusy(true)
    setError('')
    try {
      const got = await discord.sendPublished(clipId, {
        destination_id: destination, platform: post.platform, provider: post.provider
      })
      const message = got.check_discord ? t('Check Discord: this link may already be posted.')
        : got.already_sent ? t('Already sent to Discord') : t('Queued for Discord')
      setMessages((before) => ({ ...before, [keyFor(post)]: message }))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  if (!destinations.length || !posts.length) return null
  return (
    <section className="border border-raised/60 rounded-lg p-3 space-y-2">
      <h3 className="label">{t('Published links')}</h3>
      <label className="block text-xs space-y-1">
        <span>{t('Send links to Discord')}</span>
        <select
          className="input"
          value={destination ?? ''}
          disabled={busy}
          onChange={(e) => setDestination(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">{t('None')}</option>
          {destinations.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
        </select>
      </label>
      {posts.map((post) => (
        <div key={`${post.provider}:${post.platform}`} className="flex items-center gap-3 text-xs flex-wrap">
          <span>{platformLabel(post.platform)}</span>
          <span className="text-muted">
            {post.provider === 'youtube' ? t('direct upload')
              : post.provider === 'woopsocial' ? 'WoopSocial' : 'Upload-Post'}
          </span>
          <button className="text-accent hover:underline" onClick={() => window.studio.openExternal(post.post_url)}>
            {t('Open')}
          </button>
          <button
            className="btn-ghost !py-1 text-xs ml-auto"
            disabled={busy || destination === null || Boolean(messages[keyFor(post)])}
            onClick={() => void send(post)}
          >
            {messages[keyFor(post)] || t('Send to Discord')}
          </button>
        </div>
      ))}
      {error && <p className="text-xs text-error">{error}</p>}
    </section>
  )
}

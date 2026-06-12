import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { Settings as SettingsT } from '../lib/types'

export default function Settings(): JSX.Element {
  const [settings, setSettings] = useState<SettingsT | null>(null)
  const [channel, setChannel] = useState('')
  const [privacy, setPrivacy] = useState('public')
  const [autoUpload, setAutoUpload] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setSettings(s)
        setChannel(s.channel)
        setPrivacy(s.privacy)
        setAutoUpload(s.auto_upload)
      })
      .catch(() => setSettings(null))
  }, [])

  const save = async (): Promise<void> => {
    try {
      await api.patchSettings({ channel, privacy, auto_upload: autoUpload })
      setNotice('Saved. Pipeline-level changes apply on next backend start.')
    } catch (e) {
      setNotice(`Error: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  if (!settings) return <div className="p-6 text-muted">Connecting to backend…</div>

  return (
    <div className="p-6 space-y-5 max-w-xl">
      <h2 className="text-2xl font-bold">Settings</h2>

      <div className="card space-y-4">
        <div>
          <label className="label">Your channel (@handle or URL)</label>
          <input
            className="input mt-1"
            value={channel}
            placeholder="@YourHandle"
            onChange={(e) => setChannel(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Upload privacy</label>
          <select className="input mt-1" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
            <option value="public">public</option>
            <option value="unlisted">unlisted</option>
            <option value="private">private</option>
          </select>
        </div>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={autoUpload}
            onChange={(e) => setAutoUpload(e.target.checked)}
            className="size-4 accent-[#38BDF8]"
          />
          <span>
            Auto-upload Shorts
            <span className="block text-xs text-muted">
              Requires YouTube API credentials — dormant until Twitch/Kick support ships.
            </span>
          </span>
        </label>
        <button className="btn-accent" onClick={save}>
          Save settings
        </button>
        {notice && <p className="text-sm text-accent">{notice}</p>}
      </div>

      <div className="card text-sm text-muted">
        The active AI model is managed on the <span className="text-ink">Models</span> page. Advanced
        options (scoring weights, tracking, captions) live in <code>config/settings.yaml</code>.
      </div>
    </div>
  )
}

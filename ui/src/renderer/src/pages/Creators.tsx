import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { CreatorDetail, CreatorSuggestion, CreatorSummary } from '../lib/types'

const PLATFORM_BADGE: Record<string, string> = {
  youtube: 'bg-red-500/15 text-red-400',
  twitch: 'bg-purple-500/15 text-purple-400',
  kick: 'bg-green-500/15 text-green-400'
}

const KNOWLEDGE_LABELS: Record<string, string> = {
  topic: 'Topics',
  game: 'Games',
  series: 'Series',
  catchphrase: 'Catchphrases',
  joke: 'Running jokes',
  collaborator: 'Collaborators',
  format: 'Formats'
}

const EVENT_CHIP: Record<string, string> = {
  announced: 'bg-blue-500/15 text-blue-400',
  in_progress: 'bg-yellow-500/15 text-yellow-400',
  completed: 'bg-green-500/15 text-green-400',
  stale: 'bg-raised text-muted'
}

function PlatformBadge({ platform }: { platform: string }): JSX.Element {
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded font-medium uppercase ${PLATFORM_BADGE[platform] ?? 'bg-raised text-muted'}`}
    >
      {platform}
    </span>
  )
}

export default function Creators(): JSX.Element {
  const [creators, setCreators] = useState<CreatorSummary[]>([])
  const [suggestions, setSuggestions] = useState<CreatorSuggestion[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [detail, setDetail] = useState<CreatorDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [addPlatform, setAddPlatform] = useState('youtube')
  const [addChannel, setAddChannel] = useState('')

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const data = await api.creators()
      setCreators(data.creators)
      setSuggestions(data.suggestions)
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (selected === null) {
      setDetail(null)
      return
    }
    api
      .creatorDetail(selected)
      .then(setDetail)
      .catch((e) => setError(String(e)))
  }, [selected])

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setError('')
    try {
      await fn()
      await refresh()
      if (selected !== null) setDetail(await api.creatorDetail(selected).catch(() => null))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const knowledgeByType = (detail?.knowledge ?? []).reduce<
    Record<string, CreatorDetail['knowledge']>
  >((acc, k) => {
    ;(acc[k.knowledge_type] ??= []).push(k)
    return acc
  }, {})

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-5">
      <div>
        <h2 className="text-xl font-bold">Creators</h2>
        <p className="text-sm text-muted mt-1">
          The app learns each creator over time — their channels, topics, running jokes and
          storylines — to pick and title clips better. Everything stays on this computer.
        </p>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded-lg px-4 py-2.5">
          {error}
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="bg-surface border border-accent/40 rounded-xl p-4 space-y-3">
          <h3 className="font-semibold text-sm">
            Possible matches — are these the same creator?
          </h3>
          {suggestions.map((s, i) => (
            <div key={i} className="flex items-center gap-3 text-sm flex-wrap">
              <span className="font-medium">{s.creator_a.name}</span>
              <PlatformBadge platform={s.creator_a.platform} />
              <span className="text-muted">+</span>
              <span className="font-medium">{s.creator_b.name}</span>
              <PlatformBadge platform={s.creator_b.platform} />
              <button
                disabled={busy}
                onClick={() => act(() => api.mergeCreators(s.creator_b.id, s.creator_a.id))}
                className="ml-auto px-3 py-1 rounded-lg bg-accent/15 text-accent text-xs font-medium hover:bg-accent/25 disabled:opacity-50"
              >
                Link as same creator
              </button>
            </div>
          ))}
          <p className="text-xs text-muted">
            Linking combines their videos, stats and learned knowledge under one profile.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
        <div className="lg:col-span-2 space-y-2">
          {creators.length === 0 && (
            <p className="text-sm text-muted">
              No creators yet — process a video and its channel will appear here automatically.
            </p>
          )}
          {creators.map((c) => (
            <button
              key={c.creator_id}
              onClick={() => setSelected(c.creator_id === selected ? null : c.creator_id)}
              className={`w-full text-left bg-surface border rounded-xl p-4 transition-colors ${
                selected === c.creator_id
                  ? 'border-accent'
                  : 'border-raised/60 hover:border-raised'
              }`}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold">{c.display_name}</span>
                {c.accounts.map((a) => (
                  <PlatformBadge key={a.account_id} platform={a.platform} />
                ))}
              </div>
              <p className="text-xs text-muted mt-1.5">
                {c.videos} video{c.videos === 1 ? '' : 's'} · {c.clips} clip
                {c.clips === 1 ? '' : 's'}
                {c.avg_score != null && ` · avg score ${c.avg_score}`}
              </p>
            </button>
          ))}
        </div>

        <div className="lg:col-span-3">
          {!detail ? (
            <p className="text-sm text-muted pt-2">
              Select a creator to see their channels and what the app has learned.
            </p>
          ) : (
            <div className="bg-surface border border-raised/60 rounded-xl p-5 space-y-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-bold text-lg">{detail.display_name}</h3>
                  {detail.aliases.length > 1 && (
                    <p className="text-xs text-muted mt-0.5">
                      Also known as: {detail.aliases.filter((a) => a !== detail.display_name).join(', ')}
                    </p>
                  )}
                </div>
                <label className="flex items-center gap-2 text-xs text-muted shrink-0 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!!detail.learning_enabled}
                    disabled={busy}
                    onChange={(e) =>
                      act(() => api.setCreatorLearning(detail.creator_id, e.target.checked))
                    }
                  />
                  Learning enabled
                </label>
              </div>

              <div>
                <h4 className="text-sm font-semibold mb-2">Channels</h4>
                <div className="space-y-1.5">
                  {detail.accounts.map((a) => (
                    <div key={a.account_id} className="flex items-center gap-2 text-sm">
                      <PlatformBadge platform={a.platform} />
                      <span>{a.username}</span>
                      {detail.accounts.length > 1 && (
                        <button
                          disabled={busy}
                          onClick={() => act(() => api.splitCreatorAccount(a.account_id))}
                          className="ml-auto text-xs text-muted hover:text-red-400"
                          title="Detach this channel into its own profile"
                        >
                          Detach
                        </button>
                      )}
                    </div>
                  ))}
                </div>
                <form
                  className="flex items-center gap-2 mt-3"
                  onSubmit={(e) => {
                    e.preventDefault()
                    if (!addChannel.trim()) return
                    void act(() =>
                      api.addCreatorAccount(detail.creator_id, addPlatform, addChannel.trim())
                    ).then(() => setAddChannel(''))
                  }}
                >
                  <select
                    value={addPlatform}
                    onChange={(e) => setAddPlatform(e.target.value)}
                    className="bg-base border border-raised rounded-lg px-2 py-1.5 text-xs"
                    aria-label="Platform of the channel to add"
                  >
                    <option value="youtube">YouTube</option>
                    <option value="twitch">Twitch</option>
                    <option value="kick">Kick</option>
                  </select>
                  <input
                    value={addChannel}
                    onChange={(e) => setAddChannel(e.target.value)}
                    placeholder="Add another channel (exact name)"
                    className="flex-1 bg-base border border-raised rounded-lg px-3 py-1.5 text-xs"
                    aria-label="Channel name to add"
                  />
                  <button
                    type="submit"
                    disabled={busy || !addChannel.trim()}
                    className="px-3 py-1.5 rounded-lg bg-accent/15 text-accent text-xs font-medium hover:bg-accent/25 disabled:opacity-50"
                  >
                    Add
                  </button>
                </form>
                <p className="text-[11px] text-muted mt-1.5">
                  Use this when the same creator has a channel the app didn&apos;t connect
                  automatically. Their future videos will land on this profile.
                </p>
              </div>

              {detail.events.length > 0 && (
                <div>
                  <h4 className="text-sm font-semibold mb-2">Storylines &amp; events</h4>
                  <div className="space-y-1.5">
                    {detail.events.map((ev) => (
                      <div key={ev.event_id} className="flex items-center gap-2 text-sm">
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${EVENT_CHIP[ev.status] ?? 'bg-raised text-muted'}`}
                        >
                          {ev.status.replace('_', ' ')}
                        </span>
                        <span>{ev.event_name}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <h4 className="text-sm font-semibold mb-2">Learned knowledge</h4>
                {detail.knowledge.length === 0 ? (
                  <p className="text-xs text-muted">
                    Nothing yet — knowledge builds up as this creator&apos;s videos are processed.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {Object.entries(knowledgeByType).map(([type, items]) => (
                      <div key={type}>
                        <p className="text-xs text-muted font-medium mb-1">
                          {KNOWLEDGE_LABELS[type] ?? type}
                        </p>
                        <div className="space-y-1">
                          {items!.map((k) => (
                            <div key={k.knowledge_id} className="flex items-center gap-2 text-sm">
                              <span className="flex-1">{k.information}</span>
                              <button
                                disabled={busy}
                                onClick={() =>
                                  act(() =>
                                    api.deleteCreatorKnowledge(detail.creator_id, k.knowledge_id)
                                  )
                                }
                                className="text-xs text-muted hover:text-red-400"
                                title="Delete — this fact is wrong"
                                aria-label={`Delete learned fact: ${k.information}`}
                              >
                                🗑
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <h4 className="text-sm font-semibold mb-1">Your preferences for this creator</h4>
                {detail.preferences ? (
                  <p className="text-xs text-muted">
                    Active — based on {detail.preferences.signals} of your exports/edits, clip
                    scoring now leans toward what you keep
                    {detail.preferences.preferred_duration != null &&
                      ` (you tend to keep ~${Math.round(detail.preferences.preferred_duration)}s clips)`}
                    .
                  </p>
                ) : (
                  <p className="text-xs text-muted">
                    Not enough data yet — as you export and edit clips, scoring will learn what
                    works for this creator.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

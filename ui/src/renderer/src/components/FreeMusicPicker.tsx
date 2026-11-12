import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { LibrarySound } from '../lib/types'

// The list is the same for the whole session (the tracks ship inside the
// app), so it is asked for once and shared by every editor that opens.
let tracksOnce: Promise<LibrarySound[]> | null = null

/** The free music that ships with the app, without the sound effects: those
 *  have nowhere to go until the new editor's audio tracks. */
export function freeMusic(): Promise<LibrarySound[]> {
  tracksOnce ??= api
    .audioLibrary()
    .then((r) => r.items.filter((s) => s.kind === 'music'))
    .catch(() => {
      tracksOnce = null // engine not up yet: ask again next time
      return []
    })
  return tracksOnce
}

/** 75 -> "1:15" */
export function trackLength(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** A track's mood in the person's language. The four words the bundled
 *  tracks use are written out, so each is a key translations can carry; a
 *  mood this list doesn't know is shown as it is. */
export function moodLabel(mood: string): string {
  const known: Record<string, string> = {
    chill: t('chill'),
    upbeat: t('upbeat'),
    funny: t('funny'),
    tense: t('tense')
  }
  return known[mood] ?? mood
}

/** The Effects tab's "Free music" button and the list it opens: hear a track,
 *  pick it for the clip, pick it again to take it off. One track plays at a
 *  time, and it stops when this leaves the screen (another tab, the editor
 *  closing), because the player lives and dies with the component. */
export default function FreeMusicPicker({
  picked,
  onPick
}: {
  /** The clip's library track id, if its music is one. */
  picked: string | undefined
  /** A track id to use, or null to take the picked one off. */
  onPick: (id: string | null) => void
}): JSX.Element | null {
  const [tracks, setTracks] = useState<LibrarySound[]>([])
  const [open, setOpen] = useState(!!picked)
  const [playing, setPlaying] = useState<string | null>(null)
  const player = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    let alive = true
    void freeMusic().then((list) => alive && setTracks(list))
    return () => {
      alive = false
    }
  }, [])

  const stop = (): void => {
    player.current?.pause()
    player.current = null
    setPlaying(null)
  }
  useEffect(
    () => () => {
      player.current?.pause()
      player.current = null
    },
    []
  )

  const toggle = (track: LibrarySound): void => {
    const wasPlaying = playing === track.id
    stop()
    if (wasPlaying) return
    const audio = new Audio(api.audioLibraryUrl(track))
    const done = (): void => {
      if (player.current === audio) stop()
    }
    audio.onended = done
    audio.onerror = done
    player.current = audio
    setPlaying(track.id)
    audio.play().catch(done)
  }

  // A copy of the app with no library shows nothing new at all.
  if (tracks.length === 0) return null
  const current = tracks.find((s) => s.id === picked)

  return (
    <>
      {/* data-free-music: the editor's Space shortcut (play/pause the video)
          skips these buttons, so Space presses the button it is on. */}
      <button
        data-free-music
        className={`px-2.5 py-1 rounded-md ${
          picked ? 'bg-accent/15 text-accent hover:bg-accent/25' : 'bg-raised hover:bg-raised/70'
        }`}
        aria-expanded={open}
        onClick={() => {
          if (open) stop()
          setOpen(!open)
        }}
        title={t(
          'Music that comes with Clips Kitty. It is free to use in any video, also monetised ones, with no credit needed. Press play to hear a track, click its name to put it under this clip, and click it again to take it off.'
        )}
      >
        {t('Free music')}
        {/* The name shows only while the list is closed: the open list marks
            the picked track itself, and a button that grew on a pick could
            wrap and move the list away from the pointer. */}
        {current && !open ? `: ${current.title}` : ''}
      </button>
      {open && (
        <div
          data-free-music
          className="basis-full order-last grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-0.5"
        >
          {tracks.map((track) => {
            const on = track.id === picked
            return (
              <div key={track.id} className="flex items-center gap-1.5 min-w-0">
                <button
                  className="w-5 h-5 shrink-0 rounded bg-raised hover:bg-raised/70 text-[9px] leading-none"
                  aria-label={`${playing === track.id ? t('Stop') : t('Play')} ${track.title}`}
                  onClick={() => toggle(track)}
                >
                  {playing === track.id ? '■' : '▶'}
                </button>
                <button
                  className={`flex-1 min-w-0 text-left truncate rounded px-1.5 py-0.5 ${
                    on ? 'bg-accent/15 text-accent' : 'hover:bg-raised/70'
                  }`}
                  aria-pressed={on}
                  onClick={() => onPick(on ? null : track.id)}
                >
                  {track.title}
                  <span className={on ? 'opacity-70' : 'text-muted'}>
                    {' · '}
                    {moodLabel(track.mood)}
                    {' · '}
                    {trackLength(track.seconds)}
                  </span>
                </button>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}

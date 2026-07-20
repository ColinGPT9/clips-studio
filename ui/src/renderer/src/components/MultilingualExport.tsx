import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getExportFolder, pickExportFolder, setExportFolder } from '../lib/exportFolder'
import { Folder } from './icons'
import { activeLocale, t } from '../lib/i18n'
import TranslationReview from './TranslationReview'
import type { TranslationPreview } from '../lib/types'

/** Language names in the language the INTERFACE is set to: "Spanish" in an
 *  English UI, "español" in a Spanish one. Intl ships these with the OS, so
 *  there is no per-language dictionary to keep in sync. Falls back to the
 *  English name if a platform lacks the data. */
function displayName(code: string, englishName: string): string {
  try {
    const dn = new Intl.DisplayNames([activeLocale()], { type: 'language' })
    const name = dn.of(code)
    return name ? name.charAt(0).toUpperCase() + name.slice(1) : englishName
  } catch {
    return englishName
  }
}

/** Optional multilingual publishing for a finished clip.
 *
 *  Writes a subtitle track per language beside the clip (the naming
 *  YouTube expects for per-language captions), so one clip reaches viewers
 *  in every language picked. Runs entirely on this PC through the model
 *  already installed — no account, no API, no cost.
 *
 *  Nothing here touches the clip: it only adds files next to it. */

const REMEMBER = 'multilingual-languages'
// Clipping models are chosen for judgement; translation wants multilingual
// strength, and Qwen is markedly better at it than Gemma. Offered, never
// required — the app keeps working with whatever is installed.
const RECOMMENDED = 'qwen2.5:7b'

export default function MultilingualExport({
  clipId,
  videoId,
  onPreview,
  previewing
}: {
  clipId: number
  videoId?: string
  /** Draw a language's captions over the editor video (editor only). */
  onPreview?: (p: TranslationPreview | null) => void
  previewing?: string | null
}): JSX.Element {
  const [langs, setLangs] = useState<
    { code: string; name: string; native: string; can_dub: boolean; caption_font: string | null }[]
  >([])
  const [picked, setPicked] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(REMEMBER) ?? '[]')
    } catch {
      return []
    }
  })
  const [folder, setFolder] = useState('')
  const includeVideo = true
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [burnIn, setBurnIn] = useState(false)
  const [installed, setInstalled] = useState<string[]>([])
  const [transModel, setTransModel] = useState('')
  const [pulling, setPulling] = useState(false)
  const [allClips, setAllClips] = useState(false)
  const [clipCount, setClipCount] = useState(0)
  const [dubIn, setDubIn] = useState(false)
  const [canDub, setCanDub] = useState(false)
  // Side files are off by default: most people want the video and nothing
  // else cluttering the folder.
  const [wantSubs, setWantSubs] = useState(false)
  const [wantPost, setWantPost] = useState(false)
  // Chosen dubbing voice per language, remembered between sessions.
  const [voiceFor, setVoiceFor] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(localStorage.getItem('multilingual-voices') ?? '{}')
    } catch {
      return {}
    }
  })
  const [voiceList, setVoiceList] = useState<
    Record<string, { id: string; name: string; country: string; quality: string }[]>
  >({})
  // Translation is a separate first step now: the text is reviewed here
  // before anything is written. `reloadKey` re-reads it once a job lands.
  const [waiting, setWaiting] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [reviewed, setReviewed] = useState<string[]>([])

  // Load the voice menu for each picked language that can be dubbed.
  useEffect(() => {
    if (!canDub || !dubIn) return
    picked
      .filter((c) => langs.find((l) => l.code === c)?.can_dub && !voiceList[c])
      .forEach((c) => {
        api
          .voicesFor(c)
          .then((r) => setVoiceList((prev) => ({ ...prev, [c]: r.voices })))
          .catch(() => {})
      })
  }, [picked, canDub, dubIn, langs])

  // A visible <audio> element rather than a detached Audio object: the
  // first play has to fetch a ~60 MB voice, so people need to see it
  // loading and have a play button if autoplay is refused.
  const [player, setPlayer] = useState<{ url: string; label: string } | null>(null)
  const play = (language: string, voice?: string): void => {
    setNotice('')
    setPlayer({
      url: api.voicePreviewUrl(language, voice),
      label: `${displayName(language, language)} — ${voice ?? t('default voice')}`
    })
  }

  useEffect(() => {
    api
      .languages()
      .then((r) => {
        setLangs(r.languages)
        setCanDub(r.dubbing_available)
      })
      .catch(() => {})
    getExportFolder().then(setFolder)
    api.models().then((m) => setInstalled(m.installed.map((i) => i.name))).catch(() => {})
    api.settings().then((st) => setTransModel(st.translation_model || '')).catch(() => {})
  }, [])

  useEffect(() => {
    if (!videoId) return
    api.clips(videoId).then((c) => setClipCount(c.length)).catch(() => {})
  }, [videoId])

  const hasQwen = installed.some((n) => n.startsWith('qwen'))
  const useQwen = async (): Promise<void> => {
    const name = installed.find((n) => n.startsWith('qwen')) ?? RECOMMENDED
    await api.patchSettings({ translation_model: name })
    setTransModel(name)
    setNotice(`Translation will use ${name}.`)
  }

  // How many of the picked languages actually have text waiting to export.
  const readyCount = picked.filter((c) => reviewed.includes(c)).length

  const toggle = (code: string): void => {
    setPicked((prev) => {
      const next = prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
      localStorage.setItem(REMEMBER, JSON.stringify(next))
      return next
    })
  }

  const targetClips = async (): Promise<number[]> =>
    allClips && videoId ? (await api.clips(videoId)).map((c) => c.id) : [clipId]

  /** Step 1: produce the text only. No files, no rendering — so a bad
   *  translation is caught before it is burned into a video. */
  const translate = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const ids = await targetClips()
      const res = await api.translateClips({
        clip_ids: ids,
        languages: picked,
        stage: 'translate'
      })
      setNotice(
        `Translating ${res.clips} clip(s) into ${res.languages.length} language(s) — the text appears below when it's ready.`
      )
      watchJob(res.job_id)
    } catch (e) {
      setNotice(`Error: ${e instanceof Error ? e.message : String(e)}`)
      setBusy(false)
    }
  }

  /** Poll until the translation job finishes, then show the text. Polling
   *  the job (rather than the translations) is what makes a failure visible
   *  instead of leaving the panel spinning forever. */
  const watchJob = (jobId: number): void => {
    setWaiting(true)
    const started = Date.now()
    const tick = async (): Promise<void> => {
      if (Date.now() - started > 30 * 60_000) {
        setWaiting(false)
        setBusy(false)
        setNotice('Translation is taking unusually long — check the Dashboard activity feed.')
        return
      }
      try {
        const job = (await api.jobs()).find((j) => j.id === jobId)
        if (job && job.status !== 'queued' && job.status !== 'running') {
          setWaiting(false)
          setBusy(false)
          setReloadKey((k) => k + 1)
          setNotice(
            job.status === 'done'
              ? 'Translated. Read it below, fix anything wrong, then export.'
              : `Translation ${job.status}${job.error ? `: ${job.error}` : ''}`
          )
          return
        }
      } catch {
        /* backend busy — try again on the next tick */
      }
      window.setTimeout(tick, 3000)
    }
    window.setTimeout(tick, 3000)
  }

  /** Step 2: write the files, reusing the reviewed text. */
  const exportNow = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const ids = await targetClips()
      const res = await api.translateClips({
        clip_ids: ids,
        languages: picked,
        stage: 'export',
        folder,
        include_video: includeVideo,
        burn: burnIn,
        dub: dubIn,
        voices: voiceFor,
        subtitles: wantSubs,
        post_text: wantPost
      })
      setNotice(
        `Queued — ${res.clips} clip(s) × ${res.languages.length} language(s). Files land in your export folder; watch the Dashboard activity feed.`
      )
    } catch (e) {
      setNotice(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-raised/60 rounded-lg p-3 space-y-3">
      <div>
        <p className="font-medium text-sm">{t('Publish in other languages')}</p>
        <p className="text-xs text-muted mt-0.5">
          {t(
            'Translate first, read what it wrote and fix anything wrong, then export. Runs on this PC, free.'
          )}
        </p>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {langs.map((l) => (
          <button
            key={l.code}
            onClick={() => toggle(l.code)}
            className={`px-2.5 py-1 rounded-full text-xs border ${
              picked.includes(l.code)
                ? 'border-accent text-accent bg-accent/10'
                : 'border-raised text-muted hover:text-ink'
            }`}
            title={l.native}
          >
            {displayName(l.code, l.name)}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2 flex-wrap text-xs">
        <input
          className="input !w-44 !py-1"
          value={folder}
          onChange={(e) => {
            setFolder(e.target.value)
            setExportFolder(e.target.value)
          }}
          placeholder="export folder"
          title={folder}
        />
        <button
          className="btn-ghost !py-1"
          onClick={async () => {
            const chosen = await pickExportFolder()
            if (chosen) setFolder(chosen)
          }}
          aria-label="Choose export folder"
        >
          <Folder />
        </button>
        <label className="flex items-center gap-1.5 cursor-pointer text-muted">
          <input
            type="checkbox"
            className="size-3.5 accent-[#38BDF8]"
            checked={wantSubs}
            onChange={(e) => setWantSubs(e.target.checked)}
          />
          {t('Subtitle files (.srt)')}
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer text-muted">
          <input
            type="checkbox"
            className="size-3.5 accent-[#38BDF8]"
            checked={wantPost}
            onChange={(e) => setWantPost(e.target.checked)}
          />
          {t('Post text (.txt)')}
        </label>
        <label
          className="flex items-center gap-1.5 cursor-pointer text-muted"
          title="TikTok, Reels and Shorts don't read subtitle files — this makes one video per language with the captions painted in. Slower: the clip is re-rendered once without captions first."
        >
          <input
            type="checkbox"
            className="size-3.5 accent-[#38BDF8]"
            checked={burnIn}
            onChange={(e) => setBurnIn(e.target.checked)}
          />
          {t('Burn captions in (TikTok/Reels)')}
        </label>
        {canDub && (
          <label
            className="flex items-center gap-1.5 cursor-pointer text-muted"
            title="Speak the translation over the clip with a local voice. The original audio stays underneath at low volume, so music and room tone survive. Slower — each language is synthesized sentence by sentence."
          >
            <input
              type="checkbox"
              className="size-3.5 accent-[#38BDF8]"
              checked={dubIn}
              onChange={(e) => setDubIn(e.target.checked)}
            />
            {t('Dub the audio')}
          </label>
        )}
        {videoId && clipCount > 1 && (
          <label
            className="flex items-center gap-1.5 cursor-pointer text-muted"
            title="Publish every clip from this video in the chosen languages, in one run"
          >
            <input
              type="checkbox"
              className="size-3.5 accent-[#38BDF8]"
              checked={allClips}
              onChange={(e) => setAllClips(e.target.checked)}
            />
            {t('All')} {clipCount} {t('clips of this video')}
          </label>
        )}
        <div className="flex items-center gap-2 ml-auto">
          <button
            className="btn-ghost !py-1"
            disabled={busy || picked.length === 0}
            onClick={translate}
            title={
              picked.length === 0
                ? 'Pick at least one language'
                : 'Translate the captions so you can read and fix them before anything is written'
            }
          >
            {waiting ? t('Translating…') : t('Translate & review')}
          </button>
          <button
            className="btn-accent !py-1"
            disabled={busy || picked.length === 0 || !folder || readyCount === 0}
            onClick={exportNow}
            title={
              readyCount === 0
                ? 'Translate first, so you can check the text before it is written'
                : undefined
            }
          >
            {busy && !waiting ? t('Queueing…') : `${t('Export')} ${readyCount || ''}`.trim()}
          </button>
        </div>
      </div>

      <TranslationReview
        clipId={clipId}
        languages={picked}
        nameOf={(c) => displayName(c, c)}
        reloadKey={reloadKey}
        onLoaded={setReviewed}
        // The burn font comes from the language, not the panel — attach it
        // here where the language list is already loaded.
        onPreview={
          onPreview &&
          ((p) =>
            onPreview(
              p && {
                ...p,
                font: langs.find((l) => l.code === p.language)?.caption_font ?? null
              }
            ))
        }
        previewing={previewing}
      />
      {allClips && readyCount > 0 && (
        <p className="text-xs text-muted">
          {t(
            'You are reviewing this clip. The other clips use their machine translation unless you open and correct them too.'
          )}
        </p>
      )}
      {/* Translation quality depends on the model — offer the better one. */}
      {!transModel && (
        <p className="text-xs text-muted border-t border-raised/60 pt-2">
          {hasQwen ? (
            <>
              {t('Qwen is installed and translates noticeably better than the clipping model.')}{' '}
              <button className="text-accent hover:underline" onClick={useQwen}>
                {t('Use it for translation')}
              </button>
            </>
          ) : (
            <>
              {t('Translations use your clipping model. For better quality, install')}{' '}
              <code>{RECOMMENDED}</code> ({t('about 4.7 GB, one time, stays on your PC')}){' '}
              <button
                className="text-accent hover:underline disabled:opacity-50"
                disabled={pulling}
                onClick={async () => {
                  setPulling(true)
                  setNotice(`Downloading ${RECOMMENDED} — watch the Models page for progress.`)
                  try {
                    await api.pullModel(RECOMMENDED)
                    await api.patchSettings({ translation_model: RECOMMENDED })
                    setTransModel(RECOMMENDED)
                    setNotice(`${RECOMMENDED} installed and set as the translation model.`)
                  } catch (e) {
                    setNotice(`Install failed: ${e instanceof Error ? e.message : String(e)}`)
                  } finally {
                    setPulling(false)
                  }
                }}
              >
                {pulling ? t('Installing…') : t('Install & use')}
              </button>
            </>
          )}
        </p>
      )}
      {transModel && (
        <p className="text-xs text-muted border-t border-raised/60 pt-2">
          {t('Translating with')} <code>{transModel}</code>{' '}
          <button
            className="text-accent hover:underline"
            onClick={async () => {
              await api.patchSettings({ translation_model: '' })
              setTransModel('')
            }}
          >
            {t('use the clipping model instead')}
          </button>
        </p>
      )}
      {canDub && picked.some((c) => langs.find((l) => l.code === c)?.can_dub) && (
        <div className="border-t border-raised/60 pt-2 space-y-1.5">
          <p className="label">{t('Dubbing voice')}</p>
          {picked
            .filter((c) => langs.find((l) => l.code === c)?.can_dub)
            .map((c) => (
              <div key={c} className="flex items-center gap-2 text-xs">
                <span className="text-muted w-20 shrink-0">{displayName(c, c)}</span>
                <select
                  className="input !w-56 !py-1 text-xs"
                  value={voiceFor[c] ?? ''}
                  onChange={(e) => {
                    const next = { ...voiceFor, [c]: e.target.value }
                    setVoiceFor(next)
                    localStorage.setItem('multilingual-voices', JSON.stringify(next))
                  }}
                >
                  <option value="">{t('Default voice')}</option>
                  {(voiceList[c] ?? []).map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name} · {v.country} · {v.quality}
                    </option>
                  ))}
                </select>
                <button
                  className="px-2 py-0.5 rounded-md bg-raised text-muted hover:text-ink disabled:opacity-50"
                  onClick={() => play(c, voiceFor[c] || undefined)}
                  title="Hear this voice"
                >
                  ▶ {t('Listen')}
                </button>
              </div>
            ))}
          {player && (
            <div className="flex items-center gap-2 pt-1">
              <audio
                key={player.url}
                src={player.url}
                controls
                autoPlay
                className="h-8 flex-1 min-w-0"
                onError={() =>
                  setNotice('That voice could not be played — check the Dashboard activity feed.')
                }
              />
              <button
                className="text-muted hover:text-ink px-1"
                onClick={() => setPlayer(null)}
                aria-label="Close preview"
              >
                ✕
              </button>
            </div>
          )}
          <p className="text-muted/70">
            {t('The first play downloads that voice (~60 MB), so it can take a moment.')}
          </p>
        </div>
      )}
      {dubIn && picked.some((c) => !langs.find((l) => l.code === c)?.can_dub) && (
        <p className="text-xs text-warn">
          {t('No voice exists for')}{' '}
          {picked
            .filter((c) => !langs.find((l) => l.code === c)?.can_dub)
            .map((c) => displayName(c, c))
            .join(', ')}
          {' — '}
          {t('those languages get subtitles only.')}
        </p>
      )}
      {!canDub && (
        <p className="text-xs text-muted">
          {t(
            'Dubbing needs one extra local package (Piper). Install it with: pip install piper-tts — then restart the app. Voices download per language, about 60 MB each, and stay on your PC.'
          )}
        </p>
      )}
      {notice && <p className="text-xs text-muted">{notice}</p>}
    </div>
  )
}

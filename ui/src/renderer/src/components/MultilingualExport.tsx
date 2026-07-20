import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getExportFolder, pickExportFolder, setExportFolder } from '../lib/exportFolder'
import { Folder } from './icons'
import { activeLocale, t } from '../lib/i18n'
import TranslationReview from './TranslationReview'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'
import GlossaryEditor from './GlossaryEditor'
import { formatEta } from '../lib/jobProgress'
import { useEvents } from '../lib/useEvents'
import type { CaptionLine, CaptionStyle, TranslationPreview } from '../lib/types'

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
const STYLE_KEY = 'multilingual-style'
// Clipping models are chosen for judgement; translation wants multilingual
// strength, and Qwen is markedly better at it than Gemma. Offered, never
// required — the app keeps working with whatever is installed.
const RECOMMENDED = 'qwen2.5:7b'

export default function MultilingualExport({
  clipId,
  videoId,
  onPreview
}: {
  clipId: number
  videoId?: string
  /** Draw a language's captions over the editor video (editor only). */
  onPreview?: (p: TranslationPreview | null) => void
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
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [installed, setInstalled] = useState<string[]>([])
  const [transModel, setTransModel] = useState('')
  const [pulling, setPulling] = useState(false)
  const [dubIn, setDubIn] = useState(false)
  const [canDub, setCanDub] = useState(false)
  // .srt/.vtt sidecars and the .txt post text are deliberately NOT offered:
  // this app's output is a video you upload, and those files only cluttered
  // the folder. The backend still supports them if that ever changes.
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
  // Which language is drawn over the video, and how subtitles are styled.
  // The style is remembered so a creator's look carries across clips, the
  // same way the language and voice choices already do.
  const [previewLang, setPreviewLang] = useState<string | null>(null)
  const [style, setStyle] = useState<Required<CaptionStyle>>(() => {
    try {
      return { ...DEFAULT_CAPTION_STYLE, ...JSON.parse(localStorage.getItem(STYLE_KEY) ?? '{}') }
    } catch {
      return DEFAULT_CAPTION_STYLE
    }
  })
  // The lines TranslationReview is currently showing, before font/style are
  // attached. Kept here so a style change re-draws immediately, without
  // waiting for the text to change.
  const [rawPreview, setRawPreview] = useState<{
    language: string
    lines: CaptionLine[]
    source: CaptionLine[]
  } | null>(null)

  // ---- live progress -------------------------------------------------
  // Translating and burning take minutes. Without this the panel just
  // disables its buttons, which is indistinguishable from being stuck.
  const [run, setRun] = useState<{
    kind: 'translate' | 'export'
    startedAt: number
    fraction: number
    label: string
  } | null>(null)
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    if (!run) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [run !== null])

  useEvents((e) => {
    if (e.type === 'progress' && e.stage === 'multilingual') {
      setRun((r) =>
        r
          ? {
              ...r,
              // never let the bar move backwards
              fraction: Math.max(r.fraction, e.fraction ?? r.fraction),
              label: e.message || r.label
            }
          : r
      )
    }
  })

  const elapsed = run ? (now - run.startedAt) / 1000 : 0
  // Extrapolate from work done so far. Below ~6% the estimate is noise, so
  // say nothing rather than show a wild number.
  const eta = run && run.fraction >= 0.06 ? (elapsed * (1 - run.fraction)) / run.fraction : null

  const setStyleField = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void => {
    setStyle((s) => {
      const next = { ...s, [key]: value }
      localStorage.setItem(STYLE_KEY, JSON.stringify(next))
      return next
    })
  }

  // Push the preview up to the editor whenever the text, the style or the
  // chosen language changes — this is what makes subtitles behave like
  // captions: you see them on the video, you don't read them in a list.
  useEffect(() => {
    if (!onPreview) return
    onPreview(
      rawPreview && {
        ...rawPreview,
        font: langs.find((l) => l.code === rawPreview.language)?.caption_font ?? null,
        style
      }
    )
  }, [rawPreview, style, langs])

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



  const hasQwen = installed.some((n) => n.startsWith('qwen'))
  const useQwen = async (): Promise<void> => {
    const name = installed.find((n) => n.startsWith('qwen')) ?? RECOMMENDED
    await api.patchSettings({ translation_model: name })
    setTransModel(name)
    setNotice(`Translation will use ${name}.`)
  }

  // How many of the picked languages actually have text waiting to export.
  const readyCount = picked.filter((c) => reviewed.includes(c)).length
  /** Exactly what Export will write, per language, named — so the result is
   *  concrete before the button is pressed. */
  const outputs = (): string[] => {
    const ex = picked[0] ?? 'xx'
    return dubIn
      ? [`clip.${ex}.dubbed.mp4 — ${displayName(ex, ex)} subtitles and voice`]
      : [`clip.${ex}.mp4 — ${displayName(ex, ex)} subtitles`]
  }

  const toggle = (code: string): void => {
    setPicked((prev) => {
      const next = prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
      localStorage.setItem(REMEMBER, JSON.stringify(next))
      return next
    })
  }

  // This tab edits ONE clip, so it publishes one clip. Doing every clip of
  // a video belongs wherever clips are managed as a set, not in a panel
  // whose whole job is the clip in front of you.
  const targetClips = async (): Promise<number[]> => [clipId]

  /** Step 1: produce the text only. No files, no rendering — so a bad
   *  translation is caught before it is burned into a video. */
  const translate = async (): Promise<void> => {
    setBusy(true)
    setNotice('')
    try {
      const ids = await targetClips()
      setRun({ kind: 'translate', startedAt: Date.now(), fraction: 0, label: 'Starting…' })
      const res = await api.translateClips({
        clip_ids: ids,
        languages: picked,
        stage: 'translate'
      })
      watchJob(res.job_id)
    } catch (e) {
      setNotice(`Error: ${e instanceof Error ? e.message : String(e)}`)
      setRun(null)
      setBusy(false)
    }
  }

  /** Poll until the translation job finishes, then show the text. Polling
   *  the job (rather than the translations) is what makes a failure visible
   *  instead of leaving the panel spinning forever. */
  const watchJob = (jobId: number, kind: 'translate' | 'export' = 'translate'): void => {
    setWaiting(true)
    const started = Date.now()
    const tick = async (): Promise<void> => {
      if (Date.now() - started > 60 * 60_000) {
        setWaiting(false)
        setBusy(false)
        setRun(null)
        setNotice(`${kind === 'export' ? 'Export' : 'Translation'} is taking unusually long — check the Dashboard activity feed.`)
        return
      }
      try {
        const job = (await api.jobs()).find((j) => j.id === jobId)
        if (job && job.status !== 'queued' && job.status !== 'running') {
          setWaiting(false)
          setBusy(false)
          setRun(null)
          setReloadKey((k) => k + 1)
          const took = formatEta((Date.now() - started) / 1000)
          const what = kind === 'export' ? 'Export' : 'Translation'
          setNotice(
            job.status === 'done'
              ? kind === 'export'
                ? `Exported in ${took} — the files are in your export folder.`
                : `Translated in ${took}. It's on the video now — fix anything wrong, then export.`
              : `${what} ${job.status}${job.error ? `: ${job.error}` : ''}`
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
      setRun({ kind: 'export', startedAt: Date.now(), fraction: 0, label: 'Starting…' })
      const res = await api.translateClips({
        clip_ids: ids,
        languages: picked,
        stage: 'export',
        folder,
        // Always burn: this tab's whole output is a video with that
        // language's subtitles in it, and that is what it previews.
        burn: true,
        dub: dubIn,
        voices: voiceFor,
        style
      })
      watchJob(res.job_id, 'export')
    } catch (e) {
      setNotice(`Error: ${e instanceof Error ? e.message : String(e)}`)
      setRun(null)
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
      </div>

      {/* One decision, not four checkboxes. Every export from this tab is a
          video with that language's subtitles in it — the only real question
          is whether the voice is dubbed too. */}
      {canDub && (
        <div className="flex gap-1.5 text-xs">
          {[
            [false, t('Subtitles'), 'Subtitles in the picture, original audio kept.'],
            [
              true,
              t('Subtitles + dubbed voice'),
              'Also speaks the translation with a local voice, with the original audio low underneath. Slower.'
            ]
          ].map(([value, label, hint]) => (
            <button
              key={String(value)}
              onClick={() => setDubIn(value as boolean)}
              title={hint as string}
              className={`px-2.5 py-1 rounded-md border ${
                dubIn === value
                  ? 'border-accent text-accent bg-accent/10'
                  : 'border-raised text-muted hover:text-ink'
              }`}
            >
              {label as string}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          className="btn-ghost !py-1 text-xs"
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
          className="btn-accent !py-1 text-xs ml-auto"
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

      {/* Live progress: what it's doing, how far in, how long it has taken
          and roughly how much is left. */}
      {run && (
        <div className="space-y-1.5" aria-live="polite">
          <div className="flex items-baseline justify-between gap-3 text-xs">
            <span className="truncate">{run.label}</span>
            <span className="text-muted tabular-nums shrink-0">
              {Math.round(run.fraction * 100)}% · {formatEta(elapsed)}
              {eta !== null ? ` · ~${formatEta(eta)} left` : ''}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-raised overflow-hidden">
            <div
              className="h-full bg-accent transition-[width] duration-500"
              style={{ width: `${Math.max(2, Math.round(run.fraction * 100))}%` }}
            />
          </div>
        </div>
      )}

      {!run && picked.length > 0 && (
        <div className="text-[11px] text-muted/80 space-y-0.5">
          <p className="label !mb-0 !text-[11px]">{t('Each language gives you')}</p>
          {outputs().map((line) => (
            <p key={line} className="font-mono truncate" title={line}>
              {line}
            </p>
          ))}
        </div>
      )}

      {/* Say why Export can't run. A greyed-out button with the reason hidden
          in a tooltip just reads as "it didn't work". */}
      {picked.length > 0 && !waiting && readyCount === 0 && (
        <p className="text-xs text-warn">
          {t('Translate these languages first — Export writes the text you have reviewed.')}
        </p>
      )}

      {/* Subtitles get the same treatment captions do: pick one, watch it on
          the video, restyle it. The style is always editable — it is a
          setting, not something that should require translating first. */}
      {onPreview && (
        <div className="border-t border-raised/60 pt-2 space-y-2">
          {readyCount > 0 && (
            <div className="flex items-center gap-2 text-xs">
              <span className="label !mb-0">{t('Show on video')}</span>
              <select
                className="input !w-44 !py-1 text-xs"
                value={previewLang ?? ''}
                onChange={(e) => setPreviewLang(e.target.value || null)}
              >
                <option value="">{t('Off — original captions')}</option>
                {picked
                  .filter((c) => reviewed.includes(c))
                  .map((c) => (
                    <option key={c} value={c}>
                      {displayName(c, c)}
                    </option>
                  ))}
              </select>
            </div>
          )}
          {/* Collapsed by default: the look is set once and then left alone,
              so it shouldn't cost half the panel's height every time. */}
          <details className="border border-raised/60 rounded-lg">
            <summary className="px-3 py-2 text-xs cursor-pointer hover:bg-raised/40 rounded-lg">
              {t('Subtitle font & style')}
              <span className="text-muted ml-2">
                {style.font} · {style.font_size} · {style.position}
              </span>
            </summary>
            <div className="p-3 pt-0">
              <CaptionStyleControls
                idPrefix={`subs-${clipId}`}
                style={style}
                onChange={setStyleField}
                hideWordsPerCaption
              />
              <p className="text-[11px] text-muted/70 mt-2">
                {t(
                  'Applies to every language you export. Non-Latin scripts switch to a font that has the glyphs automatically.'
                )}
                {readyCount === 0 && ` ${t('Translate a language to see it on the video.')}`}
              </p>
            </div>
          </details>
          <GlossaryEditor clipId={clipId} />
        </div>
      )}

      <TranslationReview
        clipId={clipId}
        languages={picked}
        nameOf={(c) => displayName(c, c)}
        reloadKey={reloadKey}
        onLoaded={(codes) => {
          setReviewed(codes)
          // Start previewing the first language as soon as one exists, so
          // the subtitles appear on the video without hunting for a control.
          setPreviewLang((cur) => (cur && codes.includes(cur) ? cur : (codes[0] ?? null)))
        }}
        onPreview={setRawPreview}
        previewing={previewLang}
      />
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

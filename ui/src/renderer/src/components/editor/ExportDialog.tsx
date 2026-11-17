/** The new editor's Export: step 1's presets (Original, or a size, shape,
 *  quality, format and size limit with the size it comes out at), progress
 *  from the lane's events, Cancel, then Play or Show in folder. Below, the
 *  project's earlier exports; one whose file is gone, or that a later export
 *  replaced, is said so and never offered.
 *
 *  A project made from a clip replaces that clip's file when it is exported
 *  (the clip keeps its title and posts, and the Library shows the new file):
 *  the Export button's tooltip says so. A project with no clip (made by
 *  hand, or whose clip was deleted) saves its file in the export folder from
 *  Settings, named after the project; the dialog names that folder before
 *  and after. How the engine makes the file is never shown. Caption choices
 *  (burn or not, a .srt file) are step 9's.
 *
 *  While it is open the keyboard stays in it (useModalFocus): the editor
 *  behind is inert, and Esc closes it wherever the focus is. */
import { useEffect, useRef, useState } from 'react'
import { api, errorText as apiErrorText } from '../../lib/api'
import { t } from '../../lib/i18n'
import { editorApi, engineUrl, errorText, refusalReason } from '../../lib/editorApi'
import { formatBytes } from '../../lib/exportJobs'
import { formatLength, programLength } from '../../lib/editorProgram'
import type { EditorEvent, Project, ProjectExportJob, ProjectExportRow } from '../../lib/editorTypes'
import type { ExportCodecInfo, ExportEstimate, ExportPreset, ExportPresetsInfo, StudioEvent } from '../../lib/types'
import { useEvents } from '../../lib/useEvents'
import { getExportFolder } from '../../lib/exportFolder'
import { useModalFocus } from './useModalFocus'

const SIZES: [ExportPreset['resolution'], string][] = [
  ['same', 'Same as the project'],
  ['720p', '720p'],
  ['1080p', '1080p'],
  ['1440p', '1440p'],
  ['2160p', '2160p (4K)']
]
const SHAPES: [ExportPreset['aspect'], string][] = [
  ['same', 'Same as the project'],
  ['9:16', 'Tall (9:16)'],
  ['16:9', 'Wide (16:9)'],
  ['1:1', 'Square (1:1)'],
  ['4:5', '4:5']
]
const FILLS: [ExportPreset['fill'], string][] = [
  ['crop', 'Crop to fit'],
  ['blur', 'Blurred background'],
  ['bars', 'Colour bars']
]
const QUALITIES: [ExportPreset['quality'], string][] = [
  ['smallest', 'Smallest file'],
  ['balanced', 'Balanced'],
  ['high', 'High']
]
const CODEC_NAMES: Record<string, string> = { h264: 'H.264', hevc: 'HEVC', av1: 'AV1' }

/** A preset the project keeps, or Settings → Export's. */
function startingPreset(project: Project, info: ExportPresetsInfo): ExportPreset {
  const kept = (project.document.export?.preset ?? null) as ExportPreset | null
  if (kept && typeof kept === 'object' && 'kind' in kept) return { ...info.custom, ...kept }
  return info.default
}

const STATUS: Record<ProjectExportRow['status'], string> = {
  queued: 'Waiting',
  running: 'Exporting…',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  interrupted: 'Stopped when the app closed',
  replaced: 'Replaced by a later export'
}

function Choice<T extends string>({
  label,
  tip,
  value,
  options,
  onChange
}: {
  label: string
  tip: string
  value: T
  options: [T, string][]
  onChange: (v: T) => void
}): JSX.Element {
  return (
    <label className="block min-w-0 text-xs" title={tip}>
      <span className="label">{label}</span>
      <select className="input !py-1 text-xs mt-1" value={value} aria-label={label} title={tip} onChange={(e) => onChange(e.target.value as T)}>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {t(l)}
          </option>
        ))}
      </select>
    </label>
  )
}

/** The folder part of a file's path. */
function folderOf(path: string | undefined): string {
  if (!path) return ''
  const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return cut > 0 ? path.slice(0, cut) : ''
}

export default function ExportDialog({
  project,
  captionsShown = true,
  onClose
}: {
  project: Project
  /** False when the captions are hidden (the lane's Hide): none are burned,
   *  so "Burn captions" is shown off and greyed (step 9). */
  captionsShown?: boolean
  onClose: () => void
}): JSX.Element {
  const root = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const [folder, setFolder] = useState('')
  const [info, setInfo] = useState<ExportPresetsInfo | null>(null)
  const [preset, setPreset] = useState<ExportPreset | null>(null)
  const [codecs, setCodecs] = useState<ExportCodecInfo[] | null>(null)
  const [estimate, setEstimate] = useState<ExportEstimate | null>(null)
  const [job, setJob] = useState<ProjectExportJob | null>(null)
  const [rows, setRows] = useState<ProjectExportRow[]>([])
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState<string | null>(null)
  const [clipChanged, setClipChanged] = useState(false)
  // Step 9: burn the captions into the picture (on), and a .srt beside the video (off).
  const [burn, setBurn] = useState(true)
  const [srt, setSrt] = useState(false)
  // Hidden captions are never burned, whatever the box said before.
  const burns = burn && captionsShown
  const seconds = programLength(project.document) / 1e6
  const fromClip = project.clip_id !== null
  useModalFocus(root, box, onClose)

  const loadRows = (): void => {
    editorApi
      .exports(project.id)
      .then((r) => setRows(r.exports))
      .catch(() => undefined)
  }
  useEffect(() => {
    box.current?.querySelector<HTMLElement>('select, button')?.focus()
    api
      .exportPresets()
      .then((p) => {
        setInfo(p)
        setPreset(startingPreset(project, p))
      })
      .catch((e) => setError(apiErrorText(e)))
    loadRows()
    if (!fromClip) {
      getExportFolder()
        .then(setFolder)
        .catch(() => undefined)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id])

  const custom = preset?.kind === 'custom'
  useEffect(() => {
    if (!custom || codecs !== null) return
    api
      .exportCodecs()
      .then((c) => setCodecs(c.codecs))
      .catch(() => setCodecs([]))
  }, [custom, codecs])

  const shown = preset ? JSON.stringify(preset) : ''
  useEffect(() => {
    if (!preset) return
    let alive = true
    const timer = window.setTimeout(() => {
      api
        .exportEstimate({ preset, seconds: Math.max(1, seconds) })
        .then((e) => alive && setEstimate(e))
        .catch(() => alive && setEstimate(null))
    }, 250)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, seconds])

  // ---- the export's progress ----
  const follow = (next: ProjectExportJob): void => {
    setJob((cur) => {
      if (!cur || cur.export_id !== next.export_id) return cur
      if (next.status !== cur.status && (next.status === 'done' || next.status === 'failed' || next.status === 'cancelled')) loadRows()
      return { ...cur, ...next }
    })
  }
  useEvents((e: StudioEvent) => {
    const ev = e as unknown as EditorEvent & { export?: ProjectExportJob }
    if (ev.type === 'export' && ev.export) follow(ev.export)
  })
  const active = job !== null && (job.status === 'queued' || job.status === 'running')
  useEffect(() => {
    if (!active || !job) return
    const timer = window.setInterval(() => {
      editorApi
        .exportJob(job.export_id)
        .then(follow)
        .catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, job?.export_id])

  const start = async (replaceClipChanges = false): Promise<void> => {
    if (!preset) return
    setError('')
    setClipChanged(false)
    setPlaying(null)
    try {
      const ans = await editorApi.exportProject(
        project.id,
        project.revision,
        preset.kind === 'original' ? 'original' : preset,
        replaceClipChanges,
        fromClip ? undefined : folder || undefined,
        { captions: burns ? 'burn' : 'none', srt }
      )
      setJob(ans.export)
      loadRows()
    } catch (e) {
      // The clip was changed in the clip editor since (8-D6): asked here.
      if (refusalReason(e) === 'clip_changed') setClipChanged(true)
      else setError(errorText(e))
    }
  }
  const cancel = async (): Promise<void> => {
    if (!job) return
    try {
      follow(await editorApi.cancelExport(job.export_id))
    } catch (e) {
      setError(errorText(e))
    }
  }
  const show = (path: string): void => {
    void window.studio?.showInFolder?.(path)
  }
  const canShow = typeof window.studio?.showInFolder === 'function'

  const change = (patch: Partial<ExportPreset>): void => setPreset((p) => (p ? { ...p, ...patch } : p))
  const close = (): void => {
    onClose()
  }

  const fraction = job ? (job.smaller ? (job.attempt_fraction ?? 0) : (job.fraction ?? 0)) : 0
  const doneRow = job?.status === 'done' ? rows.find((r) => r.id === job.project_export_id) : undefined
  const available = (codec: string): boolean => (codecs === null ? codec === 'h264' : codecs.some((c) => c.codec === codec))
  const doneFolder = job?.result ? job.result.folder ?? folderOf(job.result.path) : ''
  const exportTip = fromClip
    ? t("This project was made from a clip. Exporting replaces that clip's video in your Library with this one; the clip keeps its title and its posts.")
    : `${t('Makes the video file of this project')}. ${t('It is saved in your export folder, named after the project. Change the folder in Settings.')}`

  return (
    <div
      ref={root}
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !active) close()
      }}
    >
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label={t('Export')}
        className="card w-[34rem] max-w-full max-h-full overflow-y-auto space-y-3"
        onKeyDown={(e) => {
          // The editor's shortcuts (Space, S, Delete…) stay with the editor.
          e.stopPropagation()
        }}
      >
        <div className="flex items-center gap-2">
          <h3 className="font-semibold">{t('Export')}</h3>
          <span className="text-xs text-muted tabular-nums">{formatLength(seconds * 1e6)}</span>
          <button type="button" className="ml-auto text-muted hover:text-ink px-1" onClick={close} aria-label={t('Close')} title={t('Close')}>
            ×
          </button>
        </div>

        {!info || !preset ? (
          <p className="text-xs text-muted">{error || t('Checking…')}</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Choice
                label={t('Export as')}
                tip={t('Original makes the video as Clips Kitty makes clips. Custom lets you choose the size, shape, quality and format.')}
                value={preset.kind}
                options={[
                  ['original', 'Original'],
                  ['custom', 'Custom']
                ]}
                onChange={(kind) => change(kind === 'custom' ? { ...info.custom, ...(preset.kind === 'custom' ? preset : {}), kind } : { kind })}
              />
              {custom && (
                <>
                  <Choice
                    label={t('Size')}
                    tip={t('How many pixels the video has, counted on its short side')}
                    value={SIZES.some(([v]) => v === preset.resolution) ? preset.resolution : 'same'}
                    options={SIZES}
                    onChange={(resolution) => change({ resolution })}
                  />
                  <Choice
                    label={t('Shape')}
                    tip={t('The shape of the file. Another shape than the project is filled as you choose below.')}
                    value={SHAPES.some(([v]) => v === preset.aspect) ? preset.aspect : 'same'}
                    options={SHAPES}
                    onChange={(aspect) => change({ aspect })}
                  />
                  {preset.aspect !== 'same' && (
                    <Choice
                      label={t('Fill')}
                      tip={t('How the video fills a shape it was not made in')}
                      value={preset.fill}
                      options={FILLS}
                      onChange={(fill) => change({ fill })}
                    />
                  )}
                  <Choice
                    label={t('Quality')}
                    tip={t('Smallest file for sending, High for keeping. Balanced suits most posts.')}
                    value={QUALITIES.some(([v]) => v === preset.quality) ? preset.quality : 'balanced'}
                    options={QUALITIES}
                    onChange={(quality) => change({ quality })}
                  />
                  <Choice
                    label={t('Format')}
                    tip={t('H.264 plays everywhere. HEVC and AV1 are smaller at the same quality, where this PC can make them.')}
                    value={preset.codec}
                    options={(['h264', 'hevc', 'av1'] as const).filter(available).map((c) => [c, CODEC_NAMES[c]])}
                    onChange={(codec) => change({ codec })}
                  />
                  <label className="flex items-end gap-2 text-xs col-span-2" title={t('Make the file smaller than this many MB, for apps with an upload limit')}>
                    <input
                      type="checkbox"
                      checked={preset.max_mb !== null}
                      onChange={(e) => change({ max_mb: e.target.checked ? (preset.max_mb ?? 100) : null })}
                      aria-label={t('Keep under')}
                    />
                    <span>{t('Keep under')}</span>
                    <input
                      type="number"
                      className="input !py-0.5 !w-20 text-xs"
                      min={info.limits.max_mb?.[0] ?? 5}
                      max={info.limits.max_mb?.[1] ?? 4000}
                      value={preset.max_mb ?? ''}
                      disabled={preset.max_mb === null}
                      aria-label={t('MB')}
                      title={t('Make the file smaller than this many MB, for apps with an upload limit')}
                      onChange={(e) => {
                        const v = Number(e.target.value)
                        if (Number.isFinite(v) && v > 0) change({ max_mb: v })
                      }}
                    />
                    <span>{t('MB')}</span>
                  </label>
                </>
              )}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
              <label
                className={`flex items-center gap-1.5 ${captionsShown ? 'cursor-pointer' : 'opacity-50 cursor-not-allowed'}`}
                title={
                  !captionsShown
                    ? [
                        t('Your captions are hidden, so none are burned. Show them again with the first button on the Captions lane.'),
                        t('Hide captions: they are left out of the video and the preview. Click again to show them.')
                      ].join('\n')
                    : fromClip
                    ? t('The captions are drawn into the picture, styled as you see them. Off: the video has no captions, but your texts stay. Your clip in the library then has its captions off too.')
                    : t('The captions are drawn into the picture, styled as you see them. Off: the video has no captions, but your texts stay.')
                }
              >
                <input type="checkbox" checked={burns} disabled={active || !captionsShown} onChange={(e) => setBurn(e.target.checked)} />
                {t('Burn captions into the video')}
              </label>
              <label
                className="flex items-center gap-1.5 cursor-pointer"
                title={t('Saves the captions as a subtitle file next to the video, for YouTube and other players. Plain text and times only, without the style.')}
              >
                <input type="checkbox" checked={srt} disabled={active} onChange={(e) => setSrt(e.target.checked)} />
                {t('Also save captions as a .srt file')}
              </label>
            </div>
            {estimate && (
              <p className="text-xs text-muted" role="status">
                {t('About')}{' '}
                {estimate.kind === 'range'
                  ? `${formatBytes(estimate.low)} – ${formatBytes(estimate.high)}`
                  : formatBytes(estimate.bytes)}
              </p>
            )}

            {/* ---- the export ---- */}
            {job && (
              <div className="space-y-1.5" role="status" aria-live="polite">
                {active ? (
                  <>
                    <p className="text-xs">
                      {job.smaller ? t('Making it again, smaller…') : job.status === 'queued' ? t('Waiting') : t('Exporting…')}{' '}
                      <span className="tabular-nums text-muted">{Math.round(fraction * 100)}%</span>
                    </p>
                    <div
                      className="h-1.5 rounded bg-raised overflow-hidden"
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(fraction * 100)}
                      aria-label={t('Exporting…')}
                    >
                      <div className="h-full bg-accent transition-all" style={{ width: `${Math.round(fraction * 100)}%` }} />
                    </div>
                  </>
                ) : job.status === 'done' ? (
                  <div className="flex items-center gap-2 text-xs">
                    <span className="min-w-0 flex-1" title={job.result?.path}>
                      <span className="block text-emerald-300 truncate">
                        {t('Exported')} {job.result?.file} · {formatBytes(job.result?.bytes ?? 0)}
                      </span>
                      {doneFolder && (
                        <span className="block text-muted truncate">
                          {t('Saved in')} {doneFolder}
                        </span>
                      )}
                      {job.result?.srt_file && (
                        <span className="block text-muted truncate" title={job.result.srt ?? ''}>
                          {t('Captions saved as')} {job.result.srt_file}
                        </span>
                      )}
                      {job.result?.srt === null && job.result.srt_error && (
                        <span className="block text-amber-300 truncate" title={job.result.srt_error}>
                          {job.result.srt_error}
                        </span>
                      )}
                    </span>
                    {doneRow?.url && (
                      <button type="button" className="btn-ghost !py-1 !px-2 text-xs" onClick={() => setPlaying(doneRow.url)}>
                        {t('Play')}
                      </button>
                    )}
                    {canShow && job.result?.path && (
                      <button type="button" className="btn-ghost !py-1 !px-2 text-xs" onClick={() => show(job.result?.path ?? '')}>
                        {t('Show in folder')}
                      </button>
                    )}
                  </div>
                ) : (
                  <p className={`text-xs ${job.status === 'failed' ? 'text-red-300' : 'text-muted'}`}>
                    {job.status === 'failed' ? job.error || t('The export failed.') : t('Cancelled')}
                  </p>
                )}
              </div>
            )}
            {playing && (
              <video src={engineUrl(playing)} controls autoPlay playsInline className="w-full max-h-72 bg-black rounded" aria-label={t('The exported video')} />
            )}
            {error && <p className="text-xs text-red-300">{error}</p>}
            {clipChanged && !active && (
              <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 p-2 space-y-2" role="alert">
                <p className="text-xs">
                  {t("This clip was changed in the clip editor after you last worked on it here. Exporting puts this version in its place, so those changes are lost.")}
                </p>
                <div className="flex gap-2">
                  <button type="button" className="btn-accent !py-1 text-xs" onClick={() => void start(true)}>
                    {t('Export anyway')}
                  </button>
                  <button type="button" className="btn-ghost !py-1 text-xs" onClick={() => setClipChanged(false)}>
                    {t('Cancel')}
                  </button>
                </div>
              </div>
            )}

            <div className="flex items-center gap-2">
              {active ? (
                <button type="button" className="btn-ghost !py-1.5 text-sm" onClick={() => void cancel()} disabled={job?.cancelling}>
                  {job?.cancelling ? t('Cancelling…') : t('Cancel')}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-accent !py-1.5 text-sm"
                  onClick={() => void start()}
                  disabled={seconds <= 0 || project.read_only || (estimate !== null && !estimate.available && custom)}
                  title={exportTip}
                >
                  {t('Export')}
                </button>
              )}
              {fromClip && !active && (
                <span className="text-[11px] text-muted cursor-help" title={exportTip}>
                  {t('Replaces the clip in your Library')}
                </span>
              )}
              {!fromClip && !active && folder && (
                <span className="text-[11px] text-muted truncate min-w-0" title={[folder, t('Change the folder in Settings.')].join('\n')}>
                  {t('Saves to')} {folder}
                </span>
              )}
            </div>
          </>
        )}

        {/* ---- earlier exports ---- */}
        {rows.length > 0 && (
          <div className="border-t border-raised/60 pt-2 space-y-1">
            <h4 className="text-xs font-semibold text-muted">{t('Earlier exports')}</h4>
            <ul className="space-y-1 max-h-40 overflow-y-auto">
              {rows.map((r) => {
                const gone = r.status === 'done' && !r.file_exists
                const file = r.path ? r.path.split(/[\\/]/).pop() : ''
                return (
                  <li key={r.id} className="flex items-center gap-2 text-xs">
                    <span className="text-muted tabular-nums shrink-0">{r.created_at.replace('T', ' ').slice(0, 16)}</span>
                    <span className="flex-1 truncate" title={r.path ?? ''}>
                      {file}
                    </span>
                    <span className={`shrink-0 ${r.status === 'failed' || gone ? 'text-red-300' : 'text-muted'}`} title={r.error ?? ''}>
                      {gone ? t('File gone') : t(STATUS[r.status] ?? r.status)}
                      {r.status === 'done' && !gone && r.bytes ? ` · ${formatBytes(r.bytes)}` : ''}
                    </span>
                    {r.url && (
                      <button type="button" className="text-accent hover:underline shrink-0" onClick={() => setPlaying(r.url)}>
                        {t('Play')}
                      </button>
                    )}
                    {canShow && r.url && r.path && (
                      <button type="button" className="text-accent hover:underline shrink-0" onClick={() => show(r.path ?? '')}>
                        {t('Show in folder')}
                      </button>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

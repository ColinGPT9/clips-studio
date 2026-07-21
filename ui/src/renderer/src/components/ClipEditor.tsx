import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getExportFolder, pickExportFolder, setExportFolder } from '../lib/exportFolder'
import { Folder, Scissors, Trash } from './icons'
import type { Clip } from '../lib/types'

const CHANNELS = ['text', 'audio', 'visual', 'reaction', 'engagement'] as const

export default function ClipEditor({
  clip,
  onChanged,
  onOpenEditor
}: {
  clip: Clip
  onChanged: () => void
  onOpenEditor: () => void
}): JSX.Element {
  const [title, setTitle] = useState(clip.title)
  const [description, setDescription] = useState(clip.description)
  const [hashtags, setHashtags] = useState(clip.hashtags.join(' '))
  const [start, setStart] = useState(clip.start_s)
  const [end, setEnd] = useState(clip.end_s)
  const [folder, setFolder] = useState('')

  // Default the export destination to the remembered folder / OS Downloads.
  useEffect(() => {
    getExportFolder().then(setFolder)
  }, [])
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    setTitle(clip.title)
    setDescription(clip.description)
    setHashtags(clip.hashtags.join(' '))
    setStart(clip.start_s)
    setEnd(clip.end_s)
    setNotice(null)
  }, [clip.id])

  const flash = (msg: string): void => {
    setNotice(msg)
    setTimeout(() => setNotice(null), 4000)
  }

  const run = async (label: string, fn: () => Promise<void>): Promise<void> => {
    setBusy(label)
    try {
      await fn()
    } catch (e) {
      flash(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(null)
    }
  }

  const saveMetadata = (): Promise<void> =>
    run('save', async () => {
      await api.patchClip(clip.id, {
        title,
        description,
        hashtags: hashtags.split(/\s+/).filter(Boolean)
      })
      flash('Saved')
      onChanged()
    })

  const rerender = (): Promise<void> =>
    run('render', async () => {
      const range = start !== clip.start_s || end !== clip.end_s ? { start, end } : undefined
      await api.rerenderClip(clip.id, range)
      flash('Re-render queued — watch the Dashboard activity feed')
    })

  const exportOne = (): Promise<void> =>
    run('export', async () => {
      const res = await api.exportClip(clip.id, folder)
      flash(res.exported.length ? `Exported: ${res.exported[0]}` : 'Nothing exported')
    })

  const deleteThisClip = (): Promise<void> =>
    run('delete', async () => {
      if (!window.confirm(`Delete this clip and its file?\n\n"${clip.title || clip.hook || 'Untitled clip'}"\n\nOnly this clip is removed — the video and your other clips are untouched. This can't be undone.`)) {
        return
      }
      const res = await api.deleteClip(clip.id)
      onChanged() // refresh the grid; this clip drops out and deselects
      flash(`Deleted — freed ${(res.bytes_freed / 1e6).toFixed(0)} MB`)
    })

  return (
    <div className="card space-y-4">
      <video
        key={clip.id}
        src={api.mediaUrl(clip.id)}
        controls
        aria-label={`Preview of clip: ${clip.title || clip.hook || 'untitled'}. Captions are burned into the video.`}
        className="w-full rounded-lg bg-base max-h-96"
      />

      <button
        onClick={onOpenEditor}
        className="btn-accent w-full !py-3 text-base font-semibold"
        title="Open the editor: trim, cut, mute words, censor, color, captions, AI edit"
      >
        <span className="inline-flex items-center gap-2 justify-center">
          <Scissors size={16} /> Edit this clip
        </span>
      </button>

      <div className="flex gap-2 flex-wrap text-xs items-center">
        {CHANNELS.map((ch) => (
          <span key={ch} className="bg-raised px-2 py-1 rounded-md text-muted">
            {ch} <span className="text-ink font-semibold">{clip.scores[ch] ?? '–'}</span>
          </span>
        ))}
      </div>

      <div className="space-y-3">
        <div>
          <label className="label">Title</label>
          <input className="input mt-1" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <label className="label">Description</label>
          <textarea
            className="input mt-1 h-20 resize-none"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Hashtags (space-separated)</label>
          <input className="input mt-1" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
        </div>
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="label">Start (s)</label>
            <input
              type="number"
              className="input mt-1"
              value={start}
              step={0.5}
              onChange={(e) => setStart(Number(e.target.value))}
            />
          </div>
          <div className="flex-1">
            <label className="label">End (s)</label>
            <input
              type="number"
              className="input mt-1"
              value={end}
              step={0.5}
              onChange={(e) => setEnd(Number(e.target.value))}
            />
          </div>
        </div>
      </div>

      <div className="flex gap-2 flex-wrap items-center">
        <button className="btn-accent" onClick={saveMetadata} disabled={busy !== null}>
          {busy === 'save' ? 'Saving…' : 'Save metadata'}
        </button>
        <button className="btn-ghost" onClick={rerender} disabled={busy !== null}>
          {busy === 'render' ? 'Queueing…' : 'Re-render'}
        </button>
        <input
          className="input !w-44"
          value={folder}
          onChange={(e) => {
            setFolder(e.target.value)
            setExportFolder(e.target.value)
          }}
          placeholder="export folder"
          title={folder}
        />
        <button
          className="btn-ghost"
          onClick={async () => {
            const chosen = await pickExportFolder()
            if (chosen) setFolder(chosen)
          }}
          title="Choose where exported clips are saved"
          aria-label="Choose export folder"
        >
          <Folder />
        </button>
        <button className="btn-ghost" onClick={exportOne} disabled={busy !== null}>
          {busy === 'export' ? 'Exporting…' : 'Export'}
        </button>
        {/* Manual cull: delete a clip you won't post so it stops taking up
            space. Only this clip is removed — the video and the rest stay. */}
        <button
          className="btn-ghost ml-auto text-muted hover:text-red-400"
          onClick={deleteThisClip}
          disabled={busy !== null}
          title="Delete this clip and its file from your computer. The video and other clips are untouched."
        >
          <Trash className="mr-1.5" />
          {busy === 'delete' ? 'Deleting…' : 'Delete clip'}
        </button>
      </div>
      {notice && <p className="text-sm text-accent">{notice}</p>}
    </div>
  )
}

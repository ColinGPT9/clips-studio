/** The new editor's calls to the engine (docs/editor-suite/api/step8-editor.md).
 *
 *  Every project and media route takes the desktop app's session header; the
 *  addresses a `<video>` plays carry their own token instead. Errors come back
 *  as an EditorError with the HTTP status and the engine's `detail`, so the
 *  editor can tell a 409 (someone changed the project first) or a superseded
 *  still from a real failure, and show the engine's own plain sentence. */
import { API_BASE, sessionHeaders } from './api'
import type {
  CaptionLane,
  CanvasChoice,
  DraftAnswer,
  ImportedAsset,
  MediaInfo,
  MediaJob,
  Op,
  OpsAnswer,
  Project,
  ProjectExportAnswer,
  ProjectExportJob,
  ProjectExportRow,
  ProjectSummary,
  ThumbIndex
} from './editorTypes'
import type { ExportPreset } from './types'

export class EditorError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
  /** 409 from the ops, undo and redo routes: the project moved on. */
  get conflict(): boolean {
    return this.status === 409 && !this.superseded
  }
  /** 409 from the still route: a newer still asked for took its place. */
  get superseded(): boolean {
    return this.status === 409 && Boolean((this.detail as { superseded?: boolean } | null)?.superseded)
  }
}

/** The engine's reason for a 409 that is a question for the person
 *  ("clip_changed": exporting over a clip changed since; "project_export"),
 *  or ''. */
export function refusalReason(e: unknown): string {
  if (!(e instanceof EditorError) || e.status !== 409) return ''
  const reason = (e.detail as { reason?: unknown } | null)?.reason
  return typeof reason === 'string' ? reason : ''
}

/** The engine's plain sentence for a refusal: `detail` itself, or its `message`. */
function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail
  const message = (detail as { message?: unknown } | null)?.message
  if (typeof message === 'string') return message
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: unknown } | undefined
    if (typeof first?.msg === 'string') return first.msg
  }
  return ''
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers: await sessionHeaders() })
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
    const detail = body?.detail ?? null
    throw new EditorError(res.status, detailText(detail) || `${res.status} ${path}`, detail)
  }
  return res.json() as Promise<T>
}

const post = <T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> =>
  call<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body), signal })

/** An address the engine gave (`/media/source/...?t=...`) as a full URL. */
export const engineUrl = (path: string): string => (path.startsWith('http') ? path : `${API_BASE}${path}`)

/** The id of one of a project's media for the media routes (step 6): it
 *  follows the document, whatever the media's source is. */
export const projectMediaId = (projectId: string, mediaId: string): string =>
  `project:${projectId}:${mediaId}`

/** A still: the program's frame as the export draws it. `time_us` is the
 *  frame's start; `approximate` when an AI item had no tracking kept yet. */
export interface Still {
  bitmap: ImageBitmap
  time_us: number | null
  approximate: boolean
}

export const editorApi = {
  projects: (limit = 500) => call<{ projects: ProjectSummary[] }>(`/projects?limit=${limit}`),
  /** A clip's project: made the first time, the same one after. */
  clipProject: (clipId: number) => post<Project>(`/clips/${clipId}/project`),
  /** "Start again from the clip as it is now" (8-D6): the clip's project
   *  replaced by the clip, as one step Undo takes back. */
  restartClipProject: (clipId: number, revision: number) =>
    post<Project>(`/clips/${clipId}/project`, { start_again: true, expected_revision: revision }),
  /** A project made by hand: `media` is `video:<id>` or `asset:<sha256>`,
   *  and the whole of it goes on V1 (no AI run). */
  createProject: (body: { media?: string; canvas?: CanvasChoice; name?: string; insert?: boolean }) =>
    post<Project>('/projects', body),
  project: (id: string) => call<Project>(`/projects/${encodeURIComponent(id)}`),
  deleteProject: (id: string) =>
    call<{ deleted: string }>(`/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  ops: (id: string, expectedRevision: number, ops: Op[], label?: string) =>
    post<OpsAnswer>(`/projects/${encodeURIComponent(id)}/ops`, {
      expected_revision: expectedRevision,
      ops,
      ...(label ? { label } : {})
    }),
  undo: (id: string, expectedRevision: number) =>
    post<OpsAnswer>(`/projects/${encodeURIComponent(id)}/undo`, { expected_revision: expectedRevision }),
  redo: (id: string, expectedRevision: number) =>
    post<OpsAnswer>(`/projects/${encodeURIComponent(id)}/redo`, { expected_revision: expectedRevision }),

  mediaInfo: (mediaId: string) => call<MediaInfo>(`/media/${encodeURIComponent(mediaId)}/info`),
  requestProxy: (mediaId: string) => post<{ state: string }>(`/media/${encodeURIComponent(mediaId)}/proxy`),
  /** A file picked on this PC. 200: the asset; 202: `{job}` for a big file. */
  importMedia: (path: string) => post<ImportedAsset | { job: MediaJob }>('/media/import', { path }),
  importJob: (jobId: number) => call<MediaJob>(`/media/import/${jobId}`),

  /** One still. Pass an AbortSignal: the editor asks for one at a time and
   *  drops the older. Decoded here, so the viewer draws it on a canvas
   *  (the page's rules let no blob: or data: address into an <img>). */
  still: async (id: string, revision: number, tUs: number, signal: AbortSignal): Promise<Still> => {
    const res = await fetch(
      `${API_BASE}/projects/${encodeURIComponent(id)}/still?t_us=${Math.max(0, Math.round(tUs))}&rev=${revision}`,
      { headers: await sessionHeaders(), signal }
    )
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
      const detail = body?.detail ?? null
      throw new EditorError(res.status, detailText(detail) || `${res.status}`, detail)
    }
    const time = res.headers.get('X-Clips-Kitty-Still-Time')
    const note = res.headers.get('X-Clips-Kitty-Still-Note')
    const bitmap = await createImageBitmap(await res.blob())
    return { bitmap, time_us: time === null ? null : Number(time), approximate: note === 'approximate' }
  },
  /** The caption lines in program time at `revision` (step 9), for the
   *  viewer's caption layer, the caption lane and the transcript list. */
  captionLines: (id: string, revision: number, signal?: AbortSignal) =>
    call<CaptionLane>(`/projects/${encodeURIComponent(id)}/captions?rev=${revision}`, { signal }),
  /** The ASS file the export burns at a revision (captions, text, the hook,
   *  the watermark), for the viewer to draw live. `url` is the caption
   *  lines' `urls.captions_ass` (token included). */
  captionsAss: async (url: string, signal?: AbortSignal): Promise<string> => {
    const res = await fetch(engineUrl(url), { headers: await sessionHeaders(), signal })
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
      const detail = body?.detail ?? null
      throw new EditorError(res.status, detailText(detail) || `${res.status}`, detail)
    }
    return res.text()
  },
  /** Caption blocks placed on the lane (program time) as the source times to
   *  save: null where no captioned video plays at a block's start. */
  captionSourceTimes: (id: string, revision: number, ranges: { start_us: number; end_us: number }[]) =>
    post<{ revision: number; ranges: ({ in_us: number; out_us: number } | null)[] }>(
      `/projects/${encodeURIComponent(id)}/captions/source_times`,
      { revision, ranges }
    ),
  /** The Windows caption fonts this PC has (GET /fonts `system`): each
   *  family's files' addresses, for the live captions (9-D9). */
  systemFonts: async (): Promise<{ family: string; urls: string[] }[]> => {
    const got = await call<{ system?: { family: string; urls: string[] }[] }>('/fonts')
    return Array.isArray(got.system) ? got.system : []
  },
  /** Ask for the draft of `revision`: made already, or a lane job. */
  draft: (id: string, revision: number) =>
    post<DraftAnswer>(`/projects/${encodeURIComponent(id)}/draft`, { revision }),

  /** Export the project at `revision` with a step 1 preset (`'original'` is
   *  the clip's own encode). Progress comes as `export` events. */
  /** `replaceClipChanges`: the person chose to export over a clip that was
   *  changed in today's editor since (the engine asks first: 409, reason
   *  "clip_changed"). `folder`: the person's export folder (Settings); a
   *  project with no clip saves its file there, named after the project. A
   *  clip's project replaces the clip's file and leaves it aside. */
  exportProject: (
    id: string,
    revision: number,
    preset: ExportPreset | 'original',
    replaceClipChanges = false,
    folder?: string,
    captions: { captions: 'burn' | 'none'; srt: boolean } = { captions: 'burn', srt: false }
  ) =>
    post<ProjectExportAnswer>(`/projects/${encodeURIComponent(id)}/export`, {
      revision,
      preset,
      ...(replaceClipChanges ? { replace_clip_changes: true } : {}),
      ...(folder ? { folder } : {}),
      // Step 9: burn the captions into the picture or not, and a .srt beside the file.
      captions: captions.captions,
      srt: captions.srt
    }),
  cancelExport: (exportId: number) => post<ProjectExportJob>(`/export/jobs/${exportId}/cancel`),
  exportJob: (exportId: number) => call<ProjectExportJob>(`/export/jobs/${exportId}`),
  /** The project's exports, newest first. */
  exports: (id: string) => call<{ exports: ProjectExportRow[] }>(`/projects/${encodeURIComponent(id)}/exports`),

  /** The thumbnail index of a media (its `urls.thumbs`, token included):
   *  null while it is being made (202). */
  thumbs: async (url: string, signal?: AbortSignal): Promise<ThumbIndex | null> => {
    const res = await fetch(engineUrl(url), { headers: await sessionHeaders(), signal })
    if (res.status === 202) return null
    if (!res.ok) throw new EditorError(res.status, `${res.status}`, null)
    return res.json() as Promise<ThumbIndex>
  },
  /** The waveform (step 6's CKPK bytes) of a media, at `perSecond` and every
   *  coarser level: null while it is being made, 'none' for no sound. */
  peaks: async (url: string, perSecond: number, signal?: AbortSignal): Promise<ArrayBuffer | null | 'none'> => {
    const join = url.includes('?') ? '&' : '?'
    const res = await fetch(engineUrl(`${url}${join}per_second=${perSecond}&wait=20`), {
      headers: await sessionHeaders(),
      signal
    })
    if (res.status === 202) return null
    if (res.status === 404) return 'none'
    if (!res.ok) throw new EditorError(res.status, `${res.status}`, null)
    return res.arrayBuffer()
  },
  /** Any picture the engine serves (a thumbnail sheet), decoded: the page's
   *  rules let no blob: or data: address into an <img>, and the dev server's
   *  none of the engine's. */
  bitmap: async (url: string, signal?: AbortSignal): Promise<ImageBitmap> => {
    const res = await fetch(engineUrl(url), { headers: await sessionHeaders(), signal })
    if (!res.ok) throw new EditorError(res.status, `${res.status}`, null)
    return createImageBitmap(await res.blob())
  },

  /** The editor cache (Settings → Storage). */
  cacheSize: () => call<{ folders: number; files: number; bytes: number; cap_bytes: number }>('/media/cache/size'),
  clearCache: () => post<{ files_removed: number; bytes_freed: number; kept_in_use: number }>('/media/cache/clear')
}

/** The engine's sentence for a failed call, for the person. */
export const errorText = (e: unknown): string => (e instanceof Error ? e.message : String(e))

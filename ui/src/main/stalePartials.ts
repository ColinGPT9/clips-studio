import { type Dirent, readFileSync, readdirSync, statSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'

/** How long an unfinished model download is kept without being touched. */
const KEEP_MS = 7 * 24 * 60 * 60 * 1000

/** The only manifest this reads: what the bundled Ollama (v0.32.6,
 *  scripts/fetch_ollama.py) writes under manifests/, listing a config and its
 *  layers. */
const MANIFEST_V2 = 'application/vnd.docker.distribution.manifest.v2+json'

/** Every blob digest an installed model uses, as blob file names
 *  ("sha256-…"), or null when that can't be known for certain, in which case
 *  nothing that might belong to a model is deleted: a manifest that can't be
 *  read, or one in a shape other than MANIFEST_V2's. Newer Ollama keeps its
 *  manifests in manifests-v2/, as blobs themselves, and can list other
 *  manifests; read with this, a model it downloaded would look like orphans,
 *  so any sign of that layout stops it. No manifests folder at all (and no
 *  manifests-v2) means no model was ever installed here: nothing is
 *  referenced. */
function referencedBlobs(modelsDir: string): Set<string> | null {
  try {
    statSync(join(modelsDir, 'manifests-v2'))
    return null
  } catch {
    // Not there: the layout this understands.
  }
  const used = new Set<string>()
  const walk = (dir: string): boolean => {
    let entries: Dirent[]
    try {
      entries = readdirSync(dir, { withFileTypes: true })
    } catch (e) {
      return (e as NodeJS.ErrnoException).code === 'ENOENT' && dir === join(modelsDir, 'manifests')
    }
    for (const entry of entries) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) {
        if (!walk(path)) return false
        continue
      }
      try {
        const manifest = JSON.parse(readFileSync(path, 'utf8')) as {
          mediaType?: string
          config?: { digest?: string }
          layers?: { digest?: string }[]
          manifests?: unknown
        }
        if (
          manifest.mediaType !== MANIFEST_V2 ||
          manifest.manifests !== undefined ||
          typeof manifest.config?.digest !== 'string'
        ) {
          return false
        }
        for (const digest of [manifest.config.digest, ...(manifest.layers ?? []).map((l) => l.digest)]) {
          if (digest) used.add(digest.replace(':', '-'))
        }
      } catch {
        return false
      }
    }
    return true
  }
  return walk(join(modelsDir, 'manifests')) ? used : null
}

/** Delete what model downloads nobody has carried on with for a week left in
 *  blobs/.
 *
 *  Ollama is started with OLLAMA_NOPRUNE so that a download stopped part-way
 *  carries on where it stopped when it is tried again (#148, #150). That also
 *  kept the ones never tried again, for as long as the app was installed:
 *  gigabytes no page lists and nothing can remove. A week without a write is
 *  taken as given up on. Two kinds are left behind:
 *
 *  - the `sha256-…-partial` data file of a download and the small
 *    `-partial-0`, `-partial-1`… files recording its progress. They go
 *    together, judged by the newest of them, so a download that is resumed
 *    never finds half of what it resumes from gone;
 *  - finished `sha256-…` blobs no installed model's manifest lists: Ollama
 *    renames each part when it has it all, before the long "verifying"
 *    step, so a quit during that step (the #150 case) leaves whole files
 *    with no model. These go only when every manifest could be read.
 *
 *  Called before Ollama starts, so nothing is downloading. Anything that
 *  can't be read or deleted is left for the next start. */
export function dropStalePartials(modelsDir: string, now: number = Date.now()): string[] {
  const blobs = join(modelsDir, 'blobs')
  let names: string[]
  try {
    names = readdirSync(blobs)
  } catch {
    return [] // no models downloaded yet
  }
  const modifiedAt = (file: string): number | null => {
    try {
      return statSync(file).mtimeMs
    } catch {
      return null
    }
  }
  const downloads = new Map<string, { files: string[]; newest: number }>()
  const whole: { name: string; file: string; modified: number }[] = []
  for (const name of names) {
    const file = join(blobs, name)
    const modified = modifiedAt(file)
    if (modified === null) continue
    const at = name.indexOf('-partial')
    if (at < 0) {
      if (name.startsWith('sha256-')) whole.push({ name, file, modified })
      continue
    }
    const digest = name.slice(0, at)
    const download = downloads.get(digest) ?? { files: [], newest: 0 }
    download.files.push(file)
    download.newest = Math.max(download.newest, modified)
    downloads.set(digest, download)
  }
  const doomed: string[] = []
  for (const { files, newest } of downloads.values()) {
    if (now - newest >= KEEP_MS) doomed.push(...files)
  }
  const used = whole.length ? referencedBlobs(modelsDir) : null
  if (used) {
    for (const { name, file, modified } of whole) {
      if (!used.has(name) && now - modified >= KEEP_MS) doomed.push(file)
    }
  }
  const dropped: string[] = []
  for (const file of doomed) {
    try {
      unlinkSync(file)
      dropped.push(file)
    } catch {
      // Held open or already gone: the next start tries again.
    }
  }
  return dropped
}

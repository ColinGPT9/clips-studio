import { readdirSync, statSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'

/** How long an unfinished model download is kept without being touched. */
const KEEP_MS = 7 * 24 * 60 * 60 * 1000

/** Delete the pieces of model downloads nobody has carried on with for a week.
 *
 *  Ollama is started with OLLAMA_NOPRUNE so that a download stopped part-way
 *  carries on where it stopped when it is tried again (#148, #150). That also
 *  kept the ones never tried again, for as long as the app was installed:
 *  gigabytes of `sha256-…-partial` files in blobs/ that no page lists and
 *  nothing can remove. A week without a write is taken as given up on.
 *
 *  A download is a data file and the small `-partial-0`, `-partial-1`… files
 *  that record its progress. They go together, judged by the newest of them,
 *  so a download that is resumed never finds half of what it resumes from
 *  gone. Called before Ollama starts, so nothing is downloading. Anything that
 *  can't be read or deleted is left for the next start. */
export function dropStalePartials(modelsDir: string, now: number = Date.now()): string[] {
  const blobs = join(modelsDir, 'blobs')
  let names: string[]
  try {
    names = readdirSync(blobs)
  } catch {
    return [] // no models downloaded yet
  }
  const downloads = new Map<string, { files: string[]; newest: number }>()
  for (const name of names) {
    const at = name.indexOf('-partial')
    if (at < 0) continue
    const file = join(blobs, name)
    let modified: number
    try {
      modified = statSync(file).mtimeMs
    } catch {
      continue
    }
    const digest = name.slice(0, at)
    const download = downloads.get(digest) ?? { files: [], newest: 0 }
    download.files.push(file)
    download.newest = Math.max(download.newest, modified)
    downloads.set(digest, download)
  }
  const dropped: string[] = []
  for (const { files, newest } of downloads.values()) {
    if (now - newest < KEEP_MS) continue
    for (const file of files) {
      try {
        unlinkSync(file)
        dropped.push(file)
      } catch {
        // Held open or already gone: the next start tries again.
      }
    }
  }
  return dropped
}

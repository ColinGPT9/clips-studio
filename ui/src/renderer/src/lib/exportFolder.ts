/** Where exported clips are saved. Defaults to the OS Downloads folder (like
 *  other video editors) and remembers the user's chosen folder. */

const KEY = 'export-folder'

/** The remembered export folder, or the OS Downloads folder on first use. */
export async function getExportFolder(): Promise<string> {
  const saved = localStorage.getItem(KEY)
  if (saved) return saved
  try {
    return await window.studio.getDownloadsPath()
  } catch {
    return 'exports'
  }
}

export function setExportFolder(path: string): void {
  localStorage.setItem(KEY, path)
}

/** Open the native folder picker; returns the chosen folder (and remembers
 *  it) or null if cancelled. */
export async function pickExportFolder(): Promise<string | null> {
  const chosen = await window.studio.pickFolder()
  if (chosen) setExportFolder(chosen)
  return chosen
}

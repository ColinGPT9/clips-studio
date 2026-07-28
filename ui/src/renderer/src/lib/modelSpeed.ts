/** What picking a bigger model actually costs you, in time.
 *
 *  The selector used to show only a name and a size on disk, which says
 *  nothing about the trade being made. Bigger models choose and title clips
 *  better and take longer per video — on a two-hour stream that difference is
 *  the difference between a coffee and an afternoon. Someone who switches to
 *  27b and sees "152 minutes remaining" should have been told first.
 */

export type SpeedTone = 'ok' | 'slow' | 'warn'

export interface SpeedNote {
  tone: SpeedTone
  text: string
}

/** Parameter count from an Ollama tag: gemma3:12b -> 12, llama3.1:8b -> 8.
 *
 *  Reads the tag after the colon, so the "3" in "gemma3" is never mistaken
 *  for a size. Returns null for tags that don't say (a bare `:latest`), and
 *  the caller then says nothing rather than guessing.
 */
export function paramsOf(name: string): number | null {
  const tag = name.includes(':') ? name.slice(name.indexOf(':') + 1) : name
  const match = tag.match(/(\d+(?:\.\d+)?)\s*b\b/i)
  if (!match) return null
  const n = parseFloat(match[1])
  return Number.isFinite(n) && n > 0 ? n : null
}

// The smallest model anyone is steered toward, and the baseline every
// comparison is made against.
const BASELINE_PARAMS = 4

/** Weights have to sit in VRAM to run at full speed. Anything above roughly
 *  the card's capacity spills into system memory, where it does not run a bit
 *  slower — it crawls, badly enough that people report it as frozen. The
 *  margin covers the context window and working buffers on top of weights. */
const VRAM_HEADROOM = 1.25

/**
 * A one-line honest note about what this model costs in time.
 *
 * `vramTotalBytes` is what `/system/stats` reports — BYTES, not gigabytes,
 * and taken raw here on purpose. Converting at each call site is how one of
 * them ends up comparing gigabytes against bytes, and that mistake is
 * invisible: the warning simply never appears. Optional, because without a
 * GPU reading the relative speed is still worth saying.
 */
export function speedNote(
  name: string,
  sizeGb: number,
  vramTotalBytes?: number | null
): SpeedNote | null {
  // Decimal GB, matching how the rest of the app reports sizes (SystemStats).
  const vramGb = vramTotalBytes && vramTotalBytes > 0 ? vramTotalBytes / 1e9 : null

  // The expensive case first: it does not matter how big the model is if it
  // does not fit, because that dwarfs every other difference here.
  if (vramGb !== null && sizeGb * VRAM_HEADROOM > vramGb) {
    return {
      tone: 'warn',
      text:
        // One decimal, matching SystemStats. Rounding 12.88 to "13 GB" would
        // contradict the figure shown on the dashboard for the same card.
        `Too big for your ${vramGb.toFixed(1)} GB graphics card. It will run on the ` +
        `processor instead and be many times slower — often hours for one video.`
    }
  }

  const params = paramsOf(name)
  if (params === null) return null

  // Generation time scales roughly with parameter count once the model fits
  // in VRAM. Rough is the honest word: real speed depends on the card, the
  // video's length and how much of it is speech.
  const factor = params / BASELINE_PARAMS

  if (factor <= 1.25) {
    return { tone: 'ok', text: 'Fastest option. Picks clips less carefully than the bigger models.' }
  }
  if (factor < 2) {
    return { tone: 'ok', text: `Better clip picking, roughly ${factor.toFixed(1)}x longer to process.` }
  }
  return {
    tone: 'slow',
    text:
      `Best clip picking, but roughly ${Math.round(factor)}x longer to process than the 4b model. ` +
      `On a long stream that can mean a couple of hours.`
  }
}

/** What the caption previews need to look like the burned captions: the
 *  fonts that ship with the app, the named caption looks, and where a
 *  caption stands once an app's safe zone is kept clear.
 *
 *  - Fonts: GET /fonts lists the bundled families (assets/fonts). Each file is
 *    registered with the FontFace API from the engine's own URL, so the
 *    preview draws with the very file the render burns. `display: swap`
 *    shows the fallback font while a face loads.
 *  - Presets: GET /caption-presets (config/caption_presets.json).
 *  - Layout: video/captions.py _layout, at the preview's scale. The safe
 *    zones come from gaming/layouts.json (lib/gamingLayouts.ts), which
 *    video/captions.py SAFE_ZONES equals (tests keep both the same). */
import { useEffect, useState } from 'react'
import { api, API_BASE } from './api'
import { LAYOUTS } from './gamingLayouts'
import type { BundledFont, CaptionPreset, CaptionStyle } from './types'

let fonts: BundledFont[] = []
let fontsLoading: Promise<BundledFont[]> | null = null
const fontListeners = new Set<(f: BundledFont[]) => void>()

/** Fetch the bundled fonts once and register their faces. A failure (the
 *  engine not up yet) is not remembered, so the next caller tries again. */
export function loadBundledFonts(): Promise<BundledFont[]> {
  if (!fontsLoading) {
    fontsLoading = api
      .bundledFonts()
      .then((res) => {
        const list = Array.isArray(res?.fonts) ? res.fonts : []
        for (const f of list) {
          for (const url of f.urls ?? []) {
            try {
              const face = new FontFace(f.family, `url("${API_BASE}${url}")`, {
                weight: String(f.weight ?? 400),
                display: 'swap'
              })
              document.fonts.add(face)
              face.load().catch(() => undefined) // the name stays in the fallback font
            } catch {
              // An odd name or URL: that one font previews in the fallback.
            }
          }
        }
        fonts = list
        fontListeners.forEach((fn) => fn(list))
        return list
      })
      .catch(() => {
        fontsLoading = null
        return fonts
      })
  }
  return fontsLoading
}

/** The bundled fonts, [] until they have loaded (or with no engine). */
export function useBundledFonts(): BundledFont[] {
  const [list, setList] = useState<BundledFont[]>(fonts)
  useEffect(() => {
    fontListeners.add(setList)
    loadBundledFonts().then(setList)
    return () => {
      fontListeners.delete(setList)
    }
  }, [])
  return list
}

/** The CSS weight a caption font is drawn at. Windows fonts burn bold (700);
 *  a bundled family burns at its own weight, and one that comes in a single
 *  regular weight is not made bold (video/captions.py _header). */
export function captionFontWeight(font: string, list: BundledFont[] = fonts): number {
  const f = list.find((b) => b.family === font)
  if (!f) return 700
  const w = f.weight ?? 400
  return w < 600 ? 400 : w
}

let presets: CaptionPreset[] = []
let presetsLoading: Promise<CaptionPreset[]> | null = null

export function useCaptionPresets(): CaptionPreset[] {
  const [list, setList] = useState<CaptionPreset[]>(presets)
  useEffect(() => {
    let alive = true
    if (!presetsLoading) {
      presetsLoading = api
        .captionPresets()
        .then((res) => {
          presets = (Array.isArray(res?.presets) ? res.presets : []).filter(
            (p) => p && typeof p.id === 'string' && typeof p.name === 'string' && p.style
          )
          return presets
        })
        .catch(() => {
          presetsLoading = null
          return presets
        })
    }
    presetsLoading.then((p) => {
      if (alive) setList(p)
    })
    return () => {
      alive = false
    }
  }, [])
  return list
}

export interface SafeZoneArea {
  top: number
  bottom: number
  right: number
}

/** The area an app covers on a 1080x1920 Short, or null for "off". */
export function safeZoneArea(zone: string | undefined): SafeZoneArea | null {
  if (zone !== 'tiktok' && zone !== 'reels' && zone !== 'shorts') return null
  const z = LAYOUTS.safe_zones[zone]
  return { top: z.top, bottom: z.bottom, right: z.right }
}

const SAFE_CLEARANCE = 20 // video/captions.py _SAFE_CLEARANCE
const INK_SPREAD = 9 // video/captions.py _INK_SPREAD: the outline and shadow past the letters
const SLIDE_PX = 40 // video/captions.py _SLIDE_PX: how far below its place a sliding caption starts
const MARGIN_V = { bottom: 440, middle: 0, top: 140 } // video/captions.py _POSITIONS

/** Where a caption stands on a w x h frame, in that frame's pixels, as
 *  video/captions.py _layout places it: the side margins (null = today's
 *  60 px, scaled as the caller always has) and the bottom or top margin. */
export function captionLayout(
  style: Pick<CaptionStyle, 'position' | 'safe_zone'> & Partial<Pick<CaptionStyle, 'animation'>>,
  w: number,
  h: number
): { position: 'bottom' | 'middle' | 'top'; side: number | null; margin: number } {
  const position = style.position === 'top' || style.position === 'middle' ? style.position : 'bottom'
  let margin = MARGIN_V[position]
  let side: number | null = null
  const zone = h > w ? safeZoneArea(style.safe_zone) : null
  if (zone) {
    const rise = style.animation === 'slide' ? SLIDE_PX : 0
    if (position === 'bottom') margin = Math.max(margin, zone.bottom + SAFE_CLEARANCE + rise)
    else if (position === 'top') margin = Math.max(margin, zone.top + SAFE_CLEARANCE)
    side = Math.round(((Math.max(60, zone.right) + INK_SPREAD) * w) / 1080)
  }
  return { position, side, margin: (margin * h) / 1920 }
}

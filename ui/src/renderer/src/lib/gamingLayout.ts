/** Gaming / Reaction geometry for the editor's preview.
 *
 *  Mirrors gaming/layout.py plan() line for line, so the preview beside the
 *  boxes you drag shows exactly the crops the render will make. A test
 *  (tests/test_ui_gaming_layout_sync.py) keeps the numbers in step. */

import type { FrameBox } from './types'

export const OUT_W = 1080
export const OUT_H = 1920
export const BAND_H = 960
export const SPLIT_ASPECT = OUT_W / BAND_H
export const FILL_ASPECT = OUT_W / OUT_H

/** [x, y, w, h] in source pixels. */
export type PxBox = [number, number, number, number]

export interface GamingPlan {
  kind: 'split' | 'fill'
  game: PxBox
  cam: PxBox | null
  camPosition: 'top' | 'bottom'
  /** 'fit': the game whole on a blurred copy of itself; 'fill': zoomed to fill. */
  gameFit: 'fit' | 'fill'
}

/** An even size of at least 2, and an even position that can be 0. */
const even = (v: number): number => Math.max(2, Math.floor(Math.trunc(v) / 2) * 2)
const pos = (v: number): number => Math.max(0, Math.floor(Math.trunc(v) / 2) * 2)

function clampBox(box: FrameBox, srcW: number, srcH: number): PxBox {
  const [x, y, w, h] = box
  const x0 = Math.min(Math.max(0, x), 1) * srcW
  const y0 = Math.min(Math.max(0, y), 1) * srcH
  const x1 = Math.min(Math.max(x + w, 0), 1) * srcW
  const y1 = Math.min(Math.max(y + h, 0), 1) * srcH
  return [pos(x0), pos(y0), even(Math.max(2, x1 - x0)), even(Math.max(2, y1 - y0))]
}

function aligned(srcW: number, cropW: number, align: string): number {
  if (align === 'left') return 0
  if (align === 'right') return srcW - cropW
  return pos((srcW - cropW) / 2)
}

/** The x for a full-height crop that overlaps the webcam least, nearest the
 *  centre among equals. */
function clearOf(srcW: number, cropW: number, cam: PxBox): number {
  const cx0 = cam[0]
  const cx1 = cam[0] + cam[2]
  const centre = (srcW - cropW) / 2
  let best = 0
  let bestKey: [number, number] | null = null
  for (let x = 0; x <= srcW - cropW; x += 2) {
    const overlap = Math.max(0, Math.min(x + cropW, cx1) - Math.max(x, cx0))
    const key: [number, number] = [overlap, Math.abs(x - centre)]
    if (bestKey === null || key[0] < bestKey[0] || (key[0] === bestKey[0] && key[1] < bestKey[1])) {
      best = x
      bestKey = key
    }
  }
  return best
}

function cover(box: PxBox, aspect: number): PxBox {
  const [x, y, w, h] = box
  if (w / h > aspect) {
    const cw = even(h * aspect)
    return [pos(x + (w - cw) / 2), y, cw, h]
  }
  const ch = even(w / aspect)
  return [x, pos(y + (h - ch) / 2), w, ch]
}

/** The area a box covers when fitted whole into a band of this aspect. */
function shown(box: PxBox, aspect: number): number {
  const [, , w, h] = box
  const scale = Math.min(aspect / w, 1 / h)
  return w * h * scale * scale
}

/** The strip beside the webcam that shows biggest when fitted into the band. */
function beside(srcW: number, srcH: number, cam: PxBox, aspect: number): PxBox {
  const [cx, cy, cw, ch] = cam
  const strips: PxBox[] = [
    [0, 0, cx, srcH],
    [cx + cw, 0, srcW - cx - cw, srcH],
    [0, 0, srcW, cy],
    [0, cy + ch, srcW, srcH - cy - ch]
  ].filter((s) => s[2] >= 0.2 * srcW && s[3] >= 0.2 * srcH) as PxBox[]
  if (strips.length === 0) return [0, 0, even(srcW), even(srcH)]
  let best = strips[0]
  for (const s of strips) if (shown(s, aspect) > shown(best, aspect)) best = s
  const [x, y, w, h] = best
  return [pos(x), pos(y), even(w), even(h)]
}

export function plan(
  srcW: number,
  srcH: number,
  camBox: FrameBox | null,
  opts: { camPosition?: string; gameAlign?: string; gameBox?: FrameBox | null; gameFit?: string } = {}
): GamingPlan {
  const camPosition = opts.camPosition === 'bottom' ? 'bottom' : 'top'
  const gameAlign = ['left', 'center', 'right'].includes(opts.gameAlign ?? '') ? opts.gameAlign! : 'center'
  const gameFit = opts.gameFit === 'fill' ? 'fill' : 'fit'
  const aspect = camBox === null ? FILL_ASPECT : SPLIT_ASPECT
  const cam = camBox === null ? null : clampBox(camBox, srcW, srcH)
  let game: PxBox
  if (opts.gameBox) {
    const region = clampBox(opts.gameBox, srcW, srcH)
    game = gameFit === 'fit' ? region : cover(region, aspect)
  } else if (gameFit === 'fit') {
    game = cam === null ? [0, 0, even(srcW), even(srcH)] : beside(srcW, srcH, cam, aspect)
  } else {
    const cropW = Math.min(srcW, even(srcH * aspect))
    const x =
      cam === null || gameAlign !== 'center' ? aligned(srcW, cropW, gameAlign) : clearOf(srcW, cropW, cam)
    game = [pos(x), 0, cropW, even(srcH)]
  }
  if (cam === null) return { kind: 'fill', game, cam: null, camPosition, gameFit }
  return { kind: 'split', game, cam, camPosition, gameFit }
}

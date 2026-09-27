/** Gaming / Reaction geometry for the layout editor's preview.
 *
 *  Mirrors gaming/layout.py plan() and gaming/framing.py cam_crop() line for
 *  line, so the preview beside the boxes you drag shows exactly the crops the
 *  render will make, face shift included. A test runs this file under Node
 *  and compares every number with the Python (tests/test_ui_gaming_layout_sync.py). */

import { LAYOUTS } from './gamingLayouts'
import type { FrameBox } from './types'

export const OUT_W = LAYOUTS.canvas[0]
export const OUT_H = LAYOUTS.canvas[1]
const HEADROOM = LAYOUTS.headroom
const MAX_SHIFT = 0.3
const PIP_GAP = 24
const PIP_MARGIN = 0.02
export const MIN_PLACE = 0.12
/** Layers moved and resized on the Short itself. */
export type Placeable = 'cam' | 'cam2' | 'ui'

/** [x, y, w, h] in pixels (source or canvas). */
export type PxBox = [number, number, number, number]
/** The streamer's head in source px: [centre x, top, chin]. */
export type Head = [number, number, number]
export type Role = 'cam' | 'cam2' | 'game' | 'ui' | 'bg'

export interface SafeZone {
  top: number
  bottom: number
  left: number
  right: number
}

export interface Element {
  role: Role
  src: PxBox
  dest: PxBox
  fit: 'cover' | 'contain' | 'blur'
  anchor: 'top' | 'bottom' | 'center'
  shift: number
  shape: 'rect' | 'circle'
}

export interface GamingPlan {
  preset: string
  elements: Element[]
  order: 'cam_top' | 'game_top'
  safe: string
}

/** The settings plan() reads; the same keys as render_opts.gaming. */
export interface LayoutSettings {
  preset?: string
  order?: string
  divider?: number
  safe?: string
  game_fit?: string
  game_align?: string
  cam?: FrameBox | null
  cam2?: FrameBox | null
  game_box?: FrameBox | null
  ui_box?: FrameBox | null
  panels?: FrameBox[]
  /** Where the user put the facecams and the Game UI on the Short (fractions of it). */
  places?: Partial<Record<Placeable, FrameBox>>
  cam_position?: string
}

type Spec = {
  label: string
  type: 'stack' | 'full' | 'pip'
  rows?: readonly (readonly string[])[]
  divider?: readonly number[]
  ui_share?: number
  overlay?: readonly string[]
  order?: string
  game_fit?: string
  cams?: readonly string[]
  pip_width?: number
  pip_aspect?: number
  shape?: string
}

export const PRESETS = LAYOUTS.presets as unknown as Record<string, Spec>
export const SAFE_ZONES = LAYOUTS.safe_zones as unknown as Record<string, SafeZone & { label: string }>

// Python's int(v) // 2 * 2, for the positive values used here.
const even = (v: number): number => Math.max(2, Math.floor(Math.trunc(v) / 2) * 2)
const pos = (v: number): number => Math.max(0, Math.floor(Math.trunc(v) / 2) * 2)
const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))
/** Python's round(): halves go to the even neighbour. */
const roundHalfEven = (v: number): number => {
  const f = Math.floor(v)
  const d = v - f
  if (Math.abs(d - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1
  return Math.round(v)
}

export function safeZone(name?: string | null): SafeZone {
  return SAFE_ZONES[name ?? 'tiktok'] ?? SAFE_ZONES.tiktok
}

// ---- framing (gaming/framing.py) --------------------------------------------------------

export function camCrop(box: PxBox, head: Head | null, dest: PxBox, safe: SafeZone): [PxBox, number] {
  const [bx, by, bw, bh] = box
  const aspect = dest[2] / dest[3]
  let cw: number
  let ch: number
  if (bw / bh > aspect) {
    ch = bh
    cw = ch * aspect
  } else {
    cw = bw
    ch = cw / aspect
  }
  if (head === null) {
    const x = bx + (bw - cw) / 2
    const y = by + (bh - ch) / 2
    return [[pos(x), pos(y), even(cw), even(ch)], 0]
  }
  const [hx, top, chin] = head
  const scale = dest[3] / ch
  const [, dy, , dh] = dest
  let wantTop = dy + HEADROOM * dh
  if (dy < safe.top) wantTop = Math.max(wantTop, safe.top)
  // The chin first: the hair under the top bar, or the top of the head cut,
  // rather than the chin under the game.
  wantTop = Math.min(wantTop, dy + dh - (chin - top + 2) * scale)
  const x = clamp(hx - cw / 2, bx, bx + bw - cw)
  const y = clamp(top - (wantTop - dy) / scale, by, by + bh - ch)
  const lands = dy + (top - y) * scale
  const shift = roundHalfEven(Math.min(Math.max(0, wantTop - lands), MAX_SHIFT * dh))
  return [[pos(x), pos(y), even(cw), even(ch)], shift]
}

export function headOnCanvas(head: Head, crop: PxBox, dest: PxBox, shift: number): [number, number] {
  const [, top, chin] = head
  const scale = dest[3] / crop[3]
  return [dest[1] + shift + (top - crop[1]) * scale, dest[1] + shift + (chin - crop[1]) * scale]
}

export function faceClear(
  head: Head | null,
  crop: PxBox,
  dest: PxBox,
  shift: number,
  safe: SafeZone
): { top: boolean; bottom: boolean } {
  if (head === null) return { top: true, bottom: true }
  const [top, chin] = headOnCanvas(head, crop, dest, shift)
  return {
    top: top >= Math.max(dest[1], safe.top) - 1,
    bottom: chin <= Math.min(dest[1] + dest[3], OUT_H - safe.bottom) + 1
  }
}

// ---- layout (gaming/layout.py) -----------------------------------------------------------

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

function clearOf(srcW: number, cropW: number, cams: PxBox[]): number {
  const centre = (srcW - cropW) / 2
  let best = 0
  let bestKey: [number, number] | null = null
  for (let x = 0; x <= srcW - cropW; x += 2) {
    let overlap = 0
    for (const c of cams) overlap += Math.max(0, Math.min(x + cropW, c[0] + c[2]) - Math.max(x, c[0]))
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

function shown(box: PxBox, aspect: number): number {
  const [, , w, h] = box
  const scale = Math.min(aspect / w, 1 / h)
  return w * h * scale * scale
}

const sortedSet = (values: number[]): number[] => [...new Set(values)].sort((a, b) => a - b)

function clear(box: PxBox, obstacles: PxBox[]): boolean {
  const [x, y, w, h] = box
  return obstacles.every((o) => x + w <= o[0] || o[0] + o[2] <= x || y + h <= o[1] || o[1] + o[3] <= y)
}

function openAreas(srcW: number, srcH: number, obstacles: PxBox[]): PxBox[] {
  const lefts = sortedSet([0, ...obstacles.filter((o) => o[0] + o[2] < srcW).map((o) => o[0] + o[2])])
  const rights = sortedSet([srcW, ...obstacles.filter((o) => o[0] > 0).map((o) => o[0])])
  const tops = sortedSet([0, ...obstacles.filter((o) => o[1] + o[3] < srcH).map((o) => o[1] + o[3])])
  const bottoms = sortedSet([srcH, ...obstacles.filter((o) => o[1] > 0).map((o) => o[1])])
  const areas: PxBox[] = []
  for (const x0 of lefts)
    for (const x1 of rights) {
      if (x1 <= x0) continue
      for (const y0 of tops)
        for (const y1 of bottoms) {
          if (y1 <= y0) continue
          const a: PxBox = [x0, y0, x1 - x0, y1 - y0]
          if (!clear(a, obstacles)) continue
          const across = (o: PxBox): boolean => o[1] < a[1] + a[3] && o[1] + o[3] > a[1]
          const along = (o: PxBox): boolean => o[0] < a[0] + a[2] && o[0] + o[2] > a[0]
          if (
            (x0 === 0 || obstacles.some((o) => o[0] + o[2] === x0 && across(o))) &&
            (x1 === srcW || obstacles.some((o) => o[0] === x1 && across(o))) &&
            (y0 === 0 || obstacles.some((o) => o[1] + o[3] === y0 && along(o))) &&
            (y1 === srcH || obstacles.some((o) => o[1] === y1 && along(o)))
          )
            areas.push(a)
        }
    }
  return areas
}

function whole(srcW: number, srcH: number, obstacles: PxBox[], aspect: number): PxBox {
  const areas = openAreas(srcW, srcH, obstacles).filter((a) => a[2] >= 0.2 * srcW && a[3] >= 0.2 * srcH)
  if (areas.length === 0) return [0, 0, even(srcW), even(srcH)]
  let best = areas[0]
  for (const a of areas) if (shown(a, aspect) > shown(best, aspect)) best = a
  const [x, y, w, h] = best
  return [pos(x), pos(y), even(w), even(h)]
}

function picture(srcW: number, srcH: number, panels: PxBox[]): PxBox {
  const areas = openAreas(srcW, srcH, panels)
  if (areas.length === 0) return [0, 0, srcW, srcH]
  let best = areas[0]
  for (const a of areas) if (a[2] * a[3] > best[2] * best[3]) best = a
  return best
}

function spots(t: number, size: number, limit: number, obstacles: PxBox[], axis: 0 | 1, align: string): number[] {
  const near = align === 'center' ? t - size / 2 : align === 'left' ? t : t - size
  const raw = [near, 0, limit - size]
  for (const o of obstacles) raw.push(o[axis] + o[axis + 2], o[axis] - size)
  return sortedSet(raw.map((v) => pos(Math.min(Math.max(v, 0), limit - size))))
}

function zoom(srcW: number, srcH: number, aspect: number, align: string, cams: PxBox[], panels: PxBox[]): PxBox | null {
  const obstacles = [...cams, ...panels]
  const [gx, gy, gw, gh] = picture(srcW, srcH, panels)
  const tx = align === 'left' ? gx : align === 'right' ? gx + gw : gx + gw / 2
  const ty = gy + gh / 2
  const fullH = srcH * aspect <= srcW ? srcH : srcW / aspect
  const tops = [0, ...obstacles.map((o) => o[1] + o[3])]
  const bottoms = [srcH, ...obstacles.map((o) => o[1])]
  const lefts = [0, ...obstacles.map((o) => o[0] + o[2])]
  const rights = [srcW, ...obstacles.map((o) => o[0])]
  const heights = [fullH]
  for (const t of tops) for (const b of bottoms) if (b > t) heights.push(b - t)
  for (const l of lefts) for (const r of rights) if (r > l) heights.push((r - l) / aspect)
  const tries = sortedSet(heights.filter((h) => fullH / 2 <= h && h <= fullH).map(even)).reverse()
  for (const ch of tries) {
    const cw = even(ch * aspect)
    let best: { key: [number, number, number]; crop: PxBox } | null = null
    for (const x of spots(tx, cw, srcW, obstacles, 0, align))
      for (const y of spots(ty, ch, srcH, obstacles, 1, 'center')) {
        const crop: PxBox = [x, y, cw, ch]
        if (!(x <= tx && tx <= x + cw && y <= ty && ty <= y + ch) || !clear(crop, obstacles)) continue
        const lined = align === 'left' ? x : align === 'right' ? x + cw : x + cw / 2
        const key: [number, number, number] = [Math.abs(lined - tx) + Math.abs(y + ch / 2 - ty), x, y]
        if (
          best === null ||
          key[0] < best.key[0] ||
          (key[0] === best.key[0] && (key[1] < best.key[1] || (key[1] === best.key[1] && key[2] < best.key[2])))
        )
          best = { key, crop }
      }
    if (best !== null) return best.crop
  }
  return null
}

/** Every part a preset shows: rows, picture-in-picture webcams and overlays. */
export function rolesOfSpec(spec: Spec): Set<string> {
  return new Set<string>([...(spec.rows ?? []).flat(), ...(spec.cams ?? []), ...(spec.overlay ?? [])])
}

/** A layer placed on the Short, as even canvas px kept on the canvas; with
 *  `aspect` its height follows its width (layout._placed). */
export function placed(box: FrameBox, aspect: number | null = null): PxBox {
  let w = Math.min(Math.max(box[2], MIN_PLACE), 1) * OUT_W
  let h: number
  if (aspect) {
    h = w / aspect
    if (h > OUT_H) {
      h = OUT_H
      w = OUT_H * aspect
    }
  } else h = Math.min(Math.max(box[3], 0.02), 1) * OUT_H
  const x = Math.min(Math.max(box[0] * OUT_W, 0), OUT_W - w)
  const y = Math.min(Math.max(box[1] * OUT_H, 0), OUT_H - h)
  return [pos(x), pos(y), even(w), even(h)]
}

/** The preset that can actually be drawn with these settings (layout.resolve). */
export function resolve(settings: LayoutSettings): LayoutSettings & { preset: string } {
  const s: LayoutSettings = { ...settings }
  let preset = s.preset
  if (!preset || !(preset in PRESETS)) {
    preset = 'half'
    if (s.order === undefined) s.order = s.cam_position === 'bottom' ? 'game_top' : 'cam_top'
    if (s.game_fit === undefined) s.game_fit = s.game_fit || 'fit'
  }
  const spec = PRESETS[preset]
  const roles = rolesOfSpec(spec)
  if (roles.has('cam') && !s.cam) preset = 'blurred'
  else if (roles.has('ui') && !s.ui_box) preset = 'split'
  else if (roles.has('cam2') && !s.cam2) preset = ({ dual_cam: 'small_cam', duo_split: 'split' } as Record<string, string>)[preset] ?? preset
  return { ...s, preset }
}

function stackRegions(
  spec: Spec,
  order: string,
  divider: number | undefined,
  gameH: number | null = null,
  top = 0
): [string, PxBox, Element['anchor']][] {
  const rows = (spec.rows ?? []).map((r) => [...r])
  if (order === 'game_top') rows.reverse()
  const [lo, hi, def] = spec.divider ?? [0.5, 0.5, 0.5]
  const camH = OUT_H * Math.min(Math.max(divider ?? def, lo), hi)
  let heights: (number | null)[] = rows.map((row) =>
    row.includes('game') ? null : row.includes('ui') && row.length === 1 ? OUT_H * (spec.ui_share ?? 0.12) : camH
  )
  const rest = OUT_H - heights.reduce<number>((a, h) => a + (h ?? 0), 0)
  const packed = gameH !== null && gameH < rest
  heights = heights.map((h) => (h === null ? (packed ? (gameH as number) : rest) : h))
  const edges = [packed ? pos(Math.min(rest - (gameH as number), top)) : 0]
  for (const h of heights.slice(0, -1)) edges.push(pos(edges[edges.length - 1] + (h as number)))
  edges.push(packed ? pos(edges[edges.length - 1] + (heights[heights.length - 1] as number)) : OUT_H)
  const out: [string, PxBox, Element['anchor']][] = []
  rows.forEach((row, i) => {
    const anchor: Element['anchor'] = i === 0 ? 'top' : i === rows.length - 1 ? 'bottom' : 'center'
    const cols = [...row.map((_, j) => pos((j * OUT_W) / row.length)), OUT_W]
    row.forEach((role, j) => {
      out.push([role, [cols[j], edges[i], cols[j + 1] - cols[j], edges[i + 1] - edges[i]], anchor])
    })
  })
  return out
}

function pipRegions(spec: Spec, safe: SafeZone): [string, PxBox][] {
  const cams = spec.cams ?? ['cam']
  const w = even(OUT_W * (spec.pip_width ?? 0.4))
  const h = even(w / (spec.pip_aspect ?? 1))
  const y = pos(safe.top + PIP_MARGIN * OUT_H)
  const total = cams.length * w + (cams.length - 1) * PIP_GAP
  const x0 = (OUT_W - total) / 2
  return cams.map((role, i) => [role, [pos(x0 + i * (w + PIP_GAP)), y, w, h]])
}

function gameSrc(
  srcW: number,
  srcH: number,
  s: LayoutSettings,
  aspect: number,
  fit: string,
  cams: PxBox[],
  panels: PxBox[]
): PxBox {
  if (s.game_box) {
    const region = clampBox(s.game_box, srcW, srcH)
    return fit === 'fit' ? region : cover(region, aspect)
  }
  if (fit === 'fit') return whole(srcW, srcH, [...cams, ...panels], aspect)
  const align = ['left', 'center', 'right'].includes(s.game_align ?? '') ? (s.game_align as string) : 'center'
  const zoomed = zoom(srcW, srcH, aspect, align, cams, panels)
  if (zoomed !== null) return zoomed
  const cropW = Math.min(srcW, even(srcH * aspect))
  const x = cams.length > 0 && align === 'center' ? clearOf(srcW, cropW, cams) : aligned(srcW, cropW, align)
  const cropH = cropW === srcW ? Math.min(srcH, even(cropW / aspect)) : even(srcH)
  return [pos(x), pos((srcH - cropH) / 2), cropW, cropH]
}

export function plan(
  srcW: number,
  srcH: number,
  settings: LayoutSettings,
  heads: Partial<Record<Role, Head>> = {}
): GamingPlan {
  const s = resolve(settings)
  const preset = s.preset
  const spec = PRESETS[preset]
  const order = (s.order === 'cam_top' || s.order === 'game_top' ? s.order : spec.order ?? 'cam_top') as
    | 'cam_top'
    | 'game_top'
  const safeName = s.safe && s.safe in SAFE_ZONES ? s.safe : 'tiktok'
  const safe = safeZone(safeName)
  // Blurred and Fullscreen ARE the game shown whole or zoomed: the layout decides.
  const fit =
    spec.type === 'full'
      ? (spec.game_fit as string)
      : s.game_fit === 'fit' || s.game_fit === 'fill'
        ? s.game_fit
        : spec.game_fit ?? 'fill'
  const camBoxes: Partial<Record<Role, PxBox>> = {}
  for (const role of ['cam', 'cam2'] as const) {
    const box = s[role]
    if (box) camBoxes[role] = clampBox(box, srcW, srcH)
  }
  // The game keeps clear of the webcams this layout shows.
  const uses = rolesOfSpec(spec)
  const places = s.places ?? {}
  const shownCams = (['cam', 'cam2'] as const)
    .filter((r) => camBoxes[r] && (r === 'cam' || uses.has(r)))
    .map((r) => camBoxes[r] as PxBox)
  const panels = (s.panels ?? []).map((b) => clampBox(b, srcW, srcH))
  const elements: Element[] = []

  const camera = (role: Role, dest: PxBox, shape: Element['shape'] = 'rect'): void => {
    const [crop, shift] = camCrop(camBoxes[role] as PxBox, heads[role] ?? null, dest, safe)
    elements.push({ role, src: crop, dest, fit: 'cover', anchor: 'center', shift, shape })
  }
  const game = (dest: PxBox, anchor: Element['anchor'], given: PxBox | null = null): void => {
    const src = given ?? gameSrc(srcW, srcH, s, dest[2] / dest[3], fit, shownCams, panels)
    elements.push({ role: 'game', src, dest, fit: fit === 'fit' ? 'contain' : 'cover', anchor, shift: 0, shape: 'rect' })
  }

  if (spec.type === 'full') {
    game([0, 0, OUT_W, OUT_H], 'center')
  } else if (spec.type === 'pip') {
    game([0, 0, OUT_W, OUT_H], 'center')
    let size: [number, number] | null = null
    for (const [role, region] of pipRegions(spec, safe)) {
      let dest = region
      const put = places[role as Placeable]
      if (put) dest = placed(put, spec.pip_aspect ?? 1)
      if (size === null) size = [dest[2], dest[3]]
      else if (dest[2] !== size[0] || dest[3] !== size[1])
        // Two webcams are always the same size: the first one's.
        dest = [Math.min(dest[0], OUT_W - size[0]), Math.min(dest[1], OUT_H - size[1]), size[0], size[1]]
      camera(role as Role, dest, (spec.shape ?? 'rect') as Element['shape'])
    }
  } else {
    let regions = stackRegions(spec, order, s.divider)
    let wholeGame: PxBox | null = null
    if (fit === 'fit') {
      // The whole game right against the webcam; blur above and below the two.
      const space = (regions.find(([role]) => role === 'game') as [string, PxBox, Element['anchor']])[1]
      wholeGame = gameSrc(srcW, srcH, s, space[2] / space[3], fit, shownCams, panels)
      regions = stackRegions(spec, order, s.divider, (OUT_W * wholeGame[3]) / wholeGame[2], safe.top)
      const last = regions[regions.length - 1][1]
      if (regions[0][1][1] > 0 || last[1] + last[3] < OUT_H)
        elements.push({ role: 'bg', src: wholeGame, dest: [0, 0, OUT_W, OUT_H], fit: 'blur', anchor: 'center', shift: 0, shape: 'rect' })
    }
    for (const [role, dest, anchor] of regions) {
      if (role === 'game') game(dest, anchor, wholeGame)
      else if (role === 'ui') {
        // Cut to its space's shape, like every other part: no blur round it.
        const ui = clampBox(s.ui_box as FrameBox, srcW, srcH)
        elements.push({ role: 'ui', src: cover(ui, dest[2] / dest[3]), dest, fit: 'cover', anchor: 'center', shift: 0, shape: 'rect' })
      } else camera(role as Role, dest)
    }
    if ((spec.overlay ?? []).includes('ui')) {
      // The Game UI layer: over the game, against the webcam, until it's moved.
      const [, gy, , gh] = (elements.find((e) => e.role === 'game') as Element).dest
      const h = even(OUT_H * (spec.ui_share ?? 0.12))
      let dest: PxBox = [0, pos(order === 'cam_top' ? gy : gy + gh - h), OUT_W, h]
      if (places.ui) dest = placed(places.ui)
      const ui = clampBox(s.ui_box as FrameBox, srcW, srcH)
      elements.push({ role: 'ui', src: cover(ui, dest[2] / dest[3]), dest, fit: 'cover', anchor: 'center', shift: 0, shape: 'rect' })
    }
  }
  return { preset, elements, order, safe: safeName }
}

/** The element for a role, if the plan has one. */
export const elementOf = (p: GamingPlan, role: Role): Element | undefined => p.elements.find((e) => e.role === role)

/** Moving and resizing a layer on the Short (a facecam, the Game UI), with
 *  snapping to guide lines the way a design editor does it: the layer's edges
 *  and middle snap to the Short's middle and edges, the platform's safe lines,
 *  and the other layers' edges and middles. Pure maths, in canvas px, so it can
 *  be tested on its own (tests/test_ui_layer_snapping.py). */

export type Box = [number, number, number, number]
export type Handle = 'move' | 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'
export interface Targets {
  xs: number[]
  ys: number[]
}
export interface DragOptions {
  /** width / height to keep (a facecam), or null for a free box (the Game UI). */
  aspect: number | null
  canvas: [number, number]
  minW: number
  minH: number
  /** Lines to snap to, or null with snapping off (Alt held). */
  targets: Targets | null
  /** How close counts as lined up, canvas px. */
  tol: number
}
export interface DragResult {
  box: Box
  /** The lines it snapped to, to draw while dragging. */
  guides: Targets
}

function nearest(values: number[], targets: number[], tol: number): { delta: number; line: number } | null {
  let best: { delta: number; line: number } | null = null
  for (const v of values)
    for (const t of targets) {
      const d = t - v
      if (Math.abs(d) <= tol && (best === null || Math.abs(d) < Math.abs(best.delta))) best = { delta: d, line: t }
    }
  return best
}

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))

export function dragLayer(start: Box, handle: Handle, dx: number, dy: number, o: DragOptions): DragResult {
  const [W, H] = o.canvas
  const [x0, y0, w0, h0] = start
  const guides: Targets = { xs: [], ys: [] }

  if (handle === 'move') {
    let x = x0 + dx
    let y = y0 + dy
    if (o.targets) {
      const sx = nearest([x, x + w0 / 2, x + w0], o.targets.xs, o.tol)
      if (sx) {
        x += sx.delta
        guides.xs.push(sx.line)
      }
      const sy = nearest([y, y + h0 / 2, y + h0], o.targets.ys, o.tol)
      if (sy) {
        y += sy.delta
        guides.ys.push(sy.line)
      }
    }
    return { box: [clamp(x, 0, W - w0), clamp(y, 0, H - h0), w0, h0], guides }
  }

  const west = handle.includes('w')
  const east = handle.includes('e')
  const north = handle.includes('n')
  const south = handle.includes('s')

  if (o.aspect === null) {
    // A free box: each moving edge follows the pointer and snaps on its own.
    let L = x0
    let R = x0 + w0
    let T = y0
    let B = y0 + h0
    const snapEdge = (v: number, axis: 'xs' | 'ys'): number => {
      if (!o.targets) return v
      const s = nearest([v], o.targets[axis], o.tol)
      if (!s) return v
      guides[axis].push(s.line)
      return v + s.delta
    }
    if (west) L = snapEdge(clamp(x0 + dx, 0, R - o.minW), 'xs')
    if (east) R = snapEdge(clamp(x0 + w0 + dx, L + o.minW, W), 'xs')
    if (north) T = snapEdge(clamp(y0 + dy, 0, B - o.minH), 'ys')
    if (south) B = snapEdge(clamp(y0 + h0 + dy, T + o.minH, H), 'ys')
    return { box: [L, T, R - L, B - T], guides }
  }

  // A facecam keeps its shape: the edge (or the corner's stronger direction)
  // being pulled decides the size, and the opposite side stays put.
  const a = o.aspect
  const wx = west ? w0 - dx : east ? w0 + dx : w0
  const hy = north ? h0 - dy : south ? h0 + dy : h0
  const horizontal = (west || east) && (!(north || south) || Math.abs(wx / w0 - 1) >= Math.abs(hy / h0 - 1))
  let w = horizontal ? wx : hy * a
  if (o.targets) {
    if (horizontal) {
      const edge = west ? x0 + w0 - w : x0 + w
      const s = nearest([edge], o.targets.xs, o.tol)
      if (s) {
        w += west ? -s.delta : s.delta
        guides.xs.push(s.line)
      }
    } else {
      const h = w / a
      const edge = north ? y0 + h0 - h : y0 + h
      const s = nearest([edge], o.targets.ys, o.tol)
      if (s) {
        w = (h + (north ? -s.delta : s.delta)) * a
        guides.ys.push(s.line)
      }
    }
  }
  w = clamp(w, o.minW, Math.min(W, H * a))
  const h = w / a
  // Where it grows from: the opposite corner, or the opposite edge's middle.
  const x = west ? x0 + w0 - w : east ? x0 : x0 + (w0 - w) / 2
  const y = north ? y0 + h0 - h : south ? y0 : y0 + (h0 - h) / 2
  return { box: [clamp(x, 0, W - w), clamp(y, 0, H - h), w, h], guides }
}

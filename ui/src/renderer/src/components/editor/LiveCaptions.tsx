/** The captions and text drawn live over the video (step 9), exactly as the
 *  export will burn them: JASSUB (libass in WebAssembly, the library FFmpeg's
 *  own burn uses) draws the engine's ASS file (GET /projects/{id}/captions.ass,
 *  the export's own script) at the playhead's program time, with the app's
 *  bundled fonts from /fonts. So what plays is what burns, and every edit is
 *  seen as soon as the engine has it.
 *
 *  - It draws while playing and while the playhead is dragged. Paused, the
 *    engine's still lies over it ("pause shows the truth").
 *  - While a caption or a text is being dragged, the viewer hands it a
 *    script with that layer moved (lib/captionLayer.ts placeEvents), so the
 *    words follow the pointer before the engine has the change.
 *  - Fonts: the bundled ones by name; a Windows caption font a script names
 *    (Arial, Arial Black, Impact...) is added from this PC's own copy, which
 *    the engine serves (GET /fonts `system`, 9-D9), so it is drawn with the
 *    file the burn uses. Never from the internet.
 *  - A font loads the first time a script uses it, after that draw (JASSUB
 *    doesn't draw again by itself), so every new script is drawn again a
 *    moment later (9-D10).
 *  - JASSUB loads only when a project is open (its own chunk, about 2 MB of
 *    WebAssembly). If it can't start, the viewer says so and the paused
 *    still keeps working. */
import { useEffect, useRef } from 'react'
import type { PlayheadStore } from '../../lib/editorPlayhead'
import { loadBundledFonts } from '../../lib/captionLook'
import { API_BASE } from '../../lib/api'
import { editorApi } from '../../lib/editorApi'
import { assFamilies } from '../../lib/captionLayer'
import workerUrl from 'jassub/dist/worker/worker.js?worker&url'
import wasmUrl from 'jassub/dist/wasm/jassub-worker.wasm?url'
import modernWasmUrl from 'jassub/dist/wasm/jassub-worker-modern.wasm?url'

/** When a script is drawn again after it is set: its fonts load on first
 *  use, after the draw that asked for them (9-D10). */
const REDRAW_MS = [250, 1000]

/** A script with nothing in it, until the engine's arrives. */
const EMPTY = '[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'

type Jassub = import('jassub').default

export type LiveState = 'starting' | 'ready' | 'failed'

export default function LiveCaptions({
  ass,
  playhead,
  width,
  height,
  hidden,
  onState
}: {
  /** The script to draw, or null for none yet. */
  ass: string | null
  playhead: PlayheadStore
  /** The canvas (the project's frame), in its own pixels. */
  width: number
  height: number
  /** Not drawn (a media on its own, the draft). */
  hidden: boolean
  onState: (s: LiveState, error?: string) => void
}): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  const inst = useRef<Jassub | null>(null)
  const assRef = useRef<string | null>(ass)
  const size = useRef({ width, height })
  size.current = { width, height }
  const stateRef = useRef(onState)
  stateRef.current = onState
  /** This PC's Windows caption fonts (family in lower case -> addresses),
   *  and the families already handed to JASSUB. */
  const systemFonts = useRef<Promise<Map<string, string[]>> | null>(null)
  const added = useRef(new Set<string>())
  const redraws = useRef<number[]>([])

  const draw = (repaint = false): void => {
    const j = inst.current
    if (!j) return
    void j
      .manualRender(
        {
          mediaTime: playhead.get() / 1e6,
          width: size.current.width,
          height: size.current.height,
          expectedDisplayTime: performance.now()
        },
        repaint
      )
      ?.catch(() => undefined)
  }

  /** The Windows caption fonts `script` names that JASSUB doesn't have yet,
   *  added from this PC's copy (9-D9). */
  const addSystemFonts = async (j: Jassub, script: string | null): Promise<void> => {
    if (!script) return
    if (!systemFonts.current) {
      systemFonts.current = editorApi
        .systemFonts()
        .then((list) => new Map(list.map((f) => [f.family.toLowerCase(), f.urls.map((u) => `${API_BASE}${u}`)])))
        .catch(() => new Map())
    }
    const have = await systemFonts.current
    const urls: string[] = []
    for (const family of assFamilies(script)) {
      if (added.current.has(family) || !have.has(family)) continue
      added.current.add(family)
      urls.push(...(have.get(family) ?? []))
    }
    if (urls.length) await j.renderer.addFonts(urls)
  }
  /** Draw now and again a moment later, once the fonts it asked for are in. */
  const drawSoon = (): void => {
    for (const t of redraws.current) window.clearTimeout(t)
    draw(true)
    redraws.current = REDRAW_MS.map((ms) => window.setTimeout(() => draw(true), ms))
  }
  useEffect(() => () => redraws.current.forEach((t) => window.clearTimeout(t)), [])

  // Start JASSUB once, on a canvas of its own (it hands the canvas to its
  // worker, which can be done only once per canvas).
  useEffect(() => {
    const host = box.current
    if (!host) return
    let alive = true
    let made: Jassub | null = null
    const canvas = document.createElement('canvas')
    canvas.className = 'absolute inset-0 w-full h-full pointer-events-none'
    canvas.setAttribute('aria-hidden', 'true')
    host.appendChild(canvas)
    stateRef.current('starting')
    void (async () => {
      try {
        const [{ default: JASSUB }, fonts] = await Promise.all([import('jassub'), loadBundledFonts()])
        if (!alive) return
        const availableFonts: Record<string, string> = {}
        for (const f of fonts) if (f.urls?.[0]) availableFonts[f.family.toLowerCase()] = `${API_BASE}${f.urls[0]}`
        const first = assRef.current
        made = new JASSUB({
          canvas,
          subContent: first ?? EMPTY,
          workerUrl,
          wasmUrl,
          modernWasmUrl,
          availableFonts,
          // This PC's own fonts when the app may read them; never the internet.
          queryFonts: 'local'
        })
        await made.ready
        if (!alive) {
          void made.destroy()
          return
        }
        // A script that came while it started.
        if (assRef.current !== first) await made.renderer.setTrack(assRef.current ?? EMPTY)
        await addSystemFonts(made, assRef.current).catch(() => undefined)
        if (!alive) return
        inst.current = made
        stateRef.current('ready')
        drawSoon()
      } catch (e) {
        if (!alive) return
        stateRef.current('failed', e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      alive = false
      inst.current = null
      if (made) void made.destroy().catch(() => undefined)
      canvas.remove()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A new script: drawn at once where the playhead is.
  useEffect(() => {
    assRef.current = ass
    const j = inst.current
    if (!j) return
    let alive = true
    void (async () => {
      try {
        await j.ready
        await j.renderer.setTrack(ass ?? EMPTY)
        await addSystemFonts(j, ass).catch(() => undefined)
        if (alive) drawSoon()
      } catch {
        /* the next script tries again */
      }
    })()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ass])

  // Follow the playhead: every frame while playing, every move while dragged.
  useEffect(() => playhead.subscribe(() => draw()), [playhead])
  // A new frame size.
  useEffect(() => draw(true), [width, height])

  return <div ref={box} className="absolute inset-0 pointer-events-none" style={{ visibility: hidden ? 'hidden' : 'visible' }} aria-hidden="true" />
}

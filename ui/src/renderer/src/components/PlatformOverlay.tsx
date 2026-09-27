/** What TikTok, Instagram Reels and YouTube Shorts draw over a Short, for the
 *  layout editor's preview: the top bar, the column of buttons down the right,
 *  the name, caption and sound at the bottom.
 *
 *  Positions are on the 1080x1920 canvas, measured (September 2026) from
 *  replicas of each app's feed on a phone taller than 9:16, where the apps fill
 *  the height (kreatli.com's safe zone checkers). The icons are open-licensed
 *  sets in each app's style (below), not the apps' own artwork. The safe zones in gaming/layouts.json are drawn from the
 *  same measurements, so the face check agrees with what you see here. */

type Icon =
  | 'tt-heart'
  | 'tt-comment'
  | 'tt-bookmark'
  | 'tt-share'
  | 'search'
  | 'note'
  | 'ig-back'
  | 'ig-camera'
  | 'ig-heart'
  | 'ig-comment'
  | 'ig-bookmark'
  | 'ig-send'
  | 'ig-more'
  | 'yt-more'
  | 'yt-like'
  | 'yt-dislike'
  | 'yt-comment'
  | 'yt-share'
  | 'yt-remix'
  | 'yt-play'

type Shape =
  | { k: 'icon'; icon: Icon; x: number; y: number; s: number; label?: string; labelY?: number }
  | { k: 'text'; x: number; y: number; size: number; text: string; bold?: boolean; dim?: boolean; anchor?: 'start' | 'middle' }
  | { k: 'circle'; x: number; y: number; r: number; ring?: boolean }
  | { k: 'square'; x: number; y: number; s: number }
  | { k: 'pill'; x: number; y: number; w: number; h: number; text: string }

/* Icons on a 24x24 grid, in each app's own style but not its artwork (which
 * isn't licensed for reuse):
 *  - Material Icons, (c) Google, Apache License 2.0: the filled glyphs TikTok's
 *    feed uses, and YouTube's own icon family for Shorts;
 *  - Lucide, (c) Lucide Contributors (portions (c) Cole Bemis, Feather), ISC
 *    licence: the thin rounded outline style of Instagram's Reels.
 * The notices are in third_party/icons/NOTICE.md and NOTICE. */
type Glyph = { d: string[]; circles?: [number, number, number][]; stroke?: boolean; flip?: boolean }
const ICONS: Record<Icon, Glyph> = {
  // Material: favorite, sms, bookmark, reply (mirrored), search, music_note
  'tt-heart': {
    d: ['M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z']
  },
  'tt-comment': {
    d: ['M20 2H4c-1.1 0-1.99.9-1.99 2L2 22l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM9 11H7V9h2v2zm4 0h-2V9h2v2zm4 0h-2V9h2v2z']
  },
  'tt-bookmark': { d: ['M17 3H7c-1.1 0-1.99.9-1.99 2L5 21l7-3 7 3V5c0-1.1-.9-2-2-2z'] },
  'tt-share': { d: ['M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z'], flip: true },
  search: {
    d: [
      'M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z'
    ]
  },
  note: { d: ['M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z'] },
  // Lucide: chevron-left, camera, heart, message-circle, bookmark, send, ellipsis
  'ig-back': { d: ['m15 18-6-6 6-6'], stroke: true },
  'ig-camera': {
    d: ['M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z'],
    circles: [[12, 13, 3]],
    stroke: true
  },
  'ig-heart': {
    d: ['M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z'],
    stroke: true
  },
  'ig-comment': { d: ['M7.9 20A9 9 0 1 0 4 16.1L2 22Z'], stroke: true },
  'ig-bookmark': { d: ['m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z'], stroke: true },
  'ig-send': {
    d: [
      'M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.497.497 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z',
      'm21.854 2.147-10.94 10.939'
    ],
    stroke: true
  },
  'ig-more': { d: [], circles: [[12, 12, 1], [19, 12, 1], [5, 12, 1]], stroke: true },
  // Material: more_vert, thumb_up / thumb_down (outlined), comment, reply
  // (mirrored), cached, play_arrow
  'yt-more': {
    d: ['M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z']
  },
  'yt-like': {
    d: [
      'M9 21h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.58 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2zM9 9l4.34-4.34L12 10h9v2l-3 7H9V9zM1 9h4v12H1z'
    ]
  },
  'yt-dislike': {
    d: [
      'M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L9.83 23l6.59-6.59c.36-.36.58-.86.58-1.41V5c0-1.1-.9-2-2-2zm0 12l-4.34 4.34L12 14H3v-2l3-7h9v10zm4-12h4v12h-4z'
    ]
  },
  'yt-comment': {
    d: [
      'M21.99 4c0-1.1-.89-2-1.99-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14l4 4-.01-18zM20 4v13.17L18.83 16H4V4h16zM6 12h12v2H6zm0-3h12v2H6zm0-3h12v2H6z'
    ]
  },
  'yt-share': { d: ['M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z'], flip: true },
  'yt-remix': {
    d: [
      'M19 8l-4 4h3c0 3.31-2.69 6-6 6-1.01 0-1.97-.25-2.8-.7l-1.46 1.46C8.97 19.54 10.43 20 12 20c4.42 0 8-3.58 8-8h3l-4-4zM6 12c0-3.31 2.69-6 6-6 1.01 0 1.97.25 2.8.7l1.46-1.46C15.03 4.46 13.57 4 12 4c-4.42 0-8 3.58-8 8H1l4 4 4-4H6z'
    ]
  },
  'yt-play': { d: ['M8 5v14l11-7z'] }
}

const TIKTOK: Shape[] = [
  { k: 'text', x: 447, y: 162, size: 38, text: 'Following', bold: true, anchor: 'middle' },
  { k: 'text', x: 653, y: 162, size: 38, text: 'For You', bold: true, dim: true, anchor: 'middle' },
  { k: 'icon', icon: 'search', x: 1008, y: 150, s: 56 },
  { k: 'circle', x: 961, y: 875, r: 56, ring: true },
  { k: 'icon', icon: 'tt-heart', x: 961, y: 1050, s: 84, label: '12.3K', labelY: 1118 },
  { k: 'icon', icon: 'tt-comment', x: 961, y: 1213, s: 74, label: '482', labelY: 1283 },
  { k: 'icon', icon: 'tt-bookmark', x: 961, y: 1378, s: 70, label: '1,204', labelY: 1447 },
  { k: 'icon', icon: 'tt-share', x: 961, y: 1537, s: 78, label: 'Share', labelY: 1610 },
  { k: 'circle', x: 954, y: 1744, r: 56 },
  { k: 'text', x: 70, y: 1607, size: 36, text: 'username · 1-28', bold: true },
  { k: 'text', x: 70, y: 1683, size: 32, text: 'Your caption and #hashtags go here, and' },
  { k: 'text', x: 70, y: 1727, size: 32, text: 'run over two lines like this ... more' },
  { k: 'icon', icon: 'note', x: 93, y: 1781, s: 40 },
  { k: 'text', x: 131, y: 1795, size: 32, text: 'original sound - username' }
]

const REELS: Shape[] = [
  { k: 'icon', icon: 'ig-back', x: 91, y: 202, s: 56 },
  { k: 'text', x: 516, y: 216, size: 38, text: 'Reels', bold: true, anchor: 'middle' },
  { k: 'icon', icon: 'ig-camera', x: 946, y: 202, s: 64 },
  { k: 'icon', icon: 'ig-heart', x: 935, y: 1010, s: 64, label: '12.3K', labelY: 1092 },
  { k: 'icon', icon: 'ig-comment', x: 935, y: 1188, s: 64, label: '482', labelY: 1270 },
  { k: 'icon', icon: 'ig-bookmark', x: 935, y: 1369, s: 60, label: '1,204', labelY: 1456 },
  { k: 'icon', icon: 'ig-send', x: 935, y: 1547, s: 60, label: '96', labelY: 1628 },
  { k: 'icon', icon: 'ig-more', x: 935, y: 1715, s: 56 },
  { k: 'square', x: 930, y: 1828, s: 80 },
  { k: 'circle', x: 110, y: 1751, r: 34 },
  { k: 'text', x: 168, y: 1762, size: 32, text: 'username', bold: true },
  { k: 'text', x: 56, y: 1853, size: 32, text: 'Your caption and #hashtags go here ... more' }
]

const SHORTS: Shape[] = [
  { k: 'text', x: 79, y: 228, size: 44, text: 'Shorts', bold: true },
  { k: 'icon', icon: 'search', x: 842, y: 211, s: 62 },
  { k: 'icon', icon: 'yt-more', x: 960, y: 211, s: 56 },
  { k: 'icon', icon: 'yt-like', x: 951, y: 1075, s: 62, label: '12K', labelY: 1142 },
  { k: 'icon', icon: 'yt-dislike', x: 951, y: 1227, s: 62, label: 'Dislike', labelY: 1292 },
  { k: 'icon', icon: 'yt-comment', x: 952, y: 1377, s: 62, label: '482', labelY: 1442 },
  { k: 'icon', icon: 'yt-share', x: 950, y: 1525, s: 64, label: 'Share', labelY: 1591 },
  { k: 'icon', icon: 'yt-remix', x: 950, y: 1674, s: 62, label: 'Remix', labelY: 1741 },
  { k: 'square', x: 951, y: 1837, s: 62 },
  { k: 'circle', x: 128, y: 1651, r: 32 },
  { k: 'text', x: 177, y: 1665, size: 34, text: '@channel', bold: true },
  { k: 'pill', x: 343, y: 1612, w: 233, h: 76, text: 'Subscribe' },
  { k: 'icon', icon: 'yt-play', x: 106, y: 1733, s: 40 },
  { k: 'text', x: 138, y: 1746, size: 34, text: 'Your Short’s title', bold: true },
  { k: 'text', x: 79, y: 1810, size: 32, text: 'A line of the description goes here' },
  { k: 'icon', icon: 'note', x: 111, y: 1860, s: 40 },
  { k: 'text', x: 147, y: 1872, size: 32, text: 'Sound name' }
]

export const PLATFORM_UI: Record<string, Shape[]> = { tiktok: TIKTOK, reels: REELS, shorts: SHORTS }

function IconShape({ s }: { s: Extract<Shape, { k: 'icon' }> }): JSX.Element {
  const g = ICONS[s.icon]
  const k = s.s / 24
  const paint = g.stroke
    ? { fill: 'none', stroke: 'white', strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
    : { fill: 'white' }
  const flip = g.flip ? ' translate(24 0) scale(-1 1)' : ''
  return (
    <g>
      <g transform={`translate(${s.x - s.s / 2} ${s.y - s.s / 2}) scale(${k})${flip}`}>
        {g.d.map((d, i) => (
          <path key={i} d={d} {...paint} />
        ))}
        {(g.circles ?? []).map(([cx, cy, r], i) => (
          <circle key={`c${i}`} cx={cx} cy={cy} r={r} {...paint} />
        ))}
      </g>
      {s.label && (
        <text x={s.x} y={s.labelY} fontSize={26} fontWeight={600} fill="white" textAnchor="middle">
          {s.label}
        </text>
      )}
    </g>
  )
}

/** The app's UI over a Short, sized to the preview (pointer events pass through). */
export default function PlatformOverlay({ platform }: { platform: string }): JSX.Element | null {
  const shapes = PLATFORM_UI[platform]
  if (!shapes) return null
  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox="0 0 1080 1920"
      preserveAspectRatio="none"
      aria-hidden="true"
      style={{ fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif' }}
    >
      <defs>
        <linearGradient id="ui-scrim" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="black" stopOpacity="0" />
          <stop offset="1" stopColor="black" stopOpacity="0.45" />
        </linearGradient>
        <linearGradient id="ui-scrim-top" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="black" stopOpacity="0.35" />
          <stop offset="1" stopColor="black" stopOpacity="0" />
        </linearGradient>
        <filter id="ui-shadow" x="-10%" y="-10%" width="120%" height="120%">
          <feDropShadow dx="0" dy="2" stdDeviation="3" floodColor="black" floodOpacity="0.55" />
        </filter>
      </defs>
      <rect x="0" y="0" width="1080" height="300" fill="url(#ui-scrim-top)" />
      <rect x="0" y="1450" width="1080" height="470" fill="url(#ui-scrim)" />
      <g filter="url(#ui-shadow)" opacity="0.92">
        {shapes.map((s, i) => {
          if (s.k === 'icon') return <IconShape key={i} s={s} />
          if (s.k === 'text')
            return (
              <text
                key={i}
                x={s.x}
                y={s.y}
                fontSize={s.size}
                fontWeight={s.bold ? 700 : 400}
                fill="white"
                fillOpacity={s.dim ? 0.6 : 1}
                textAnchor={s.anchor ?? 'start'}
              >
                {s.text}
              </text>
            )
          if (s.k === 'circle')
            return (
              <circle
                key={i}
                cx={s.x}
                cy={s.y}
                r={s.r}
                fill="#3a3a3a"
                stroke="white"
                strokeWidth={s.ring ? 4 : 0}
              />
            )
          if (s.k === 'square')
            return <rect key={i} x={s.x - s.s / 2} y={s.y - s.s / 2} width={s.s} height={s.s} rx={12} fill="#3a3a3a" stroke="white" strokeWidth={3} />
          return (
            <g key={i}>
              <rect x={s.x} y={s.y} width={s.w} height={s.h} rx={s.h / 2} fill="white" />
              <text x={s.x + s.w / 2} y={s.y + s.h / 2 + 11} fontSize={32} fontWeight={600} fill="#0f0f0f" textAnchor="middle">
                {s.text}
              </text>
            </g>
          )
        })}
      </g>
    </svg>
  )
}

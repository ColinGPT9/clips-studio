/** Tiny inline SVG icon set — one visual language across the editor instead
 *  of mixed-weight emoji. Stroke-based, inherits currentColor, no external
 *  dependency (the CSP blocks remote assets anyway). */

interface IconProps {
  size?: number
  className?: string
}

function base(
  props: IconProps,
  children: React.ReactNode,
  viewBox = '0 0 24 24'
): JSX.Element {
  const { size = 14, className } = props
  return (
    <svg
      width={size}
      height={size}
      viewBox={viewBox}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`inline-block shrink-0 ${className ?? ''}`}
      aria-hidden
    >
      {children}
    </svg>
  )
}

export const Scissors = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <circle cx="6" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M8.1 8.1 20 20M8.1 15.9 20 4M14.7 9.3l-2.6 2.6" />
    </>
  )

export const Undo = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M3 7v6h6" />
      <path d="M21 17a9 9 0 0 0-15-6.7L3 13" />
    </>
  )

export const Keyboard = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <rect x="2" y="6" width="20" height="12" rx="2" />
      <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8" />
    </>
  )

export const TrimStart = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M4 4v16" />
      <path d="M20 12H9" />
      <path d="m13 8-4 4 4 4" />
    </>
  )

export const TrimEnd = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M20 4v16" />
      <path d="M4 12h11" />
      <path d="m11 8 4 4-4 4" />
    </>
  )

export const Trash = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M3 6h18" />
      <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M10 11v6M14 11v6" />
    </>
  )

export const Zap = (p: IconProps = {}): JSX.Element =>
  base(p, <path d="M13 2 3 14h7l-1 8 12-13h-8l1-7z" />)

export const Ban = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m5.6 5.6 12.8 12.8" />
    </>
  )

export const Pencil = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
      <path d="m15 5 4 4" />
    </>
  )

export const Badge = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <circle cx="9" cy="9" r="2" />
      <path d="m21 15-4-4-8 8" />
    </>
  )

export const Rotate = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M21 12a9 9 0 1 1-2.6-6.4" />
      <path d="M21 3v5h-5" />
    </>
  )

export const Folder = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.2 3.9A2 2 0 0 0 7.5 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
  )

export const Refresh = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
      <path d="M3 21v-5h5" />
    </>
  )

export const Palette = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M12 22a10 10 0 1 1 10-10c0 2.2-1.8 3-3 3h-2.4a2.6 2.6 0 0 0-1.9 4.4c.4.4.6.9.6 1.4a1.9 1.9 0 0 1-2 1.9z" />
      <circle cx="7.5" cy="11.5" r=".5" />
      <circle cx="10.5" cy="7.5" r=".5" />
      <circle cx="15.5" cy="7.5" r=".5" />
    </>
  )

export const Sparkle = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M12 3v0l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9Z" />
      <path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9Z" />
    </>
  )

export const Note = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </>
  )

export const Film = (p: IconProps = {}): JSX.Element =>
  base(
    p,
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4" />
    </>
  )

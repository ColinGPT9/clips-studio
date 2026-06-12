/** Score badge tinting from sky (high) through blue-grey (low). */
export default function ScoreBadge({ score }: { score: number }): JSX.Element {
  const cls =
    score >= 80
      ? 'bg-accent/20 text-accent'
      : score >= 65
        ? 'bg-accent/10 text-accent/80'
        : 'bg-raised text-muted'
  return (
    <span className={`px-2 py-0.5 rounded-md text-sm font-bold tabular-nums ${cls}`}>{score}</span>
  )
}

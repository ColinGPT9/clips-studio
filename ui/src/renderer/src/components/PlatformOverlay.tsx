/** Platform UI mockups drawn over the editor preview — see exactly what
 *  TikTok / YouTube Shorts / Instagram Reels will cover before posting. */

export type Platform = 'none' | 'tiktok' | 'youtube' | 'instagram'

export const PLATFORMS: { id: Platform; label: string }[] = [
  { id: 'none', label: 'No overlay' },
  { id: 'tiktok', label: 'TikTok' },
  { id: 'youtube', label: 'YT Shorts' },
  { id: 'instagram', label: 'Reels' }
]

function Rail({ items, bottom }: { items: [string, string][]; bottom: string }): JSX.Element {
  return (
    <div
      className="absolute right-2 flex flex-col items-center gap-3 text-white drop-shadow"
      style={{ bottom }}
    >
      {items.map(([icon, label], i) => (
        <div key={i} className="flex flex-col items-center leading-none">
          <span className="text-2xl">{icon}</span>
          {label && <span className="text-[10px] mt-0.5 font-semibold">{label}</span>}
        </div>
      ))}
    </div>
  )
}

export default function PlatformOverlay({ platform }: { platform: Platform }): JSX.Element | null {
  if (platform === 'none') return null
  return (
    <div className="absolute inset-0 pointer-events-none select-none z-10 text-white">
      {/* soft top/bottom gradients like the real apps */}
      <div className="absolute inset-x-0 top-0 h-[14%] bg-gradient-to-b from-black/50 to-transparent" />
      <div className="absolute inset-x-0 bottom-0 h-[22%] bg-gradient-to-t from-black/60 to-transparent" />

      {platform === 'tiktok' && (
        <>
          <div className="absolute top-[3%] inset-x-0 flex justify-center gap-4 text-sm drop-shadow">
            <span className="opacity-70">Following</span>
            <span className="font-bold border-b-2 border-white pb-0.5">For You</span>
          </div>
          <Rail
            bottom="14%"
            items={[
              ['🤍', '234K'],
              ['💬', '1.2K'],
              ['🔖', '18K'],
              ['↪', 'Share']
            ]}
          />
          <div className="absolute left-3 bottom-[6%] max-w-[70%] space-y-1 drop-shadow">
            <p className="font-semibold text-sm">@creator</p>
            <p className="text-xs opacity-90">the caption of your post goes here… #fyp</p>
            <p className="text-xs opacity-80">♫ original sound — creator</p>
          </div>
        </>
      )}

      {platform === 'youtube' && (
        <>
          <div className="absolute top-[2.5%] right-3 flex gap-4 text-xl drop-shadow">
            <span>🔍</span>
            <span>⋮</span>
          </div>
          <Rail
            bottom="12%"
            items={[
              ['👍', '12K'],
              ['👎', 'Dislike'],
              ['💬', '302'],
              ['↪', 'Share']
            ]}
          />
          <div className="absolute left-3 bottom-[4.5%] max-w-[70%] space-y-1.5 drop-shadow">
            <p className="text-sm">Your video title shows here</p>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold">@yourchannel</span>
              <span className="bg-white text-black text-[11px] font-semibold px-2.5 py-1 rounded-full">
                Subscribe
              </span>
            </div>
          </div>
        </>
      )}

      {platform === 'instagram' && (
        <>
          <div className="absolute top-[3%] left-3 font-bold text-lg drop-shadow">Reels</div>
          <div className="absolute top-[3%] right-3 text-xl drop-shadow">📷</div>
          <Rail
            bottom="13%"
            items={[
              ['🤍', '45.2K'],
              ['💬', '891'],
              ['✈', ''],
              ['⋯', '']
            ]}
          />
          <div className="absolute left-3 bottom-[5%] max-w-[70%] space-y-1 drop-shadow">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">@creator</span>
              <span className="border border-white/80 text-[11px] px-2 py-0.5 rounded-md">Follow</span>
            </div>
            <p className="text-xs opacity-90">your caption here… </p>
          </div>
        </>
      )}
    </div>
  )
}

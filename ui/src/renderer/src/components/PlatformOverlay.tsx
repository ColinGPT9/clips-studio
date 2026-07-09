/** Platform UI mockups drawn over the editor preview — see exactly what
 *  TikTok / YouTube Shorts / Instagram Reels will cover before posting.
 *  Layouts and icons mirror each app's real Shorts/Reels chrome. */

export type Platform = 'none' | 'tiktok' | 'youtube' | 'instagram'

export const PLATFORMS: { id: Platform; label: string }[] = [
  { id: 'none', label: 'No overlay' },
  { id: 'tiktok', label: 'TikTok' },
  { id: 'youtube', label: 'YT Shorts' },
  { id: 'instagram', label: 'Reels' }
]

function Avatar({ plus }: { plus?: boolean }): JSX.Element {
  return (
    <div className="relative w-10 h-10">
      <div className="w-10 h-10 rounded-full bg-white/25 border-2 border-white" />
      {plus && (
        <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-4 h-4 rounded-full bg-[#FE2C55] text-white text-[11px] font-bold flex items-center justify-center leading-none">
          +
        </div>
      )}
    </div>
  )
}

function Disc(): JSX.Element {
  return (
    <div className="w-9 h-9 rounded-full bg-black/80 border-[3px] border-white/20 flex items-center justify-center text-xs">
      ♫
    </div>
  )
}

function RailItem({ icon, label }: { icon: JSX.Element | string; label?: string }): JSX.Element {
  return (
    <div className="flex flex-col items-center leading-none gap-1">
      {typeof icon === 'string' ? <span className="text-[26px] drop-shadow">{icon}</span> : icon}
      {label && <span className="text-[10px] font-semibold drop-shadow">{label}</span>}
    </div>
  )
}

export default function PlatformOverlay({ platform }: { platform: Platform }): JSX.Element | null {
  if (platform === 'none') return null
  return (
    <div className="absolute inset-0 pointer-events-none select-none z-10 text-white overflow-hidden rounded-xl">
      <div className="absolute inset-x-0 top-0 h-[12%] bg-gradient-to-b from-black/45 to-transparent" />
      <div className="absolute inset-x-0 bottom-0 h-[24%] bg-gradient-to-t from-black/60 to-transparent" />

      {platform === 'tiktok' && (
        <>
          <div className="absolute top-[2.5%] inset-x-0 flex items-center px-3 drop-shadow">
            <span className="text-[10px] border border-white/80 rounded px-1 py-0.5 font-bold tracking-wide">
              LIVE
            </span>
            <div className="flex-1 flex justify-center gap-4 text-[15px]">
              <span className="opacity-75">Following</span>
              <span className="font-bold border-b-[3px] border-white pb-1">For You</span>
            </div>
            <span className="text-xl">🔍</span>
          </div>
          <div className="absolute right-2 bottom-[11%] flex flex-col items-center gap-4">
            <RailItem icon={<Avatar plus />} />
            <RailItem icon="♥" label="250.5K" />
            <RailItem icon="💬" label="100K" />
            <RailItem icon="🔖" label="88K" />
            <RailItem icon="➦" label="132.5K" />
            <RailItem icon={<Disc />} />
          </div>
          <div className="absolute left-3 bottom-[4%] max-w-[68%] space-y-1.5 drop-shadow">
            <p className="font-bold text-[15px]">@creator</p>
            <p className="text-[13px] leading-snug">Use me every day 😊 #fyp #foryou</p>
            <p className="text-[12px] opacity-90">🌐 Show translation</p>
            <p className="text-[12px] flex items-center gap-1.5">
              <span>♫</span> original sound — creator
            </p>
          </div>
        </>
      )}

      {platform === 'youtube' && (
        <>
          <div className="absolute top-[2.5%] inset-x-0 flex items-center px-3 text-xl drop-shadow">
            <span>←</span>
            <div className="flex-1" />
            <div className="flex gap-5">
              <span>🔍</span>
              <span>📷</span>
              <span>⋮</span>
            </div>
          </div>
          <div className="absolute right-2 bottom-[9%] flex flex-col items-center gap-3.5">
            <RailItem icon="👍" label="13 mln" />
            <RailItem icon="👎" label="Dislike" />
            <RailItem icon="💬" label="8,989" />
            <RailItem icon="➦" label="Share" />
            <RailItem icon="🔄" label="Remix" />
            <RailItem icon={<Disc />} />
          </div>
          <div className="absolute left-3 bottom-[3.5%] max-w-[66%] space-y-2 drop-shadow">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-full bg-white/25 border border-white/70" />
              <span className="text-[13px] font-semibold">@yourchannel</span>
              <span className="bg-white text-black text-[12px] font-semibold px-3 py-1 rounded-full">
                Subscribe
              </span>
            </div>
            <p className="text-[13px] leading-snug">You should subscribe to us #now #ok</p>
            <p className="text-[12px] flex items-center gap-1.5">
              <span>♫</span> Darude — @SandStorm
            </p>
          </div>
        </>
      )}

      {platform === 'instagram' && (
        <>
          <div className="absolute top-[2.5%] inset-x-0 flex items-center px-3 drop-shadow">
            <span className="text-xl">‹</span>
            <span className="font-bold text-lg ml-2">Reels</span>
            <div className="flex-1" />
            <span className="text-xl">📷</span>
          </div>
          <div className="absolute right-2 bottom-[10%] flex flex-col items-center gap-4">
            <RailItem icon="♡" label="420" />
            <RailItem
              icon={
                <span className="text-[24px] drop-shadow" style={{ transform: 'scaleX(-1)' }}>
                  💬
                </span>
              }
              label="4,000"
            />
            <RailItem icon="➤" label="69" />
            <RailItem icon="⋯" />
            <div className="w-7 h-7 rounded-md bg-white/25 border-2 border-white flex items-center justify-center text-[11px]">
              ♫
            </div>
          </div>
          <div className="absolute left-3 bottom-[4%] max-w-[68%] space-y-1.5 drop-shadow">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-white/25 border border-white/70" />
              <span className="text-[13px] font-semibold">creator ✓</span>
              <span className="border border-white/90 text-[12px] px-2.5 py-0.5 rounded-lg font-medium">
                Follow
              </span>
            </div>
            <p className="text-[13px] leading-snug">You are such a beautiful person</p>
            <div className="flex items-center gap-2 text-[12px]">
              <span className="bg-black/40 rounded-full px-2.5 py-1 flex items-center gap-1.5">
                <span>♫</span> Darude sandstorm
              </span>
              <span className="bg-black/40 rounded-full px-2.5 py-1">👥 55 users</span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

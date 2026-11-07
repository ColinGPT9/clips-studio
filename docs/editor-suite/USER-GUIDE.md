# Clips Kitty editor: user guide

This guide explains Clips Kitty's new editing tools, for creators. Each tool has its own section below. A section fills in once its tool is ready to use; until then, it says which step of the work brings it.

None of this changes automatic clipping: Clips Kitty keeps making your clips the way it does today, unless you force an encoder in settings.yaml (see **Good to know** under Export settings). The new tools are there when you want them.

## Export settings and smaller files

Clips Kitty makes its clips at 1080p: tall 1080x1920 videos, or wide 1920x1080 ones in Longform. Export used to save an exact copy of the clip, and it still does unless you choose otherwise. Now you can also export smaller files, other sizes and shapes, or files kept under a size limit for sites that refuse big ones.

### Choose how clips export

Open **Settings → Export**. What you choose there is what the **Export** button uses from then on. Each choice is saved as soon as you make it. Hover over any choice to see what it does.

- **Export as: Original** is the default. Your clip is saved exactly as Clips Kitty made it, straight away. Nothing changes for you unless you pick something else.
- **Export as: Custom** shows the rest of the choices:
  - **Size:** from 720p up to 2160p (4K). The number is the short side of the picture, so 720p makes a tall clip 720x1280 and a wide one 1280x720. No side goes past 4096, so a long custom shape can come out smaller than the number says. **Custom size** lets you type your own width and height, each from 720 to 4096.
  - **Shape:** the clip's own shape (**Same as clip**), **Tall (9:16)**, **Wide (16:9)**, **Square (1:1)**, **4:5**, or a custom shape. When the shape changes, **Fill** says how the extra space is filled: **Crop to fit**, **Blurred background** (a blurred copy of the clip behind it), or **Colour bars**, in a colour you pick.
  - **Quality:** Smallest file, Balanced or High. **Custom** lets you set the quality number or the bitrate yourself.
  - **Format:** H.264 plays everywhere. HEVC makes smaller files at the same quality, but some older devices and sites can't play it. AV1 is offered only if your graphics card can make it. A format you chose before that this PC can't make now is marked **(not on this PC)**. For a format both your processor and your graphics card can make, **Made on** lets you choose Automatic, Processor or Graphics card.
  - **Frame rate:** the clip's own, or 24, 25, 30, 50 or 60. It never goes above the clip's, so a 30 fps clip stays at 30.
  - **Sound:** from 64 to 320 kbps. 128 is plenty for speech.
  - **Keep files under a size limit:** off unless you tick it, and while it's off there's no limit. Ticking it lets you type a limit of 5 to 4000 MB in the box under it. The box fills in the last limit you used, or 100 MB the first time. A clip that could fit under your limit is made just as it would be without one, so the limit never makes it bigger. So is a clip whose size Clips Kitty can't tell beforehand, unless you set its bitrate yourself (Custom): one made on an NVIDIA graphics card, or on an Intel one in H.264 or HEVC; or one made on the processor that has colour bars or a blurred background or is very still, such as a slideshow. Only a clip Clips Kitty is sure won't fit is made smaller straight away, just enough to fit its length as far as the picture allows, and that size is below the smallest it expects the clip to be without a limit. Clips Kitty checks every finished file. If one came out over your limit, it makes it again to fit, as far as the picture allows, and you get the smallest file it made. An export made again takes longer, often twice as long or more: that's what the **⚠ Slower** warning beside the ticked box means. A smaller file can also look less sharp.

The bottom of the card shows about how big a minute of a 60 fps clip comes out with your choices. Where the size depends on the footage, it's a range, because busy footage (gaming, fast motion) needs more space than someone talking. On an AMD graphics card, or for AV1 on an Intel one, it's one figure instead, for a typical clip. If your choices enlarge the picture of a tall 1080x1920 clip, as 1440p and 4K usually do, an **⚠ Enlarged** warning appears beside the figure: an enlarged picture looks no sharper. If your size limit is too small for a clip of up to 3 minutes, at up to 60 fps, to look good, the card shows **⚠ Limit too small for long clips**; hover over it to see how long a 60 fps clip can be under that limit. A smaller Size or frame rate fits longer clips.

Going back to **Original** keeps your Custom choices, so they're still there the next time you pick Custom.

### Exporting

Click **Export** as usual.

- With **Original**, the copy is saved straight away, as before.
- With **Custom**, the clip is made in the background. A progress bar under the buttons shows how far along it is (**Waiting to export…** while another export finishes first, and **Making it smaller…** if the file came out over your size limit and is being made again), and you can keep working or press **Cancel**. When it's done, you see the file's name, its size and its width and height, for example "Exported my-clip.mp4 · 23 MB · 720×1280". A cancelled export leaves no file behind.
- **Export all** works the same way: one bar for all of them, and **Cancel** stops the ones that aren't finished. When they're all done, it says how many were exported and how big they are together. Each clip gets its star as its file is made.

Exports have a lane of their own. They don't wait for videos being processed, and processing doesn't wait for them. Exports also run at a lower priority, so your PC stays usable. While the Dashboard is open, its **Activity** list notes when each export starts and how it ended.

If your graphics card fails while making an H.264 or HEVC export, Clips Kitty makes it on the processor instead. That takes longer, but you still get your file. AV1 can only be made on a graphics card, so if yours can't make an AV1 export, the export fails. If you chose **Graphics card** under **Made on** and it isn't working, the export doesn't start, and Clips Kitty says why.

### Clips over 100 MB on WoopSocial

WoopSocial accepts clips up to 100 MB. A 60 fps clip longer than about 2 minutes usually goes over that. In the WoopSocial card in Settings (switched on), tick **Make a smaller copy when a clip is over 100 MB**. Clips Kitty then sends a copy under about 95 MB: the same size picture if it still looks good, or 720p if not. The copy is deleted once it's sent, and your clip stays as it was. It's off until you tick it.

### Good to know

- Clips Kitty makes your clips exactly as before, unless you force an encoder in settings.yaml (the last point below).
- Some limits are too small for a long clip to look good. Clips Kitty won't squeeze a clip until the picture falls apart: it squeezes it no further than still looks all right, rough but watchable, so the file can come out bigger than your limit. The finished export then says **over the size limit**; hover over it to see why. Settings → Export warns you beforehand when your limit is too small for a clip of up to 3 minutes at up to 60 fps. For a longer clip, or one above 60 fps, you may only find out when its export is done.
- If you force a particular graphics card or the processor for rendering in settings.yaml (`video.encoder`), that choice now applies to everything Clips Kitty renders, not just some of it.

## Make clips again from a video you already added

Coming in step 2

## Caption fonts and animations

Coming in step 3

## Creator notes for the AI, and free music and sound effects

Coming in step 4

## AI voices

Coming in step V1

## The new editor (beta)

Coming in step 8

## Captions and text in the editor

Coming in step 9

## Music, sound effects and voice in the editor

Coming in step 10

## Voice-over

Coming in step V2

## AI clips in other shapes: square, 4:5 and more

Coming in step 11

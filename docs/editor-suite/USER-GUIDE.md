# Clips Kitty editor: user guide

This guide explains Clips Kitty's new editing tools, for creators. Each tool has its own section below. A section fills in once its tool is ready to use; until then, it says which step of the work brings it.

None of this changes automatic clipping. Clips Kitty keeps making your clips the way it does today, and the new tools are there when you want them.

## Export settings and smaller files

Clips Kitty makes every clip as a tall 1080x1920 video. Export used to save an exact copy of it, and it still does unless you choose otherwise. Now you can also export smaller files, other sizes and shapes, or files kept under a size limit for sites that refuse big ones.

### Choose how clips export

Open **Settings → Export**. What you choose there is what the **Export** button uses from then on. Each choice is saved as soon as you make it. Hover over any choice to see what it does.

- **Export as: Original** is the default. Your clip is saved exactly as Clips Kitty made it, straight away. Nothing changes for you unless you pick something else.
- **Export as: Custom** shows the rest of the choices:
  - **Size:** from 240p up to 2160p (4K). The number is the short side of the picture, so 720p makes a tall clip 720x1280 and a wide one 1280x720. **Custom size** lets you type your own width and height.
  - **Shape:** the clip's own shape (**Same as clip**), **Tall (9:16)**, **Wide (16:9)**, **Square (1:1)**, **4:5**, or a custom shape. When the shape changes, **Fill** says how the extra space is filled: **Crop to fit**, **Blurred background** (a blurred copy of the clip behind it), or **Colour bars**, in a colour you pick.
  - **Quality:** Smallest file, Balanced or High. **Custom** lets you set the quality number or the bitrate yourself.
  - **Format:** H.264 plays everywhere. HEVC makes smaller files at the same quality, but some older devices and sites can't play it. AV1 shows up only if your graphics card can make it. Clips Kitty only offers the formats your PC can actually make. If your graphics card can make the format, **Made on** lets you choose Automatic, Processor or Graphics card.
  - **Frame rate:** the clip's own, or 24, 25, 30, 50 or 60. It never goes above the clip's, so a 30 fps clip stays at 30.
  - **Sound:** from 64 to 320 kbps. 128 is plenty for speech.
  - **Keep under ___ MB:** leave it empty for no limit, or type a limit (5 to 4000 MB), and Clips Kitty picks the quality that fits the clip's length. It checks the finished file, and if it came out over, it makes it once more a little smaller. Short clips aren't made bigger to fill the limit.

The bottom of the card shows about how big a minute of video comes out with your choices. For quality settings it's a range, because busy footage (gaming, fast motion) needs more space than someone talking. A size bigger than your clip gets an **⚠ Enlarged** warning beside it: Clips Kitty makes clips at 1080p, so 1440p and 4K are enlarged from that and look no sharper.

Going back to **Original** keeps your Custom choices, so they're still there the next time you pick Custom.

### Exporting

Click **Export** as usual.

- With **Original**, the copy is saved straight away, as before.
- With **Custom**, the clip is made in the background. A progress bar under the buttons shows how far along it is (**Waiting to export…** while another export finishes first), and you can keep working or press **Cancel**. When it's done, you see the file's name, its size and its width and height, for example "Exported my-clip.mp4 · 23 MB · 720×1280". A cancelled export leaves no file behind.
- **Export all** works the same way: one bar for all of them, and **Cancel** stops the ones that aren't finished. When they're all done, it says how many were exported and how big they are together. Each clip gets its star as its file is made.

Exports have a lane of their own. They don't wait for videos being processed, and processing doesn't wait for them. Exports also run at a lower priority, so your PC stays usable. The **Activity** list on the Dashboard notes when each export starts and how it ended.

If your graphics card can't make an export, Clips Kitty makes it on the processor instead. That takes longer, but you still get your file.

### Clips over 100 MB on WoopSocial

WoopSocial accepts clips up to 100 MB. A 60 fps clip longer than about 2 minutes usually goes over that. In the WoopSocial card in Settings (switched on), tick **Make a smaller copy when a clip is over 100 MB**. Clips Kitty then sends a copy under about 95 MB: the same size picture if it still looks good, or 720p if not. The copy is deleted once it's sent, and your clip stays as it was. It's off until you tick it.

### Good to know

- Only exports change. Clips Kitty makes your clips exactly as before.
- A size limit that is too small for a long clip can't always be met. The clip is then made as small as it can be, and the finished export says **over the size limit**.
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

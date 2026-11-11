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

Made a video's clips with a setting you didn't want, like 60+ second clips? You can make them again with other settings, without adding the video again.

### Making clips again

In the **Clip Editor**, pick the video and click **Make clips again…**. The window opens with the settings the video's clips were last made with. Change what you want, then click **Make clips again**. The video joins the queue like any other, the queue starts if it was stopped, and you see its progress as usual. When it's done, its clip list shows the new clips, and a line under the buttons says what changed, for example "15 new · 7 removed · 2 kept as they were". If the new settings found no new clips, it says so, and your clips stay as they were. The Queue page shows the same line under the finished video.

If those settings aren't known any more (clearing the Queue page's finished list forgets them), the window starts from your usual settings instead.

If the whole video was made into one clip last time (a watched channel's Twitch clips are), the window shows **Whole video (one clip)**, ticked. Leave it ticked to make that clip again with your new settings, or untick it to let the AI pick the best moments instead. 60s+, Sports and Marketplace plugins can't be used with it, so ticking one of them unticks Whole video.

**Make clips again** is off, and hovering over it says why, while the video is waiting or being made in the queue, or when Clips Kitty no longer has the video:
- A video you added by link is downloaded again if its download was deleted.
- A file from your PC has to still be in Clips Kitty. If it's gone, add the file again.

It's also off while the queue is full: let a video finish first. And it's off while one of the video's clips is being published, exported or edited: try again once that's done.

If you start publishing or exporting one of its clips while the video waits in the queue, Clips Kitty waits up to two minutes for it when the video's turn comes. If it still isn't done by then, nothing is changed and the queue shows why. Press **Retry** on the Queue page once it's done.

### What's kept and what's replaced

The window tells you how many clips will be replaced and how many kept. Hover over that line to see why each kept clip is kept.

These are **kept**:
- clips you edited: cut, muted, recoloured, captions changed, a new start or end, a new title, description or hashtags, or a thumbnail you chose. An edit counts as soon as you apply it, even if making it fails, so **Retry** on the Queue page still has its clip;
- clips you published or sent to publish, scheduled, exported (even if you took the star off since) or starred, translated or exported in other languages, or sent to a clipping campaign;
- clips a watched channel chose to post;
- clips with an edit, a translation or an export still being made, or a post still being sent, even one you started while the new clips were being made;
- clips made before this update, or in another version of Clips Kitty;
- 16:9 Longform clips and match reels.

A new title, description or hashtags you gave a clip in another version of Clips Kitty counts too.

The **other clips are replaced**, but only once the new clips are ready. A moment the new settings find again is made again with them, and keeps its title; its new video shows straight away, in the Clip Editor and in an open editor. The rest are deleted, with their files.

Nothing is replaced, and the clips from before stay exactly as they were, if making the new clips fails, finds no clips, or can't make one of the clips it found. In that last case, the new clips it did make are added beside the old ones.

**Cancel** works right up to the moment Clips Kitty starts replacing the clips from before, a moment at the very end: before then, pressing it stops everything and nothing is replaced. Once it has started replacing them, it finishes.

A kept clip stays exactly as it was, its video too, even when the new settings find the same moment again. A scheduled one goes out as the video you scheduled.

Make clips again makes the tall 9:16 clips. For 16:9 Longform clips, add the video again.

### Your cuts stay where they were

When you change a clip's start or end in its details, or ask the AI chat to, your cuts and muted words stay on the same moments of the video:
- Made longer, the part you added plays.
- Made shorter, cuts and mutes outside the clip go.
- If none of the parts you kept are inside the new start and end, the whole clip plays.

Before, a new start moved every cut and mute to a different moment.

### Good to know

- Clips made before this update, or in another version of Clips Kitty (an older one you went back to, say), are kept, because Clips Kitty can't tell whether you changed their titles there. If the new settings find one of them again at exactly the same start and end, it's made again with them and keeps its title. Delete the old ones you don't want yourself.
- Closing Clips Kitty while it's making clips again is safe, and so is a power cut. The next time it starts, before the queue makes anything, every clip from before plays its own video again, and the video goes back in the queue as usual. If it was closed in the last moments, while it was replacing the clips from before, the ones it had already replaced stay replaced.
- Wiping a creator's learned memory or deleting the creator (on the Creators page), taking a clip's star off, or clearing the Queue page doesn't make Clips Kitty forget a clip you edited, exported or published.
- Edits you haven't applied don't keep a clip. If a clip you have open in the editor is deleted when the new clips are ready, the editor closes; one that's made again stays open and shows its new video. A preview you're making keeps its clip.
- Process again, offered when you add a video that was already done, still keeps every clip and adds the new ones beside them.
- A match reel from before stays as it is, so no new reel of the same kind is made. For a new one, delete the old reel first, then make clips again.
- While the new clips are being made, the video files of the clips from before get a new name in their folder (for example `clip_00010-00020.clip-17.mp4`), so nothing can write over them. When it's done they get their names back, unless a new clip now has that name.
- **Settings → Storage → Free up** is safe while clips are being made again, and after Clips Kitty was closed in the middle: it leaves every file Clips Kitty still needs for them. So is deleting one of the new clips.
- Making clips again from a file you added from your PC keeps that file's copy in Clips Kitty safe. On some USB drives Clips Kitty can only check the copy before it starts, so if the copy can't be read later in the run, add the file again.

## Caption fonts and animations

Your captions can now use 12 bold caption fonts that come with Clips Kitty, move when they appear, and keep out of the way of TikTok's, Reels' and Shorts' own buttons. Nothing changes until you pick something: Classic, the look your captions have always had, stays the default.

### Pick a look

The caption settings (when you add a video, in a queued video's settings, and in the Clip Editor) start with **Presets**, a row of ready-made looks, each name written in its own font. Click one and every caption setting is filled in for you:

- **Classic:** white Arial, three words at a time, at the bottom. Your captions as they've always been.
- **Bold pop:** a heavy, round-lettered font, two words at a time, each word lighting up yellow as it's said, and every caption popping in.
- **Clean fade:** a clean font in ordinary capitals and small letters, four words at a time, fading in and out.
- **Karaoke:** each word fills with yellow as it's said, like a sing-along.
- **Bounce:** a fun rounded font in the middle of the screen, bouncing in, each word lighting up green.
- **Slide up:** a tall, narrow font that slides up into place.

Every look except Classic also turns on a **Safe zone** (TikTok for Bold pop, Karaoke and Bounce, Instagram Reels for Clean fade, YouTube Shorts for Slide up), which keeps the captions narrower so they stay clear of the app's buttons. Set **Safe zone** to Off if you want them wider.

After picking a look, change anything you like: the font, the size, the colours. Your change just edits the look, and the preset no longer shows as picked. Picking a look doesn't change **Second speaker in another colour**.

### Fonts

The font list has two groups: **Windows fonts**, the ones on every Windows PC (Arial, Impact and the rest), and **Fonts that come with Clips Kitty**, 12 of them: Anton, Archivo Black, Bangers, Barlow ExtraBold, Bebas Neue, Bowlby One, Kanit ExtraBold, Lilita One, Passion One Black, Poppins ExtraBold, Russo One and Titan One. Each name is shown in its own font, so you can see it before you pick it.

They're built into the app, so your clips look the same on any PC, even one that doesn't have them installed.

### Animations

**Animation** sets how each caption comes on screen. Hover over it for a short description.

- **None:** captions just appear, as before.
- **Pop:** each caption grows quickly into place.
- **Fade:** each caption fades in, and fades out just before the next one.
- **Bounce:** each caption grows a little past full size and settles back.
- **Slide up:** each caption rises a little into place.
- **Karaoke:** each word fills with your highlight colour while it's said. It does the same job as **Highlight each word as it's said**, so that switch shows as included while Karaoke is on. On a clip you've cut or sped up in the editor, the fill moves through each caption evenly by word length instead of following each word exactly. In Arabic, Hebrew and other right-to-left languages Karaoke isn't used, and the captions are shown without it.

The animations are quick (a fifth of a second or less), so captions still appear exactly when the words are said, and they never change the words. They play in full even when the first word is very short, and a caption that pops or bounces keeps the same lines from start to finish, also when it has a hyphen, a dollar sign or a percent sign in it (TWENTY-FIVE, $500, 50%). A very short caption (one quick word) fades in and out faster, so it still shows at full colour. A caption with an emoji, or in a language such as Thai, Hindi or Arabic, fades in at full size when you pick Pop or Bounce, because growing it could move its line breaks. If you edit caption times so two captions are on screen together, those two just appear (or fade), so they stay stacked and readable. The example below the settings plays the animation roughly; your finished clip shows exactly how it looks.

### Keep clear of the app's buttons

TikTok, Reels and Shorts put their own buttons down the right side of your video and your name and description at the bottom. **Safe zone** keeps your captions out from under them. Choose the app you post to most:

- **Off:** captions where they've always been.
- **TikTok**, **Instagram Reels** or **YouTube Shorts:** captions stay narrower so they keep away from the buttons on the right (and stay centred), and captions at the top move down below the app's top bar. Captions at the bottom already sit above all three apps' bottom text, so they stay at the same height. With **Slide up**, bottom captions sit a little higher on TikTok and Reels, so they don't slide in from under the app's text.

A word can't be split across two lines, so a caption with one very long word (like CONGRATULATIONS, or a long word at a big size) is made a little smaller, just that caption, so the whole word stays clear of the buttons. Chinese, Japanese and Thai are written without spaces, so a whole caption counts as one word there and is made smaller the same way; fewer **words per caption** keeps those captions big. Hindi and Arabic captions can't be measured this way yet: a single very long word in those languages can still reach under the buttons.

The example below the settings shades the area that app covers, and shows your caption where it will sit. The apps move their buttons a little from phone to phone, so treat it as a guide. Safe zones are for tall clips only; wide clips aren't changed.

### Other languages

The 12 new fonts are made for languages written in the English alphabet. Some cover more: Russo One has Russian, Ukrainian and other Cyrillic letters, Kanit ExtraBold has Thai and Vietnamese, and several have the accented letters of Polish, Czech, Romanian, Turkish and Vietnamese. Clips Kitty checks every letter your video's language uses. If your chosen font is missing even one (for example Romanian's Ț, or the extra letters of Kazakh), it uses a Windows font that has them all, the same way it always has for languages like Hindi, Japanese and Arabic: your captions never show empty boxes, or a letter in a different font in the middle of a word. Translated captions work the same way, language by language.

### Good to know

- If one of the new fonts is ever missing from your copy of Clips Kitty, that clip's captions are made in Arial instead, and the clip is still made. The video's progress notes say which font was missing, and say so too if all the new fonts are missing.
- If you render on a second PC (Settings → Advanced settings → Remote rendering), update Clips Kitty on that PC too. Until you do, that PC says it needs an update and takes no clips.
- Exports (Settings → Export) keep the captions exactly as they are in your clip.
- Captions clear in the quiet parts of a clip: a caption goes when its last word has been said, and after a pause of more than 0.8 seconds the next word starts a new caption, even if the one before had fewer words than you set. Captions you edited yourself keep their times.

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

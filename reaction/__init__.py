"""Reaction-video pipeline — fully separate from the standard clip path.

The standard pipeline (video/tracker.py + video/cropper.py) is built for
talking-head and IRL content: it decides ONE crop that follows the person.
Reaction videos need something it has no vocabulary for — two regions that
must BOTH stay visible (the creator and what they're reacting to).

Nothing in video/ or analysis/ imports this package. The only touchpoint is
one guarded branch in core.pipeline._render_files, entered exclusively when
a clip is explicitly routed here, and every failure inside falls back to the
standard letterbox render. A bug in this package therefore cannot change,
slow down, or break clips that aren't reaction clips.
"""

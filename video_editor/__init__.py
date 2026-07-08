"""Lightweight Shorts finishing editor.

Applies a small, non-destructive edit list (stored in the clip's render_opts
under "edit") during the normal re-render: trims/cuts, section + word mutes,
volume and fades. The original source is never modified; clearing the edit
list restores the untouched clip. All heavy lifting is FFmpeg; caption
timestamps come from the existing Whisper transcript.

All edit times are seconds RELATIVE TO THE CLIP (its start_s), in the
clip's ORIGINAL timeline — mutes are applied before cuts, so removing a
section never shifts other edits' coordinates.
"""

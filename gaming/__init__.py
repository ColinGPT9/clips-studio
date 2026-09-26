"""Gaming / Split-Screen mode: an opt-in, self-contained layout for game streams.

Imported only when the toggle is on. Nothing in the standard pipeline imports
it, and every entry point fails closed to the standard renderer.
See docs/GAMING.md.
"""

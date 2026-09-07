"""Checking a thumbnail before it goes anywhere near YouTube.

Thumbnails reach the API as base64 data rather than as a path. That is a
deliberate choice and worth writing down, because the obvious design is the
other one: the desktop app's file dialog runs in Electron's main process, which
has already read the file, so sending a filename would only mean the backend
re-opening an arbitrary path handed to it by an unauthenticated local endpoint.

The side benefit is that the format can be decided from the file's own leading
bytes. An extension is a claim by whoever sent it; YouTube checks the actual
content, and rejects the upload after the render and the upload if the claim
was wrong. Checking here costs nothing and fails in the right place.

Pure and stdlib-only on purpose, like the rest of this package, so it is
testable on a CI runner with four packages installed and no web framework.
"""

from publish.errors import PublishError

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

# YouTube's limit. WebP is absent from the accepted formats on purpose: it is
# fine for the app's own watermarks and is rejected as a thumbnail.
MAX_BYTES = 2 * 1024 * 1024


def decode_thumbnail(encoded: str) -> bytes:
    """Base64 image data -> the bytes to store, or PublishError with a reason."""
    import base64
    import binascii

    # base64 of nothing IS the empty string, so this covers both "the field was
    # blank" and "it decoded to no bytes"; a separate empty-result check below
    # would be unreachable.
    if not encoded:
        raise PublishError("No image was given.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise PublishError("That image could not be read.") from e

    if len(raw) > MAX_BYTES:
        raise PublishError("YouTube's limit for a thumbnail is 2 MB.")
    if not looks_like_image(raw):
        raise PublishError("YouTube accepts JPEG and PNG thumbnails.")
    return raw


def looks_like_image(raw: bytes) -> bool:
    """True for the two formats YouTube takes, judged by content."""
    return raw.startswith(PNG_MAGIC) or raw.startswith(JPEG_MAGIC)

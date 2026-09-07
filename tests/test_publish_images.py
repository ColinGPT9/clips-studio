"""Thumbnail validation.

The point of checking the bytes rather than a filename: an extension is a
claim by whoever sent the request, and YouTube checks the actual content. A
WebP named .png used to sail through and only fail at YouTube, after the
render and the upload.

These run with no web framework and no image library installed, which is why
the validation lives in publish/ rather than in the route.
"""

import base64

import pytest

from publish.errors import PublishError
from publish.images import JPEG_MAGIC, MAX_BYTES, PNG_MAGIC, decode_thumbnail, looks_like_image


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def png(payload: bytes = b"body") -> bytes:
    return PNG_MAGIC + payload


def jpeg(payload: bytes = b"body") -> bytes:
    return JPEG_MAGIC + payload


def test_a_png_is_accepted_and_returned_unchanged():
    raw = png(b"pixels")
    assert decode_thumbnail(b64(raw)) == raw


def test_a_jpeg_is_accepted():
    raw = jpeg(b"pixels")
    assert decode_thumbnail(b64(raw)) == raw


def test_a_webp_is_refused_however_it_is_named():
    """RIFF....WEBP — the format YouTube will not take as a thumbnail."""
    webp = b"RIFF" + (0).to_bytes(4, "little") + b"WEBPVP8 "
    with pytest.raises(PublishError) as e:
        decode_thumbnail(b64(webp))
    assert "JPEG and PNG" in str(e.value)


def test_a_gif_is_refused():
    with pytest.raises(PublishError):
        decode_thumbnail(b64(b"GIF89a" + b"\x00" * 20))


def test_text_pretending_to_be_an_image_is_refused():
    with pytest.raises(PublishError):
        decode_thumbnail(b64(b"not an image at all"))


def test_an_executable_is_refused():
    """The one that matters if this ever stops being localhost-only."""
    with pytest.raises(PublishError):
        decode_thumbnail(b64(b"MZ\x90\x00" + b"\x00" * 60))


def test_empty_data_is_refused():
    """base64 of no bytes is the empty string, so this is the same case as an
    absent field — worth pinning, because it means a separate empty-result
    check inside the decoder would be dead code."""
    assert b64(b"") == ""
    with pytest.raises(PublishError) as e:
        decode_thumbnail(b64(b""))
    assert "No image" in str(e.value)


def test_no_data_at_all_is_refused():
    with pytest.raises(PublishError):
        decode_thumbnail("")


def test_something_that_is_not_base64_is_refused():
    with pytest.raises(PublishError) as e:
        decode_thumbnail("!!! not base64 !!!")
    assert "could not be read" in str(e.value)


def test_whitespace_padded_base64_is_refused_rather_than_guessed_at():
    """validate=True: quietly ignoring stray characters is how a decoder ends
    up accepting something the sender did not mean."""
    with pytest.raises(PublishError):
        decode_thumbnail(b64(png()) + "%%%")


def test_exactly_the_limit_is_allowed():
    raw = png(b"\x00" * (MAX_BYTES - len(PNG_MAGIC)))
    assert len(raw) == MAX_BYTES
    assert len(decode_thumbnail(b64(raw))) == MAX_BYTES


def test_one_byte_over_the_limit_is_refused():
    raw = png(b"\x00" * (MAX_BYTES - len(PNG_MAGIC) + 1))
    with pytest.raises(PublishError) as e:
        decode_thumbnail(b64(raw))
    assert "2 MB" in str(e.value)


def test_the_size_check_runs_before_the_format_check():
    """A huge file of the wrong type should be refused for being huge, not
    read further than it needs to be."""
    with pytest.raises(PublishError) as e:
        decode_thumbnail(b64(b"X" * (MAX_BYTES + 1)))
    assert "2 MB" in str(e.value)


def test_looks_like_image_is_content_not_extension():
    assert looks_like_image(png()) is True
    assert looks_like_image(jpeg()) is True
    assert looks_like_image(b"") is False
    assert looks_like_image(b"\x89PN") is False, "a truncated signature is not a PNG"


def test_the_signatures_are_the_real_ones():
    assert PNG_MAGIC == bytes([137, 80, 78, 71, 13, 10, 26, 10])
    assert JPEG_MAGIC == bytes([255, 216, 255])

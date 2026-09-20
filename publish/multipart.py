"""Building a multipart/form-data body with nothing but the standard library.

Shared by the publishing providers. `publish/` deliberately carries no
third-party HTTP dependency, so the tests can import it on a CI runner with
none of the pipeline's packages installed — which means there is no requests
or httpx here to do this.
"""

import mimetypes
import uuid
from pathlib import Path


def encode(
    fields: list[tuple[str, str]],
    files: list[tuple[str, Path]],
) -> tuple[bytes, str]:
    """Return the body and the Content-Type header to send with it.

    `fields` is a list of PAIRS rather than a dict, and that is load-bearing:
    some APIs take repeated names. Upload-Post's `platform[]` appears once per
    destination and that repetition is what makes one request fan out, which a
    dict would collapse to a single value.
    """
    boundary = "----ClipsKitty" + uuid.uuid4().hex
    marker = f"--{boundary}".encode()
    parts: list[bytes] = []

    for name, value in fields:
        parts.append(marker)
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))

    for name, path in files:
        guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts.append(marker)
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"'.encode()
        )
        parts.append(f"Content-Type: {guessed}".encode())
        parts.append(b"")
        parts.append(path.read_bytes())

    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    # CRLF, not a bare newline: some servers reject a body that uses \n.
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"

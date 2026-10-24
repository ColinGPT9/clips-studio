"""Where a clip went, whichever way it was sent.

One read for every provider. The publish panel used Upload-Post's own route,
which answers 404 while Upload-Post is switched off, so a creator publishing
only through WoopSocial lost "Where it went" the moment they reopened a clip.
This route sits behind no provider's switch and adds the direct YouTube
upload beside the provider posts (server/posts.py posts_for_clip).

Internal: no entry in server/api_stability.py ROUTES.
"""

from fastapi import FastAPI, HTTPException

from server import posts


def install(app: FastAPI, *, db) -> None:
    @app.get("/clips/{clip_id}/posts")
    def clip_posts(clip_id: int):
        """Every post of one clip: one row per provider and platform."""
        d = db()
        try:
            found = posts.posts_for_clip(d, clip_id)
            if not found and d.get_clip(clip_id) is None:
                raise HTTPException(404, "no such clip")
            return {"clip_id": clip_id, "posts": found}
        finally:
            d.close()

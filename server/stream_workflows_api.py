"""Desktop session protected stream nominations, mappings and digest approval."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from connected_accounts.models import AccountError
from plugins import session
from stream_workflows.models import WorkflowError
from stream_workflows.runtime import WorkflowRuntime


class MappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    broadcaster_id: str = Field(pattern=r"^[0-9]{1,30}$")
    vod_id: str = Field(pattern=r"^[0-9]{1,30}$")
    stream_id: str = Field(pattern=r"^[0-9]{1,30}$")
    stream_start: str = Field(max_length=64)
    offset_seconds: float = Field(ge=-31536000, le=31536000, allow_inf_nan=False)
    verified: bool
    source_offset_seconds: float | None = Field(default=None, ge=0, le=31536000, allow_inf_nan=False)
    source_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class DigestIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    candidates: list[dict] = Field(min_length=1, max_length=100)
    top_k: int = 5
    auto_publish: bool = False


def install(app, *, data_dir: Path, service, event_manager, session_secret: str, transport=None):
    """Return runtime; the host wires its sanitized EventSub event sink."""
    runtime = WorkflowRuntime(data_dir, service, event_manager, transport=transport)

    def require_session(given: str | None = Header(default=None, alias=session.HEADER)):
        if not session.matches(session_secret, given):
            raise HTTPException(403, f"{session.HEADER} is required to manage stream workflows.")

    dependencies = [Depends(require_session)]

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (WorkflowError, AccountError) as error:
            raise HTTPException(error.status, {"code": error.code, "message": str(error)}) from None

    @app.post("/stream-workflows/{account_id}/monitor", dependencies=dependencies)
    def start_monitor(account_id: str):
        return call(runtime.start_monitor, account_id)

    @app.delete("/stream-workflows/{account_id}/monitor", dependencies=dependencies)
    def stop_monitor(account_id: str):
        return call(runtime.stop_monitor, account_id)

    @app.get("/stream-workflows/{account_id}/streams", dependencies=dependencies)
    def streams(account_id: str):
        return call(runtime.streams, account_id)

    @app.put("/stream-workflows/{account_id}/vod-mapping", dependencies=dependencies)
    def mapping(account_id: str, body: MappingIn):
        return call(runtime.bind_vod, account_id, **body.model_dump())

    @app.post("/stream-workflows/{account_id}/digests", dependencies=dependencies)
    def prepare_digest(account_id: str, body: DigestIn):
        call(runtime._account, account_id)
        return call(lambda: runtime.digests.prepare(account_id, **body.model_dump()))

    @app.get("/stream-workflows/{account_id}/digests", dependencies=dependencies)
    def list_digests(account_id: str):
        call(runtime._account, account_id)
        return {"digests": call(lambda: runtime.digests.list(account_id))}

    @app.post("/stream-workflows/{account_id}/digests/{digest_id}/approve", dependencies=dependencies)
    def approve_digest(account_id: str, digest_id: str):
        call(runtime._account, account_id)
        return call(lambda: runtime.digests.approve(account_id, digest_id))

    @app.get("/stream-workflows/{account_id}/digests/{digest_id}/export", dependencies=dependencies)
    def export_digest(account_id: str, digest_id: str):
        call(runtime._account, account_id)
        return call(lambda: runtime.digests.export(account_id, digest_id))

    return runtime

"""G1C intake panel UI fixture: real services, real markup, isolated data.

The import routes are thin pass-throughs over the same SupplierImportService
the production server exposes; the intake confirmation router is the real
one. Unrelated console scripts are intentionally empty so browser acceptance
exercises this module against real routes without legacy side calls.
"""
import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from xianyu_agent.candidate_evidence_store import CandidateEvidenceStore
from xianyu_agent.commerce import JsonCommerceRepositories
from xianyu_agent.intake_confirmation import build_intake_confirmation_router
from xianyu_agent.supplier_import import (
    ConfirmMismatch,
    ImportPreviewError,
    SupplierImportService,
)

TOKEN = "intake-ui-fixture-token-0123456789abcdef"
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def build_fixture(root: Path):
    repos = JsonCommerceRepositories(root)
    imports = SupplierImportService(repos, root)
    evidence = CandidateEvidenceStore(root / "candidate_evidence")
    app = FastAPI()

    app.include_router(build_intake_confirmation_router(
        repos, imports, evidence, root,
        token_sha256=hashlib.sha256(TOKEN.encode("utf-8")).hexdigest(),
        operator="ui-fixture-operator",
    ))

    @app.post("/commerce/supplier-imports/preview")
    async def preview(request: Request):
        payload = await request.json()
        try:
            if payload.get("kind") == "pasted_text":
                return imports.preview_pasted_text(str(payload.get("content") or ""))
            return imports.preview_bytes(
                _fixture_bytes(payload.get("content_b64")),
                kind=str(payload.get("kind") or ""),
                filename=str(payload.get("filename") or ""),
            )
        except ImportPreviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/commerce/supplier-imports/{preview_id}/confirm")
    async def confirm(preview_id: str, request: Request):
        payload = await request.json()
        try:
            return imports.confirm(
                preview_id,
                content_sha256=str(payload.get("content_sha256") or ""),
                revision=int(payload.get("revision") or 0),
                supplier=str(payload.get("supplier") or ""),
                operator=str(payload.get("operator") or "operator"),
            )
        except ConfirmMismatch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ImportPreviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/commerce/supplier-imports")
    async def list_batches():
        if getattr(build_fixture, "fail_next_list", False):
            build_fixture.fail_next_list = False
            raise HTTPException(status_code=500, detail="fixture injected failure")
        return {"batches": imports.list_batches()}

    @app.get("/console")
    def console():
        return FileResponse(WEB_ROOT / "console.html")

    @app.get("/console/style.css")
    def styles():
        return FileResponse(WEB_ROOT / "style.css")

    @app.get("/console/intake-confirmation.js")
    def script():
        return FileResponse(WEB_ROOT / "intake-confirmation.js")

    @app.get("/console/app.js")
    @app.get("/console/pi-usage.js")
    def unrelated_scripts():
        return Response(content="", media_type="application/javascript")

    return app, imports


def _fixture_bytes(content_b64):
    import base64
    import binascii

    try:
        return base64.b64decode(str(content_b64 or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid base64 content") from exc

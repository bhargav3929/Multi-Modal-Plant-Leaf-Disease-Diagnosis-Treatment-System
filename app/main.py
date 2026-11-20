"""FastAPI application: REST API + static frontend for the plant-pathology console."""
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, graph, imaging, llm, prompts, ratelimit, samples, schemas

log = logging.getLogger("agri")
APP_TITLE = "Agentic Multi-Modal Plant Pathology & Treatment System via LangGraph"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    samples.ensure_samples()
    yield


app = FastAPI(title=APP_TITLE, version="1.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- helpers
def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_loopback(request: Request) -> bool:
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1", "localhost")


def _read_upload(upload: UploadFile) -> bytes:
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Image is larger than {config.MAX_UPLOAD_MB} MB. Please upload a smaller photo.")
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    return data


def _prepare(upload: UploadFile):
    try:
        return imaging.prepare_image(_read_upload(upload))
    except imaging.ImageError as exc:
        raise HTTPException(400, str(exc))


def _require_provider() -> None:
    if not llm.configured_providers():
        raise HTTPException(
            503, "No AI provider key is configured yet. Click 'API Keys' and paste a free Groq key.")


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- status & keys
@app.get("/api/status")
def api_status(request: Request):
    providers = {}
    for p in config.PROVIDERS:
        key, source = llm.get_key(p)
        providers[p] = {"configured": bool(key), "source": source, "masked": llm.mask(key)}
    return {
        "title": APP_TITLE,
        "providers": providers,
        "any_configured": any(v["configured"] for v in providers.values()),
        "can_edit_keys": _is_loopback(request) or config.ALLOW_REMOTE_KEY_EDIT,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "quota": ratelimit.status(client_ip(request)),
    }


@app.get("/api/quota")
def api_quota(request: Request):
    return ratelimit.status(client_ip(request))


class KeysRequest(BaseModel):
    groq_api_key: Optional[str] = Field(default=None, max_length=300)


@app.post("/api/keys")
def save_keys(body: KeysRequest, request: Request):
    if not (_is_loopback(request) or config.ALLOW_REMOTE_KEY_EDIT):
        raise HTTPException(403, "API keys can only be changed from the machine running the server.")
    saved = []
    for provider, value in (("groq", body.groq_api_key),):
        value = (value or "").strip()
        if not value:
            continue
        if len(value) < 10 or any(c.isspace() for c in value):
            raise HTTPException(400, f"That {provider.title()} key doesn't look valid.")
        db.set_setting(f"{provider}_api_key", value)
        saved.append(provider)
    if not saved:
        raise HTTPException(400, "Paste your Groq API key.")
    return {"saved": saved}


@app.delete("/api/keys/{provider}")
def delete_key(provider: str, request: Request):
    if not (_is_loopback(request) or config.ALLOW_REMOTE_KEY_EDIT):
        raise HTTPException(403, "API keys can only be changed from the machine running the server.")
    if provider not in config.PROVIDERS:
        raise HTTPException(404, "Unknown provider.")
    db.delete_setting(f"{provider}_api_key")
    return {"removed": provider}


@app.get("/api/samples")
def api_samples():
    return samples.sample_manifest()


# --------------------------------------------------------------------------- analyze (LangGraph)
@app.post("/api/analyze")
def analyze(request: Request, file: UploadFile = File(...), crop: str = Form(""), city: str = Form("")):
    """Run the 5-node pipeline and stream progress as newline-delimited JSON events."""
    ip = client_ip(request)
    crop, city = crop.strip()[:60], city.strip()[:80]
    image_b64, thumb = _prepare(file)      # validate first so bad uploads never burn quota
    _require_provider()
    quota = ratelimit.consume(ip)          # raises HTTP 429 when over the hourly limit
    t0 = time.perf_counter()

    init = {"image_b64": image_b64, "thumb": thumb, "client_ip": ip, "crop_hint": crop, "city": city, "trace": []}

    def event_stream():
        state = dict(init)
        yield _ndjson({"event": "start", "quota": quota, "payload_kb": round(len(image_b64) * 0.75 / 1024, 1)})
        try:
            for chunk in graph.PIPELINE.stream(init, stream_mode="updates"):
                for node, update in chunk.items():
                    if not update:
                        continue
                    for k, v in update.items():
                        if k == "trace":
                            state["trace"] = state.get("trace", []) + v
                        else:
                            state[k] = v
                    yield _ndjson({"event": "node", "node": node, "trace": update["trace"][0]})
            result = graph.build_result(state)
            scan_id = state.get("scan_id")
            if scan_id:  # re-save so the stored record includes node 5's own latency
                db.update_scan_record(scan_id, result)
            result.update(scan_id=scan_id, thumb=thumb, created_at=time.time(),
                          elapsed_ms=round((time.perf_counter() - t0) * 1000), payload_kb=round(len(image_b64) * 0.75 / 1024, 1))
            yield _ndjson({"event": "result", "result": result, "quota": ratelimit.status(ip)})
        except llm.LLMError as exc:
            yield _ndjson({"event": "error", "message": str(exc), "quota": ratelimit.status(ip)})
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected to the UI
            log.exception("analysis failed")
            yield _ndjson({"event": "error", "message": f"Unexpected error: {type(exc).__name__}",
                           "quota": ratelimit.status(ip)})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- compare (v1.1)
@app.post("/api/compare")
def compare(request: Request, baseline: UploadFile = File(...), followup: UploadFile = File(...),
            crop: str = Form("")):
    ip = client_ip(request)
    b64_a, thumb_a = _prepare(baseline)
    b64_b, thumb_b = _prepare(followup)
    _require_provider()
    quota = ratelimit.consume(ip)
    crop = crop.strip()[:60]
    text = "Compare IMAGE 1 (Day 1 Baseline) with IMAGE 2 (Follow-Up Scan)." + (f" Crop: {crop}." if crop else "")
    try:
        comp, provider = llm.invoke_json(prompts.COMPARE, text, schemas.Comparison, images=[b64_a, b64_b])
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc))
    data = comp.model_dump()
    if not (data["baseline"]["is_valid_plant_leaf"] and data["followup"]["is_valid_plant_leaf"]):
        data["trend"] = "Not Comparable"
        data["change_summary"] = data["change_summary"] or "At least one image does not show a plant leaf."
    return {"comparison": data, "provider": provider, "thumbs": {"baseline": thumb_a, "followup": thumb_b},
            "quota": quota}


# --------------------------------------------------------------------------- history
@app.get("/api/history")
def history(request: Request, q: str = Query("", max_length=80), pathogen_class: str = Query("", max_length=20)):
    return db.list_scans(client_ip(request), q.strip(), pathogen_class.strip())


@app.get("/api/history/{scan_id}")
def history_item(scan_id: int, request: Request):
    scan = db.get_scan(client_ip(request), scan_id)
    if not scan:
        raise HTTPException(404, "Scan not found.")
    return scan


@app.delete("/api/history/{scan_id}")
def history_delete(scan_id: int, request: Request):
    if not db.delete_scan(client_ip(request), scan_id):
        raise HTTPException(404, "Scan not found.")
    return {"deleted": scan_id}


# --------------------------------------------------------------------------- frontend
@app.middleware("http")
async def no_store_static(request: Request, call_next):
    """Always revalidate JS/CSS/HTML so a browser never runs a stale script against a newer page."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

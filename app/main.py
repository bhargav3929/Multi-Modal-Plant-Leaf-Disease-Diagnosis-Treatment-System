"""FastAPI application: REST API + static frontend for the plant-pathology console."""
import asyncio
import contextvars
import json
import logging
import queue
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, demo, errors, graph, imaging, llm, prompts, samples, schemas
from .errors import ApiError

log = logging.getLogger("agri")
APP_TITLE = config.PROJECT_NAME


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    samples.ensure_samples()
    task = None
    if config.DEMO_MODE:
        if not config.DEMO_TICKET_SECRET:
            log.warning("DEMO_MODE is on but DEMO_TICKET_SECRET is empty: every ticket will be rejected.")
        task = asyncio.create_task(_cleanup_loop())
    yield
    if task:
        task.cancel()


async def _cleanup_loop() -> None:
    """Delete demo history and run rows older than 24 h, now and then every hour."""
    while True:
        try:
            await asyncio.to_thread(demo.cleanup)
        except Exception:  # noqa: BLE001 - cleanup must never kill the server
            log.exception("demo cleanup failed")
        await asyncio.sleep(3600)


app = FastAPI(title=APP_TITLE, version=config.VERSION, lifespan=lifespan)
errors.install(app)


# --------------------------------------------------------------------------- helpers
def owner(request: Request) -> str:
    """Whose history and quota this is: the ticket id in demo mode, the client address otherwise."""
    return demo.owner_id(request)


def _keys_guard(request: Request) -> None:
    """Key routes exist only in the download version, and only for this machine."""
    if config.DEMO_MODE:
        raise ApiError("NOT_FOUND", "Not found.")
    local = bool(request.client) and request.client.host in ("127.0.0.1", "::1")
    proxied = "x-forwarded-for" in request.headers or "forwarded" in request.headers
    if not local or proxied:
        raise ApiError("KEYS_LOCAL_ONLY", "API keys can only be changed from the machine running the server.")


def _read_upload(upload: UploadFile) -> bytes:
    limit = config.UPLOAD_LIMIT_MB * 1024 * 1024
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise ApiError("TOO_LARGE", f"Image is larger than {config.UPLOAD_LIMIT_MB} MB. Please upload a smaller photo.")
    if not data:
        raise ApiError("BAD_INPUT", "The uploaded file is empty.")
    return data


def _prepare(upload: UploadFile):
    try:
        return imaging.prepare_image(_read_upload(upload))
    except imaging.ImageError as exc:
        raise ApiError("BAD_INPUT", str(exc))


def _require_provider() -> None:
    if not llm.configured_providers():
        raise ApiError("AI_NOT_CONFIGURED", llm.MESSAGES["AI_NOT_CONFIGURED"])


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def _stream_error(code: str, message: str, quota) -> bytes:
    return _ndjson({"event": "error", "message": message, "error": message, "code": code, "quota": quota})


# --------------------------------------------------------------------------- standard routes
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/info")
def api_info(request: Request):
    key, _ = llm.get_key("groq")
    return {
        "name": config.PROJECT_NAME,
        "slug": config.PROJECT_SLUG,
        "number": config.PROJECT_NUMBER,
        "version": config.VERSION,
        "mode": "demo" if config.DEMO_MODE else "local",
        "ai": {"provider": "groq", "ready": bool(key), "model": config.GROQ_VISION_MODEL},
        "limits": {"max_upload_mb": config.UPLOAD_LIMIT_MB},
        "demo": demo.info(request),
        "quota": demo.quota(request),
    }


@app.get("/api/quota")
def api_quota(request: Request):
    return demo.quota(request)


@app.get("/api/samples")
def api_samples():
    return samples.sample_manifest()


@app.get("/api/samples/{sample_id}")
def api_sample(sample_id: str):
    detail = samples.sample_detail(sample_id)
    if detail is None:
        raise ApiError("NOT_FOUND", "That sample doesn't exist.")
    return detail


# --------------------------------------------------------------------------- keys (download version only)
@app.get("/api/keys")
def get_keys():
    if config.DEMO_MODE:
        raise ApiError("NOT_FOUND", "Not found.")
    key, source = llm.get_key("groq")
    return {"groq": {"configured": bool(key), "source": source or "none", "masked": llm.mask(key)}}


class KeysRequest(BaseModel):
    groq_api_key: Optional[str] = Field(default=None, max_length=300)


@app.post("/api/keys")
def save_keys(body: KeysRequest, request: Request):
    _keys_guard(request)
    value = (body.groq_api_key or "").strip()
    if not llm.usable_key(value):
        raise ApiError("BAD_INPUT", "That doesn't look like a Groq key. It should start with gsk_ and have no spaces.")
    db.set_setting("groq_api_key", value)
    return {"saved": ["groq"]}


@app.delete("/api/keys/groq")
def delete_key(request: Request):
    _keys_guard(request)
    db.delete_setting("groq_api_key")
    return {"removed": "groq"}


# --------------------------------------------------------------------------- analyze (LangGraph)
@app.post("/api/analyze")
def analyze(request: Request, file: UploadFile = File(...), crop: str = Form(""), city: str = Form(""),
            fresh: bool = Form(False)):
    """Run the 5-node pipeline and stream progress as newline-delimited JSON events.

    Events: start, node (one per finished node), waiting (Groq's per-minute limit: seconds until we retry),
    then result or error.
    """
    who = owner(request)
    crop, city = crop.strip()[:60], city.strip()[:80]
    image_b64, thumb = _prepare(file)      # validate first so bad uploads never burn a run
    ticket = demo.require_run(request)
    _require_provider()
    t0 = time.perf_counter()

    init = {"image_b64": image_b64, "thumb": thumb, "client_ip": who, "crop_hint": crop, "city": city,
            "fresh": fresh, "trace": []}

    def run_pipeline(events: "queue.Queue") -> None:
        """Worker thread: runs the graph and pushes its updates (and wait notices) onto `events`."""
        llm.WAIT_HOOK.set(lambda secs: events.put(("waiting", secs)))
        try:
            for chunk in graph.PIPELINE.stream(init, stream_mode="updates"):
                events.put(("chunk", chunk))
            events.put(("end", None))
        except Exception as exc:  # noqa: BLE001 - handed to the streaming side below
            events.put(("fail", exc))

    def event_stream():
        state = dict(init)
        yield _ndjson({"event": "start", "quota": demo.quota(request), "payload_kb": round(len(image_b64) * 0.75 / 1024, 1)})
        events: "queue.Queue" = queue.Queue()
        threading.Thread(target=contextvars.copy_context().run, args=(run_pipeline, events), daemon=True).start()
        try:
            while True:
                kind, item = events.get()
                if kind == "waiting":
                    yield _ndjson({"event": "waiting", "seconds": round(item)})
                    continue
                if kind == "fail":
                    raise item
                if kind == "end":
                    break
                for node, update in item.items():
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
            demo.record_run(ticket)            # count a run only now that it worked
            quota = demo.quota(request)
            result.update(scan_id=scan_id, thumb=thumb, created_at=time.time(),
                          elapsed_ms=round((time.perf_counter() - t0) * 1000), payload_kb=round(len(image_b64) * 0.75 / 1024, 1))
            yield _ndjson({"event": "result", "result": result, "quota": quota})
        except llm.LLMError as exc:
            yield _stream_error(exc.code, str(exc), demo.quota(request))
        except Exception:  # noqa: BLE001 - surface anything unexpected to the UI, politely
            log.exception("analysis failed")
            yield _stream_error("AI_FAILED", llm.MESSAGES["AI_FAILED"], demo.quota(request))

    return StreamingResponse(event_stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- compare (v1.1)
@app.post("/api/compare")
def compare(request: Request, baseline: UploadFile = File(...), followup: UploadFile = File(...),
            crop: str = Form(""), fresh: bool = Form(False)):
    b64_a, thumb_a = _prepare(baseline)
    b64_b, thumb_b = _prepare(followup)
    ticket = demo.require_run(request)
    _require_provider()
    crop = crop.strip()[:60]
    text = "Compare IMAGE 1 (Day 1 Baseline) with IMAGE 2 (Follow-Up Scan)." + (f" Crop: {crop}." if crop else "")
    try:
        comp, provider, reused = llm.invoke_json_cached(prompts.COMPARE, text, schemas.Comparison,
                                                        images=[b64_a, b64_b], fresh=fresh)
    except llm.LLMError as exc:
        raise ApiError(exc.code, str(exc))
    demo.record_run(ticket)
    quota = demo.quota(request)
    data = comp.model_dump()
    if not (data["baseline"]["is_valid_plant_leaf"] and data["followup"]["is_valid_plant_leaf"]):
        data["trend"] = "Not Comparable"
        data["change_summary"] = data["change_summary"] or "At least one image does not show a plant leaf."
    return {"comparison": data, "provider": provider, "reused": reused,
            "thumbs": {"baseline": thumb_a, "followup": thumb_b}, "quota": quota}


# --------------------------------------------------------------------------- history
@app.get("/api/history")
def history(request: Request, q: str = Query("", max_length=80), pathogen_class: str = Query("", max_length=20)):
    if demo.no_ticket(request):
        return []
    return db.list_scans(owner(request), q.strip(), pathogen_class.strip())


@app.get("/api/history/{scan_id}")
def history_item(scan_id: int, request: Request):
    scan = None if demo.no_ticket(request) else db.get_scan(owner(request), scan_id)
    if not scan:
        raise ApiError("NOT_FOUND", "Scan not found.")
    return scan


@app.delete("/api/history/{scan_id}")
def history_delete(scan_id: int, request: Request):
    if demo.no_ticket(request) or not db.delete_scan(owner(request), scan_id):
        raise ApiError("NOT_FOUND", "Scan not found.")
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
config.SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/samples", StaticFiles(directory=str(config.SAMPLES_DIR)), name="samples")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

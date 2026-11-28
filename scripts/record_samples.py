"""Record the saved sample results from REAL live runs.

Needs a usable Groq key (API Keys screen or GROQ_API_KEY in .env). Run from the project root:
    .venv/bin/python scripts/record_samples.py      (Windows: .venv/Scripts/python scripts/record_samples.py)
Writes samples/results/<id>.json in the same shape /api/analyze returns.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, graph, imaging, llm, samples  # noqa: E402


def main() -> int:
    if not llm.get_key("groq")[0]:
        print("No usable Groq key found. Add one in the app (API Keys) or in .env, then run again.")
        return 1
    samples.ensure_samples()
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for s in samples.SAMPLES:
        path = config.SAMPLES_DIR / s["file"]
        if not path.exists():
            print("missing sample image:", s["file"])
            continue
        image_b64, thumb = imaging.prepare_image(path.read_bytes())
        t0 = time.perf_counter()
        for attempt in range(3):   # free-tier tokens-per-minute limits: wait a minute and retry
            try:
                state = graph.PIPELINE.invoke({"image_b64": image_b64, "thumb": thumb, "client_ip": "sample",
                                               "crop_hint": "", "city": "", "fresh": True, "trace": []})
                break
            except llm.LLMError as exc:
                print("  AI error:", exc, "- waiting 65 s")
                time.sleep(65)
        else:
            print("giving up on", s["id"])
            continue
        result = graph.build_result(state)
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        (config.RESULTS_DIR / f"{s['id']}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("saved", s["id"], "->", (result.get("diagnosis") or {}).get("disease", result.get("warning", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The 5-node stateful LangGraph pipeline (optimised for a 6-18 s end-to-end run).

    START -> specimen_gatekeeper_node --(real leaf)--+--> pathogen_vision_node --+--> treatment_protocol_node
                       |                             |     (ONE consolidated      |            |
                  (not a leaf)                       |      LLM call: dx +        |    preventive_advisor_node -> END
                       v                             |      treatment + prev.)    |
                      END                            +--> weather_telemetry_node -+
                                                           (Open-Meteo, concurrent)

All five nodes stay visible in the UI. Node 1 is its own short call; nodes 2/4/5 share one structured
JSON call (node 2 makes it, nodes 4/5 publish their slices and persist); node 3 runs in parallel with it.
Every node is wrapped by `@traced`, which records latency/status/provider into the shared state.
"""
import functools
import json
import operator
import time
from typing import Annotated, Any, Callable, Dict, List

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from . import config, db, imaging, llm, prompts, schemas, weather

GATEKEEPER_NODE = "specimen_gatekeeper_node"
VISION_NODE = "pathogen_vision_node"
WEATHER_NODE = "weather_telemetry_node"
TREATMENT_NODE = "treatment_protocol_node"
PREVENTIVE_NODE = "preventive_advisor_node"
NODE_ORDER = [GATEKEEPER_NODE, VISION_NODE, WEATHER_NODE, TREATMENT_NODE, PREVENTIVE_NODE]


class PathologyState(TypedDict, total=False):
    """Shared state that flows through (and is enriched by) every node."""
    # inputs
    image_b64: str
    thumb: str
    client_ip: str
    crop_hint: str
    city: str
    # node outputs
    gatekeeper: Dict[str, Any]
    diagnosis: Dict[str, Any]
    climate: Dict[str, Any]       # raw Open-Meteo data (node 3); risk is added once the diagnosis is known
    bundle: Dict[str, Any]        # treatment + prevention from the consolidated call
    weather: Dict[str, Any]
    treatment: Dict[str, Any]
    prevention: Dict[str, Any]
    # control / bookkeeping
    halted: bool
    warning: str
    scan_id: int
    provider: str
    trace: Annotated[List[Dict[str, Any]], operator.add]  # reducer: node traces accumulate


def traced(node_id: str, label: str) -> Callable:
    """Decorator: time the node and append a trace entry. Nodes pass status via update['_meta']."""
    def decorator(fn: Callable[[PathologyState], Dict[str, Any]]):
        @functools.wraps(fn)
        def wrapper(state: PathologyState) -> Dict[str, Any]:
            started = time.perf_counter()
            update = fn(state)
            meta = update.pop("_meta", {})
            update["trace"] = [{
                "node": node_id,
                "label": label,
                "status": meta.get("status", "ok"),
                "detail": meta.get("detail", ""),
                "provider": meta.get("provider", ""),
                "ms": round((time.perf_counter() - started) * 1000),
            }]
            return update
        return wrapper
    return decorator


# --------------------------------------------------------------------------- nodes
@traced(GATEKEEPER_NODE, "Specimen Gatekeeper")
def specimen_gatekeeper_node(state: PathologyState) -> Dict[str, Any]:
    """Node 1 - short vision check. Passes every real leaf photo; halts only for non-plants / illustrations."""
    gate, provider = llm.invoke_json(prompts.GATEKEEPER, "Inspect this image.", schemas.Gatekeeper,
                                     images=[imaging.shrink_b64(state["image_b64"])], temperature=0.0,
                                     timeout=config.GATE_TIMEOUT_SECONDS, max_tokens=config.GATE_MAX_TOKENS)
    data = gate.model_dump()
    if not data["is_real_leaf"]:
        warning = data["user_message"] or (
            "This doesn't look like a real photo of a plant leaf. Please upload a camera photo of a leaf "
            "(good light, close-up).")
        return {"gatekeeper": data, "halted": True, "warning": warning, "provider": provider,
                "_meta": {"status": "halted", "detail": f"Rejected ({data['category'] or 'not a leaf'}) - pipeline stopped",
                          "provider": provider}}
    return {"gatekeeper": data, "halted": False, "provider": provider,
            "_meta": {"status": "ok", "detail": "Real leaf photo", "provider": provider}}


def route_after_gatekeeper(state: PathologyState):
    """Conditional edge: stop early for invalid specimens, otherwise fan out to nodes 2 and 3 in parallel."""
    return END if state.get("halted") else [VISION_NODE, WEATHER_NODE]


@traced(VISION_NODE, "Pathogen Vision")
def pathogen_vision_node(state: PathologyState) -> Dict[str, Any]:
    """Node 2 - ONE consolidated structured call: diagnosis (this node) + treatment + prevention (nodes 4, 5)."""
    hint = (state.get("crop_hint") or "").strip()
    text = "Diagnose this leaf and write the treatment and prevention plans." + (
        f" The grower says the crop is: {hint}." if hint else "")
    out, provider = llm.invoke_json(prompts.BUNDLE, text, schemas.Bundle, images=[state["image_b64"]],
                                    timeout=config.BUNDLE_TIMEOUT_SECONDS, max_tokens=config.BUNDLE_MAX_TOKENS, hedge_after=7.0)
    d = out.diagnosis.model_dump()
    if hint and d["crop"].lower() in ("", "unknown"):
        d["crop"] = hint
    return {"diagnosis": d, "provider": provider,
            "bundle": {"treatment": out.treatment.model_dump(), "prevention": out.prevention.model_dump()},
            "_meta": {"status": "ok", "provider": provider, "detail": f"{d['disease']} - {d['confidence']:.0f}%"}}


@traced(WEATHER_NODE, "Weather Telemetry")
def weather_telemetry_node(state: PathologyState) -> Dict[str, Any]:
    """Node 3 - Open-Meteo microclimate (runs concurrently with node 2; skipped when no city is given)."""
    w = weather.fetch_climate((state.get("city") or "").strip(), timeout=config.WEATHER_TIMEOUT_SECONDS)
    if w["available"]:
        detail = f"{w['city']}: {w['temperature_c']} C, RH {w['humidity_pct']}%, rain 48h {w['rain_48h_mm']} mm"
        status = "ok"
    else:
        detail, status = w["reason"], "skipped"
    return {"climate": w, "_meta": {"status": status, "detail": detail, "provider": "open-meteo"}}


@traced(TREATMENT_NODE, "Treatment Protocol")
def treatment_protocol_node(state: PathologyState) -> Dict[str, Any]:
    """Node 4 - join point: publishes the dual-track protocol and combines weather with the diagnosis."""
    w = weather.apply_risk(state["climate"], state["diagnosis"]["pathogen_class"])
    detail = "Organic + chemical protocols ready"
    if w.get("available"):
        detail += f" | spread risk {w['risk_level']} ({w['risk_score']})"
    return {"treatment": state["bundle"]["treatment"], "weather": w,
            "_meta": {"status": "ok", "provider": state.get("provider", ""), "detail": detail}}


@traced(PREVENTIVE_NODE, "Preventive Advisor")
def preventive_advisor_node(state: PathologyState) -> Dict[str, Any]:
    """Node 5 - publishes the prevention plan, then persists the complete record to SQLite."""
    provider = state.get("provider", "")
    merged = {**state, "prevention": state["bundle"]["prevention"]}
    record = build_result(merged)
    scan_id = db.save_scan(
        ip=state.get("client_ip", "unknown"), crop=record["diagnosis"]["crop"], city=state.get("city", ""),
        diagnosis=record["diagnosis"], provider=provider, thumb=state.get("thumb", ""), record=record,
    )
    return {"prevention": state["bundle"]["prevention"], "scan_id": scan_id,
            "_meta": {"status": "ok", "provider": provider, "detail": f"Saved as scan #{scan_id}"}}


# --------------------------------------------------------------------------- assembly
def build_result(state: Dict[str, Any]) -> Dict[str, Any]:
    """Public, JSON-serialisable view of the final state (excludes image bytes / thumb / ids)."""
    return {
        "crop_hint": state.get("crop_hint", ""),
        "city": state.get("city", ""),
        "provider": state.get("provider", ""),
        "halted": bool(state.get("halted")),
        "warning": state.get("warning", ""),
        "gatekeeper": state.get("gatekeeper"),
        "diagnosis": state.get("diagnosis"),
        "weather": state.get("weather"),
        "treatment": state.get("treatment"),
        "prevention": state.get("prevention"),
        "trace": state.get("trace", []),
    }


def build_graph():
    g = StateGraph(PathologyState)
    g.add_node(GATEKEEPER_NODE, specimen_gatekeeper_node)
    g.add_node(VISION_NODE, pathogen_vision_node)
    g.add_node(WEATHER_NODE, weather_telemetry_node)
    g.add_node(TREATMENT_NODE, treatment_protocol_node)
    g.add_node(PREVENTIVE_NODE, preventive_advisor_node)

    g.add_edge(START, GATEKEEPER_NODE)
    g.add_conditional_edges(GATEKEEPER_NODE, route_after_gatekeeper, [VISION_NODE, WEATHER_NODE, END])
    g.add_edge([VISION_NODE, WEATHER_NODE], TREATMENT_NODE)   # join: waits for BOTH parallel branches
    g.add_edge(TREATMENT_NODE, PREVENTIVE_NODE)
    g.add_edge(PREVENTIVE_NODE, END)
    return g.compile()


PIPELINE = build_graph()

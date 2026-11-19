"""Open-Meteo geocoding + forecast (free, no API key) and outbreak spread-risk scoring."""
from typing import Any, Dict

import httpx

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# How strongly weather drives spread for each pathogen class.
CLASS_WEIGHT = {"Fungal": 1.0, "Bacterial": 1.0, "Viral": 0.6, "Pest/Nutrient": 0.5, "Healthy": 0.8}

CLASS_NOTE = {
    "Fungal": "Spores germinate and spread fastest in humid, mild, wet conditions.",
    "Bacterial": "Bacteria spread through rain splash and leaf wetness; warm wet spells are high risk.",
    "Viral": "Viruses spread via insect vectors; weather mainly affects vector activity.",
    "Pest/Nutrient": "Weather has a moderate, indirect effect on pests and nutrient stress.",
    "Healthy": "No infection detected - this is the general disease pressure for your location.",
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_spread_risk(temp_c: float, rh_now: float, rh_avg: float, rain_mm: float, pathogen_class: str) -> Dict[str, Any]:
    """Return {score 0-100, level, note}. Humidity 40 pts + temperature 25 pts + rainfall 35 pts."""
    rh = 0.5 * rh_now + 0.5 * rh_avg
    rh_pts = _clamp((rh - 55) / 40, 0, 1) * 40
    if 18 <= temp_c <= 28:
        temp_pts = 25.0
    elif 15 <= temp_c < 18 or 28 < temp_c <= 30:
        temp_pts = 17.5
    else:
        gap = (15 - temp_c) if temp_c < 15 else (temp_c - 30)
        temp_pts = max(0.0, 17.5 - gap * 3)
    rain_pts = _clamp(rain_mm / 25, 0, 1) * 35
    score = (rh_pts + temp_pts + rain_pts) * CLASS_WEIGHT.get(pathogen_class, 0.7)
    score = round(_clamp(score, 0, 100))
    level = "Low" if score < 35 else "Moderate" if score < 65 else "High"
    return {"risk_score": score, "risk_level": level, "risk_note": CLASS_NOTE.get(pathogen_class, "")}


def fetch_climate(city: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Look up a city and summarise humidity/temperature/48h rainfall (no risk score yet). Never raises."""
    city = (city or "").strip()
    if not city:
        return {"available": False, "reason": "No city supplied - microclimate analysis skipped."}
    try:
        with httpx.Client(timeout=timeout) as client:
            geo = client.get(GEO_URL, params={"name": city, "count": 1, "language": "en", "format": "json"})
            geo.raise_for_status()
            results = geo.json().get("results") or []
            if not results:
                return {"available": False, "reason": f"Could not find a city named '{city}'."}
            loc = results[0]
            fc = client.get(FORECAST_URL, params={
                "latitude": loc["latitude"], "longitude": loc["longitude"],
                "current": "temperature_2m,relative_humidity_2m",
                "hourly": "relative_humidity_2m,precipitation",
                "forecast_days": 3, "timezone": "auto",
            })
            fc.raise_for_status()
            data = fc.json()

        cur = data["current"]
        hourly = data["hourly"]
        times = hourly["time"]
        hour_key = cur["time"][:13] + ":00"
        start = times.index(hour_key) if hour_key in times else 0
        window = slice(start, start + 48)
        rain = sum(float(p or 0) for p in hourly["precipitation"][window])
        rh_values = [float(h) for h in hourly["relative_humidity_2m"][window] if h is not None]
        rh_avg = sum(rh_values) / len(rh_values) if rh_values else float(cur["relative_humidity_2m"])

        temp = float(cur["temperature_2m"])
        rh_now = float(cur["relative_humidity_2m"])
        return {
            "available": True,
            "city": loc.get("name", city),
            "region": loc.get("admin1", ""),
            "country": loc.get("country", ""),
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "temperature_c": round(temp, 1),
            "humidity_pct": round(rh_now),
            "avg_humidity_48h_pct": round(rh_avg),
            "rain_48h_mm": round(rain, 1),
            "_rh_now": rh_now,
            "_rh_avg": rh_avg,
        }
    except Exception as exc:  # network down, API change, etc. - degrade gracefully
        return {"available": False, "reason": f"Weather service unavailable ({type(exc).__name__})."}


def apply_risk(climate: Dict[str, Any], pathogen_class: str) -> Dict[str, Any]:
    """Add the outbreak spread-risk score to a `fetch_climate` result (once the diagnosis is known)."""
    if not climate.get("available"):
        return climate
    out = {k: v for k, v in climate.items() if not k.startswith("_")}
    out.update(compute_spread_risk(climate["temperature_c"], climate["_rh_now"], climate["_rh_avg"],
                                   climate["rain_48h_mm"], pathogen_class))
    return out

# Viva notes: Multi-Modal Plant Leaf Disease Diagnosis & Treatment System

## One-line pitch
Upload a leaf photo; a five-node LangGraph pipeline checks it is a leaf, diagnoses the disease with a Groq vision
model, adds live Open-Meteo weather risk, and returns organic and chemical treatment plans plus prevention advice.

## Architecture in 60 seconds
- **Backend:** Python + FastAPI (`app/main.py`), SQLite for history and the saved key (`app/db.py`).
- **Pipeline:** LangGraph `StateGraph` (`app/graph.py`) with shared `PathologyState`, one conditional edge
  (stop if not a leaf) and a parallel branch (diagnosis and weather run together, then join).
- **AI:** Groq only (`app/llm.py`). Vision model for images, text model fallback. Retries with backoff on 429/5xx/timeouts,
  then a fallback model, then a plain-English error. Failed runs never count against a limit.
- **Frontend:** plain HTML/CSS/JS in `static/`, streaming progress from `POST /api/analyze` (newline-delimited JSON).
- **Port:** 8001 (8000 + project number 1).

## Likely questions
- **Why LangGraph?** Explicit state, a conditional stop, a parallel fan-out/join, and per-node timing for the UI.
- **Why a gatekeeper node?** Stops wasted AI calls and wrong diagnoses on non-leaf photos.
- **How is weather risk computed?** Humidity (0-40) + temperature (0-25) + 48 h rain (0-35), times a weight per pathogen class.
- **How is output kept reliable?** Replies are requested as JSON, parsed defensively, validated by Pydantic (`app/schemas.py`),
  confidence clamped to 0-100.
- **Where is the API key kept?** In the local SQLite file or `.env`, never committed and never sent back in full to the browser.
- **What if Groq is busy?** Retry with exponential backoff, switch to the fallback model, then show "The AI service is busy".
- **Why no Docker or GPU?** One command must work on a student laptop: SQLite, pure-Python dependencies, hosted AI.
- **Limits?** AI output is decision support only; it can be wrong. Not a replacement for an agronomist.

## Run and demo checklist
1. `start.bat` (Windows) or `./start.sh` (macOS/Linux); the browser opens at http://127.0.0.1:8001.
2. Paste a Groq key under **API Keys**.
3. Click a sample, then **Analyse**; show the five nodes lighting up and the timings.
4. Show the non-leaf control sample being rejected by the gatekeeper.
5. Show **Compare Progress**, **History** and **Report** (print to PDF).

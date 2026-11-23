# Multi-Modal Plant Leaf Disease Diagnosis & Treatment System

Take a photo of a plant leaf and this app tells you what disease it has, how sure it is, how likely it is to spread
in your local weather, and what to do about it (an organic plan and a chemical plan, side by side). It checks that
the photo really is a leaf first, can compare an older and a newer photo to see if the plant is recovering, keeps a
history of your scans, and prints a PDF report. It is built for final-year students and for growers who want a
second opinion.

## What you need

- **Python 3.10 or newer.** Download it from <https://www.python.org/downloads/>. On Windows, tick **Add python.exe to PATH** during install.
- **A free Groq key.** Get one in about a minute, with no card, at <https://console.groq.com/keys>.
- An internet connection (the AI and the weather data are online).

## Run it (one command)

- **macOS / Linux:** `chmod +x start.sh && ./start.sh`
- **Windows:** double-click `start.bat`

The first run sets up everything by itself (a private Python environment, the requirements, a `.env` file and the
sample photos). Later runs start in seconds. When the server is ready your browser opens at
**http://127.0.0.1:8001**. Press `Ctrl+C` (or close the window) to stop.

## Add your Groq key

Click **API Keys** in the app and paste your key (it starts with `gsk_`), or open `.env` and set
`GROQ_API_KEY=gsk_your_key`. A key saved in the app is used first, then the one in `.env`.

## How to use it

1. **Try a sample.** Click one of the sample leaves under *1-click samples*. Its saved result appears straight away.
   Press **Analyse** to run the same leaf through the AI yourself.
2. **Use your own photo.** Drag a leaf photo into the box (or click it). Optionally type the crop name and your city
   (the city adds a live weather spread-risk score). Press **Analyse** and watch the five steps light up.
3. **Read the result.** You get the disease, confidence, severity, weather risk, an organic plan, a chemical plan and
   prevention tips. Click **Report** to print or save it as a PDF.
4. **Compare progress.** Open the *Compare Progress* tab, add an older and a newer photo of the same plant and see if
   it is improving, stable or worsening.
5. **History.** Click **History** to search, reopen or delete your earlier scans.

## How it works

Your photo goes through five steps (a LangGraph pipeline in `app/graph.py`):

1. **Specimen Gatekeeper** checks the photo really shows a plant leaf. If not, it stops and tells you why.
2. **Pathogen Vision** asks a Groq vision model for the disease, pathogen type, confidence and severity. The same
   AI call also drafts the treatment and prevention plans.
3. **Weather Telemetry** looks up your city on Open-Meteo (free, no key) and scores how likely the disease is to spread.
4. **Treatment Protocol** joins the diagnosis with the weather and publishes the organic and chemical plans.
5. **Preventive Advisor** adds crop rotation, resistant varieties and soil advice, then saves the scan to a local SQLite file.

Every AI call (`app/llm.py`) has a timeout, retries when Groq is busy, and switches to a backup model if needed. If it
still fails you get a plain message, not an error dump.

## Project structure

```
Multi-Modal-Plant-Leaf-Disease-Diagnosis-Treatment-System/
├── start.bat / start.sh     one-command start (Windows / macOS / Linux)
├── run.py                   alternative launcher: python run.py
├── scripts/record_samples.py  re-records the saved sample results
├── VIVA_NOTES.md            talking points for your viva
├── requirements.txt         pinned Python requirements
├── .env.example             settings template (copied to .env on first run)
├── app/
│   ├── main.py              FastAPI routes
│   ├── graph.py             the five-step LangGraph pipeline
│   ├── llm.py               Groq calls: retries, fallback model, key handling
│   ├── prompts.py           AI prompts
│   ├── schemas.py           checks and cleans the AI output
│   ├── weather.py           Open-Meteo weather and spread risk
│   ├── imaging.py           image checks and resizing
│   ├── samples.py           sample photos and their saved results
│   ├── db.py                SQLite: history and saved key
│   ├── demo.py              access checks for the hosted preview
│   ├── errors.py            one error format for every route
│   └── config.py            settings read from .env
├── static/                  the web page (index.html, css/, js/)
└── samples/                 sample leaf photos and samples/results/ (saved results)
```

## Troubleshooting

- **Port already in use.** Another program is using port 8001. Close it, or change `PORT=` in `.env` and run the start
  script again.
- **"The AI service is busy".** Groq's free tier is overloaded. Wait a minute and try again. The app already retried
  and tried a backup model.
- **"Your Groq key was rejected".** The key is wrong or was deleted. Click **API Keys** and paste a fresh key from
  <https://console.groq.com/keys>.
- **Python not found.** Install Python 3.10+ from <https://www.python.org/downloads/>, tick *Add python.exe to PATH*,
  then run the start script again.
- **Install failed or is very slow.** Check your internet connection and run the start script again; it continues
  where it stopped.
- **Weather risk is skipped.** Leave the city blank or check its spelling. Everything else still works.

## Safety disclaimer

AI output is decision support only and can be wrong. Confirm with a qualified agronomist, follow pesticide labels, wear
the specified PPE, and respect pre-harvest intervals and local rules. The app never gives brand-specific dosages.

"""Launch the console: `python run.py` (opens your browser automatically)."""
import threading
import webbrowser

import uvicorn

from app import config


def _open_browser() -> None:
    webbrowser.open(f"http://{config.HOST}:{config.PORT}")


if __name__ == "__main__":
    threading.Timer(1.5, _open_browser).start()
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="info")

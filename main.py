"""Entry point: `python main.py` starts the FastAPI server on port 8000."""

from __future__ import annotations

import uvicorn

from server.app import create_app

app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

"""FastAPI-based GUI server for Esprit live dashboard."""

import asyncio
import json
import logging
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from esprit.gui.tracer_bridge import TracerBridge


if TYPE_CHECKING:
    from esprit.telemetry.tracer import Tracer

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


class GUIServer:
    """Runs a FastAPI app in a daemon thread, serving the Esprit dashboard."""

    def __init__(self, port: int = 7860) -> None:
        self.port = port
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bridge: TracerBridge | None = None
        self._started = threading.Event()
        self._should_stop = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, tracer: "Tracer", open_browser: bool = True) -> None:
        """Start the GUI server in a daemon thread."""
        if self._thread and self._thread.is_alive():
            return

        self._should_stop.clear()
        self._thread = threading.Thread(
            target=self._run_server, args=(tracer, open_browser), daemon=True
        )
        self._thread.start()
        self._started.wait(timeout=5.0)

    def stop(self) -> None:
        """Signal the server to shut down."""
        self._should_stop.set()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._bridge:
            self._bridge.stop()
        if self._thread:
            self._thread.join(timeout=3.0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_server(self, tracer: "Tracer", open_browser: bool) -> None:
        """Entry-point for the server thread."""
        try:
            from fastapi import FastAPI, WebSocket, WebSocketDisconnect
            from fastapi.responses import FileResponse, JSONResponse
            from fastapi.staticfiles import StaticFiles
        except ImportError as exc:
            logger.error(
                "FastAPI/uvicorn not installed. Install with: pip install 'esprit-cli[gui]'"
            )
            raise RuntimeError(
                "Missing GUI dependencies. Install with: pip install 'esprit-cli[gui]'"
            ) from exc

        app = FastAPI(title="Esprit Dashboard")
        self._bridge = TracerBridge(tracer)

        # --- Routes ---

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")

        @app.get("/api/screenshot/{agent_id}")
        async def get_screenshot(agent_id: str) -> JSONResponse:
            data = self._bridge.get_screenshot(agent_id)
            return JSONResponse(data)

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket) -> None:
            await websocket.accept()
            self._bridge.add_client(websocket)
            try:
                # Send full state on connect
                full = self._bridge.get_full_state()
                await websocket.send_text(json.dumps(full))
                # Keep alive — the bridge push loop sends deltas
                while not self._should_stop.is_set():
                    try:
                        # Just keep the connection alive; we read client pings
                        await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Send a heartbeat
                        try:
                            await websocket.send_text(json.dumps({"type": "heartbeat"}))
                        except Exception:  # noqa: BLE001
                            break
            except WebSocketDisconnect:
                pass
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._bridge.remove_client(websocket)

        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

        # --- Run ---

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Start bridge polling
        self._bridge.start(self._loop)

        import uvicorn

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        self._started.set()

        if open_browser:
            self._loop.call_later(1.5, lambda: webbrowser.open(f"http://localhost:{self.port}"))

        try:
            self._loop.run_until_complete(server.serve())
        except Exception:  # noqa: BLE001
            pass

    def get_url(self) -> str:
        return f"http://localhost:{self.port}"

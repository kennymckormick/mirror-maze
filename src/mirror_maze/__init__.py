"""Python entry point for the mirror-maze environment.

Rules run in the same JavaScript module as the web UI. Rendering is lazy and
uses that module's WebGL renderer in an isolated headless browser.
"""
from __future__ import annotations

import base64
from collections import deque
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue, Empty
import shutil
import subprocess
import threading
from typing import Any, Optional, Sequence, Union

__version__ = "0.2.0"
__all__ = ["MirrorMaze", "MazeError", "__version__"]


class MazeError(RuntimeError):
    """Environment process or rendering failure."""


def _assets() -> Path:
    package = Path(__file__).resolve().parent
    for directory in (package / "assets", package.parent / "dist"):
        if (directory / "maze.js").is_file():
            return directory
    raise MazeError("Browser assets are missing; install a built wheel or use the full checkout.")


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class _Renderer:
    def __init__(self, assets: Path, executable: Optional[str]):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise MazeError("Rendering needs: pip install 'mirror-maze-lab[render]' and python -m playwright install chromium") from exc
        self.server = self.driver = self.browser = None
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(assets)))
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            self.driver = sync_playwright().start()
            # Prefer Playwright's matching Chromium; an installed Chrome is a
            # fallback. It uses a fresh temporary profile, never the user's tabs.
            if executable is None and not Path(self.driver.chromium.executable_path).is_file():
                executable = next((str(p) for p in [
                    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                    Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"),
                ] if p.is_file()), None)
            launch = {"headless": True}
            if executable is not None:
                launch["executable_path"] = str(executable)
            self.browser = self.driver.chromium.launch(**launch)
            self.page = self.browser.new_page(viewport={"width": 960, "height": 600}, device_scale_factor=1)
            self.page.goto(f"http://127.0.0.1:{self.server.server_port}/render.html", wait_until="load")
            self.page.wait_for_function("window.rendererReady === true", timeout=15000)
        except Exception:
            self.close()
            raise

    def render(self, params: dict) -> dict:
        return self.page.evaluate("params => window.renderObservation(params)", params)

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            try:
                if self.driver:
                    self.driver.stop()
            finally:
                if self.server:
                    self.server.shutdown()
                    self.server.server_close()
                    self.server_thread.join(timeout=2)
                self.browser = self.driver = self.server = None


class MirrorMaze:
    """A deterministic episode, with no model calls or external service.

    Actions: ``left``, ``right``, ``forward``. Constructing initializes an episode.
    Use as a context manager to close the worker and lazy rendering browser.
    A rendering instance must be used and closed on the thread that created it.
    """

    def __init__(self, topology: str = "square", difficulty: str = "easy",
                 seed: str = "MIRROR-001", *, side_length: Optional[int] = None,
                 bounces: int = 10, mirror: bool = True,
                 node_executable: Optional[str] = None,
                 browser_executable: Optional[str] = None, timeout: float = 30):
        if isinstance(bounces, bool) or not isinstance(bounces, int) or not 1 <= bounces <= 12:
            raise ValueError("bounces must be an integer from 1 to 12")
        if not isinstance(mirror, bool):
            raise ValueError("mirror must be a bool")
        self.assets = _assets()
        node = node_executable or os.environ.get("MIRROR_MAZE_NODE") or shutil.which("node")
        if not node:
            raise MazeError("Node.js 18+ is required. Install Node or set MIRROR_MAZE_NODE.")
        self.bounces, self.mirror = bounces, mirror
        self.browser_executable, self.timeout = browser_executable, timeout
        self._renderer = None
        self._closed = False
        self._lock = threading.RLock()
        self._messages: Queue = Queue()
        self._errors = deque(maxlen=20)
        self._request_id = 0
        self._options = {}
        self._render_history = []
        self._process = subprocess.Popen(
            [node, str(Path(__file__).parent / "bridge.mjs"), str(self.assets)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        try:
            self.initialize(topology=topology, difficulty=difficulty, seed=seed, side_length=side_length)
        except Exception:
            self.close()
            raise

    def _read_stdout(self):
        for line in self._process.stdout:
            self._messages.put(line)
        self._messages.put(None)

    def _read_stderr(self):
        for line in self._process.stderr:
            self._errors.append(line.strip())

    def _rpc(self, method: str, params: Optional[dict] = None) -> Any:
        with self._lock:
            if self._closed:
                raise MazeError("Environment is closed")
            self._request_id += 1
            request = {"id": self._request_id, "method": method, "params": params or {}}
            try:
                self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MazeError("Environment worker exited: " + "\n".join(self._errors)) from exc
            try:
                line = self._messages.get(timeout=self.timeout)
            except Empty as exc:
                self._process.kill()
                self._closed = True
                raise MazeError(f"Environment request timed out after {self.timeout}s; close this instance.") from exc
            if line is None:
                raise MazeError("Environment worker exited: " + "\n".join(self._errors))
            response = json.loads(line)
            if response.get("id") != request["id"]:
                raise MazeError("Environment protocol request mismatch")
            if not response["ok"]:
                raise ValueError(response["error"]["message"])
            return response["result"]

    def initialize(self, *, topology: Optional[str] = None, difficulty: Optional[str] = None,
                   seed: Optional[str] = None, side_length: Optional[int] = None) -> dict:
        """Start over. Omitted options retain the previous episode's values.

        Difficulty selects topology-specific sides: square/triangle 7/11/15,
        hexagon 5/7/9. Explicit side_length preserves historical geometry.
        An explicit side_length overrides that standard, for research/debugging.
        """
        options = dict(self._options)
        if topology is not None:
            options["topology"] = topology
        if difficulty is not None:
            options["difficulty"] = difficulty
            options.pop("sideLength", None)
        if seed is not None:
            options["seed"] = str(seed)
        if side_length is not None:
            if isinstance(side_length, bool) or not isinstance(side_length, int):
                raise ValueError("side_length must be an integer")
            options["sideLength"] = side_length
        result = self._rpc("initialize", options)
        self._options = options
        self._render_history = []
        result["render"] = {"bounces": self.bounces, "mirror": self.mirror}
        return result

    reset = initialize

    def info(self) -> dict:
        """Public configuration, geometry counts, action rules and counters."""
        result = self._rpc("info")
        result["render"] = {"bounces": self.bounces, "mirror": self.mirror}
        return result

    def state(self) -> dict:
        """Public counters and terminal status; no hidden position or goal."""
        return self._rpc("state")

    def review_state(self) -> dict:
        """Privileged host-only pose/counters, without exporting the full history.

        Never expose this method to a tested agent. It avoids quadratic history
        serialization when the evaluator renders a long action sequence.
        """
        return self._rpc("export", {"stateOnly": True})

    def perform(self, actions: Union[str, Sequence[str]], *, dry_run: bool = False,
                stop_on_collision: bool = True) -> dict:
        """Apply one action or a sequence, or inspect it without applying it.

        A collision consumes an action. By default the suffix after the first
        collision is not attempted; the accepted prefix remains applied.
        allExecutable is false if any action is blocked or after termination.
        blockedAt is a zero-based index into the supplied sequence.
        """
        if not isinstance(actions, str):
            if not isinstance(actions, (list, tuple)):
                raise ValueError("actions must be a string, list or tuple")
            actions = list(actions)
        if not isinstance(dry_run, bool) or not isinstance(stop_on_collision, bool):
            raise ValueError("dry_run and stop_on_collision must be bools")
        return self._rpc("perform", {"actions": actions, "dryRun": dry_run, "stopOnCollision": stop_on_collision})

    def preview_sequence(self, actions: Sequence[str]) -> dict:
        """Inspect feasibility without changing state, counters or history."""
        return self.perform(actions, dry_run=True)

    step = perform

    def export_episode(self, path: Optional[Union[str, Path]] = None) -> dict:
        """Privileged review data: includes full geometry, position and goal."""
        data = self._rpc("export")
        data["render"] = {"bounces": self.bounces, "mirror": self.mirror}
        data["renderHistory"] = [dict(entry) for entry in self._render_history]
        if path is not None:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def debug_solution(self) -> list:
        """Privileged reference solution. Do not expose to an evaluated agent."""
        return self._rpc("solution")

    def render(self, path: Optional[Union[str, Path]] = None, *, width: int = 960,
               height: int = 600, view: str = "first_person",
               bounces: Optional[int] = None, mirror: Optional[bool] = None) -> bytes:
        """Return PNG bytes (and optionally save them), without changing state.

        first_person: width x height, no map/HUD; minimap: height x height;
        review: (width + height) x height, first-person plus a square map.
        Requires the [render] extra and Chromium or an installed Chrome.
        """
        if any(isinstance(v, bool) or not isinstance(v, int) or not 64 <= v <= 4096 for v in (width, height)):
            raise ValueError("width and height must be integers from 64 to 4096")
        if view not in ("first_person", "minimap", "review"):
            raise ValueError("view must be first_person, minimap or review")
        bounces = self.bounces if bounces is None else bounces
        mirror = self.mirror if mirror is None else mirror
        if isinstance(bounces, bool) or not isinstance(bounces, int) or not 1 <= bounces <= 12:
            raise ValueError("bounces must be an integer from 1 to 12")
        if not isinstance(mirror, bool):
            raise ValueError("mirror must be a bool")
        with self._lock:
            snapshot = self.export_episode()
            if self._renderer is None:
                try:
                    self._renderer = _Renderer(self.assets, self.browser_executable)
                except Exception as exc:
                    raise MazeError("Could not start renderer. Install Chromium with `python -m playwright install chromium`, or set browser_executable. Details: " + str(exc)) from exc
            result = self._renderer.render({"snapshot": snapshot, "width": width, "height": height,
                                            "view": view, "bounces": bounces, "mirror": mirror})
            data = base64.b64decode(result["image"].split(",", 1)[1])
            self._render_history.append({"atAction": snapshot["state"]["actionCount"],
                                         "view": view, "width": width, "height": height,
                                         "bounces": bounces, "mirror": mirror})
            if path is not None:
                target = Path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            return data

    def close(self):
        """Close isolated browser, local renderer server and rule worker."""
        with self._lock:
            try:
                if self._renderer is not None:
                    self._renderer.close()
                    self._renderer = None
            finally:
                if self._process.poll() is None:
                    self._process.stdin.close()
                    try:
                        self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=3)
                self._stdout_thread.join(timeout=2)
                self._stderr_thread.join(timeout=2)
                for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                    if not stream.closed:
                        stream.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

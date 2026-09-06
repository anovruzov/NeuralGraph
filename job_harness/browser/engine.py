"""Playwright browser engine with a persistent context and crash recovery."""
from __future__ import annotations

import glob
import os
import time
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import (
    Browser, BrowserContext, Error as PlaywrightError, Page, Playwright,
    TimeoutError as PlaywrightTimeout, sync_playwright,
)

from ..config.logging_setup import get_logger
from ..config.settings import BrowserConfig

log = get_logger("browser")


def resolve_chromium_path(configured: str = "") -> Optional[str]:
    """Find a usable Chromium.

    Playwright pins a browser revision; a pre-provisioned image may ship a
    different one. Prefer an explicit path, then the pinned build, then any
    Chromium under PLAYWRIGHT_BROWSERS_PATH, then a system Chrome.
    """
    if configured and Path(configured).exists():
        return configured
    env_path = os.environ.get("BROWSER_EXECUTABLE") or os.environ.get("CHROMIUM_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "",
             str(Path.home() / ".cache" / "ms-playwright"),
             "/opt/pw-browsers", "/ms-playwright"]
    candidates: list[str] = []
    for root in roots:
        if not root or not Path(root).exists():
            continue
        candidates += sorted(glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome")),
                             reverse=True)
        candidates += sorted(glob.glob(os.path.join(root, "chromium*", "chrome-mac*", "Chromium.app",
                                                    "Contents", "MacOS", "Chromium")), reverse=True)
        candidates += sorted(glob.glob(os.path.join(root, "chromium_headless_shell-*",
                                                    "chrome-linux", "headless_shell")), reverse=True)
    for system in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
                   "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"):
        if Path(system).exists():
            candidates.append(system)
    for candidate in candidates:
        if Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


class BrowserEngine:
    """Owns the Playwright lifecycle. Use as a context manager.

    A persistent context keeps cookies and logins across restarts, which is what
    lets a long campaign reuse an authenticated ATS session.
    """

    def __init__(self, config: BrowserConfig, db: Any = None,
                 run_id: Optional[str] = None) -> None:
        self.config = config
        self.db = db
        self.run_id = run_id
        self._pw: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._browser: Optional[Browser] = None
        self._session_id: Optional[int] = None
        self.pages_opened = 0
        self.crashes = 0
        self.executable_path: Optional[str] = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> "BrowserEngine":
        self._pw = sync_playwright().start()
        self.executable_path = resolve_chromium_path(self.config.executable_path)
        Path(self.config.persistent_profile_dir).mkdir(parents=True, exist_ok=True)
        Path(self.config.screenshot_dir).mkdir(parents=True, exist_ok=True)

        launch_args: dict[str, Any] = {
            "headless": self.config.headless,
            "slow_mo": self.config.slow_mo_ms or 0,
            "viewport": {"width": self.config.viewport_width,
                         "height": self.config.viewport_height},
            "locale": self.config.locale,
            "timezone_id": self.config.timezone_id,
            "accept_downloads": True,
            "args": ["--disable-blink-features=AutomationControlled",
                     "--no-sandbox", "--disable-dev-shm-usage"],
        }
        if self.executable_path:
            launch_args["executable_path"] = self.executable_path

        try:
            self._context = self._pw.chromium.launch_persistent_context(
                self.config.persistent_profile_dir, **launch_args
            )
        except PlaywrightError as exc:
            log.error("persistent context failed; falling back to an ephemeral context",
                      extra={"error": str(exc)[:300]})
            self._browser = self._pw.chromium.launch(
                headless=self.config.headless,
                executable_path=self.executable_path or None,
                args=launch_args["args"],
            )
            self._context = self._browser.new_context(
                viewport=launch_args["viewport"], locale=self.config.locale,
                timezone_id=self.config.timezone_id, accept_downloads=True,
            )

        self._context.set_default_timeout(self.config.action_timeout_ms)
        self._context.set_default_navigation_timeout(self.config.navigation_timeout_ms)

        if self.db is not None:
            try:
                cur = self.db.execute(
                    "INSERT INTO browser_sessions (run_id, profile_dir, started_at, user_agent) "
                    "VALUES (?,?,datetime('now'),?)",
                    (self.run_id, self.config.persistent_profile_dir,
                     self.executable_path or "default"),
                )
                self._session_id = int(cur.lastrowid)
            except Exception:
                pass
        log.info("browser started", extra={"executable": self.executable_path or "playwright-default",
                                           "headless": self.config.headless})
        return self

    def stop(self) -> None:
        if self.db is not None and self._session_id:
            try:
                self.db.execute(
                    "UPDATE browser_sessions SET ended_at=datetime('now'), pages_opened=?, "
                    "crashes=? WHERE id=?",
                    (self.pages_opened, self.crashes, self._session_id),
                )
            except Exception:
                pass
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._context, self._browser, self._pw = None, None, None
        log.info("browser stopped")

    def __enter__(self) -> "BrowserEngine":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -------------------------------------------------------------- context

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("browser not started")
        return self._context

    def healthy(self) -> bool:
        try:
            _ = self.context.pages
            return True
        except Exception:
            return False

    def recover(self) -> None:
        """Restart the browser after a crash. Persistent state survives."""
        self.crashes += 1
        log.warning("recovering browser", extra={"crashes": self.crashes})
        try:
            self.stop()
        except Exception:
            pass
        time.sleep(2)
        self.start()

    # ---------------------------------------------------------------- pages

    def new_page(self) -> Page:
        page = self.context.new_page()
        page.set_default_timeout(self.config.action_timeout_ms)
        page.set_default_navigation_timeout(self.config.navigation_timeout_ms)
        self.pages_opened += 1
        return page

    def close_page(self, page: Optional[Page]) -> None:
        try:
            if page and not page.is_closed():
                page.close()
        except Exception:
            pass

    def goto(self, page: Page, url: str, retries: int = 2,
             wait_until: str = "domcontentloaded") -> bool:
        for attempt in range(retries + 1):
            try:
                page.goto(url, wait_until=wait_until,
                          timeout=self.config.navigation_timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except PlaywrightTimeout:
                    pass  # Many ATS pages poll forever; DOM-ready is enough.
                return True
            except (PlaywrightTimeout, PlaywrightError) as exc:
                log.warning("navigation failed",
                            extra={"url": url, "attempt": attempt + 1, "error": str(exc)[:200]})
                if attempt < retries:
                    time.sleep(2 ** attempt)
        return False

    def screenshot(self, page: Page, name: str) -> Optional[str]:
        if not self.config.screenshot_on_blocker:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:120]
        path = Path(self.config.screenshot_dir) / f"{int(time.time())}_{safe}.png"
        try:
            page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as exc:
            log.debug("screenshot failed", extra={"error": str(exc)[:150]})
            return None

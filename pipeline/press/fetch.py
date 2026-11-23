"""Polite HTTP for the press pipeline: the one place the politeness rules live.

One request at a time per host, at least `delay` seconds apart (more when
robots.txt asks for a larger Crawl-delay), robots.txt read each run, our own
honest user agent. A 401/403/429 stops that outlet for the run: it is recorded
in `refused` and never retried around.
"""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

CONFIG = Path(__file__).resolve().parents[2] / "config" / "press.json"
REFUSING = (401, 403, 429)
TIMEOUT = 30


def load_press_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


class Refused(Exception):
    def __init__(self, outlet: str, reason: str):
        super().__init__(f"{outlet}: {reason}")
        self.outlet, self.reason = outlet, reason


class PoliteClient:
    def __init__(self, session, user_agent: str, delay: float, store=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.session, self.user_agent, self.delay = session, user_agent, delay
        self.store, self.clock, self.sleep = store, clock, sleep
        self.refused: dict[str, str] = {}
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._blocked: set[str] = set()  # recorded in `refused` but answered None, not raised

    def _refuse(self, outlet: str, reason: str):
        self.refused[outlet] = reason
        raise Refused(outlet, reason)

    def _wait(self, host: str, delay: float) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = delay - (self.clock() - last)
            if gap > 0:
                self.sleep(gap)
        self._last[host] = self.clock()

    def _request(self, host: str, url: str, delay: float, headers: dict | None = None):
        self._wait(host, delay)
        h = {"User-Agent": self.user_agent, **(headers or {})}
        try:
            return self.session.get(url, headers=h, timeout=TIMEOUT)
        finally:
            self._last[host] = self.clock()

    def _rules(self, outlet: str, scheme: str, host: str) -> RobotFileParser:
        if host in self._robots:
            return self._robots[host]
        rp = RobotFileParser()
        try:
            r = self._request(host, f"{scheme}://{host}/robots.txt", self.delay)
        except Exception:
            self._refuse(outlet, "robots unreachable")
        code = r.status_code
        if code == 429:
            self._refuse(outlet, "robots 429")
        if code in (401, 403):
            rp.disallow_all = True
            self.refused[outlet] = f"robots {code}"
            self._blocked.add(outlet)
        elif code >= 500:
            self._refuse(outlet, "robots unreachable")
        elif code >= 400:
            rp.allow_all = True
        else:
            rp.parse(r.text.splitlines())
        self._robots[host] = rp
        return rp

    def get(self, outlet: str, url: str, conditional: bool = False):
        """Response, or None (disallowed by robots.txt, or 304 on a conditional get)."""
        if outlet in self.refused and outlet not in self._blocked:
            raise Refused(outlet, self.refused[outlet])
        parts = urlsplit(url)
        rp = self._rules(outlet, parts.scheme, parts.netloc)
        if not rp.can_fetch(self.user_agent, url):
            return None
        delay = max(self.delay, float(rp.crawl_delay(self.user_agent) or 0))
        headers = {}
        if conditional and self.store:
            etag, modified = self.store.feed_cache(url)
            if etag:
                headers["If-None-Match"] = etag
            if modified:
                headers["If-Modified-Since"] = modified
        r = self._request(parts.netloc, url, delay, headers)
        if r.status_code in REFUSING:
            self._refuse(outlet, str(r.status_code))
        if r.status_code == 304:
            return None
        return r

    def remember(self, url: str, response) -> None:
        """Store a feed's validators for the next conditional get; called once its entries are all read (a
        stream stopped part-way keeps the old ones, so the unread entries are not hidden behind a 304)."""
        if self.store and 200 <= response.status_code < 300:
            self.store.set_feed_cache(url, response.headers.get("ETag"), response.headers.get("Last-Modified"))

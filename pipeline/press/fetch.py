"""Polite HTTP for the press pipeline: the one place the politeness rules live.

One request at a time per host and per outlet, at least `delay` seconds apart (more when a host's
robots.txt asks for a larger Crawl-delay), robots.txt read each run (see robots), our own
honest user agent. A 401/403/429, from an article or feed or from the outlet's own robots.txt, stops that outlet
for the run and, kept in the store, for every later run of the same UTC day: nothing is asked of it, robots.txt
included, and it is never retried around. An outlet removed after a week of refusals (see store.PressStore) is never
asked. An unreachable robots.txt stops the outlet for the run only. Redirects, robots.txt's own included, are followed by hand (at most five
hops, each paced), each new host with its own robots.txt and delay.
"""
import json
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from pipeline.press.robots import Robots

CONFIG = Path(__file__).resolve().parents[2] / "config" / "press.json"
REFUSING = (401, 403, 429)
REDIRECTS = (301, 302, 303, 307, 308)
MAX_HOPS = 5
TIMEOUT = 30


def load_press_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


class Refused(Exception):
    def __init__(self, outlet: str, reason: str):
        super().__init__(f"{outlet}: {reason}")
        self.outlet, self.reason = outlet, reason


def _path(url: str) -> str:
    """The path and query requests sends for the URL (dot segments resolved, no fragment)."""
    prepared = requests.PreparedRequest()
    prepared.prepare_url(url, None)
    return prepared.path_url


def _robots_text(r) -> str:
    """The file is UTF-8 by definition, whatever charset its header names or leaves out."""
    content = getattr(r, "content", None)
    return r.text if content is None else content.decode("utf-8", "replace")


class RobotsUnavailable(requests.RequestException):
    """A redirect target's host (not the outlet's own) has a robots.txt we cannot use: that one fetch fails."""


class PoliteClient:
    def __init__(self, session, user_agent: str, delay: float, store=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.session, self.user_agent, self.delay = session, user_agent, delay
        self.store, self.clock, self.sleep = store, clock, sleep
        # outlet -> why nothing is asked of it: refused or removed by the store, or refused in this run
        self.refused: dict[str, str] = store.stopped() if store else {}
        self._last: dict[str, float] = {}
        self._last_outlet: dict[str, float] = {}
        self._robots: dict[str, Robots] = {}
        self._blocked: set[str] = set()  # recorded in `refused` but answered None, not raised
        self._foreign_down: dict[str, str] = {}  # redirect targets' hosts whose robots.txt failed this run

    def _refuse(self, outlet: str, reason: str, keep: bool = True):
        self._record(outlet, reason, keep)
        raise Refused(outlet, reason)

    def _record(self, outlet: str, reason: str, keep: bool = True) -> None:
        self.refused[outlet] = reason
        if keep and self.store:
            self.store.refuse(outlet, reason)

    def _wait(self, outlet: str, host: str, delay: float) -> None:
        """Sleep until the later of the host's deadline (its own delay) and the outlet's (the configured one)."""
        deadlines = [self._last[host] + delay] if host in self._last else []
        if outlet in self._last_outlet:
            deadlines.append(self._last_outlet[outlet] + self.delay)
        if deadlines:
            gap = max(deadlines) - self.clock()
            if gap > 0:
                self.sleep(gap)
        self._last[host] = self._last_outlet[outlet] = self.clock()

    def _request(self, outlet: str, host: str, url: str, delay: float, headers: dict | None = None):
        self._wait(outlet, host, delay)
        h = {"User-Agent": self.user_agent, **(headers or {})}
        try:
            return self.session.get(url, headers=h, timeout=TIMEOUT, allow_redirects=False)
        finally:
            self._last[host] = self._last_outlet[outlet] = self.clock()

    def _robots_txt(self, outlet: str, scheme: str, host: str):
        """The robots.txt response, its redirects followed by hand and paced; None after MAX_HOPS redirects."""
        url = f"{scheme}://{host}/robots.txt"
        for _ in range(MAX_HOPS + 1):
            r = self._request(outlet, urlsplit(url).netloc, url, self.delay)
            location = r.headers.get("Location") if r.status_code in REDIRECTS else None
            if not location:
                return r
            url = urljoin(url, location)
        return None

    def _rules(self, outlet: str, scheme: str, host: str, foreign: bool = False) -> Robots:
        """The host's robots.txt rules. On the outlet's own host, trouble refuses or blocks the outlet; on a
        redirect target's host (`foreign`), it only fails this fetch (RobotsUnavailable, and the host is not asked
        again this run), leaving the outlet alone."""
        if foreign and host in self._foreign_down:
            raise RobotsUnavailable(f"{host}: {self._foreign_down[host]}")
        if host in self._robots:
            return self._robots[host]
        try:
            r = self._robots_txt(outlet, scheme, host)
        except Exception:
            r = None
        code = r.status_code if r is not None else None
        trouble = ("robots unreachable" if code is None or code >= 500
                   else f"robots {code}" if code in REFUSING else None)
        if trouble and foreign:
            self._foreign_down[host] = trouble
            raise RobotsUnavailable(f"{host}: {trouble}")
        if code == 429 or trouble == "robots unreachable":
            self._refuse(outlet, trouble, keep=code == 429)
        if code in (401, 403):
            rp = Robots("User-agent: *\nDisallow: /", self.user_agent)
            self._record(outlet, trouble)
            self._blocked.add(outlet)
        elif code >= 400:
            rp = Robots("", self.user_agent)
        else:
            rp = Robots(_robots_text(r), self.user_agent)
        self._robots[host] = rp
        return rp

    def get(self, outlet: str, url: str, conditional: bool = False):
        """Response, or None (disallowed by robots.txt, the outlet's robots.txt refused us on one of its own
        hosts this run, or 304 on a conditional get)."""
        if outlet in self._blocked:
            return None
        if self.store:  # another process may have been refused since
            self.refused = self.store.stopped() | self.refused
        if outlet in self.refused:
            raise Refused(outlet, self.refused[outlet])
        headers = {}
        if conditional and self.store:
            etag, modified = self.store.feed_cache(url)
            if etag:
                headers["If-None-Match"] = etag
            if modified:
                headers["If-Modified-Since"] = modified
        target, home = url, urlsplit(url).netloc
        for _ in range(MAX_HOPS + 1):
            parts = urlsplit(target)
            rp = self._rules(outlet, parts.scheme, parts.netloc, foreign=parts.netloc != home)
            if not rp.allowed(_path(target)):
                return None
            delay = max(self.delay, rp.crawl_delay)
            r = self._request(outlet, parts.netloc, target, delay, headers)
            location = r.headers.get("Location") if r.status_code in REDIRECTS else None
            if not location:
                break
            target = urljoin(target, location)
        else:
            raise requests.TooManyRedirects(f"{url}: more than {MAX_HOPS} redirects")
        if r.status_code in REFUSING:
            self._refuse(outlet, str(r.status_code))
        if self.store and (200 <= r.status_code < 300 or r.status_code == 304):
            self.store.succeeded(outlet)
        if r.status_code == 304:
            return None
        return r

    def remember(self, url: str, response) -> None:
        """Store a feed's validators for the next conditional get; called once its entries are all read (a
        stream stopped part-way keeps the old ones, so the unread entries are not hidden behind a 304)."""
        if self.store and 200 <= response.status_code < 300:
            self.store.set_feed_cache(url, response.headers.get("ETag"), response.headers.get("Last-Modified"))

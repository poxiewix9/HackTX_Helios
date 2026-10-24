"""Edge -> base-station link: bounded queue + sender thread over WebSocket with backoff.

Privacy guard: the destination must be on the local network (loopback, RFC1918,
link-local, `.local`, or a bare compose service name). Anything else is refused unless
`ARCO_ALLOW_REMOTE_BASE=1` is set explicitly — and even then only feature packets (never
pixels) are sent, see `arco_edge.packet`.
"""

from __future__ import annotations

import contextlib
import ipaddress
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from websockets.sync.client import connect

from arco_edge.logging import get_logger

log = get_logger("edge.client")


class RemoteDestinationError(ValueError):
    pass


def is_local_host(host: str) -> bool:
    """True for loopback / private / link-local addresses, `*.local`, and dot-less names
    (Docker compose service names resolve on the compose network only)."""
    h = host.strip("[]").lower()
    if h in {"localhost", "host.docker.internal"} or h.endswith(".local") or "." not in h:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(h))
        except OSError:
            return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def assert_local_destination(url: str, *, allow_remote: bool = False) -> None:
    host = urlparse(url).hostname or ""
    if not host:
        raise RemoteDestinationError(f"base station URL has no host: {url!r}")
    if not is_local_host(host) and not allow_remote:
        raise RemoteDestinationError(
            f"refusing to stream features to non-local host {host!r}; "
            "set ARCO_ALLOW_REMOTE_BASE=1 to override (raw frames are never sent either way)"
        )


class Link(Protocol):
    def send(self, frame: bytes) -> bool: ...

    def close(self) -> None: ...

    @property
    def dropped(self) -> int: ...

    @property
    def sent_bytes(self) -> int: ...


@dataclass
class LinkStats:
    sent: int = 0
    sent_bytes: int = 0
    dropped: int = 0
    reconnects: int = 0
    last_error: str | None = None
    send_ms: list[float] = field(default_factory=list)


class WebSocketLink:
    """Non-blocking `send()`: frames go to a bounded queue; a thread ships them. When the
    queue is full the OLDEST frame is dropped (fresh features beat stale ones) and the
    drop is counted — never hidden."""

    def __init__(
        self,
        url: str,
        *,
        queue_size: int = 4,
        allow_remote: bool = False,
        connect_timeout_s: float = 5.0,
    ) -> None:
        assert_local_destination(url, allow_remote=allow_remote)
        self.url = url
        self.stats = LinkStats()
        self._q: queue.Queue[bytes | None] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._timeout = connect_timeout_s
        self._thread = threading.Thread(target=self._run, name="edge-link", daemon=True)
        self._thread.start()

    @property
    def dropped(self) -> int:
        return self.stats.dropped

    @property
    def sent_bytes(self) -> int:
        return self.stats.sent_bytes

    def send(self, frame: bytes) -> bool:
        try:
            self._q.put_nowait(frame)
            return True
        except queue.Full:
            try:
                self._q.get_nowait()
                self.stats.dropped += 1
            except queue.Empty:
                pass
            self._q.put_nowait(frame)
            return False

    def _run(self) -> None:
        backoff = 0.25
        while not self._stop.is_set():
            try:
                with connect(self.url, open_timeout=self._timeout, max_size=64 * 1024 * 1024) as ws:
                    backoff = 0.25
                    while not self._stop.is_set():
                        item = self._q.get()
                        if item is None:
                            return
                        t0 = time.perf_counter()
                        ws.send(item)
                        self.stats.send_ms.append((time.perf_counter() - t0) * 1000.0)
                        if len(self.stats.send_ms) > 256:
                            del self.stats.send_ms[:-256]
                        self.stats.sent += 1
                        self.stats.sent_bytes += len(item)
            except Exception as exc:  # connection errors: back off, keep draining later
                self.stats.last_error = str(exc)
                self.stats.reconnects += 1
                log.warning("link error; reconnecting", url=self.url, error=str(exc), in_s=backoff)
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 5.0)

    def close(self, drain_s: float = 2.0) -> None:
        deadline = time.monotonic() + drain_s
        while not self._q.empty() and time.monotonic() < deadline:
            time.sleep(0.02)
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self._q.put_nowait(None)
        self._thread.join(timeout=drain_s)


class RecordingLink:
    """In-process stand-in used by tests: keeps every frame, nothing crosses a socket."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    @property
    def dropped(self) -> int:
        return 0

    @property
    def sent_bytes(self) -> int:
        return sum(len(f) for f in self.frames)

    def send(self, frame: bytes) -> bool:
        self.frames.append(frame)
        return True

    def close(self) -> None:
        return None

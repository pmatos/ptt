"""Best-effort wait for DNS/network readiness before a scheduled run.

A ``Persistent=true`` systemd timer fires the instant the machine resumes from
suspend — often before the resolver is back up. Without a gate, every git clone
and the SMTP summary then fail with "Could not resolve host", so the run reports
all-failed and no email arrives. ``wait_online`` blocks until a host resolves or
a timeout elapses; it never *guarantees* connectivity, it only rides out the
obvious resume race. Callers treat a False return as "proceed anyway" — the gate
must never make a run that would have worked fail instead."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable

DEFAULT_HOST = "github.com"
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_INTERVAL_S = 3.0


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def wait_online(
    host: str = DEFAULT_HOST,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    interval_s: float = DEFAULT_INTERVAL_S,
    *,
    resolves: Callable[[str], bool] = _resolves,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """Return True as soon as *host* resolves; keep probing every *interval_s*
    until *timeout_s* has elapsed, then return False. The resolver/clock/sleep
    are injectable so the retry logic is unit-testable without real DNS."""
    deadline = monotonic() + timeout_s
    while True:
        if resolves(host):
            return True
        if monotonic() >= deadline:
            return False
        sleep(interval_s)

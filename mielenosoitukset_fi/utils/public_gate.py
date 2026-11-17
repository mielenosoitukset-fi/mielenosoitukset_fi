"""Lockdown gate with a defined policy for failed status reads.

Same rule as the Go API and the React host (see docs/decisions.md):

* A read that says "locked" applies at once and is sticky; a failed read never
  reopens a site that was locked.
* After a read that said "not locked", a failed read keeps that answer for
  ``PUBLIC_GATE_STALE_SECONDS`` (default 15 minutes), then the gate closes.
* A process without any successful answer (restart during a database outage)
  stays open, unless the local emergency override says closed:
  ``PUBLIC_LOCKDOWN_OVERRIDE=closed`` or the file named by
  ``PUBLIC_LOCKDOWN_OVERRIDE_FILE`` (default
  ``/etc/mielenosoitukset/lockdown-override``) exists.
"""

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

DEFAULT_OVERRIDE_FILE = "/etc/mielenosoitukset/lockdown-override"


def stale_window():
    try:
        seconds = int(os.environ.get("PUBLIC_GATE_STALE_SECONDS", ""))
    except ValueError:
        seconds = 0
    return seconds if seconds > 0 else 900


def lockdown_override():
    if os.environ.get("PUBLIC_LOCKDOWN_OVERRIDE") == "closed":
        return True
    return os.path.exists(os.environ.get("PUBLIC_LOCKDOWN_OVERRIDE_FILE") or DEFAULT_OVERRIDE_FILE)


class PublicGate:
    def __init__(self, read, clock=time.monotonic, override=lockdown_override):
        self._read, self._clock, self._override = read, clock, override
        self._lock = threading.Lock()
        self._answered = False
        self._ever_locked = False
        self._last_open = 0.0
        self.failures = 0
        self._last_log = float("-inf")

    def locked(self):
        """True when public pages must be closed. ``read`` returns True if locked."""
        try:
            value = bool(self._read())
        except Exception as exc:  # timeout, connection error
            now = self._clock()
            with self._lock:
                self.failures += 1
                if now - self._last_log >= 30:
                    self._last_log = now
                    log.error("public gate: lockdown status read failed (%d since start, answered=%s, locked=%s): %s",
                              self.failures, self._answered, self._ever_locked, exc)
                if self._ever_locked:
                    return True
                if not self._answered:
                    return self._override()
                return now - self._last_open > stale_window()
        with self._lock:
            self._answered = True
            self._ever_locked = value
            self._last_open = 0.0 if value else self._clock()
        return value

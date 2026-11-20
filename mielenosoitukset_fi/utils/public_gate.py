"""Lockdown gate with a defined policy for failed status reads.

Same rule as the Go API and the React host (see docs/decisions.md):

* A read that says "locked" applies at once and is sticky; a failed read never
  reopens a site that was locked.
* After a read that said "not locked", a failed read keeps that answer for
  ``PUBLIC_GATE_STALE_SECONDS`` (default 15 minutes), then the gate closes.
* A process without any successful answer (restart during a database outage)
  stays open. The local emergency override closes the gate on every failed read:
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
    """``read`` returns True if locked. One read per second, shared by callers."""

    def __init__(self, read, clock=time.monotonic, override=lockdown_override):
        self._read, self._clock, self._override = read, clock, override
        self._lock = threading.Lock()
        self._answered = False
        self._ever_locked = False
        self._last_open = 0.0
        self._checked = float("-inf")
        self._failed = False
        self.failures = 0
        self._last_log = float("-inf")

    def locked(self):
        with self._lock:
            now = self._clock()
            if now - self._checked >= 1:
                try:
                    value = bool(self._read())
                except Exception as exc:  # timeout, connection error
                    self._failed = True
                    self.failures += 1
                    if now - self._last_log >= 30:
                        self._last_log = now
                        log.error("public gate: lockdown status read failed (%d since start, answered=%s, locked=%s): %s",
                                  self.failures, self._answered, self._ever_locked, exc)
                else:
                    self._failed = False
                    self._answered = True
                    self._ever_locked = value
                    self._last_open = 0.0 if value else now
                self._checked = self._clock()
            if not self._failed:
                return self._ever_locked
            if self._override() or self._ever_locked:
                return True
            if not self._answered:
                return False
            return self._clock() - self._last_open > stale_window()

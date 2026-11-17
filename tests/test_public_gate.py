from mielenosoitukset_fi.utils.public_gate import PublicGate, stale_window


class Env:
    def __init__(self):
        self.t = 1000.0
        self.locked = False
        self.fail = False
        self.override = False

    def read(self):
        if self.fail:
            raise TimeoutError("database unavailable")
        return self.locked

    def gate(self):
        return PublicGate(self.read, clock=lambda: self.t, override=lambda: self.override)


def test_read_fails_after_open_stays_open_within_window_then_closes():
    env = Env()
    gate = env.gate()
    assert gate.locked() is False
    env.fail = True
    env.t += 2
    assert gate.locked() is False
    env.t += stale_window() - 5
    assert gate.locked() is False
    env.t += 10
    assert gate.locked() is True
    env.fail = False
    assert gate.locked() is False


def test_read_fails_after_locked_stays_locked():
    env = Env()
    gate = env.gate()
    assert gate.locked() is False
    env.locked = True
    assert gate.locked() is True, "real lockdown applies at once"
    env.fail = True
    for _ in range(3):
        env.t += stale_window()
        assert gate.locked() is True
    env.fail, env.locked = False, False
    assert gate.locked() is False


def test_restart_during_outage_stays_open_unless_override():
    env = Env()
    env.fail = True
    gate = env.gate()
    assert gate.locked() is False
    env.t += 3600
    assert gate.locked() is False
    env.override = True
    assert gate.locked() is True
    env.fail = False
    assert gate.locked() is False
    assert gate.failures == 3


def test_stale_window_configurable(monkeypatch):
    monkeypatch.delenv("PUBLIC_GATE_STALE_SECONDS", raising=False)
    assert stale_window() == 900
    monkeypatch.setenv("PUBLIC_GATE_STALE_SECONDS", "60")
    assert stale_window() == 60
    monkeypatch.setenv("PUBLIC_GATE_STALE_SECONDS", "junk")
    assert stale_window() == 900

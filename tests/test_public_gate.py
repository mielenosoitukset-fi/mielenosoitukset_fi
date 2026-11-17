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

    def check(self, gate, step=2):
        self.t += step
        return gate.locked()

    def gate(self):
        return PublicGate(self.read, clock=lambda: self.t, override=lambda: self.override)


def test_read_fails_after_open_stays_open_within_window_then_closes():
    env = Env()
    gate = env.gate()
    assert env.check(gate) is False
    env.fail = True
    assert env.check(gate) is False
    env.t += stale_window() - 20
    assert env.check(gate) is False
    env.t += 10
    assert env.check(gate) is True
    env.fail = False
    assert env.check(gate) is False


def test_read_fails_after_locked_stays_locked():
    env = Env()
    gate = env.gate()
    assert env.check(gate) is False
    env.locked = True
    assert env.check(gate) is True, "real lockdown applies at once"
    env.fail = True
    for _ in range(3):
        env.t += stale_window()
        assert env.check(gate) is True
    env.fail, env.locked = False, False
    assert env.check(gate) is False


def test_restart_during_outage_stays_open_unless_override():
    env = Env()
    env.fail = True
    gate = env.gate()
    assert env.check(gate) is False
    env.t += 3600
    assert env.check(gate) is False
    env.override = True
    assert env.check(gate) is True
    env.fail = False
    assert env.check(gate) is False
    assert gate.failures == 3


def test_stale_window_configurable(monkeypatch):
    monkeypatch.delenv("PUBLIC_GATE_STALE_SECONDS", raising=False)
    assert stale_window() == 900
    monkeypatch.setenv("PUBLIC_GATE_STALE_SECONDS", "60")
    assert stale_window() == 60
    monkeypatch.setenv("PUBLIC_GATE_STALE_SECONDS", "junk")
    assert stale_window() == 900


def test_cache_reads_once_per_second():
    env = Env()
    calls = []
    gate = PublicGate(lambda: calls.append(1) or False, clock=lambda: env.t, override=lambda: False)
    for _ in range(5):
        assert gate.locked() is False
    assert len(calls) == 1
    env.t += 1
    gate.locked()
    assert len(calls) == 2


def test_override_closes_after_open_answer_when_read_fails():
    env = Env()
    gate = env.gate()
    assert env.check(gate) is False
    env.fail = True
    assert env.check(gate) is False
    env.override = True
    assert env.check(gate) is True
    env.fail, env.override = False, True
    assert env.check(gate) is False, "a successful open read wins over the override"

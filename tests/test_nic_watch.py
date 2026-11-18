import importlib.util
import os

SPEC = importlib.util.spec_from_file_location(
    "nic_watch", os.path.join(os.path.dirname(__file__), "..", "deploy", "monitoring", "nic-watch.py")
)
nic_watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nic_watch)

METRICS = (
    'node_cpu_seconds_total{cpu="0",mode="softirq"} 10.5\n'
    'node_cpu_seconds_total{cpu="1",mode="softirq"} 200\n'
    'node_cpu_seconds_total{cpu="1",mode="idle"} 999\n'
)


def test_parse_softirq_keeps_only_softirq_rows():
    assert nic_watch.parse_softirq(METRICS) == {"0": 10.5, "1": 200.0}


def test_hot_cpu_needs_a_full_window_and_a_high_share():
    samples = [(0, {"0": 0.0, "1": 0.0}), (150, {"0": 5.0, "1": 130.0}), (300, {"0": 10.0, "1": 255.0})]
    assert nic_watch.hot_cpus(samples, 300, 300, 0.8) == ["1"]
    assert nic_watch.hot_cpus(samples[1:], 300, 300, 0.8) == []  # history shorter than the window


def test_kernel_hit_matches_both_faults():
    assert nic_watch.kernel_hit("WARNING: NETDEV WATCHDOG: eth0 (virtio_net): transmit queue 0 timed out")
    assert nic_watch.kernel_hit("virtio_net virtio0 eth0: TX timeout on queue: 0")
    assert not nic_watch.kernel_hit("eth0: link up")


def test_missing_recipient_is_a_failure_so_no_cooldown_starts():
    assert nic_watch.send_alert(object(), "x", "y", {}) is False


def test_url_must_be_plain_http(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        nic_watch.main(["--mode", "softirq", "--url", "file:///etc/passwd", "--state", str(tmp_path / "s.json")], env={})

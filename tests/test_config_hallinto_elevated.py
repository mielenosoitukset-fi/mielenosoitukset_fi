"""HALLINTO_ELEVATED_ENABLED must be read from config.yaml like the other Hallinto switches."""
import re
from pathlib import Path


def test_elevated_flag_is_loaded_from_config_yaml():
    source = Path(__file__).resolve().parents[1].joinpath("config.py").read_text(encoding="utf-8")
    assert re.search(r'cls\.HALLINTO_ELEVATED_ENABLED = config\.get\("HALLINTO_ELEVATED_ENABLED", False\)', source)

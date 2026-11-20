from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_settings_write_posts_route_to_go_only_with_a_release_check():
    lines = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text().splitlines()
    for path in ("profile", "settings", "deletion-request"):
        rule = f'RewriteRule "^/api/account/{path}$" "http://127.0.0.1:8091$0" [P,L]'
        index = [i for i, line in enumerate(lines) if line == rule]
        assert index, rule
        assert lines[index[-1] - 1] == 'RewriteCond %{REQUEST_METHOD} "^POST$"'
    release = (ROOT / "deploy/release_routing.sh").read_text()
    assert 'RewriteRule "\\^/api/account/profile\\$" "http://127.0.0.1:8091' in release
    assert "http://127.0.0.1:8091/api/account/profile || true) == 405" in release
    assert release.index("settings_writes_ready") < release.index('install -d -m 0700 "$backup_root"')

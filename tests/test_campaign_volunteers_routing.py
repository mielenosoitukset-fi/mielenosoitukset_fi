from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_volunteer_signup_post_routes_to_go_only_with_a_release_check():
    include = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text()
    rule = 'RewriteRule "^/kampanja/api/volunteers$" "http://127.0.0.1:8091/kampanja/api/volunteers" [P,L]'
    lines = include.splitlines()
    index = lines.index(rule)
    assert lines[index - 1] == 'RewriteCond %{REQUEST_METHOD} "^POST$"'
    # The confirm link and other campaign paths stay in Flask.
    assert "/kampanja/confirm" not in include

    release = (ROOT / "deploy/release_routing.sh").read_text()
    assert 'RewriteRule "\\^/kampanja/api/volunteers\\$" "http://127.0.0.1:8091/' in release
    assert "http://127.0.0.1:8091/kampanja/api/volunteers || true) == 405" in release
    assert release.index("volunteers_ready") < release.index('install -d -m 0700 "$backup_root"')

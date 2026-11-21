from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_volunteer_signup_post_routes_to_go_only_with_a_release_check():
    include = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text()
    rule = 'RewriteRule "^/kampanja/api/volunteers$" "http://127.0.0.1:8091/kampanja/api/volunteers" [P,L]'
    lines = include.splitlines()
    index = lines.index(rule)
    assert lines[index - 1] == 'RewriteCond %{REQUEST_METHOD} "^POST$"'
    # The confirm link goes to the React page on GET/HEAD only, and only the
    # confirm POST goes to Go (activated 21 November 2026,
    # docs/volunteer-confirm-react.md). Other campaign paths stay in Flask.
    active = [line for line in lines if not line.lstrip().startswith("#")]
    confirm = [line for line in active if "/kampanja/confirm" in line]
    assert confirm == ['RewriteRule "^/kampanja/confirm/[A-Za-z0-9_-]{1,128}$" "http://127.0.0.1:8090$0" [P,L]']
    assert active[active.index(confirm[0]) - 1] == 'RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"'
    post = 'RewriteRule "^/api/campaign-volunteers/confirm$" "http://127.0.0.1:8091$0" [P,L]'
    assert active[active.index(post) - 1] == 'RewriteCond %{REQUEST_METHOD} "^POST$"'

    release = (ROOT / "deploy/release_routing.sh").read_text()
    assert 'RewriteRule "\\^/kampanja/api/volunteers\\$" "http://127.0.0.1:8091/' in release
    assert "http://127.0.0.1:8091/kampanja/api/volunteers || true) == 405" in release
    assert release.index("volunteers_ready") < release.index('install -d -m 0700 "$backup_root"')
    assert "8091/api/campaign-volunteers/confirm || true) == 405" in release
    assert release.index("volunteer_confirm_ready") < release.index('install -d -m 0700 "$backup_root"')

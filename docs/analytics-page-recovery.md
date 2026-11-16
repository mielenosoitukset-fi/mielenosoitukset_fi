# Recovering page identity from Apache logs

Generic pageviews previously stored `page_type=page` without `resource_id`.
Those counters cannot identify which page was opened. Known fixed public pages
now retain a bounded route ID; unknown paths remain anonymous because they can
contain private tokens. Campaign pages already have a separate classification.

Apache logs provide a second source for older traffic. The admin dashboard
shows these counts separately as **Apache-lokeista palautetut sivupyynnöt**.
They are successful origin GET requests, not visitor counts or reconstructed
analytics pageviews. Browser DNT/prefetch information is absent; caches and
React navigation can omit origin requests. The same known-bot filter is used,
but it cannot identify all automation. Never subtract or add these counts to
the existing pageview counters.

## Preview and apply

Run from a reviewed production checkout with its normal configuration and
Python dependencies. No service restart or routing change is needed. Use the
single site's access log and its rotations exactly once, not API/HTTP redirect
logs or duplicated archive copies. Both combined logs and combined logs with
an origin suffix are accepted; malformed records stop the import. Select
completed Helsinki days within the last 90 days. The first/last retained day
may be partial due to rotation; the dashboard displays observed log coverage.

Example (dates deliberately fixed for the original investigation):

```sh
install -d -m 0700 /root/moso-analytics-recovery-20261116
CONFIG_YAML_PATH=/var/www/mielenosoitukset_fi/config.yaml python3 -m \
  mielenosoitukset_fi.scripts.recover_analytics_pages \
  /var/log/apache2/mielenosoitukset_access.log* \
  --start 2026-11-02 --end 2026-11-15 \
  --output /root/moso-analytics-recovery-20261116/preview.json
```

Inspect the aggregate output before repeating the command with:

```sh
  --apply --backup /root/moso-analytics-recovery-20261116/before.json
```

The backup must be a new file. Apply atomically replaces one snapshot in
`site_analytics_log_recovery`; rerunning does not add duplicate counts. A
concurrent writer causes a conflict. The snapshot contains daily counts for
allowlisted paths and coverage metadata only. IPs, agents, referrers, query
strings and arbitrary paths are neither exported nor imported. Nothing writes
to `site_analytics` or `site_analytics_visitors`; no new index is required.

## Rollback

Keep imports serialized while restoring. With the same configuration and
working directory, restore the backed-up single document:

```python
from pathlib import Path
from bson import json_util
from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.analytics_log_recovery import COLLECTION, SNAPSHOT_ID

previous = json_util.loads(Path('/root/moso-analytics-recovery-20261116/before.json').read_text())
collection = DatabaseManager.get_instance().get_db()[COLLECTION]
if previous is None:
    collection.delete_one({'_id': SNAPSHOT_ID})
else:
    collection.replace_one({'_id': SNAPSHOT_ID}, previous, upsert=True)
```

## Investigation status

On 16 November 2026, a read-only listing confirmed access-log rotations dating
back roughly two weeks. SSH subsequently became unavailable, so no successful
preview/import was confirmed. The maintainer then prohibited further server
connections. This PR supplies the recovery tool and report, not a claim that
production history has been recovered. There is no automatic production import.

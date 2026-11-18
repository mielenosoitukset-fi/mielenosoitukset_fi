# Network saturation alerts for the database host

On 17 November 2026 the database host's single-queue virtio NIC pinned one CPU
core in softirq and the site answered 503, while overall CPU looked calm. These
two checks mail the maintainer early. Nothing here is installed until the
maintainer says go. Hostnames and addresses live only in
`/etc/mielenosoitukset/nic-watch.env` on the servers, never in the repository.

## Checks

- **softirq** (`nic-watch.py --mode softirq`): reads the database host's
  node-exporter `/metrics`, once a minute, from a host that can reach it (lc-main).
  Alerts when one CPU spent more than 80 % of the last 5 minutes in softirq.
  Nothing alerts until 5 minutes of samples exist.
- **kernel** (`nic-watch.py --mode kernel`): runs on the watched host every 5
  minutes and alerts on `NETDEV WATCHDOG` or `virtio_net ... TX timeout` in the
  last 6 minutes of the kernel log.

Both send at most one mail per 6 hours per check, through the SMTP settings and
`ALERT_EMAIL` the backup alerts already use ([backups.md](backups.md)). The mail
names the host and the check only. If the watched host has no app config, the
kernel check falls back to the local `sendmail`; if neither works the unit fails
and the journal says why.

## Install (on go)

`/etc/mielenosoitukset/nic-watch.env`, mode 0600:

```
NIC_WATCH_LABEL=lc-db
NIC_WATCH_URL=http://<db-host>:9100/metrics
```

Copy the four `mielenosoitukset-nic-watch-*` units from `deploy/monitoring/` to
`/etc/systemd/system/`. On the app host enable `...-softirq.timer`. On the
database host copy `deploy/monitoring/nic-watch.py` and
`deploy/backup/backup-notify.py` to `/opt/mielenosoitukset-monitoring/deploy/...`
(same relative layout), then enable `...-kernel.timer`. Test with
`systemctl start mielenosoitukset-nic-watch-softirq.service` and check the journal.

## Revert

`systemctl disable --now` both timers and delete the unit files, the env file and
`/var/lib/mielenosoitukset/nic-watch.json`.

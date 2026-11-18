# Network saturation alerts for the database host

On 17 November 2026 the database host's single-queue virtio NIC pinned one CPU
core in softirq and the site answered 503, while overall CPU looked calm. These
two checks mail the maintainer early. Nothing here is installed until the
maintainer says go. Hostnames and addresses live only in
`/etc/mielenosoitukset/nic-watch.env` on the servers, never in the repository.

## Checks

- **softirq** (`nic-watch.py --mode softirq`): reads node-exporter's
  `/metrics` once a minute. It runs on the watched host against
  `http://127.0.0.1:9100/metrics`, because node-exporter listens on localhost only.
  Alerts when one CPU spent more than 80 % of the last 5 minutes in softirq.
  Nothing alerts until 5 minutes of samples exist.
- **kernel** (`nic-watch.py --mode kernel`): runs on the watched host every 5
  minutes and alerts on `NETDEV WATCHDOG` or `virtio_net ... TX timeout` in the
  last 6 minutes of the kernel log.

Both send at most one mail per 6 hours per check, through the SMTP settings and
`ALERT_EMAIL` the backup alerts use ([backups.md](backups.md)). The mail
names the host and the check only. If the watched host has no app config (the
database host has none), both checks use the local `sendmail`, with `ALERT_EMAIL`
set in `nic-watch.env`; if neither works the unit fails
and the journal says why.

## Other nightly load on the database VM

The hypervisor's own VM backup job (snapshot mode, all VMs, zstd) starts at 02:30
Helsinki time and took about 4.5 hours on 18 November 2026: the database VM's
16 GB image finished around 07:05. It overlaps the nightly database dump
(03:30), and a VM shutdown is refused while it runs. It reads the disk on the
hypervisor, not through the VM's network card, so it is not proven to be the
cause of the stalls; the alerts above show whether a stall coincides with it.
The job has no bandwidth limit set. Changes to it are a separate decision.

## Install (on go)

Before installing, check `python3 -c 'import yaml'` and `command -v sendmail` on the
watched host. Create `/etc/mielenosoitukset/nic-watch.env` with mode 0600:

```
NIC_WATCH_LABEL=lc-db
NIC_WATCH_URL=http://127.0.0.1:9100/metrics
ALERT_EMAIL=<maintainer address>
```

On the watched host copy `deploy/monitoring/nic-watch.py` and
`deploy/backup/backup-notify.py` to `/opt/mielenosoitukset-monitoring/deploy/...`
(same relative layout), copy the four `mielenosoitukset-nic-watch-*` units from
`deploy/monitoring/` to `/etc/systemd/system/`, then enable both timers. Test with
`systemctl start mielenosoitukset-nic-watch-softirq.service` and check the journal. The helper needs `python3-yaml` on the watched host.

## Revert

`systemctl disable --now` both timers and delete the unit files, the env file and
`/var/lib/mielenosoitukset/nic-watch.json`.

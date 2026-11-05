# Operational alerts

System health now shows active queue, worker lease, recent job failure, backup, retention and local-disk alerts.
They are computed from current persistent state, not a worker's process-local counter. Resolved conditions
disappear on the next refresh. These alerts do not automatically approve data or bypass human review.

For server notifications, mount `alerts.yml` and use `prometheus.example.yml` with your existing Prometheus
on the private API network. Configure the organisation's Alertmanager recipient and routing there; the
repository contains no credentials or preselected external recipients. No email/chat messages are sent by
these files themselves. Scrape every API replica through service discovery when running multiple hosts.

Thresholds: queued more than 15 minutes; running job with an expired lease; job failures within 15 minutes;
production backup older than 26 hours or absent; backup without objects; retention errors; local app disk
below the larger of 1 GiB and 10%. Local disk is the API/cache filesystem, not remote MinIO capacity.
Use node/container exporters on the server for physical host RAM, Docker limits and remote storage volumes.

Before rollout, validate rules with `promtool check rules alerts.yml`; configure a test receiver and exercise
an isolated stopped worker/API and delayed backup. A page warning or a valid YAML file alone does not prove
that an external recipient will receive a notification.

For an expired lease: inspect worker logs and recovery, then retry the same job. Do not upload duplicate files
as a workaround. For commit failures: use the response request ID, inspect DB connectivity, reload current
data before retrying. For a backup alert: create a full backup and restore it into a fresh isolated database
and storage; verify downloads and checksums before treating recovery as operational.

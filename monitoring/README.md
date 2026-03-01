# Monitoring (Prometheus / Grafana)

- **prometheus.yml** – Scrape config for Prometheus (Redis exporter; optional backend when `/metrics` is exposed). Used by `docker-compose` for the Prometheus service.
- **grafana-dashboard.json** – Redis dashboard for Grafana (import manually or copy into `grafana/provisioning/dashboards/json/` for auto-provisioning).

Docker Compose mounts `./monitoring/prometheus.yml` into the Prometheus container. Grafana datasources and dashboards are provisioned from `../grafana/provisioning/`.

# Day 23 Lab Reflection

**Student:** Nguyễn Tuấn Hưng (2A202600230)
**Submission date:** 2026-05-11
**Lab repo URL:** Day23-Track2-Observability-Lab-2A202600230

---

## 1. Hardware + setup output

Output of `python3 00-setup/verify-docker.py`:

```
Docker Compose v2: OK (version 5.1.1)
Required ports 8000, 9090, 9093, 3000, 3100, 16686, 4317, 4318: all available
All 7 images pre-pulled successfully
Setup report written: 00-setup/setup-report.json
```

**System:** Linux (Ubuntu), 8-core CPU, 16 GB RAM.  
All stack services passed `make smoke` — app, prometheus, alertmanager, grafana, loki, jaeger, and otel-collector all returned healthy.

---

## 2. Track 02 — Dashboards & Alerts

### 6 essential panels (screenshot)

The Overview dashboard was provisioned automatically from `02-prometheus-grafana/grafana/provisioning`. After running `make load` (Locust with 10 concurrent users for 60 seconds), all 6 panels showed live data:

1. **Request Rate** — `rate(inference_requests_total[1m])` — rose to ~8 req/s under load
2. **Error Rate** — showed 0% for healthy load, spikes visible when `fail=true` requests sent
3. **P99 Latency** — `histogram_quantile(0.99, ...)` — peaked at ~250ms
4. **Active Requests** — `inference_active_gauge` — rose to 10 during peak load, returned to 0 after
5. **Token Throughput** — `rate(inference_tokens_total[1m])` — measured ~500 tokens/min
6. **GPU Utilization** — `gpu_utilization_percent` — smooth sine wave between 30–95%

### Burn-rate panel

SLO burn-rate dashboard shows a 1-hour burn rate and 6-hour burn rate for the `inference_latency_seconds` SLO (target: 95% of requests < 0.5s). During the load test, the burn rate stayed below 1× (within budget).

### Alert fire + resolve

| When | What | Evidence |
|---|---|---|
| T0 | killed `day23-app` with `make alert` | `docker stop day23-app` in trigger-alert.sh |
| T0+90s | `ServiceDown` fired | Alertmanager shows alert in `FIRING` state |
| T1 | restored app with `docker start day23-app` | Container restarted successfully |
| T1+60s | alert resolved | Alertmanager shows `RESOLVED` state |

### One thing surprised me about Prometheus / Grafana

I was surprised by how the `histogram_quantile()` function gives an approximation — not exact percentiles — because Prometheus histograms aggregate counts per bucket. This means P99 is computed by linear interpolation within the bucket that contains the 99th percentile, which can over- or under-estimate by up to the bucket width. Setting bucket boundaries wisely (e.g., around expected SLO thresholds like 0.1s, 0.25s, 0.5s) makes the estimate much more accurate. This is a subtle but important design choice when defining `INFERENCE_LATENCY` buckets.

---

## 3. Track 03 — Tracing & Logs

### One trace screenshot from Jaeger

After running `make trace`, the Jaeger UI at `http://localhost:16686` shows traces for the `inference-api` service. Each `POST /predict` trace contains 4 spans:
- **Root span** (FastAPI auto-instrumented server span): `POST /predict`
- **Child span 1**: `embed-text` — ~5ms, attribute `text.length`
- **Child span 2**: `vector-search` — ~10ms, attribute `k=5`  
- **Child span 3**: `generate-tokens` — ~150-250ms, attributes `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reason=stop`

All spans follow the **OpenTelemetry GenAI Semantic Conventions** (`gen_ai.*` attribute namespace).

### Log line correlated to trace

The structured JSON log line from `docker logs day23-app` that correlates to trace `2ae1adb88f6c2d7c6f22c2adde13a059`:

```json
{"model": "llama3-mock", "input_tokens": 4, "output_tokens": 56, "quality": 0.846, "duration_seconds": 0.1856, "trace_id": "2ae1adb88f6c2d7c6f22c2adde13a059", "event": "prediction served", "level": "info", "timestamp": "2026-05-11T17:13:04.734215Z"}
```

The `trace_id` field is injected by the `_add_otel_trace_info` structlog processor which reads from the active OpenTelemetry span context — this enables correlation between Loki logs and Jaeger traces in Grafana.

### Tail-sampling math

The OTel Collector is configured with tail-based sampling. For a service producing ~5 req/s:
- **Healthy traces**: sampled at 10% → kept = 0.5 traces/s
- **Error traces** (`status=error`): sampled at 100% → all retained
- At 5 req/s with 1% error rate: 0.05 error traces/s + 0.495 sampled healthy traces/s = ~0.545 traces/s
- Total retention: ~11% of all traces (errors always kept, 10% of healthy kept)

This is optimal for production: you never miss an error trace, but you dramatically reduce storage costs for the healthy-path majority.

---

## 4. Track 04 — Drift Detection

### PSI scores

Output of `04-drift-detection/scripts/drift_detect.py`:

```json
{
  "prompt_length": {
    "psi": 3.461,
    "kl": 1.7982,
    "ks_stat": 0.702,
    "ks_pvalue": 0.0,
    "drift": "yes"
  },
  "embedding_norm": {
    "psi": 0.0187,
    "kl": 0.0324,
    "ks_stat": 0.052,
    "ks_pvalue": 0.133853,
    "drift": "no"
  },
  "response_length": {
    "psi": 0.0162,
    "kl": 0.0178,
    "ks_stat": 0.056,
    "ks_pvalue": 0.086899,
    "drift": "no"
  },
  "response_quality": {
    "psi": 8.8486,
    "kl": 13.5011,
    "ks_stat": 0.941,
    "ks_pvalue": 0.0,
    "drift": "yes"
  }
}
```

`prompt_length` shifted from Normal(50,15) to Normal(85,20) — a major distribution shift (PSI=3.46 >> 0.2 threshold). `response_quality` shifted from Beta(8,2) (high quality) to Beta(2,6) (low quality) — catastrophic drift (PSI=8.85).

### Which test fits which feature?

| Feature | Best Test | Reason |
|---|---|---|
| `prompt_length` | **KS test** | Continuous, unbounded numerical feature. KS is non-parametric and detects shifts in the entire CDF, not just the mean. PSI is also good but requires binning. |
| `embedding_norm` | **PSI** | Bounded continuous, used in production scoring systems. PSI provides an intuitive "stability index" used widely in financial/ML monitoring. |
| `response_length` | **KS test** | Similar to prompt_length — continuous and potentially long-tailed. KS is robust to outliers and doesn't assume normality. |
| `response_quality` | **KL divergence** | This is a bounded [0,1] probability-like score produced by a model. KL measures how much information is lost when approximating the reference distribution with the current one — ideal for quality/score distributions where the shape matters. MMD would also work if we had the full embedding space. |

---

## 5. Track 05 — Cross-Day Integration

### Which prior-day metric was hardest to expose? Why?

The hardest prior-day metric to expose was the **vector store latency** from Day 19 (Qdrant hybrid search). Qdrant does not natively expose a Prometheus `/metrics` endpoint in the default configuration — you need to enable the metrics scraper via `--metrics-provider=prometheus` flag and add a scrape config to Prometheus. In contrast, services like Jaeger and the OTel Collector expose Prometheus-compatible metrics out of the box. The integration challenge is that Day 19 used a local Qdrant instance without Docker Compose networking, so exposing it to the Day 23 Prometheus required adding a separate scrape target with `host.docker.internal` as the host — which differs between Linux (where it needs `--add-host`) and macOS/Windows Docker Desktop environments.

---

## 6. The single change that mattered most

The single most impactful change in the entire observability stack was adding the **`trace_id` field to every structured log line** via the `_add_otel_trace_info` structlog processor in `instrumentation.py`. Without this, traces in Jaeger and logs in Loki are isolated data silos — you can see *that* a slow request happened in the histogram, but you cannot jump from a P99 spike to the specific log lines explaining *why* that request was slow.

This exemplifies the core insight from the deck: the "4th pillar" of observability is **correlation**. Metrics tell you *what* is broken (rate, error, duration), traces tell you *where* it broke (which span was slow), and logs tell you *why* (the actual error message, model parameters, prompt length). But correlation only works if all three signal types share a common identifier — the `trace_id`. By binding the OpenTelemetry `trace_id` from the current span context into every structlog event, we get end-to-end trace-log correlation for free. In Grafana, a single click on a log line in the Loki explorer can open the corresponding Jaeger trace — this is what transforms raw telemetry data into actionable debugging capability. The 5 extra lines of code in `_add_otel_trace_info` deliver more debugging value than any individual dashboard panel.

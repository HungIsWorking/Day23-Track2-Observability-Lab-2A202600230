"""Drift detection on a synthetic AI input dataset.

Reference: 1000 rows of (prompt_length, embedding_norm, response_length, response_quality).
Current:   1000 rows with deliberate shift on prompt_length + response_quality.

Outputs:
  reports/drift-report.html       — Evidently HTML
  reports/drift-summary.json      — { feature: psi, ... }
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent.parent
DATA_DIR = HERE / "data"
REPORTS_DIR = HERE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def synth_dataset(rng: np.random.Generator, *, shifted: bool) -> pd.DataFrame:
    n = 1000
    if shifted:
        prompt_length = rng.normal(loc=85, scale=20, size=n)        # was loc=50
        embedding_norm = rng.normal(loc=1.0, scale=0.1, size=n)      # unchanged
        response_length = rng.normal(loc=120, scale=40, size=n)      # unchanged
        response_quality = rng.beta(2, 6, size=n)                    # was beta(8, 2) = high quality
    else:
        prompt_length = rng.normal(loc=50, scale=15, size=n)
        embedding_norm = rng.normal(loc=1.0, scale=0.1, size=n)
        response_length = rng.normal(loc=120, scale=40, size=n)
        response_quality = rng.beta(8, 2, size=n)
    return pd.DataFrame(
        {
            "prompt_length": prompt_length,
            "embedding_norm": embedding_norm,
            "response_length": response_length,
            "response_quality": response_quality,
        }
    )


def population_stability_index(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    """PSI on a single 1-D feature."""
    edges = np.linspace(min(reference.min(), current.min()), max(reference.max(), current.max()), bins + 1)
    ref_hist, _ = np.histogram(reference, bins=edges)
    cur_hist, _ = np.histogram(current, bins=edges)
    # Smooth zero bins to avoid log(0)
    ref_p = (ref_hist + 1) / (ref_hist.sum() + bins)
    cur_p = (cur_hist + 1) / (cur_hist.sum() + bins)
    return float(np.sum((cur_p - ref_p) * np.log(cur_p / ref_p)))


def kl_divergence(reference: np.ndarray, current: np.ndarray, bins: int = 20) -> float:
    """Discretized KL(P_ref || P_cur)."""
    edges = np.linspace(min(reference.min(), current.min()), max(reference.max(), current.max()), bins + 1)
    ref_hist, _ = np.histogram(reference, bins=edges, density=True)
    cur_hist, _ = np.histogram(current, bins=edges, density=True)
    ref_p = (ref_hist + 1e-9) / (ref_hist.sum() + 1e-9 * bins)
    cur_p = (cur_hist + 1e-9) / (cur_hist.sum() + 1e-9 * bins)
    return float(np.sum(ref_p * np.log(ref_p / cur_p)))


def main() -> int:
    rng = np.random.default_rng(seed=42)
    reference = synth_dataset(rng, shifted=False)
    current = synth_dataset(rng, shifted=True)
    DATA_DIR.mkdir(exist_ok=True)
    reference.to_parquet(DATA_DIR / "reference.parquet")
    current.to_parquet(DATA_DIR / "current.parquet")

    summary: dict[str, dict[str, float]] = {}
    for col in reference.columns:
        ref = reference[col].to_numpy()
        cur = current[col].to_numpy()
        psi = population_stability_index(ref, cur)
        kl = kl_divergence(ref, cur)
        ks_stat, ks_p = stats.ks_2samp(ref, cur)
        summary[col] = {
            "psi": round(psi, 4),
            "kl": round(kl, 4),
            "ks_stat": round(float(ks_stat), 4),
            "ks_pvalue": round(float(ks_p), 6),
            "drift": "yes" if psi > 0.2 else ("moderate" if psi > 0.1 else "no"),
        }

    summary_path = REPORTS_DIR / "drift-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Wrote: {summary_path}")
    for col, m in summary.items():
        print(f"  {col:<20} PSI={m['psi']:.3f}  KL={m['kl']:.3f}  KS={m['ks_stat']:.3f}  drift={m['drift']}")

    html_path = REPORTS_DIR / "drift-report.html"
    try:
        from evidently.report import Report
        from evidently.metric_preset import DataDriftPreset

        report = Report(metrics=[DataDriftPreset()])
        report.run(reference_data=reference, current_data=current)
        report.save_html(str(html_path))
        print(f"Wrote: {html_path}")
    except Exception as exc:
        rows = "\n".join(
            f"<tr><td>{col}</td><td>{m['psi']}</td><td>{m['kl']}</td><td>{m['ks_stat']}</td><td>{m['ks_pvalue']}</td><td>{m['drift']}</td></tr>"
            for col, m in summary.items()
        )
        html_path.write_text(
            """<!doctype html>
<html>
<head><meta charset='utf-8'><title>Drift Report</title></head>
<body>
  <h1>Drift report (fallback)</h1>
  <p>Evidently report generation failed at runtime; fallback summary is shown below.</p>
  <pre>{error}</pre>
  <table border='1' cellpadding='6' cellspacing='0'>
    <thead><tr><th>feature</th><th>psi</th><th>kl</th><th>ks_stat</th><th>ks_pvalue</th><th>drift</th></tr></thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
""".format(error=str(exc), rows=rows)
        )
        print(f"Wrote fallback HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

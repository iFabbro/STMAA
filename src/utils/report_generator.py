from pathlib import Path
import json
import pandas as pd

DEFAULT_THRESHOLDS = {
    "sharpe_ratio": -0.25,
    "sortino_ratio": -0.25,
    "max_drawdown": 0.05,
    "drawdown_duration": 5,
    "total_return": -0.05,
}

def load_thresholds(path):
    path = Path(path)
    if not path.exists():
        return DEFAULT_THRESHOLDS.copy()
    text = path.read_text().strip()
    data = {}
    try:
        if path.suffix.lower() in (".yml", ".yaml"):
            for line in text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    data[k.strip()] = float(v.strip())
        else:
            data = json.loads(text) or {}
    except Exception:
        data = {}
    thresholds = DEFAULT_THRESHOLDS.copy()
    if isinstance(data, dict):
        thresholds.update(data)
    return thresholds

def _delta_line(curr, prev, key):
    if key not in curr or key not in prev:
        return None
    try:
        c = float(curr[key])
        p = float(prev[key])
        d = c - p
        pct = (d / abs(p) * 100.0) if p != 0 else None
        return (key, c, p, d, pct)
    except Exception:
        return None

def _threshold_alerts(current, previous, thresholds):
    alerts = []
    if not previous:
        return alerts
    for key, threshold in thresholds.items():
        d = _delta_line(current, previous, key)
        if not d:
            continue
        metric, c, p, delta, pct = d
        if metric in ("sharpe_ratio", "sortino_ratio", "total_return"):
            if delta < -abs(threshold):
                alerts.append({
                    "metric": metric,
                    "current": round(c, 6),
                    "previous": round(p, 6),
                    "delta": round(delta, 6),
                    "threshold": threshold,
                })
        else:
            if delta > threshold:
                alerts.append({
                    "metric": metric,
                    "current": round(c, 6),
                    "previous": round(p, 6),
                    "delta": round(delta, 6),
                    "threshold": threshold,
                })
    return alerts

def generate_metrics_report(json_path="logs/metrics.json", csv_path="logs/metrics_history.csv", output_path="logs/metrics_report.md", thresholds=None, thresholds_path=None):
    thresholds = thresholds or (load_thresholds(thresholds_path) if thresholds_path else DEFAULT_THRESHOLDS.copy())
    json_p = Path(json_path)
    csv_p = Path(csv_path)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    current = json.loads(json_p.read_text()) if json_p.exists() else {}
    history = pd.read_csv(csv_p) if csv_p.exists() else pd.DataFrame()

    lines = ["# Metrics report", ""]

    if current:
        lines += ["## Current snapshot", "", pd.DataFrame([current]).to_markdown(index=False), ""]

    previous = {}
    if not history.empty:
        previous = history.iloc[-2].to_dict() if len(history) > 1 else {}
        lines += ["## Historical metrics", "", history.tail(10).to_markdown(index=False), ""]
        numeric = history.select_dtypes(include="number")
        summary = numeric.agg(["mean", "min", "max"]).round(4)
        lines += ["## Summary", "", summary.to_markdown(), ""]

        if previous:
            deltas = []
            for key in sorted(set(current.keys()) & set(previous.keys())):
                d = _delta_line(current, previous, key)
                if d:
                    k, c, p, delta, pct = d
                    deltas.append({
                        "metric": k,
                        "current": round(c, 6),
                        "previous": round(p, 6),
                        "delta": round(delta, 6),
                        "delta_pct": None if pct is None else round(pct, 2),
                    })
            if deltas:
                lines += ["## Trend", "", pd.DataFrame(deltas).to_markdown(index=False), ""]

    alerts = _threshold_alerts(current, previous, thresholds)
    if alerts:
        lines += ["## Alerts", "", pd.DataFrame(alerts).to_markdown(index=False), ""]
    elif previous:
        lines += ["## Alerts", "", "No threshold breaches detected.", ""]

    out_p.write_text("\n".join(lines))
    return out_p

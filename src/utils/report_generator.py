from pathlib import Path
import json
import pandas as pd

def generate_metrics_report(json_path="logs/metrics.json", csv_path="logs/metrics_history.csv", output_path="logs/metrics_report.md"):
    json_p = Path(json_path)
    csv_p = Path(csv_path)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    current = json.loads(json_p.read_text()) if json_p.exists() else {}
    history = pd.read_csv(csv_p) if csv_p.exists() else pd.DataFrame()

    lines = ["# Metrics report", ""]
    if current:
        lines += ["## Current snapshot", "", pd.DataFrame([current]).to_markdown(index=False), ""]
    if not history.empty:
        lines += ["## Historical metrics", "", history.tail(10).to_markdown(index=False), ""]
        summary = history.select_dtypes(include="number").agg(["mean", "min", "max"]).round(4)
        lines += ["## Summary", "", summary.to_markdown(), ""]
    out_p.write_text("\n".join(lines))
    return out_p

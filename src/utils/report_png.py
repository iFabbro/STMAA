from pathlib import Path
import json
import pandas as pd
import plotly.graph_objects as go

def generate_metrics_png(json_path="logs/metrics.json", csv_path="logs/metrics_history.csv", output_path="logs/metrics_summary.png"):
    json_p = Path(json_path)
    csv_p = Path(csv_path)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    current = json.loads(json_p.read_text()) if json_p.exists() else {}
    history = pd.read_csv(csv_p) if csv_p.exists() else pd.DataFrame()

    metrics = ["sharpe_ratio", "sortino_ratio", "max_drawdown", "drawdown_duration", "total_return"]
    values = [float(current.get(k, 0.0)) for k in metrics]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=metrics,
        y=values,
        marker_color=["#1f77b4", "#2ca02c", "#d62728", "#9467bd", "#ff7f0e"],
        text=[f"{v:.4f}" for v in values],
        textposition="auto",
    ))
    fig.update_layout(
        title="Metrics summary",
        template="plotly_white",
        height=500,
        width=1100,
        margin=dict(l=40, r=20, t=70, b=60),
        yaxis_title="Value",
    )

    try:
        fig.write_image(str(out_p), format="png", scale=2)
        return out_p
    except Exception as e:
        out_p.with_suffix(".txt").write_text(f"PNG export skipped: {e}")
        return None

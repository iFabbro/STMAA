from pathlib import Path
import json
import pandas as pd
from src.utils.report_generator import generate_metrics_report

def test_generate_metrics_report(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "metrics.json").write_text(json.dumps({"sharpe_ratio": 1.0, "total_return": 0.2}))
    pd.DataFrame([
        {"sharpe_ratio": 1.0, "total_return": 0.2},
        {"sharpe_ratio": 1.5, "total_return": 0.3},
    ]).to_csv(logs / "metrics_history.csv", index=False)
    out = generate_metrics_report(logs / "metrics.json", logs / "metrics_history.csv", logs / "metrics_report.md")
    assert out.exists()
    content = out.read_text()
    assert "# Metrics report" in content
    assert "Current snapshot" in content
    assert "Historical metrics" in content

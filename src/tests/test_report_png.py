import json
import pandas as pd
from src.utils.report_png import generate_metrics_png

def test_generate_metrics_png(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "metrics.json").write_text(json.dumps({
        "sharpe_ratio": 1.2,
        "sortino_ratio": 1.5,
        "max_drawdown": -0.2,
        "drawdown_duration": 4,
        "total_return": 0.3,
    }))
    pd.DataFrame([
        {"sharpe_ratio": 1.0, "sortino_ratio": 1.1, "max_drawdown": -0.1, "drawdown_duration": 2, "total_return": 0.2},
        {"sharpe_ratio": 1.2, "sortino_ratio": 1.5, "max_drawdown": -0.2, "drawdown_duration": 4, "total_return": 0.3},
    ]).to_csv(logs / "metrics_history.csv", index=False)
    out = generate_metrics_png(logs / "metrics.json", logs / "metrics_history.csv", logs / "metrics_summary.png")
    assert out is None or out.exists()

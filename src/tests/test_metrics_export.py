import json
from src.utils.metrics import Metrics

def test_write_metrics_json(tmp_path):
    path = tmp_path / "metrics.json"
    payload = {"sharpe_ratio": 1.23, "drawdown_duration": 4}
    out = Metrics.write_metrics_json(payload, path)
    assert out == path
    data = json.loads(path.read_text())
    assert data["sharpe_ratio"] == 1.23
    assert data["drawdown_duration"] == 4

def test_write_metrics_csv_appends(tmp_path):
    path = tmp_path / "metrics.csv"
    row = {"sharpe_ratio": 1.23, "drawdown_duration": 4}
    Metrics.write_metrics_csv(row, path)
    Metrics.write_metrics_csv(row, path)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 3
    assert lines[0].split(',') == ["sharpe_ratio", "drawdown_duration"]

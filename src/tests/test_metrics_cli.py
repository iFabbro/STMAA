from pathlib import Path
from src.utils.metrics_cli import run_cli

def test_metrics_cli_generates_outputs(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    cfg = tmp_path / "config"
    logs.mkdir()
    cfg.mkdir()
    (cfg / "report_thresholds.yaml").write_text("sharpe_ratio: -0.25\nmax_drawdown: 0.05\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))

    run_cli([
        "--json", str(logs / "metrics.json"),
        "--csv", str(logs / "metrics_history.csv"),
        "--md", str(logs / "metrics_report.md"),
        "--png", str(logs / "metrics_summary.png"),
        "--thresholds", str(cfg / "report_thresholds.yaml"),
    ])

    assert (logs / "metrics.json").exists()
    assert (logs / "metrics_history.csv").exists()
    assert (logs / "metrics_report.md").exists()

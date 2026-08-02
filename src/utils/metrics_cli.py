from argparse import ArgumentParser
from src.utils.report_generator import generate_metrics_report
from src.utils.report_png import generate_metrics_png
from src.utils.metrics import PortfolioMetrics

def build_parser():
    p = ArgumentParser(description="Generate metrics JSON, CSV, Markdown, and PNG reports")
    p.add_argument("--json", default="logs/metrics.json")
    p.add_argument("--csv", default="logs/metrics_history.csv")
    p.add_argument("--md", default="logs/metrics_report.md")
    p.add_argument("--png", default="logs/metrics_summary.png")
    p.add_argument("--thresholds", default="config/report_thresholds.yaml")
    return p

def run_cli(args=None):
    parser = build_parser()
    ns = parser.parse_args(args)
    pm = PortfolioMetrics()
    metrics = pm.get_all_metrics()
    pm.write_metrics_json(metrics, ns.json)
    pm.write_metrics_csv(metrics, ns.csv)
    generate_metrics_report(ns.json, ns.csv, ns.md, thresholds_path=ns.thresholds)
    generate_metrics_png(ns.json, ns.csv, ns.png)
    return 0

if __name__ == "__main__":
    raise SystemExit(run_cli())

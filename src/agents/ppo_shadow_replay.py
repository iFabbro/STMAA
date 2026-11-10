"""Deterministic integrity-checked replay for PPO shadow ledgers."""
from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio


def replay_shadow_ledger(path):
    records = PPOShadowLedger(path).read()
    decisions, transitions, portfolio = [], [], None
    benchmark, benchmark_metrics, provenance = None, [], None
    for index, record in enumerate(records):
        event = record["payload"]
        kind = event.get("type")
        if index == 0:
            if kind != "run_metadata" or event.get("schema") != "ppo_shadow_v1":
                raise ValueError("Shadow ledger has no valid run metadata header")
            provenance = event["provenance"]
        elif kind == "decision":
            decisions.append(event)
        elif kind == "portfolio_transition":
            portfolio = PPOShadowPortfolio.from_state(event["portfolio"])
            benchmark = event["benchmark"]
            benchmark_metrics.append(event["benchmark_metrics"])
        else:
            raise ValueError(f"Unsupported ledger event type: {kind!r}")
    return {
        "record_count": len(records),
        "decision_count": len(decisions),
        "transition_count": len(benchmark_metrics),
        "last_decision": decisions[-1] if decisions else None,
        "portfolio": portfolio.to_state() if portfolio else None,
        "benchmark": benchmark,
        "benchmark_metrics": benchmark_metrics,
        "provenance": provenance,
        "head_hash": records[-1]["hash"] if records else "0" * 64,
    }

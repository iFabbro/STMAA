"""Deterministic replay and integrity verification for shadow ledgers."""
from src.agents.ppo_shadow_ledger import PPOShadowLedger
from src.agents.ppo_shadow_portfolio import PPOShadowPortfolio


def replay_shadow_ledger(path):
    records = PPOShadowLedger(path).read()
    decisions, transitions, portfolio = [], [], None
    for record in records:
        event = record["payload"]
        if event.get("type") == "decision":
            decisions.append(event)
        elif event.get("type") == "portfolio_transition":
            portfolio = PPOShadowPortfolio.from_state(event["portfolio"])
            transitions.append(event["metrics"])
        else:
            raise ValueError(f"Unsupported ledger event type: {event.get('type')!r}")
    return {"record_count": len(records), "decision_count": len(decisions),
            "transition_count": len(transitions), "last_decision": decisions[-1] if decisions else None,
            "portfolio": portfolio.to_state() if portfolio else None, "transitions": transitions,
            "head_hash": records[-1]["hash"] if records else "0" * 64}

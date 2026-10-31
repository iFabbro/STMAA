"""Fail-closed reconciliation of Alpaca account corporate-action activities.

Uses the retail Trading API account-activities endpoint through TradingClient's
underlying REST transport. It never mutates broker state or submits orders.
"""
from datetime import datetime, timezone
import copy
import math
import re
from typing import Callable, Iterable


CORPORATE_ACTION_TYPES = frozenset({
    "SPIN", "REO", "REORG", "MA", "SPLIT", "NC", "SC",
})
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 100


class CorporateActionReconciler:
    """Read and reconcile corporate-action activities against broker positions."""

    def __init__(
        self,
        activity_page_reader: Callable[[dict], object],
        state: dict,
        persist: Callable[[], None],
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ):
        if not callable(activity_page_reader) or not isinstance(state, dict) or not callable(persist):
            raise ValueError("Corporate-action reconciler requires reader, state and persistence callbacks")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Corporate-action page_size must be an integer in [1, 100]")
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("Corporate-action max_pages must be a positive integer")
        self._read_page = activity_page_reader
        self._state = state
        self._persist = persist
        self._page_size = page_size
        self._max_pages = max_pages

    @staticmethod
    def _normalize_symbols(symbols: Iterable[str]) -> set:
        normalized = set()
        for symbol in symbols:
            if not isinstance(symbol, str) or not symbol.strip():
                raise RuntimeError("Configured corporate-action universe contains an invalid symbol")
            normalized.add(symbol.strip().upper())
        if not normalized:
            raise RuntimeError("Configured corporate-action universe is empty")
        return normalized

    @staticmethod
    def _validate_position_snapshot(positions: dict) -> set:
        if not isinstance(positions, dict):
            raise RuntimeError("Corporate-action reconciliation requires a verified positions mapping")
        held = set()
        for raw_symbol, position in positions.items():
            if not isinstance(raw_symbol, str) or not raw_symbol.strip() or not isinstance(position, dict):
                raise RuntimeError("Corporate-action reconciliation received malformed broker positions")
            symbol = raw_symbol.strip().upper()
            try:
                quantity = float(position.get("qty"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(
                    f"Corporate-action reconciliation cannot validate quantity for {symbol}"
                ) from exc
            if not math.isfinite(quantity):
                raise RuntimeError(
                    f"Corporate-action reconciliation received non-finite quantity for {symbol}"
                )
            if abs(quantity) > 1e-12:
                held.add(symbol)
        return held

    @staticmethod
    def _validate_activity(activity: object) -> dict:
        if not isinstance(activity, dict):
            raise RuntimeError("Alpaca corporate-action response contains a non-object activity")
        activity_id = activity.get("id")
        activity_type = str(activity.get("activity_type", "")).strip().upper()
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise RuntimeError("Alpaca corporate-action activity is missing its ID")
        if activity_type not in CORPORATE_ACTION_TYPES:
            raise RuntimeError(
                f"Alpaca returned unexpected activity type in corporate-action response: {activity_type or '<missing>'}"
            )
        symbol = activity.get("symbol")
        activity_sub_type = activity.get("activity_sub_type")
        if symbol is not None:
            if not isinstance(symbol, str) or not symbol.strip():
                raise RuntimeError("Alpaca corporate-action activity contains an invalid symbol")
            symbol = symbol.strip().upper()
        status = activity.get("status")
        if status is not None and str(status).strip().lower() not in {"executed", "settled", "complete", "completed"}:
            raise RuntimeError(
                f"Corporate-action activity {activity_id} has a non-final or unknown status"
            )
        return {
            "id": activity_id.strip(),
            "activity_type": activity_type,
            "symbol": symbol,
            "activity_sub_type": activity_sub_type,
            "date": activity.get("date") or activity.get("created_at"),
            "status": str(status).strip().lower() if status is not None else None,
        }

    def _read_new_activities(self) -> tuple:
        previous = self._state.get("corporate_action_reconciliation", {})
        if not isinstance(previous, dict):
            raise RuntimeError("Persisted corporate-action reconciliation state is malformed")
        checkpoint = previous.get("last_activity_id")
        if checkpoint is not None and (not isinstance(checkpoint, str) or not checkpoint.strip()):
            raise RuntimeError("Persisted corporate-action activity checkpoint is invalid")

        activities = []
        seen_ids = set()
        page_token = None
        checkpoint_seen = False
        for _ in range(self._max_pages):
            params = {
                "activity_types": ",".join(sorted(CORPORATE_ACTION_TYPES)),
                "direction": "desc",
                "page_size": self._page_size,
            }
            if page_token:
                params["page_token"] = page_token
            try:
                page = self._read_page(params)
            except Exception as exc:
                raise RuntimeError(
                    "Unable to retrieve Alpaca corporate-action activities; refusing trading"
                ) from exc
            if not isinstance(page, list):
                raise RuntimeError("Alpaca corporate-action endpoint returned an invalid page shape")
            if len(page) > self._page_size:
                raise RuntimeError("Alpaca corporate-action endpoint exceeded requested page size")
            if not page:
                if checkpoint and not checkpoint_seen:
                    raise RuntimeError(
                        "Persisted corporate-action activity checkpoint was not found in the "
                        "complete broker activity history; refusing to advance reconciliation"
                    )
                return activities, checkpoint, checkpoint_seen

            validated_page = [self._validate_activity(item) for item in page]
            page_ids = [item["id"] for item in validated_page]
            if len(page_ids) != len(set(page_ids)) or any(item_id in seen_ids for item_id in page_ids):
                raise RuntimeError("Alpaca corporate-action pagination returned duplicate activity IDs")
            seen_ids.update(page_ids)

            for activity in validated_page:
                if checkpoint and activity["id"] == checkpoint:
                    checkpoint_seen = True
                    break
                activities.append(activity)
            if checkpoint_seen:
                return activities, checkpoint, True

            if len(page) < self._page_size:
                if checkpoint and not checkpoint_seen:
                    raise RuntimeError(
                        "Persisted corporate-action activity checkpoint was not found in the "
                        "complete broker activity history; refusing to advance reconciliation"
                    )
                return activities, checkpoint, False

            next_token = validated_page[-1]["id"]
            if next_token == page_token:
                raise RuntimeError("Alpaca corporate-action pagination cursor did not advance")
            page_token = next_token

        raise RuntimeError(
            "Alpaca corporate-action history exceeded the configured page safety limit; "
            "refusing to assume reconciliation is complete"
        )


    @staticmethod
    def _validate_manual_resolution(resolution: object, activity_id: str) -> dict:
        if not isinstance(resolution, dict):
            raise RuntimeError("Persisted corporate-action manual resolution is malformed")
        symbol = resolution.get("symbol")
        if not isinstance(symbol, str) or not re.fullmatch(
            r"[A-Z0-9][A-Z0-9.\\-]{0,31}", symbol
        ):
            raise RuntimeError("Persisted corporate-action manual resolution has an invalid symbol")
        for field, maximum in (("evidence", 512), ("actor", 128), ("reason", 1000), ("resolved_at", 64)):
            value = resolution.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise RuntimeError(
                    f"Persisted corporate-action manual resolution has invalid {field}"
                )
        if resolution.get("activity_id") != activity_id:
            raise RuntimeError("Persisted corporate-action manual resolution ID does not match its key")
        return resolution

    @staticmethod
    def _required_text(value: object, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ValueError(f"Manual corporate-action resolution requires a valid {field}")
        return value.strip()

    def resolve_unresolved_activity(
        self,
        activity_id: str,
        symbol: str,
        evidence: str,
        actor: str,
        reason: str,
    ) -> dict:
        """Manually resolve one missing-symbol activity with durable audit evidence.

        This changes local reconciliation metadata only. It never reads or mutates
        broker state and never submits, replaces, or cancels orders.
        """
        normalized_id = self._required_text(activity_id, "activity_id", 256)
        normalized_symbol = self._required_text(symbol, "symbol", 32).upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\\-]{0,31}", normalized_symbol):
            raise ValueError("Manual corporate-action resolution requires a valid ticker symbol")
        evidence_ref = self._required_text(evidence, "evidence", 512)
        operator = self._required_text(actor, "actor", 128)
        explanation = self._required_text(reason, "reason", 1000)

        previous = self._state.get("corporate_action_reconciliation", {})
        if not isinstance(previous, dict):
            raise RuntimeError("Persisted corporate-action reconciliation state is malformed")
        unresolved = previous.get("unresolved_activity_ids", [])
        events = previous.get("recent_events", {})
        resolutions = previous.get("resolved_activity_symbols", {})
        history = previous.get("manual_resolution_history", [])
        if (
            not isinstance(unresolved, list)
            or any(not isinstance(item, str) or not item for item in unresolved)
            or len(unresolved) != len(set(unresolved))
            or not isinstance(events, dict)
            or not isinstance(resolutions, dict)
            or not isinstance(history, list)
        ):
            raise RuntimeError("Persisted corporate-action manual-resolution state is malformed")
        if normalized_id not in unresolved:
            raise ValueError("Corporate-action activity is not currently unresolved")
        event = events.get(normalized_id)
        if not isinstance(event, dict) or event.get("id") != normalized_id:
            raise RuntimeError(
                "Cannot resolve corporate-action activity without its persisted event evidence"
            )
        if event.get("symbol") is not None:
            raise ValueError("Only activities with a missing symbol can use manual symbol resolution")
        if normalized_id in resolutions:
            raise RuntimeError("Corporate-action activity already has a manual resolution")
        if any(
            isinstance(record, dict) and record.get("activity_id") == normalized_id
            for record in history
        ):
            raise RuntimeError("Corporate-action activity already appears in the manual-resolution audit log")

        resolution = {
            "activity_id": normalized_id,
            "symbol": normalized_symbol,
            "evidence": evidence_ref,
            "actor": operator,
            "reason": explanation,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        next_state = copy.deepcopy(previous)
        next_state["unresolved_activity_ids"] = [
            item for item in unresolved if item != normalized_id
        ]
        next_state.setdefault("resolved_activity_symbols", {})[normalized_id] = resolution
        next_state.setdefault("manual_resolution_history", []).append(resolution.copy())

        self._state["corporate_action_reconciliation"] = next_state
        try:
            self._persist()
        except Exception:
            self._state["corporate_action_reconciliation"] = previous
            raise
        return resolution.copy()

    def reconcile(self, configured_symbols: Iterable[str], positions: dict) -> dict:
        """Reconcile all newly observed events; raise before trading on uncertainty."""
        configured = self._normalize_symbols(configured_symbols)
        held = self._validate_position_snapshot(positions)
        previous = self._state.get("corporate_action_reconciliation", {})
        if not isinstance(previous, dict):
            raise RuntimeError("Persisted corporate-action reconciliation state is malformed")

        new_activities, old_checkpoint, checkpoint_seen = self._read_new_activities()
        existing_events = previous.get("recent_events", {})
        if not isinstance(existing_events, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in existing_events.items()
        ):
            raise RuntimeError("Persisted corporate-action event ledger is malformed")

        # Newest activities are first in the descending API response. Preserve
        # active historical blockers even when the bounded recent-event ledger rolls.
        ledger = {activity["id"]: activity for activity in new_activities}
        for activity_id, activity in existing_events.items():
            ledger.setdefault(activity_id, activity)

        latest_activity_id = (
            new_activities[0]["id"] if new_activities else old_checkpoint
        )
        raw_unresolved = previous.get("unresolved_activity_ids", [])
        if not isinstance(raw_unresolved, list):
            raise RuntimeError("Persisted unresolved corporate-action IDs are malformed")
        if any(not isinstance(item, str) or not item for item in raw_unresolved):
            raise RuntimeError("Persisted unresolved corporate-action IDs are malformed")
        unresolved = set(raw_unresolved)
        if len(unresolved) != len(raw_unresolved):
            raise RuntimeError("Persisted unresolved corporate-action IDs contain duplicates")

        resolved_symbols = previous.get("resolved_activity_symbols", {})
        resolution_history = previous.get("manual_resolution_history", [])
        if not isinstance(resolved_symbols, dict) or not isinstance(resolution_history, list):
            raise RuntimeError("Persisted corporate-action manual-resolution state is malformed")
        history_by_id = {}
        for record in resolution_history:
            if not isinstance(record, dict):
                raise RuntimeError("Persisted corporate-action manual-resolution history is malformed")
            record_id = record.get("activity_id")
            if not isinstance(record_id, str) or not record_id or record_id in history_by_id:
                raise RuntimeError("Persisted corporate-action manual-resolution history has invalid IDs")
            history_by_id[record_id] = self._validate_manual_resolution(record, record_id)
        if set(history_by_id) != set(resolved_symbols):
            raise RuntimeError("Corporate-action manual-resolution map and audit history disagree")
        for activity_id, record in resolved_symbols.items():
            self._validate_manual_resolution(record, activity_id)
            if history_by_id.get(activity_id) != record:
                raise RuntimeError("Corporate-action manual-resolution evidence is inconsistent")
        if unresolved.intersection(resolved_symbols):
            raise RuntimeError("Corporate-action activity is both unresolved and manually resolved")

        blocked_symbols = set()
        active_event_ids = set(unresolved)
        for activity in ledger.values():
            symbol = activity.get("symbol")
            if not symbol and activity["id"] in resolved_symbols:
                symbol = resolved_symbols[activity["id"]]["symbol"]
            cash_only_reorganization = (
                activity.get("activity_type") == "REO"
                and str(activity.get("activity_sub_type") or "").upper() == "REOC"
            )
            if not symbol:
                if not cash_only_reorganization:
                    unresolved.add(activity["id"])
                    active_event_ids.add(activity["id"])
                continue
            if symbol in held and symbol not in configured:
                blocked_symbols.add(symbol)
                active_event_ids.add(activity["id"])

        # Retain the 500 newest known events plus any still-active blockers.
        retained_ids = set(list(ledger.keys())[:500]) | active_event_ids
        ledger = {
            activity_id: activity
            for activity_id, activity in ledger.items()
            if activity_id in retained_ids
        }

        next_state = {
            "last_activity_id": latest_activity_id,
            "last_reconciled_at": datetime.now(timezone.utc).isoformat(),
            "recent_events": ledger,
            "unresolved_activity_ids": sorted(unresolved),
            "blocked_symbols": sorted(blocked_symbols),
            "pagination_checkpoint_seen": bool(checkpoint_seen),
        }
        self._state["corporate_action_reconciliation"] = next_state
        self._persist()

        if unresolved:
            raise RuntimeError(
                "Corporate-action activities without a resolvable security symbol require "
                "manual reconciliation; refusing trading: " + ", ".join(sorted(unresolved))
            )
        if blocked_symbols:
            raise RuntimeError(
                "Broker holds securities linked to corporate actions outside the configured "
                "PPO universe; manual reconciliation is required before inference or rebalancing: "
                + ", ".join(sorted(blocked_symbols))
            )

        return {
            "new_activity_count": len(new_activities),
            "last_activity_id": latest_activity_id,
            "checked_symbols": sorted(held),
            "blocked_symbols": [],
        }

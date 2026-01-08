from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymarket_watch.models import Alert, SignalEvent


@dataclass
class AggregatedSignals:
    market_id: int
    side: str | None
    score: float
    status: str
    why_json: dict[str, Any]


class ScoringAggregator:
    window: timedelta
    weights: dict[str, float]
    severity_multipliers: dict[str, float]
    bonus_per_extra_type: float
    high_threshold: float
    watch_threshold: float

    def __init__(
        self,
        window: timedelta | None = None,
        weights: dict[str, float] | None = None,
        severity_multipliers: dict[str, float] | None = None,
        bonus_per_extra_type: float = 2.5,
        high_threshold: float = 12.0,
        watch_threshold: float = 4.0,
    ) -> None:
        self.window = window or timedelta(hours=2)
        self.weights = weights or {
            "FRESH_WALLET_BIG_SIZE": 5.0,
            "LOW_ACTIVITY_WALLET_BIG_SIZE": 3.0,
            "REPEAT_ENTRIES": 2.0,
            "THIN_MARKET_IMPACT": 4.0,
            "CLUSTERING": 3.5,
            "EARLY_POSITIONING": 6.0,  # Highest weight - historically accurate wallets
        }
        self.severity_multipliers = severity_multipliers or {
            "high": 2.0,
            "medium": 1.0,
            "low": 0.5,
        }
        self.bonus_per_extra_type = bonus_per_extra_type
        self.high_threshold = high_threshold
        self.watch_threshold = watch_threshold

    def _severity_multiplier(self, severity: str | None) -> float:
        if not severity:
            return 1.0
        return self.severity_multipliers.get(severity.lower(), 1.0)

    def _score_signal(self, signal: SignalEvent) -> float:
        weight = self.weights.get(signal.signal_type, 1.0)
        return weight * self._severity_multiplier(signal.severity)

    def _group_signals(self, signals: Iterable[SignalEvent]) -> dict[tuple[int, str | None], list[SignalEvent]]:
        grouped: dict[tuple[int, str | None], list[SignalEvent]] = defaultdict(list)
        for s in signals:
            key = (s.market_id, s.side)
            grouped[key].append(s)
        return grouped

    def _compute_group_score(self, signals: list[SignalEvent]) -> float:
        base = sum(self._score_signal(s) for s in signals)
        distinct_types = {s.signal_type for s in signals}
        bonus = self.bonus_per_extra_type * max(len(distinct_types) - 1, 0)
        return float(base + bonus)

    def _status_for_score(self, score: float) -> str:
        if score >= self.high_threshold:
            return "high"
        return "watch"

    def _build_why(self, signals: list[SignalEvent], score: float) -> dict[str, Any]:
        counts: dict[str, int] = defaultdict(int)
        example_wallets: set[str] = set()
        examples: list[dict[str, Any]] = []
        for s in sorted(signals, key=lambda x: x.observed_at or x.created_at or datetime.now(timezone.utc)):
            counts[s.signal_type] += 1
            if s.wallet_address and len(example_wallets) < 5:
                example_wallets.add(s.wallet_address)
            if len(examples) < 5:
                examples.append(
                    {
                        "signal_type": s.signal_type,
                        "wallet": s.wallet_address,
                        "side": s.side,
                        "severity": s.severity,
                        "observed_at": (s.observed_at or s.created_at).isoformat()
                        if (s.observed_at or s.created_at)
                        else None,
                    }
                )
        return {
            "score": score,
            "counts_by_signal": dict(counts),
            "distinct_types": list(dict(counts).keys()),
            "example_wallets": list(example_wallets),
            "examples": examples,
            "window_hours": self.window.total_seconds() / 3600,
        }

    def aggregate(self, session: Session, now: datetime | None = None) -> list[AggregatedSignals]:
        current_time = now or datetime.now(timezone.utc)
        cutoff = current_time - self.window
        signals = (
            session.execute(
                select(SignalEvent).where(
                    (SignalEvent.observed_at >= cutoff) | (SignalEvent.created_at >= cutoff)
                )
            )
            .scalars()
            .all()
        )
        grouped = self._group_signals(signals)
        aggregated: list[AggregatedSignals] = []
        for (market_id, side), items in grouped.items():
            score = self._compute_group_score(items)
            if score < self.watch_threshold:
                continue
            status = self._status_for_score(score)
            why_json = self._build_why(items, score)
            aggregated.append(
                AggregatedSignals(
                    market_id=market_id,
                    side=side,
                    score=score,
                    status=status,
                    why_json=why_json,
                )
            )
        return aggregated

    def upsert_alerts(self, session: Session, aggregates: list[AggregatedSignals]) -> int:
        if not aggregates:
            return 0
        values = [
            {
                "market_id": agg.market_id,
                "side": agg.side,
                "event_type": "scoring",
                "status": agg.status,
                "score": agg.score,
                "why_json": agg.why_json,
                "message": f"score={agg.score:.2f} status={agg.status}",
            }
            for agg in aggregates
        ]
        stmt = insert(Alert).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["market_id", "side", "event_type"],
            set_={
                "status": stmt.excluded.status,
                "score": stmt.excluded.score,
                "why_json": stmt.excluded.why_json,
                "message": stmt.excluded.message,
            },
        )
        result = session.execute(stmt)
        return result.rowcount if hasattr(result, "rowcount") else len(values)

    def process(self, session: Session) -> int:
        aggregates = self.aggregate(session)
        return self.upsert_alerts(session, aggregates)


__all__ = ["ScoringAggregator", "AggregatedSignals"]

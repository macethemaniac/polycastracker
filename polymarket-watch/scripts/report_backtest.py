from __future__ import annotations

import argparse
import json
import logging
from statistics import mean

from sqlalchemy import select

from polymarket_watch.logging import setup_logging
from polymarket_watch.models import Alert, BacktestResult
from polymarket_watch.state import default_state

logger = logging.getLogger(__name__)


def generate_report() -> dict:
    setup_logging()
    state = default_state()
    with state.session_factory() as session:
        alerts = session.execute(select(Alert)).scalars().all()
        results = session.execute(select(BacktestResult)).scalars().all()

    total_alerts = len(alerts)
    status_counts = {}
    for a in alerts:
        status_counts[a.status or "unknown"] = status_counts.get(a.status or "unknown", 0) + 1

    top_20 = sorted(
        [r for r in results if r.delta_4h is not None],
        key=lambda x: x.delta_4h,
        reverse=True,
    )[:20]

    false_positive_pct = 0.0
    deltas_1h = [r.delta_1h for r in results if r.delta_1h is not None]
    if deltas_1h:
        false_positive_pct = sum(1 for d in deltas_1h if d <= 0) / len(deltas_1h) * 100

    correlation = None
    paired = [
        (float(a.score), float(r.delta_1h))
        for a in alerts
        for r in results
        if r.alert_id == a.id and a.score is not None and r.delta_1h is not None
    ]
    if paired:
        scores = [p[0] for p in paired]
        deltas = [p[1] for p in paired]
        mean_score = mean(scores)
        mean_delta = mean(deltas)
        cov = mean((s - mean_score) * (d - mean_delta) for s, d in paired)
        var_s = mean((s - mean_score) ** 2 for s in scores)
        var_d = mean((d - mean_delta) ** 2 for d in deltas)
        if var_s > 0 and var_d > 0:
            correlation = cov / (var_s ** 0.5 * var_d ** 0.5)

    report = {
        "total_alerts": total_alerts,
        "alerts_by_status": status_counts,
        "false_positive_pct_1h": false_positive_pct,
        "correlation_score_delta_1h": correlation,
        "top_alerts_delta_4h": [
            {
                "alert_id": r.alert_id,
                "delta_4h": float(r.delta_4h),
                "delta_1h": float(r.delta_1h) if r.delta_1h is not None else None,
                "score": float(r.score) if r.score is not None else None,
                "side": r.side,
            }
            for r in top_20
        ],
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate backtest report")
    parser.parse_args()
    report = generate_report()
    print(json.dumps(report, indent=2))
    with open("backtest_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info("Backtest report written", extra={"path": "backtest_report.json"})


if __name__ == "__main__":
    main()

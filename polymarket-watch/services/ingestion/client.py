from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

import httpx

from polymarket_watch.config import Settings, settings
from polymarket_watch.logging import setup_logging


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    # Handle Unix timestamps (seconds or milliseconds)
    if isinstance(value, (int, float)):
        try:
            # If > 10 billion, assume milliseconds
            ts = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    try:
        # Try parsing as numeric string (Unix timestamp)
        numeric = float(str(value))
        ts = numeric / 1000 if numeric > 10_000_000_000 else numeric
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


class IngestionClient:
    """Thin httpx client to retrieve markets and trades."""

    def __init__(self, config: Settings | None = None) -> None:
        cfg = config or settings
        setup_logging(cfg)
        self.markets_url = cfg.ingestion_markets_url
        self.trades_url = cfg.ingestion_trades_url
        self.timeout = cfg.ingestion_client_timeout_seconds
        headers = {"User-Agent": "polymarket-watch/0.1"}
        self._client = httpx.Client(timeout=self.timeout, headers=headers)

    def close(self) -> None:
        self._client.close()

    def fetch_markets(self) -> list[dict[str, Any]]:
        resp = self._client.get(self.markets_url)
        resp.raise_for_status()
        payload = resp.json()
        raw_markets: Iterable[Any]
        if isinstance(payload, dict) and "markets" in payload:
            raw_markets = payload["markets"]
        else:
            raw_markets = payload or []

        normalized: list[dict[str, Any]] = []
        for item in raw_markets:
            # Prefer conditionId for Data API compatibility
            external_id = (
                item.get("conditionId")
                or item.get("condition_id")
                or item.get("slug")
                or item.get("id")
                or item.get("marketId")
                or item.get("address")
                or item.get("uuid")
            )
            if not external_id:
                continue
            normalized.append(
                {
                    "external_id": str(external_id),
                    "name": item.get("question") or item.get("name") or item.get("title") or "",
                    "category": item.get("category"),
                    "status": item.get("status") or "active",
                    "resolved_at": _parse_datetime(
                        item.get("resolved_at")
                        or item.get("resolvedAt")
                        or item.get("resolutionTime")
                        or item.get("closed_time")
                    ),
                }
            )
        return normalized

    def fetch_recent_trades(self, market_id: str, since_ts: datetime | str | None) -> list[dict[str, Any]]:
        # Data API uses 'asset' or 'conditionId' parameter
        params: dict[str, Any] = {"asset": market_id}
        if since_ts:
            # Data API expects Unix timestamp in milliseconds
            ts = since_ts if isinstance(since_ts, datetime) else datetime.fromisoformat(str(since_ts))
            params["startTime"] = int(ts.timestamp() * 1000)

        resp = self._client.get(self.trades_url, params=params)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        payload = resp.json()
        raw_trades: Iterable[Any]
        if isinstance(payload, dict) and "trades" in payload:
            raw_trades = payload["trades"]
        else:
            raw_trades = payload or []

        normalized: list[dict[str, Any]] = []
        for item in raw_trades:
            traded_at = _parse_datetime(
                item.get("timestamp") or item.get("traded_at") or item.get("created_at") or item.get("time")
            )
            side_raw = item.get("side") or item.get("type") or "unknown"
            trade = {
                "market_external_id": market_id,
                "wallet_address": item.get("proxyWallet") or item.get("wallet") or item.get("wallet_address") or item.get("address"),
                "side": str(side_raw).lower(),
                "shares": Decimal(str(item.get("shares") or item.get("amount") or item.get("size") or "0")) or Decimal(
                    "0"
                ),
                "price": Decimal(str(item.get("price") or item.get("fill_price") or item.get("avg_price") or "0"))
                or Decimal("0"),
                "traded_at": traded_at,
                "trade_hash": item.get("transactionHash") or item.get("hash") or item.get("id") or item.get("txid"),
            }
            if trade["wallet_address"] and trade["traded_at"]:
                normalized.append(trade)

        return normalized


__all__ = ["IngestionClient"]

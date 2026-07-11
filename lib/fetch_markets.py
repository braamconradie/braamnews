"""Fetch live crypto/stock/FX data (no API keys required)."""

import logging

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; daily-briefing-bot/1.0)"}
_TIMEOUT = 10

_COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
}


def fetch_crypto(symbols: list[str]) -> dict:
    """Fetch USD price + 24h % change for the given crypto symbols via CoinGecko."""
    ids = [_COINGECKO_IDS[s] for s in symbols if s in _COINGECKO_IDS]
    if not ids:
        return {}

    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("Failed fetching crypto prices")
        return {}

    result = {}
    for symbol in symbols:
        coingecko_id = _COINGECKO_IDS.get(symbol)
        if coingecko_id and coingecko_id in data:
            entry = data[coingecko_id]
            result[symbol] = {
                "price": entry.get("usd"),
                "change_24h_pct": entry.get("usd_24h_change"),
            }
    return result


def fetch_yahoo_quote(symbol: str) -> dict | None:
    """Fetch current price + % change vs previous close for a Yahoo Finance symbol."""
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        meta = result["meta"]
    except Exception:
        logger.exception("Failed fetching Yahoo quote for %s", symbol)
        return None

    price = meta.get("regularMarketPrice")
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    if price is None or not prev_close:
        return None

    change_pct = (price - prev_close) / prev_close * 100
    return {"price": price, "change_pct": change_pct}


def fetch_stocks(symbols: list[str]) -> dict:
    return {symbol: fetch_yahoo_quote(symbol) for symbol in symbols}


def fetch_fx(pair: str) -> dict | None:
    return fetch_yahoo_quote(pair)

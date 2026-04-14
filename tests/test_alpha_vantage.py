from __future__ import annotations

from aibroker.data.alpha_vantage import fetch_daily_ohlc, parse_time_series_daily

SAMPLE_AV = {
    "Meta Data": {"1. Information": "Daily Prices"},
    "Time Series (Daily)": {
        "2024-01-02": {
            "1. open": "100.0",
            "2. high": "101.0",
            "3. low": "99.0",
            "4. close": "100.5",
            "5. volume": "1000",
        },
        "2024-01-03": {
            "1. open": "100.5",
            "2. high": "102.0",
            "3. low": "100.0",
            "4. close": "101.5",
            "5. volume": "1100",
        },
    },
}


def test_parse_time_series_daily_ok() -> None:
    ohlc, err = parse_time_series_daily(SAMPLE_AV, max_candles=24)
    assert err is None
    assert ohlc is not None
    assert len(ohlc) == 2
    assert ohlc[0]["c"] == 100.5
    assert ohlc[1]["h"] == 102.0


def test_fetch_daily_rejects_empty_symbol() -> None:
    ohlc, err = fetch_daily_ohlc("   ", "dummy-key")
    assert ohlc is None
    assert err == "empty symbol"


def test_parse_rate_limit_note() -> None:
    ohlc, err = parse_time_series_daily({"Note": "Thank you for using Alpha Vantage"}, max_candles=24)
    assert ohlc is None
    assert err is not None

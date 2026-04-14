"""C2 — tests for aibroker/agent/risk_profiles.py"""
from __future__ import annotations

from aibroker.agent.risk_profiles import RISK_PROFILES


class TestRiskProfiles:
    def test_all_three_levels_exist(self):
        assert set(RISK_PROFILES.keys()) == {"low", "medium", "high"}

    def test_required_keys_present(self):
        required = {
            "target_positions", "rebalance_every", "trail_pct", "trail_atr_mult",
            "invest_pct", "allow_shorts", "bear_sell_all", "bear_trigger",
            "rotation_threshold", "label_he", "momentum_w10", "momentum_w20", "momentum_w50",
        }
        for level, profile in RISK_PROFILES.items():
            missing = required - set(profile.keys())
            assert not missing, f"Missing keys in {level}: {missing}"

    def test_trail_pct_in_range(self):
        for level, p in RISK_PROFILES.items():
            assert 0 < p["trail_pct"] < 1, f"{level}: trail_pct={p['trail_pct']}"

    def test_invest_pct_in_range(self):
        for level, p in RISK_PROFILES.items():
            assert 0 < p["invest_pct"] <= 1.0, f"{level}: invest_pct={p['invest_pct']}"

    def test_momentum_weights_sum_near_one(self):
        for level, p in RISK_PROFILES.items():
            total = p["momentum_w10"] + p["momentum_w20"] + p["momentum_w50"]
            assert abs(total - 1.0) < 0.02, f"{level}: weights sum={total}"

    def test_target_positions_positive(self):
        for level, p in RISK_PROFILES.items():
            assert p["target_positions"] > 0

    def test_rebalance_every_positive(self):
        for level, p in RISK_PROFILES.items():
            assert p["rebalance_every"] > 0

    def test_high_allows_shorts(self):
        assert RISK_PROFILES["high"]["allow_shorts"] is True

    def test_low_disallows_shorts(self):
        assert RISK_PROFILES["low"]["allow_shorts"] is False

    def test_hebrew_labels(self):
        assert RISK_PROFILES["low"]["label_he"] == "נמוך"
        assert RISK_PROFILES["medium"]["label_he"] == "בינוני"
        assert RISK_PROFILES["high"]["label_he"] == "מוגבר"

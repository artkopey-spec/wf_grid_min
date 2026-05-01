"""
WP-T6 — Signal events filter awareness tests.

Plan reference: docs/zigzag_st_tester_phase2_implementation_plan.txt §8.2, §9.1.1, §9.5
Tests: #16 (signal semantics), #20 (empty-trade enabled run).

Contracts verified here:
1. Disabled path (filter_diagnostics=None) → output bit-identical to pre-WP-T6 baseline.
2. Enabled path (filter_diagnostics not None) → exactly 4 filter columns added.
3. Filter columns position: after "is_reversal", before "exec_price" (plan §9.5).
4. Close-rows (close_signal / long_close_signal / short_close_signal) → "N/A" sentinel.
5. Open-rows (open_signal / long_open_signal / short_open_signal) → per-bar lookup.
6. filter_decision correctly derived from filter_allowed_entry + filter_block_reason.
7. Initialization flips (prev==0 → ±1) are NOT injected (plan §8.2 audit-fix v0.3).
8. Empty-result (no ST flips): headers include filter columns when enabled.
9. Blocked entry visible in filter_decision / filter_block_reason.
10. CLI wiring: both cli/tester.py and run_batch_tester.py pass filter_diagnostics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlc(n: int, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    noise = rng.uniform(0.001, 0.003, size=n)
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = (close + rng.uniform(-0.5, 0.5, size=n)).clip(low, high)
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def _trend_with_flips(n: int, flip_bars: list[int]) -> np.ndarray:
    """Build trend array: 0 for first 2 bars, alternating ±1 at flip_bars."""
    trend = np.ones(n, dtype=np.int8)
    trend[0] = 0
    trend[1] = 0  # initialization phase
    for t in flip_bars:
        # Alternate: check prev value and flip
        trend[t] = -trend[t - 1]
    return trend


def _make_filter_diagnostics(n: int, signal_bars: list[int], blocked_bars: list[int] = None) -> dict:
    """Build minimal filter_diagnostics with controllable allowed/blocked at given bars.

    signal_bars: bars that have flips (open events).
    blocked_bars: subset of signal_bars that should be blocked.
    """
    blocked_bars = blocked_bars or []

    fd = {
        "trade_filter_state":         np.array(["ST_ACTIVE_MONITORING"] * n, dtype=object),
        "filter_allowed_entry":       np.ones(n, dtype=np.int8),
        "filter_block_reason":        np.array(["none"] * n, dtype=object),
        "trade_filter_trigger_source": np.array(["none"] * n, dtype=object),
    }
    for t in blocked_bars:
        fd["filter_allowed_entry"][t] = 0
        fd["filter_block_reason"][t] = "stopping_mode_no_new_entries"
        fd["trade_filter_state"][t] = "ST_STOPPING"

    return fd


_ATR = 14
_EM_O2O = None  # will be imported


def _get_em():
    from supertrend_optimizer.utils.enums import ExecutionModel
    return ExecutionModel.OPEN_TO_OPEN


# ---------------------------------------------------------------------------
# Group 1: Test #16 (signals semantics) — disabled path baseline identical
# ---------------------------------------------------------------------------

class TestDisabledPathBaselineIdentical:
    """Disabled path must be bit-identical to pre-WP-T6 (plan §8.2 rule 1)."""

    def test_none_gives_same_columns_as_baseline(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events, _COLUMN_NAMES

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [4, 8, 12])
        em = _get_em()

        res_none = build_signal_events(df, trend, _ATR, "revers", em, filter_diagnostics=None)
        res_omit = build_signal_events(df, trend, _ATR, "revers", em)

        assert list(res_none.columns) == list(res_omit.columns)
        pd.testing.assert_frame_equal(res_none.reset_index(drop=True),
                                      res_omit.reset_index(drop=True))

    def test_none_no_filter_columns_present(self) -> None:
        from supertrend_optimizer.testing.signal_events import (
            build_signal_events, _FILTER_COLUMN_NAMES)

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [4, 8])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "long", em)
        for col in _FILTER_COLUMN_NAMES:
            assert col not in result.columns, f"Filter column {col!r} must not appear in disabled path"

    def test_none_row_count_unchanged(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [5, 10, 15])
        em = _get_em()

        res_none = build_signal_events(df, trend, _ATR, "revers", em, filter_diagnostics=None)
        res_omit = build_signal_events(df, trend, _ATR, "revers", em)

        assert len(res_none) == len(res_omit)


# ---------------------------------------------------------------------------
# Group 2: Enabled path — 4 filter columns present and positioned correctly
# ---------------------------------------------------------------------------

class TestEnabledPathFilterColumns:
    """Enabled path adds exactly 4 filter columns in the correct position (plan §9.5)."""

    def test_four_filter_columns_present(self) -> None:
        from supertrend_optimizer.testing.signal_events import (
            build_signal_events, _FILTER_COLUMN_NAMES)

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [5, 10])
        fd = _make_filter_diagnostics(n, signal_bars=[5, 10])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "revers", em, filter_diagnostics=fd)
        for col in _FILTER_COLUMN_NAMES:
            assert col in result.columns, f"Missing filter column: {col!r}"

    def test_filter_columns_position_after_is_reversal_before_exec_price(self) -> None:
        """plan §9.5: filter cols must follow 'is_reversal' and precede 'exec_price'."""
        from supertrend_optimizer.testing.signal_events import (
            build_signal_events, _FILTER_COLUMN_NAMES)

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [5, 10])
        fd = _make_filter_diagnostics(n, signal_bars=[5, 10])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "revers", em, filter_diagnostics=fd)
        cols = list(result.columns)
        rev_idx = cols.index("is_reversal")
        exec_idx = cols.index("exec_price")

        for fcol in _FILTER_COLUMN_NAMES:
            fi = cols.index(fcol)
            assert fi > rev_idx, f"{fcol!r} must come after 'is_reversal'"
            assert fi < exec_idx, f"{fcol!r} must come before 'exec_price'"

    def test_enabled_has_more_columns_than_disabled(self) -> None:
        from supertrend_optimizer.testing.signal_events import (
            build_signal_events, _FILTER_COLUMN_NAMES)

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [5, 10])
        fd = _make_filter_diagnostics(n, signal_bars=[5, 10])
        em = _get_em()

        res_dis = build_signal_events(df, trend, _ATR, "revers", em)
        res_en = build_signal_events(df, trend, _ATR, "revers", em, filter_diagnostics=fd)

        assert len(res_en.columns) == len(res_dis.columns) + len(_FILTER_COLUMN_NAMES)

    def test_row_count_identical_enabled_vs_disabled(self) -> None:
        """Enabled path adds columns, NOT rows (plan §8.2 rule 2)."""
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [5, 10])
        fd = _make_filter_diagnostics(n, signal_bars=[5, 10])
        em = _get_em()

        res_dis = build_signal_events(df, trend, _ATR, "revers", em)
        res_en = build_signal_events(df, trend, _ATR, "revers", em, filter_diagnostics=fd)

        assert len(res_en) == len(res_dis)


# ---------------------------------------------------------------------------
# Group 3: Close-rows get "N/A" sentinel (plan §9.1.1)
# ---------------------------------------------------------------------------

class TestCloseRowsNASentinel:
    """Close-rows (close_signal, long_close_signal, short_close_signal) → "N/A"."""

    def _check_na_for_close_rows(self, result: pd.DataFrame) -> None:
        from supertrend_optimizer.testing.signal_events import _FILTER_COLUMN_NAMES, _NA_FILTER
        close_mask = result["event_type"].str.endswith("close_signal")
        for col in _FILTER_COLUMN_NAMES:
            bad = result.loc[close_mask, col] != _NA_FILTER
            assert not bad.any(), (
                f"Column {col!r}: close rows must have '{_NA_FILTER}' sentinel, "
                f"got {result.loc[close_mask, col].unique()}"
            )

    def test_revers_close_rows_na(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [5, 10, 15])
        fd = _make_filter_diagnostics(n, signal_bars=[5, 10, 15])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "revers", em, filter_diagnostics=fd)
        self._check_na_for_close_rows(result)

    def test_long_close_rows_na(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [5, 10])
        fd = _make_filter_diagnostics(n, signal_bars=[5, 10])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "long", em, filter_diagnostics=fd)
        self._check_na_for_close_rows(result)

    def test_short_close_rows_na(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        trend = _trend_with_flips(n, [5, 10])
        fd = _make_filter_diagnostics(n, signal_bars=[5, 10])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "short", em, filter_diagnostics=fd)
        self._check_na_for_close_rows(result)


# ---------------------------------------------------------------------------
# Group 4: Open-rows get lookup values
# ---------------------------------------------------------------------------

class TestOpenRowsLookup:
    """Open-rows must have filter values from filter_diagnostics[t]."""

    def test_allowed_entry_gives_entry_allowed_decision(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events, _NA_FILTER

        n = 20
        df = _make_ohlc(n)
        # Build a trend where bar 5 is red→green (long open), bar 10 is green→red (long close)
        trend = np.zeros(n, dtype=np.int8)
        trend[2:5] = -1
        trend[5:10] = 1
        trend[10:] = -1
        fd = _make_filter_diagnostics(n, signal_bars=[5, 10])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "long", em, filter_diagnostics=fd)
        # Only the open event (open_signal) should have non-N/A filter decision
        open_rows = result[result["event_type"] == "open_signal"]
        for _, row in open_rows.iterrows():
            assert row["filter_decision"] == "entry_allowed", (
                f"Expected 'entry_allowed', got {row['filter_decision']!r}"
            )
            assert row["filter_block_reason"] == "none", (
                f"Expected 'none', got {row['filter_block_reason']!r}"
            )
            assert row["filter_state_at_signal"] != _NA_FILTER

    def test_blocked_entry_gives_correct_decision(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        trend = np.zeros(n, dtype=np.int8)
        trend[2:5] = -1
        trend[5:] = 1  # flip at t=5 (red→green, long open)
        fd = _make_filter_diagnostics(n, signal_bars=[5], blocked_bars=[5])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "long", em, filter_diagnostics=fd)
        open_rows = result[result["event_type"] == "open_signal"]
        assert len(open_rows) >= 1
        row = open_rows.iloc[0]
        assert row["filter_decision"] == "entry_blocked_stopping", (
            f"Expected 'entry_blocked_stopping', got {row['filter_decision']!r}"
        )
        assert row["filter_block_reason"] == "stopping_mode_no_new_entries"
        assert row["filter_state_at_signal"] == "ST_STOPPING"

    def test_trigger_source_propagated(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        trend = np.zeros(n, dtype=np.int8)
        trend[2:5] = -1
        trend[5:] = 1
        fd = _make_filter_diagnostics(n, signal_bars=[5])
        fd["trade_filter_trigger_source"][5] = "candidate_threshold"
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "long", em, filter_diagnostics=fd)
        open_rows = result[result["event_type"] == "open_signal"]
        assert len(open_rows) >= 1
        assert open_rows.iloc[0]["filter_trigger_source"] == "candidate_threshold"


# ---------------------------------------------------------------------------
# Group 5: Initialization flips (prev==0 → ±1) NOT injected (plan §8.2)
# ---------------------------------------------------------------------------

class TestInitializationFlipsNotInjected:
    """Bars where prev==0 (ATR warmup transitions) must never appear as rows (plan §8.2)."""

    def test_no_initialization_rows_disabled(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        # trend starts with zeros (warmup), then has normal flips
        trend = np.zeros(n, dtype=np.int8)
        trend[3:7] = 1   # 0→1 at bar 3 (initialization), then 1→-1 at bar 7
        trend[7:] = -1

        em = _get_em()
        result = build_signal_events(df, trend, _ATR, "long", em)

        # Bar 3 (prev==0 → trend==1): should NOT appear as a row
        assert 3 not in result["signal_bar_index"].values, (
            "Bar 3 (initialization flip prev==0) must not appear as a signal row"
        )

    def test_no_initialization_rows_enabled(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events

        n = 20
        df = _make_ohlc(n)
        trend = np.zeros(n, dtype=np.int8)
        trend[3:7] = 1
        trend[7:] = -1

        fd = _make_filter_diagnostics(n, signal_bars=[])
        em = _get_em()

        result_dis = build_signal_events(df, trend, _ATR, "long", em)
        result_en = build_signal_events(df, trend, _ATR, "long", em, filter_diagnostics=fd)

        # Same bars, same row count
        assert list(result_dis["signal_bar_index"]) == list(result_en["signal_bar_index"])


# ---------------------------------------------------------------------------
# Group 6: Test #20 — empty-result case with enabled filter
# ---------------------------------------------------------------------------

class TestEmptyResultCase:
    """Empty-result (no ST flips): headers with filter columns present, 0 rows."""

    def test_empty_no_flips_disabled_headers(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events, _FILTER_COLUMN_NAMES

        n = 20
        df = _make_ohlc(n)
        trend = np.ones(n, dtype=np.int8)  # no flips
        trend[0] = 0
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "long", em)
        assert len(result) == 0
        for col in _FILTER_COLUMN_NAMES:
            assert col not in result.columns

    def test_empty_no_flips_enabled_has_filter_headers(self) -> None:
        from supertrend_optimizer.testing.signal_events import build_signal_events, _FILTER_COLUMN_NAMES

        n = 20
        df = _make_ohlc(n)
        trend = np.ones(n, dtype=np.int8)  # no flips
        trend[0] = 0
        fd = _make_filter_diagnostics(n, signal_bars=[])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "long", em, filter_diagnostics=fd)
        assert len(result) == 0
        for col in _FILTER_COLUMN_NAMES:
            assert col in result.columns, f"Filter header {col!r} must be present even in empty result"

    def test_empty_enabled_filter_columns_position(self) -> None:
        """Even with 0 rows, filter col position must respect plan §9.5."""
        from supertrend_optimizer.testing.signal_events import build_signal_events, _FILTER_COLUMN_NAMES

        n = 20
        df = _make_ohlc(n)
        trend = np.ones(n, dtype=np.int8)
        trend[0] = 0
        fd = _make_filter_diagnostics(n, signal_bars=[])
        em = _get_em()

        result = build_signal_events(df, trend, _ATR, "long", em, filter_diagnostics=fd)
        cols = list(result.columns)
        rev_idx = cols.index("is_reversal")
        exec_idx = cols.index("exec_price")
        for fcol in _FILTER_COLUMN_NAMES:
            fi = cols.index(fcol)
            assert rev_idx < fi < exec_idx


# ---------------------------------------------------------------------------
# Group 7: CLI wiring static verification (plan WP-T6 step 3)
# ---------------------------------------------------------------------------

_TESTS_DIR_T6 = Path(__file__).resolve().parent
_TESTER_ROOT_T6 = _TESTS_DIR_T6.parent
_REPO_ROOT_T6 = _TESTER_ROOT_T6.parent
_DONOR_ROOT_T6 = _REPO_ROOT_T6 / "donor"


class TestCliWiringStaticCheck:
    """cli/tester.py and run_batch_tester.py must pass filter_diagnostics to build_signal_events."""

    def _read_file(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_cli_tester_passes_filter_diagnostics(self) -> None:
        src = self._read_file(_DONOR_ROOT_T6 / "supertrend_optimizer" / "cli" / "tester.py")
        assert "filter_diagnostics=results[0].filter_diagnostics" in src, (
            "cli/tester.py must pass filter_diagnostics=results[0].filter_diagnostics "
            "to build_signal_events (WP-T6 plan step 3)"
        )

    def test_run_batch_tester_passes_filter_diagnostics(self) -> None:
        src = self._read_file(_TESTER_ROOT_T6 / "run_batch_tester.py")
        assert "filter_diagnostics=results[0].filter_diagnostics" in src, (
            "run_batch_tester.py must pass filter_diagnostics=results[0].filter_diagnostics "
            "to build_signal_events (WP-T6 plan step 3)"
        )

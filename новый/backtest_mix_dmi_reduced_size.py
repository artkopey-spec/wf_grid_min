from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path("MIX_ST_19_4_eight_loss_streak_market_regime.xlsx")
ENTRY_SOURCE = Path("MIX_ST_19_4_LONG_deeper_entries_impulse_07.xlsx")
OUTPUT = Path("MIX_ST_19_4_DMI_reduced_position_backtest.xlsx")
ENTRY_VARIANT = "100_at_0.4_plus_100_after_close_above_peak"
COMMISSION = 0.0000666667
DMI_THRESHOLD = 27.5
SPLIT = pd.Timestamp("2025-10-15 13:26:00")


def max_drawdown(values):
    curve = np.r_[0.0, np.cumsum(np.asarray(values, dtype=float))]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def longest_streak(frame, positive):
    signs = np.where(frame["net_pnl_dynamic"].to_numpy() > 0, 1, -1)
    target = 1 if positive else -1
    best = current = 0
    for sign in signs:
        current = current + 1 if sign == target else 0
        best = max(best, current)
    return best


def summarize(name, frame):
    frame = frame.sort_values("signal_time")
    pnl = frame["net_pnl_dynamic"].astype(float)
    ret = frame["return_pct_dynamic"].astype(float)
    losses = -pnl[pnl < 0].sum()
    return {
        "variant": name,
        "trades": len(frame),
        "risk_regime_trades": int(frame["risk_regime"].sum()),
        "win_rate_pct": 100 * (pnl > 0).mean(),
        "average_return_pct": ret.mean(),
        "sum_return_pct": ret.sum(),
        "profit_factor": pnl[pnl > 0].sum() / losses if losses else np.nan,
        "max_drawdown_pct": max_drawdown(ret),
        "worst_trade_pct": ret.min(),
        "max_consecutive_losses": longest_streak(frame, False),
        "max_consecutive_wins": longest_streak(frame, True),
        "net_pnl_rub": pnl.sum(),
        "max_drawdown_rub": max_drawdown(pnl),
    }


def main():
    trades = pd.read_excel(SOURCE, sheet_name="All_Trade_Features")
    trades["signal_time"] = pd.to_datetime(trades["signal_time"])
    trades = trades.sort_values("signal_time").reset_index(drop=True)
    entries = pd.read_excel(ENTRY_SOURCE, sheet_name="Trades")
    entries = entries[entries["variant"] == ENTRY_VARIANT][
        ["movement_id", "leg_04_price", "breakout_price"]
    ].copy()
    trades = trades.merge(entries, on="movement_id", how="left")
    trades["dmi_downtrend_strength"] = trades["minus_di14"] - trades["plus_di14"]
    trades["risk_regime"] = trades["dmi_downtrend_strength"] > DMI_THRESHOLD

    variants = [
        ("Baseline 10 + 10", 10, 10),
        ("Risk regime: first 5, add 10", 5, 10),
        ("Risk regime: first 5, add 5", 5, 5),
        ("Risk regime: first 5, no add", 5, 0),
    ]
    summaries = []
    detail_frames = []
    for name, risk_first_qty, risk_add_qty in variants:
        frame = trades.copy()
        frame["first_qty"] = np.where(frame["risk_regime"], risk_first_qty, 10)
        normal_add = np.where(frame["second_leg"], 10, 0)
        risk_add = np.where(frame["second_leg"], risk_add_qty, 0)
        frame["add_qty"] = np.where(frame["risk_regime"], risk_add, normal_add).astype(int)
        frame["total_qty"] = frame["first_qty"] + frame["add_qty"]
        first_price = frame["leg_04_price"].astype(float)
        add_price = frame["breakout_price"].astype(float)
        exit_price = frame["exit_price"].astype(float)
        gross = (exit_price - first_price) * frame["first_qty"]
        gross += np.where(frame["add_qty"] > 0, (exit_price - add_price) * frame["add_qty"], 0.0)
        entry_cost = first_price * frame["first_qty"] * COMMISSION
        entry_cost += np.where(frame["add_qty"] > 0, add_price * frame["add_qty"] * COMMISSION, 0.0)
        exit_cost = exit_price * frame["total_qty"] * COMMISSION
        frame["net_pnl_dynamic"] = gross - entry_cost - exit_cost
        # Comparable system return: always normalized to the original Long10 capital unit.
        frame["return_pct_dynamic"] = frame["net_pnl_dynamic"] / (first_price * 10) * 100
        frame["size_variant"] = name
        detail_frames.append(frame)
        summaries.extend([
            summarize(f"{name} — full", frame),
            summarize(f"{name} — train", frame[frame["signal_time"] <= SPLIT]),
            summarize(f"{name} — OOS", frame[frame["signal_time"] > SPLIT]),
        ])

    details = pd.concat(detail_frames, ignore_index=True)
    summary = pd.DataFrame(summaries)
    risk_only_rows = []
    for name, _, _ in variants:
        part = details[(details["size_variant"] == name) & details["risk_regime"]]
        risk_only_rows.append(summarize(name, part))
    risk_only = pd.DataFrame(risk_only_rows)

    baseline_calc = details[details["size_variant"] == variants[0][0]].sort_values("movement_id")
    baseline_source = trades.sort_values("movement_id")
    audit = pd.DataFrame({
        "movement_id": baseline_calc["movement_id"].to_numpy(),
        "source_pnl": baseline_source["net_pnl_rub_10"].to_numpy(),
        "recalculated_pnl": baseline_calc["net_pnl_dynamic"].to_numpy(),
    })
    audit["difference"] = audit["recalculated_pnl"] - audit["source_pnl"]

    method = pd.DataFrame([
        ["Risk regime", f"completed daily -DI(14) minus +DI(14) > {DMI_THRESHOLD}"],
        ["Primary tested rule", "only first entry is reduced from 10 to 5 contracts; the existing add remains +10 when allowed"],
        ["Control 1", "both first entry and add are halved to 5 + 5 in the risk regime"],
        ["Control 2", "first entry is 5 and no add is allowed in the risk regime"],
        ["Normal regime", "unchanged final strategy: first 10 and possible add +10"],
        ["Execution", "entry and exit times/prices remain those of the final strategy; the sole risk-regime add was profitable and did not approach the altered weighted-average stop"],
        ["Percentage comparison", "all returns are normalized to the standard first-10-contract notional, so reduced sizing correctly reduces both profit and loss contribution"],
        ["Costs", "commission 0.00666667% per side included; no slippage"],
        ["OOS", f"fixed chronological test starts after {SPLIT}"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        risk_only.to_excel(writer, sheet_name="Risk_Regime_Only", index=False)
        details.to_excel(writer, sheet_name="All_Variant_Trades", index=False)
        audit.to_excel(writer, sheet_name="Baseline_Audit", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(summary.to_string(index=False))
    print("\nRisk regime only\n", risk_only.to_string(index=False))
    print("max baseline audit difference", audit["difference"].abs().max())
    print("saved", OUTPUT.resolve())


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path

import pandas as pd

from test_short_impulse_volume_exception import (
    ADD5_XLSX,
    FINAL_XLSX,
    OUT_DIR,
    SPLIT,
    load_bars,
    load_variant,
    locate_breakout_time,
    replay_add,
    stats,
)


P60 = 2.922962
OUT_XLSX = OUT_DIR / "MIX_ST_19_4_RVOL_MIN30_ADD_POLICY_TEST.xlsx"


def apply_policy(baseline, bars, add_qty):
    frame = baseline.copy()
    frame["breakout_time_candidate"] = [locate_breakout_time(row, bars) for _, row in frame.iterrows()]
    frame["desired_second_leg"] = (
        (frame["bars"] > 30)
        & frame["impulse_rvol"].notna()
        & (frame["impulse_rvol"] < P60)
        & frame["breakout_time_candidate"].notna()
    )
    frame["baseline_second_leg"] = frame["second_leg"].astype(bool)
    frame["policy_changed"] = frame["desired_second_leg"] != frame["baseline_second_leg"]
    frame["baseline_return_pct"] = frame["return_pct"]
    frame["baseline_pnl_rub"] = frame["net_pnl_rub_10"]
    frame["baseline_exit_reason"] = frame["exit_reason"]

    for index, row in frame[frame["policy_changed"]].iterrows():
        target_add_qty = add_qty if bool(row["desired_second_leg"]) else 0
        result = replay_add(
            row,
            bars,
            add_qty=target_add_qty,
            breakout_time=pd.Timestamp(row["breakout_time_candidate"]),
        )
        for key, value in result.items():
            frame.at[index, key] = value

    frame["delta_return_points"] = frame["return_pct"] - frame["baseline_return_pct"]
    frame["delta_pnl_rub"] = frame["net_pnl_rub_10"] - frame["baseline_pnl_rub"]
    return frame


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_bars()
    add5 = load_variant(ADD5_XLSX)
    add10 = load_variant(FINAL_XLSX)

    summary_rows = []
    results = []
    for label, baseline, quantity in (("Long10+5", add5, 5), ("Long10+10", add10, 10)):
        tested = apply_policy(baseline, bars, quantity)
        results.append((label, baseline, tested))
        for suffix, frame in (("Current P60", baseline), ("P60 + duration >30", tested)):
            for segment, part in (
                ("Full", frame),
                ("Train", frame[frame["signal_time"] <= SPLIT]),
                ("OOS", frame[frame["signal_time"] > SPLIT]),
            ):
                summary_rows.append(stats(f"{label} · {suffix}", segment, part))

    summary = pd.DataFrame(summary_rows)
    changed = pd.concat(
        [tested[tested["policy_changed"]].assign(size_variant=label) for label, _, tested in results],
        ignore_index=True,
    )
    duration_counts = pd.DataFrame([
        {
            "segment": segment,
            "trades": len(part),
            "impulses_le_30": int((part["bars"] <= 30).sum()),
            "current_adds_le_30": int(((part["bars"] <= 30) & part["second_leg"].astype(bool)).sum()),
        }
        for segment, part in (
            ("Full", add10),
            ("Train", add10[add10["signal_time"] <= SPLIT]),
            ("OOS", add10[add10["signal_time"] > SPLIT]),
        )
    ])
    method = pd.DataFrame([
        ["No add", "Impulse duration <= 30 one-minute bars"],
        ["Add", f"Impulse duration >30, RVol < frozen P60 {P60:.6f}, confirmed breakout"],
        ["Missing RVol", "No add"],
        ["Unchanged", "First Long10, breakout confirmation, weighted stop 1.5%, 30-minute weakness check, next-session exit, commission"],
        ["Replay", "Only trades whose second-leg decision changes are replayed minute by minute"],
    ], columns=["item", "definition"])

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        duration_counts.to_excel(writer, sheet_name="Duration_Counts", index=False)
        changed.to_excel(writer, sheet_name="Changed_Trades", index=False)
        results[0][2].to_excel(writer, sheet_name="Add5_All_Trades", index=False)
        results[1][2].to_excel(writer, sheet_name="Add10_All_Trades", index=False)
        method.to_excel(writer, sheet_name="Method", index=False)

    print(duration_counts.to_string(index=False))
    print("\n", summary.to_string(index=False))
    print("\nChanged:")
    print(changed[["size_variant", "movement_id", "signal_time", "bars", "impulse_rvol", "baseline_second_leg", "desired_second_leg", "delta_return_points", "delta_pnl_rub"]].to_string(index=False))
    print("\nOutput:", OUT_XLSX)


if __name__ == "__main__":
    main()

"""Combine majority-amplitude ZZ threshold reports into one workbook."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from analyze_st_segment_zigzag_clusters import style


ROOT = Path("results_tester_st_batch_20260828")
INPUTS = [
    (1.00, ROOT / "st80_4_global_zz0p2_over1p0_majority_amplitude_09_19.json"),
    (1.25, ROOT / "st80_4_global_zz0p2_over1p25_majority_amplitude_09_19.json"),
    (1.50, ROOT / "st80_4_global_zz0p2_over1p5_majority_amplitude_09_19.json"),
    (1.75, ROOT / "st80_4_global_zz0p2_over1p75_majority_amplitude_09_19.json"),
    (2.00, ROOT / "st80_4_global_zz0p2_over2p0_majority_amplitude_09_19.json"),
]
OUTPUT = ROOT / "st80_4_global_zz0p2_majority_threshold_comparison_09_19.xlsx"


def main() -> None:
    comparison, distributions, clusters = [], [], []
    for threshold, path in INPUTS:
        data = json.loads(path.read_text(encoding="utf-8"))
        summary = data["summary"]
        segments = summary["Completed intraday ST segments"]
        legs = summary["Total qualifying ZZ legs"]
        active = summary["Segments with >=1 qualifying leg"]
        comparison.append({
            "Leg Threshold Strictly Greater %": threshold,
            "Selected ST Segments": segments,
            "Assigned Legs": legs,
            "Mean Legs per Segment": summary[f"Mean ZZ legs >{threshold:g}% per ST segment"],
            "Segments per Leg": segments / legs if legs else None,
            "Legs per 222 Selected Days": legs / 222,
            "Selected Days per Leg": 222 / legs if legs else None,
            "Segments with >=1 Leg": active,
            "Active Segment Share %": active / segments * 100,
            "Segments per Active Segment": segments / active if active else None,
            "Mean Legs per Active Segment": summary["Mean count conditional on >=1"],
            "Maximum Legs in Segment": summary["Maximum count"],
            "Global Qualifying Legs": summary["Global qualifying legs across full history"],
            "Global NO_MAJORITY Legs": summary["Global qualifying legs with NO_MAJORITY"],
            "Global NO_MAJORITY Share %": summary["NO_MAJORITY share globally"],
            "Winner Is End Segment %": summary["Winner-is-end-segment share"],
            "Mean Winning Amplitude Share %": summary["Mean winning amplitude share"],
            "Best Cluster K": max(data["cluster_diagnostics"], key=lambda row: row["Silhouette (sample <=1500)"])["K"],
            "Best Silhouette": max(row["Silhouette (sample <=1500)"] for row in data["cluster_diagnostics"]),
        })
        for count, segment_count in data["distribution"].items():
            distributions.append({
                "Leg Threshold %": threshold,
                "Legs in Segment": int(count),
                "Segments": segment_count,
                "Share %": segment_count / segments * 100,
            })
        for profile in data["cluster_profiles"]:
            clusters.append({"Leg Threshold %": threshold, **profile})

    book = Workbook()
    sheet = book.active
    sheet.title = "Threshold_Comparison"
    for name, rows in (("Threshold_Comparison", comparison), ("Distributions", distributions), ("Cluster_Profiles", clusters)):
        current = sheet if name == "Threshold_Comparison" else book.create_sheet(name)
        headers = list(rows[0])
        current.append(headers)
        for row in rows:
            current.append([row[key] for key in headers])

    method = book.create_sheet("Methodology")
    method.append(["Parameter", "Value"])
    for row in [
        ("SuperTrend", "ATR 80 / multiplier 4.0"),
        ("ZigZag", "Global continuous close-based ZZ, reversal 0.2%"),
        ("Leg thresholds", "Strictly greater than 1.00%, 1.25%, 1.50%, 1.75%, 2.00%"),
        ("Amplitude allocation", "New UP maxima / DOWN minima increments allocated to their ST segment"),
        ("Assignment", "One leg assigned once to the segment accumulating strictly >50% of full amplitude"),
        ("NO_MAJORITY", "Not forced into a segment and excluded from selected assigned-leg counts"),
        ("Selected segment", "One of 222 dates; both flips in [09:00, 19:00) on the same date"),
    ]:
        method.append(row)
    for current in book.worksheets:
        style(current)
    book.save(OUTPUT)
    json_output = OUTPUT.with_suffix(".json")
    json_output.write_text(json.dumps({"comparison": comparison, "distributions": distributions, "cluster_profiles": clusters}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    print(f"Saved: {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()

"""Summarize the independent direct-first WritingBench validation batch."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from chunkloop.datasets.writingbench import load_cases


def summarize(selection: dict, generated: dict, scored: dict, cases: dict) -> dict:
    rows = []
    for item in selection["selected"]:
        index = item["index"]
        case, outputs, grades = cases[index], generated[index], scored[index]
        row = {"index": index, "domain": item["domain"],
               "static_route": item["static_route"],
               "adaptive_attempted_chunk": outputs.get("adaptive_attempted_chunk", False),
               "adaptive_source_mode": outputs["adaptive"].get("source_mode")}
        for mode in ("direct", "adaptive"):
            output = outputs[mode]
            response = output.get("response")
            has_response = isinstance(response, str) and bool(response.strip())
            if has_response and mode not in grades:
                raise ValueError(f"missing score for {index}/{mode}")
            quality = grades[mode]["mean_10"] if has_response else 0.0
            bounds = case.whole_length_bounds()
            count = ((case.measured_word_count(response) if case.lang == "en" else
                      case.measured_han_count(response)) if has_response else None)
            if bounds:
                lower, upper, _ = bounds
                passed = (has_response and (lower is None or count >= lower) and
                          (upper is None or count <= upper))
            else:
                passed = None
            row[f"{mode}_status"] = output.get("status", "missing")
            row[f"{mode}_quality_10"] = quality
            row[f"{mode}_whole_length_count"] = count
            row[f"{mode}_whole_length_pass"] = passed
            row[f"{mode}_seconds"] = output.get("wall_seconds")
            row[f"{mode}_combined_100"] = round(
                80 * quality / 10 + 20 * (passed is not False), 2
            ) if has_response else 0.0
        rows.append(row)

    def aggregate(group):
        result = {"n": len(group),
                  "attempted_chunk": sum(row["adaptive_attempted_chunk"] for row in group),
                  "selected_chunk": sum(row["adaptive_source_mode"] == "chunkloop"
                                        for row in group),
                  "adaptive_statuses": dict(Counter(row["adaptive_status"] for row in group))}
        for metric in ("quality_10", "combined_100", "seconds"):
            for mode in ("direct", "adaptive"):
                values = [row[f"{mode}_{metric}"] for row in group
                          if row[f"{mode}_{metric}"] is not None]
                result[f"{mode}_{metric}"] = round(sum(values) / len(values), 3)
            result[f"delta_{metric}"] = round(
                result[f"adaptive_{metric}"] - result[f"direct_{metric}"], 3)
        constrained = [row for row in group if row["direct_whole_length_pass"] is not None]
        result["whole_length_cases"] = len(constrained)
        for mode in ("direct", "adaptive"):
            result[f"{mode}_whole_length_pass"] = sum(
                row[f"{mode}_whole_length_pass"] is True for row in constrained)
        return result

    return {"metric": "80% rubric mean/10 + 20% whole-output numeric length "
                      "compliance; absent numeric whole length gets full credit; "
                      "empty response gets zero.",
            "all": aggregate(rows),
            "long_route_stratum": aggregate([row for row in rows
                                             if row["static_route"] == "chunkloop"]),
            "direct_route_stratum": aggregate([row for row in rows
                                               if row["static_route"] == "direct"]),
            "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path,
                        default=Path("data/writingbench/benchmark_all.jsonl"))
    parser.add_argument("--folder", type=Path,
                        default=Path("data/writingbench/adaptive-holdout-2026-10-31"))
    parser.add_argument("--score-file", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads((args.folder / "selection.json").read_text(encoding="utf-8"))
    generated = {row["index"]: row for path in args.folder.glob("*/details.json")
                 for row in json.loads(path.read_text(encoding="utf-8"))}
    scored = {row["index"]: row for path in args.score_file
              for row in json.loads(path.read_text(encoding="utf-8"))}
    report = summarize(selection, generated, scored, load_cases(args.dataset))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

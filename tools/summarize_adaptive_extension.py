"""Aggregate the frozen 24-case adaptive extension with paired score reuse."""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from chunkloop.datasets.writingbench import load_cases


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def summarize(selection: dict, details: dict, direct_scores: dict,
              changed_scores: dict, cases: dict) -> dict:
    rows = []
    for item in selection["selected"]:
        index, case = item["index"], cases[item["index"]]
        record = details[index]
        changed = record["adaptive"].get("source_mode") == "chunkloop"
        direct_grade = (direct_scores[index]["direct"]["mean_10"]
                        if index in direct_scores else 0.0)
        adaptive_grade = (changed_scores[index]["adaptive"]["mean_10"] if changed
                          and index in changed_scores else direct_grade)
        row = {"index": index, "domain": item["domain"],
               "attempted_chunk": record.get("adaptive_attempted_chunk", False),
               "selected_chunk": changed,
               "adaptive_status": record["adaptive"].get("status")}
        for mode, grade in (("direct", direct_grade), ("adaptive", adaptive_grade)):
            output = record[mode]
            response = output.get("response")
            has_response = isinstance(response, str) and bool(response.strip())
            if has_response and ((mode == "direct" and index not in direct_scores) or
                                 (mode == "adaptive" and changed and
                                  index not in changed_scores)):
                raise ValueError(f"missing score for generated response {index}/{mode}")
            if not has_response:
                grade = 0.0
            bounds = case.whole_length_bounds()
            count = ((case.measured_word_count(response) if case.lang == "en" else
                      case.measured_han_count(response)) if has_response else None)
            if bounds:
                lower, upper, _ = bounds
                passed = (has_response and (lower is None or count >= lower) and
                          (upper is None or count <= upper))
            else:
                passed = None
            row[f"{mode}_quality_10"] = grade
            row[f"{mode}_length_count"] = count
            row[f"{mode}_length_pass"] = passed
            row[f"{mode}_combined_100"] = round(
                80 * grade / 10 + 20 * (passed is not False), 2
            ) if has_response else 0.0
            row[f"{mode}_seconds"] = output.get("wall_seconds")
        rows.append(row)

    summary = {"n": len(rows), "attempted_chunk": sum(r["attempted_chunk"] for r in rows),
               "selected_chunk": sum(r["selected_chunk"] for r in rows),
               "adaptive_statuses": dict(Counter(r["adaptive_status"] for r in rows))}
    for metric in ("quality_10", "combined_100", "seconds"):
        for mode in ("direct", "adaptive"):
            summary[f"{mode}_{metric}"] = round(
                sum(r[f"{mode}_{metric}"] for r in rows) / len(rows), 3)
        summary[f"delta_{metric}"] = round(
            summary[f"adaptive_{metric}"] - summary[f"direct_{metric}"], 3)
    constrained = [r for r in rows if r["direct_length_pass"] is not None]
    summary["whole_length_cases"] = len(constrained)
    for mode in ("direct", "adaptive"):
        summary[f"{mode}_whole_length_pass"] = sum(
            r[f"{mode}_length_pass"] is True for r in constrained)
    rng = random.Random(20261102)
    for metric in ("quality_10", "combined_100"):
        deltas = [r[f"adaptive_{metric}"] - r[f"direct_{metric}"] for r in rows]
        bootstrap = [sum(rng.choice(deltas) for _ in deltas) / len(deltas)
                     for _ in range(10000)]
        summary[f"delta_{metric}_bootstrap95"] = [
            round(percentile(bootstrap, 0.025), 3),
            round(percentile(bootstrap, 0.975), 3)]
    return {"summary": summary, "rows": rows,
            "method": "Exploratory GLM score; direct score reused for identical adaptive "
                      "output. Empty response scores zero. Paired case bootstrap is "
                      "descriptive, not an official benchmark confidence interval."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path,
                        default=Path("data/writingbench/benchmark_all.jsonl"))
    parser.add_argument("--folder", type=Path,
                        default=Path("data/writingbench/adaptive-extension-2026-10-31"))
    parser.add_argument("--direct-scores", type=Path, required=True)
    parser.add_argument("--changed-scores", type=Path, required=True)
    parser.add_argument("--details-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder
    selection = json.loads((folder / "selection.json").read_text(encoding="utf-8"))
    if args.details_file:
        details = {row["index"]: row for row in json.loads(
            args.details_file.read_text(encoding="utf-8"))}
    else:
        details = {row["index"]: row for path in folder.glob("batch-*/details.json")
                   for row in json.loads(path.read_text(encoding="utf-8"))}
    direct_scores = {row["index"]: row for row in json.loads(
        args.direct_scores.read_text(encoding="utf-8"))}
    changed_scores = {row["index"]: row for row in json.loads(
        args.changed_scores.read_text(encoding="utf-8"))}
    report = summarize(selection, details, direct_scores, changed_scores,
                       load_cases(args.dataset))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

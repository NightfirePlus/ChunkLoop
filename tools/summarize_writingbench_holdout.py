"""Score frozen WritingBench holdout, retaining failed generations as zero."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from chunkloop.datasets.writingbench import load_cases


def summarize(selection: dict, replacement: dict, manual_audit: dict,
              generated: dict[int, dict], scored: dict[int, dict], cases: dict) -> dict:
    chosen = [item for item in selection["selected"]
              if item["index"] != replacement["replaced_index"]]
    chosen.append({"index": replacement["replacement_index"],
                   "domain": cases[replacement["replacement_index"]].domain1,
                   "route": "chunkloop"})
    rows = []
    for item in chosen:
        index, route = item["index"], item["route"]
        case, pair = cases[index], generated[index]
        rubric = scored.get(index, {})
        row = {"index": index, "domain": item["domain"], "route": route}
        for label, mode in (("direct", "direct"), ("routed", route)):
            output = pair.get(mode, {})
            response = output.get("response")
            has_response = isinstance(response, str) and bool(response.strip())
            if has_response and mode not in rubric:
                raise ValueError(f"missing rubric score for {index}/{mode}")
            quality = rubric[mode]["mean_10"] if has_response else 0.0
            row[f"{label}_status"] = output.get("status", "missing")
            row[f"{label}_quality_10"] = quality
            row[f"{label}_seconds"] = output.get("wall_seconds")
            row[f"{label}_has_response"] = has_response
            unit_count = ((case.measured_word_count(response) if case.lang == "en" else
                           case.measured_han_count(response)) if has_response else None)
            row[f"{label}_count"] = unit_count

            whole_range = case.explicit_length()
            if whole_range and has_response:
                primary_pass = whole_range[0] <= unit_count <= whole_range[1]
            elif whole_range:
                primary_pass = False
            else:
                primary_pass = None
            row[f"{label}_range_pass"] = primary_pass
            row[f"{label}_primary_100"] = round(
                80 * quality / 10 + 20 * (primary_pass is not False), 2
            ) if has_response else 0.0

            audited = manual_audit.get(str(index))
            if audited:
                lower, upper, unit, _ = audited
                if unit != ("English words" if case.lang == "en" else "Han characters"):
                    raise ValueError(f"unit mismatch for {index}")
                manual_pass = (has_response and
                               (lower is None or unit_count >= lower) and
                               (upper is None or unit_count <= upper))
            else:
                manual_pass = None
            row[f"{label}_audited_length_pass"] = manual_pass
            row[f"{label}_audited_100"] = round(
                80 * quality / 10 + 20 * (manual_pass is not False), 2
            ) if has_response else 0.0
        rows.append(row)

    def aggregate(group):
        result = {"n": len(group), "status_direct": dict(Counter(
            row["direct_status"] for row in group)),
            "status_routed": dict(Counter(row["routed_status"] for row in group))}
        for field in ("quality_10", "primary_100", "audited_100"):
            for mode in ("direct", "routed"):
                result[f"{mode}_{field}"] = round(
                    sum(row[f"{mode}_{field}"] for row in group) / len(group), 3)
            result[f"delta_{field}"] = round(
                result[f"routed_{field}"] - result[f"direct_{field}"], 3)
        for kind in ("range", "audited_length"):
            relevant = [row for row in group if row[f"direct_{kind}_pass"] is not None]
            result[f"{kind}_cases"] = len(relevant)
            for mode in ("direct", "routed"):
                result[f"{mode}_{kind}_pass"] = sum(
                    row[f"{mode}_{kind}_pass"] is True for row in relevant)
        return result

    return {"method": "Frozen 80/20 range-only primary; separately audited whole-length "
                      "secondary. Failed or empty generations score zero; needs_review "
                      "drafts are scored and their status retained.",
            "all": aggregate(rows),
            "chunk_route": aggregate([row for row in rows if row["route"] == "chunkloop"]),
            "direct_route": aggregate([row for row in rows if row["route"] == "direct"]),
            "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/writingbench/benchmark_all.jsonl"))
    parser.add_argument("--holdout-dir", type=Path,
                        default=Path("data/writingbench/holdout-stratified-2026-10-31"))
    parser.add_argument("--score-file", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    folder = args.holdout_dir
    generated = {}
    for path in folder.glob("*/details.json"):
        for row in json.loads(path.read_text(encoding="utf-8")):
            if row["index"] in generated:
                raise ValueError(f"duplicate generated index {row['index']}")
            generated[row["index"]] = row
    scored = {}
    for path in args.score_file:
        for row in json.loads(path.read_text(encoding="utf-8")):
            scored.setdefault(row["index"], {}).update({
                key: value for key, value in row.items() if key in {"direct", "chunkloop"}})
    selection = json.loads((folder / "selection.json").read_text(encoding="utf-8"))
    replacement = json.loads((folder / "replacement-584.json").read_text(encoding="utf-8"))
    audit = json.loads((folder / "manual-whole-length-audit.json").read_text(encoding="utf-8"))
    report = summarize(selection, replacement, audit["requirements"],
                       generated, scored, load_cases(args.dataset))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

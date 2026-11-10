"""Export saved paired responses in WritingBench's official JSONL input shape."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def export(details_paths: list[Path], output_dir: Path) -> int:
    records: dict[int, dict] = {}
    for path in details_paths:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"expected a list in {path}")
        for row in rows:
            index = row["index"]
            if index in records:
                raise ValueError(f"duplicate case index {index}")
            for mode in ("direct", "adaptive"):
                response = row.get(mode, {}).get("response")
                if not isinstance(response, str) or not response.strip():
                    raise ValueError(f"missing {mode} response for case {index}")
            records[index] = row

    output_dir.mkdir(parents=True, exist_ok=True)
    for mode, filename in (("direct", "baseline.jsonl"),
                           ("adaptive", "adaptive.jsonl")):
        lines = [json.dumps({"index": index, "response": row[mode]["response"]},
                            ensure_ascii=False)
                 for index, row in sorted(records.items())]
        (output_dir / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output_dir / "manifest.json").write_text(
        json.dumps({"n": len(records), "indices": sorted(records),
                    "sources": [str(path) for path in details_paths],
                    "schema": {"index": "integer", "response": "string"}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(f"exported {export(args.details, args.output_dir)} paired cases")


if __name__ == "__main__":
    main()

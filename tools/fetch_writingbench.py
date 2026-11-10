"""Download a pinned WritingBench file through GitHub's official blob API."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import urllib.request
from pathlib import Path

COMMIT = "ae2d5176449b7b769815482641d35926f26793eb"
BLOB = "2d04c2d4c82f8c2d615e963393c7808f64b97129"
SHA256 = "18fee37c645166eb2e206b36366b2e354265b1e4201db2c86e759e825eaddcbe"
URL = f"https://api.github.com/repos/X-PLUG/WritingBench/git/blobs/{BLOB}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/writingbench/benchmark_all.jsonl"))
    args = parser.parse_args()
    if args.output.exists():
        digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
        if digest == SHA256:
            print(f"verified {args.output} sha256={digest}")
            return
        raise SystemExit(f"existing file hash differs: {args.output}")
    request = urllib.request.Request(URL, headers={"User-Agent": "ChunkLoop-WritingBench"})
    with urllib.request.urlopen(request) as response:
        blob = json.load(response)
    if blob.get("sha") != BLOB or blob.get("encoding") != "base64":
        raise ValueError("unexpected GitHub blob response")
    content = base64.b64decode(blob["content"])
    digest = hashlib.sha256(content).hexdigest()
    if digest != SHA256:
        raise ValueError(f"dataset hash mismatch: {digest}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(f"downloaded {args.output} sha256={digest}")


if __name__ == "__main__":
    main()

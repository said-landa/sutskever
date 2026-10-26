#!/usr/bin/env python3
"""Explicitly cache the two official pretrained CPU Watch models.

Run from the repository root:
    .venv/bin/python tools/prepare_stream_models.py --cache-dir local/models

The replay frontend never downloads weights. This preparation command only
downloads these fixed official URLs and verifies the SHA-256 prefix encoded in
each official torchvision filename. Existing corrupt files require an explicit
``--replace-corrupt``; downloads are staged and validated before atomic replace.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
from urllib.request import urlopen


MODELS = (
    ("mobilenet_v3_small-047dcff4.pth", "047dcff4"),
    ("ssdlite320_mobilenet_v3_large_coco-a79551df.pth", "a79551df"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(cache_dir: Path, replace_corrupt: bool = False) -> list[Path]:
    target = cache_dir / "hub" / "checkpoints"
    target.mkdir(parents=True, exist_ok=True)
    prepared = []
    for name, prefix in MODELS:
        destination = target / name
        if destination.exists():
            checksum = sha256(destination)
            if checksum.startswith(prefix):
                print(f"Verified {destination} sha256={checksum}", flush=True)
                prepared.append(destination)
                continue
            if not replace_corrupt:
                raise ValueError(
                    f"Cached weights failed their SHA-256 prefix check: {destination}. "
                    "Inspect the file, then use --replace-corrupt to replace it explicitly."
                )
        url = f"https://download.pytorch.org/models/{name}"
        staged: Path | None = None
        try:
            print(f"Downloading {url}", flush=True)
            with tempfile.NamedTemporaryFile(dir=target, suffix=".download", delete=False) as output:
                staged = Path(output.name)
                with urlopen(url, timeout=60) as response:
                    total = 0
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        total += len(chunk)
                        if total > 100 * 1024 * 1024:
                            raise ValueError(f"Unexpectedly large model download: {name}")
                        output.write(chunk)
            checksum = sha256(staged)
            if not checksum.startswith(prefix):
                raise ValueError(f"Downloaded weights failed SHA-256 prefix check: {name}")
            staged.replace(destination)
            print(f"Cached {destination} bytes={destination.stat().st_size} sha256={checksum}", flush=True)
            prepared.append(destination)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
    return prepared


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path("local/models"))
    parser.add_argument("--replace-corrupt", action="store_true")
    arguments = parser.parse_args()
    prepare(arguments.cache_dir, arguments.replace_corrupt)


if __name__ == "__main__":
    main()

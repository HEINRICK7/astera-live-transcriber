#!/usr/bin/env python3
"""Download and verify a supported Astera STT model explicitly."""

import argparse
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from astera_live_transcriber.infrastructure.engines.parakeet.loader import REQUIRED_FILES

DEFAULT_MODEL = "parakeet-tdt-0.6b-v3-int8"
DEFAULT_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
)


def verify(path: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (path / name).is_file()]
    if missing:
        raise SystemExit(f"model incomplete at {path}; missing: {', '.join(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=[DEFAULT_MODEL])
    parser.add_argument("--target-dir", type=Path, default=Path("models"))
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    destination = args.target_dir / args.model
    if destination.is_dir() and not args.force:
        try:
            verify(destination)
        except SystemExit:
            raise SystemExit(f"existing model is incomplete; remove or use --force: {destination}")
        print(f"model already present: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="astera-model-") as temp_dir:
        archive = Path(temp_dir) / "model.tar.bz2"
        print(f"downloading {args.url}")
        urllib.request.urlretrieve(args.url, archive)
        extract_dir = Path(temp_dir) / "extracted"
        extract_dir.mkdir()
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(extract_dir, filter="data")
        candidates = [path for path in extract_dir.rglob("tokens.txt")]
        if len(candidates) != 1:
            raise SystemExit("downloaded archive does not contain one recognizable model")
        source = candidates[0].parent
        verify(source)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    verify(destination)
    print(f"model ready: {destination}")


if __name__ == "__main__":
    main()

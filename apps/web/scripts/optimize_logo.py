from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

MAX_EDGE = 512


def optimize(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)

    before = path.stat().st_size
    with Image.open(path) as source:
        image = source.copy()

    if image.width > MAX_EDGE or image.height > MAX_EDGE:
        image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)

    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

    temporary = path.with_suffix(".optimized.png")
    image.save(temporary, format="PNG", optimize=True, compress_level=9)
    temporary.replace(path)
    after = path.stat().st_size
    print(f"optimized {path}: {before} -> {after} bytes ({image.width}x{image.height})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: optimize_logo.py <png> [<png> ...]")
    for argument in sys.argv[1:]:
        optimize(Path(argument))

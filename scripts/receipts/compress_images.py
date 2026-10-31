"""Losslessly recompresses JPEG and PNG evidence images in place, keeping all metadata.

JPEGs go through jpegtran (optimised Huffman tables, baseline or progressive, whichever is
smaller) and PNGs through oxipng. A result replaces the file only if it is smaller and decodes
to exactly the same pixels; the file keeps its name, permissions and modification time. Other
formats, 16-bit PNGs and files that don't shrink are left as they are. Directories are searched
recursively for .jpg, .jpeg and .png files.

    uv run --with pillow --with pyoxipng python scripts/receipts/compress_images.py ledger/Bills/2026-10-27

Needs jpegtran on PATH: libjpeg-turbo-progs on Debian, jpeg-turbo on Homebrew.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import oxipng
from PIL import Image

# Full-page captures can exceed Pillow's decompression-bomb limit.
Image.MAX_IMAGE_PIXELS = None
EXTENSIONS = {".jpg", ".jpeg", ".png"}


def same_pixels(a: Path, b: Path) -> bool:
    with Image.open(a) as x, Image.open(b) as y:
        if x.size != y.size:
            return False
        if x.mode != y.mode:
            # oxipng may drop an unused alpha channel or switch to a palette. Comparing as
            # RGBA is exact because 16-bit PNGs never get this far.
            x, y = x.convert("RGBA"), y.convert("RGBA")
        return x.tobytes() == y.tobytes()


def candidates(path: Path, fmt: str, work: Path) -> list[Path]:
    if fmt == "PNG":
        out = work / "oxipng.png"
        oxipng.optimize(path, out, level=4)
        return [out]
    outs = []
    for name, extra in (("baseline", []), ("progressive", ["-progressive"])):
        out = work / f"{name}.jpg"
        subprocess.run(
            ["jpegtran", "-copy", "all", "-optimize", *extra, "-outfile", str(out), str(path)],
            check=True,
        )
        outs.append(out)
    return outs


def compress(path: Path) -> tuple[str, int]:
    """Returns a description and the bytes saved; raises ValueError if pixels would change."""
    try:
        with Image.open(path) as image:
            fmt = image.format
    except OSError:
        return "left as is: not an image", 0
    if fmt not in ("JPEG", "PNG"):
        return f"left as is: {fmt}", 0
    # A PNG's bit depth is byte 24, in the IHDR chunk.
    if fmt == "PNG" and path.read_bytes()[24] == 16:
        return "left as is: 16-bit PNG", 0
    before = path.stat()
    with tempfile.TemporaryDirectory() as work:
        best = min(candidates(path, fmt, Path(work)), key=lambda p: p.stat().st_size)
        size = best.stat().st_size
        if size >= before.st_size:
            return "already compact", 0
        if not same_pixels(path, best):
            raise ValueError("the compressed copy has different pixels")
        # Copied next to the file first, so the swap is a single rename.
        staged = path.with_name(f".{path.name}.compressing")
        shutil.copyfile(best, staged)
        os.chmod(staged, before.st_mode & 0o7777)
        os.utime(staged, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(staged, path)
    saved = before.st_size - size
    return f"{before.st_size:,} -> {size:,} bytes, {100 * saved / before.st_size:.0f}% smaller", saved


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    files, total, failed = [], 0, 0
    for arg in map(Path, sys.argv[1:]):
        if arg.is_dir():
            files += sorted(
                p for p in arg.rglob("*")
                if p.is_file() and p.suffix.lower() in EXTENSIONS and not p.name.startswith(".")
            )
        elif arg.is_file():
            files.append(arg)
        else:
            print(f"{arg}: not found")
            failed += 1
    for path in files:
        try:
            result, saved = compress(path)
        except (ValueError, subprocess.CalledProcessError, oxipng.PngError) as error:
            result, saved, failed = f"left as is: {error}", 0, failed + 1
        total += saved
        print(f"{path}: {result}")
    print(f"{len(files)} files, {total:,} bytes saved, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

"""
img-compr.py — compress images in a folder
Usage:
    python img-compr.py <input_dir> [output_dir] [--quality 80] [--max-size 1920]

- Supports JPEG, PNG, WebP
- PNG is converted to WebP for best compression; JPEG/WebP are re-encoded at --quality
- If output_dir is omitted, files are saved alongside originals with a _compressed suffix
- --max-size caps the longest edge (preserves aspect ratio); 0 = no resize
"""

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install pillow")

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp"}


def compress(src: Path, dst: Path, quality: int, max_size: int) -> tuple[int, int]:
    img = Image.open(src)

    if max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)

    # Flatten transparency for JPEG targets
    if dst.suffix.lower() in (".jpg", ".jpeg") and img.mode in ("RGBA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        img = bg

    save_kwargs: dict = {"optimize": True}
    fmt = dst.suffix.lower()
    if fmt in (".jpg", ".jpeg"):
        save_kwargs["quality"] = quality
    elif fmt == ".webp":
        save_kwargs["quality"] = quality
    elif fmt == ".png":
        # PIL compress_level 0–9; map quality 0–100 → level 9–1
        save_kwargs["compress_level"] = max(1, 9 - round(quality / 100 * 8))

    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, **save_kwargs)
    return src.stat().st_size, dst.stat().st_size


def build_dst(src: Path, input_dir: Path, output_dir: Path | None, png_to_webp: bool) -> Path:
    rel = src.relative_to(input_dir)
    if png_to_webp and src.suffix.lower() == ".png":
        rel = rel.with_suffix(".webp")
    if output_dir:
        return output_dir / rel
    return src.with_stem(src.stem + "_compressed").with_suffix(rel.suffix)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch image compressor")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path, nargs="?", default=None)
    parser.add_argument("--quality", type=int, default=80, metavar="0-100")
    parser.add_argument("--max-size", type=int, default=1920, metavar="PX",
                        help="Cap longest edge in pixels (0 = no resize)")
    parser.add_argument("--no-webp", action="store_true",
                        help="Keep PNG as PNG instead of converting to WebP")
    args = parser.parse_args()

    if not args.input_dir.is_dir():
        sys.exit(f"Not a directory: {args.input_dir}")

    png_to_webp = not args.no_webp
    files = [p for p in args.input_dir.rglob("*") if p.suffix.lower() in SUPPORTED]
    if not files:
        sys.exit("No supported images found.")

    total_before = total_after = 0
    for src in sorted(files):
        dst = build_dst(src, args.input_dir, args.output_dir, png_to_webp)
        before, after = compress(src, dst, args.quality, args.max_size)
        total_before += before
        total_after += after
        pct = (1 - after / before) * 100 if before else 0
        print(f"{src.name} → {dst.name}  {before//1024}KB → {after//1024}KB  ({pct:.0f}% smaller)")

    overall = (1 - total_after / total_before) * 100 if total_before else 0
    print(f"\nTotal: {total_before//1024}KB → {total_after//1024}KB  ({overall:.0f}% smaller)")


if __name__ == "__main__":
    main()

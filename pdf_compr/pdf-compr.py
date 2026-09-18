"""
pdf-compr.py — compress PDFs in a folder to fit under a target size
Usage:
    python pdf-compr.py <input_dir> [output_dir] [--target-size 5]
    python pdf-compr.py <input_dir> [output_dir] --dpi 150   # fixed-quality mode instead

Default mode (no --dpi): auto-escalates through strategies, least to most destructive,
until the file lands comfortably under --target-size MB (default 5, e.g. an email/upload
cap) or every lever is exhausted:
  1. Estimate a starting image DPI from the size ratio (raster image size scales roughly
     with DPI²) and refine it from the actual output size — usually converges in 1-2
     passes instead of scanning a fixed ladder top-down (each pass re-renders every
     embedded image and can take minutes on a large scanned PDF).
  2. If DPI bottoms out and still misses, lower JPEG quality on color/gray images.
  3. If that still misses, drop to grayscale (DeviceGray) and repeat 1-2 — a big win for
     scanned/photo content that doesn't need color, at the cost of losing color.
Different PDFs respond very differently — a scanned book shrinks a lot from DPI alone,
a vector/text PDF (LaTeX output, plots) barely shrinks from DPI or quality at all since
it has little raster content to downsample; the tool detects when a step makes no
progress and moves to the next strategy rather than wasting passes repeating it.

--dpi mode re-downsamples embedded images at a fixed DPI with no size guarantee — one
pass, useful when size doesn't matter but speed does. No auto-escalation.

--grayscale forces DeviceGray output from the start instead of only as a last resort —
skip straight there when you already know it's a B&W scan.

- Recompresses embedded images via Ghostscript's pdfwrite device, then — if qpdf is
  installed — runs a lossless structural pass (object streams, max Flate recompression)
  on top. Image recompression alone barely helps a vector/text-heavy PDF (formulas,
  plots, hundreds of embedded font subsets, few or no raster images); qpdf's pass picks
  up real extra savings there that pure image tools miss.
- If output_dir is omitted, files are saved alongside originals with a _compressed suffix
- Requires Ghostscript on PATH: https://www.ghostscript.com/releases/gsdnld.html
  (Windows installs as gswin64c/gswin32c, not gs — both are detected automatically)
- qpdf is optional but recommended: winget install QPDF.QPDF
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

SUPPORTED = {".pdf"}

GS_CANDIDATES = ["gs", "gswin64c", "gswin32c"]

# Safety margin so we land comfortably under --target-size, not skimming right at it
# (also guards against callers meaning decimal MB — 1000*1000 — vs our binary 1024*1024).
SAFETY_MARGIN = 0.92

MIN_DPI = 50
MAX_DPI = 150
MAX_ATTEMPTS = 4

DEFAULT_JPEGQ = 75
# Once DPI bottoms out at MIN_DPI without hitting target, fall back to these in order.
JPEGQ_FALLBACK = [60, 45, 30]


def find_gs() -> str:
    for name in GS_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    sys.exit(
        "Ghostscript is required: https://www.ghostscript.com/releases/gsdnld.html\n"
        f"(looked for: {', '.join(GS_CANDIDATES)})"
    )


def find_qpdf() -> str | None:
    return shutil.which("qpdf")


def qpdf_optimize(qpdf_bin: str, dst: Path) -> None:
    """Lossless structural pass on top of Ghostscript's output: object streams +
    max Flate recompression. Only ever keeps the result if it's actually smaller."""
    tmp = dst.with_suffix(dst.suffix + ".qpdf_tmp")
    result = subprocess.run(
        [qpdf_bin, "--optimize-images", "--object-streams=generate",
         "--recompress-flate", "--compression-level=9", str(dst), str(tmp)],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and tmp.exists() and tmp.stat().st_size < dst.stat().st_size:
        tmp.replace(dst)
    else:
        tmp.unlink(missing_ok=True)


PAGE_RE = re.compile(r"^Page (\d+)")
BAR_WIDTH = 24


def ps_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def count_pages(gs_bin: str, src: Path) -> int:
    cmd = [gs_bin, "-q", "-dNODISPLAY", "-c",
           f"({ps_escape(str(src))}) (r) file runpdfbegin pdfpagecount = quit"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return int(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def run_gs(gs_bin: str, qpdf_bin: str | None, src: Path, dst: Path, dpi: int, jpegq: int,
           grayscale: bool, total_pages: int, label: str) -> None:
    cmd = [
        # 1.5, not 1.4: downgrading below the source's actual PDF version (1.5 added
        # compressed object streams) can make already-optimized, vector/text-heavy PDFs
        # bigger than the original instead of smaller.
        gs_bin, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5", "-dPDFSETTINGS=/ebook",
        "-dNOPAUSE", "-dBATCH",
        "-dDownsampleColorImages=true", "-dColorImageDownsampleType=/Bicubic", f"-dColorImageResolution={dpi}",
        "-dAutoFilterColorImages=false", "-dColorImageFilter=/DCTEncode",
        "-dDownsampleGrayImages=true", "-dGrayImageDownsampleType=/Bicubic", f"-dGrayImageResolution={dpi}",
        "-dAutoFilterGrayImages=false", "-dGrayImageFilter=/DCTEncode",
        f"-dJPEGQ={jpegq}",
        # /Subsample (not /Bicubic) for mono: these are typically 1-bit CCITT-G4 scan
        # pages, and Bicubic forces them off that efficient bilevel encoding, bloating size.
        "-dDownsampleMonoImages=true", "-dMonoImageDownsampleType=/Subsample", f"-dMonoImageResolution={dpi}",
        f"-sOutputFile={dst}", str(src),
    ]
    if grayscale:
        cmd += ["-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    output_lines = []
    last_pct = -1
    for line in proc.stdout:
        output_lines.append(line)
        m = PAGE_RE.match(line)
        if m and total_pages:
            pct = min(100, int(m.group(1)) * 100 // total_pages)
            if pct != last_pct:
                filled = pct * BAR_WIDTH // 100
                bar = "#" * filled + "-" * (BAR_WIDTH - filled)
                print(f"\r{label}: [{bar}] {pct}%", end="", flush=True)
                last_pct = pct
    proc.wait()
    if last_pct >= 0:
        print()
    if proc.returncode != 0:
        sys.exit(f"Ghostscript failed on {src.name}:\n{''.join(output_lines)[-2000:]}")
    if qpdf_bin:
        qpdf_optimize(qpdf_bin, dst)


def _fit_size(gs_bin: str, qpdf_bin: str | None, src: Path, dst: Path, target_bytes: float,
              total_pages: int, start_jpegq: int, grayscale: bool, original_size: int) -> tuple[int, bool]:
    """One DPI+quality search at a fixed grayscale setting. Returns (final_size, fits)."""
    dpi = MAX_DPI
    if original_size > target_bytes:
        # Raster image size scales roughly with DPI^2, so jump straight to a plausible
        # DPI instead of scanning down from MAX_DPI one full (slow) pass at a time.
        ratio = (target_bytes / original_size) ** 0.5
        dpi = max(MIN_DPI, min(MAX_DPI, round(MAX_DPI * ratio)))
    jpegq = start_jpegq
    jpegq_steps = [q for q in JPEGQ_FALLBACK if q < start_jpegq]

    size = prev_size = None
    for attempt in range(MAX_ATTEMPTS + len(JPEGQ_FALLBACK)):
        tag = "/gray" if grayscale else ""
        run_gs(gs_bin, qpdf_bin, src, dst, dpi, jpegq, grayscale, total_pages,
               f"  {src.name} @ {dpi}dpi/q{jpegq}{tag} (pass {attempt + 1})")
        size = dst.stat().st_size
        print(f"  {src.name}: {dpi} DPI, quality {jpegq}{tag} → {size/1024/1024:.1f}MB")
        if size <= target_bytes:
            return size, True
        if prev_size is not None and size >= prev_size * 0.99:
            # No meaningful gain from the last change (e.g. a vector/text-heavy PDF
            # with little raster content) — further passes at this setting won't help.
            return size, False
        prev_size = size
        if dpi > MIN_DPI:
            # Refine the estimate from the actual compression ratio just observed.
            dpi = max(MIN_DPI, round(dpi * (target_bytes / size) ** 0.5))
        elif jpegq_steps:
            jpegq = jpegq_steps.pop(0)
        else:
            return size, False
    return size, False


def compress_to_size(gs_bin: str, qpdf_bin: str | None, src: Path, dst: Path, target_mb: float,
                      start_jpegq: int, grayscale: bool) -> tuple[int, int]:
    target_bytes = target_mb * 1024 * 1024 * SAFETY_MARGIN
    total_pages = count_pages(gs_bin, src)
    original_size = src.stat().st_size

    size, fits = _fit_size(gs_bin, qpdf_bin, src, dst, target_bytes, total_pages,
                            start_jpegq, grayscale, original_size)
    if not fits and not grayscale:
        print(f"  {src.name}: still over target in color, retrying in grayscale...")
        size, fits = _fit_size(gs_bin, qpdf_bin, src, dst, target_bytes, total_pages,
                                start_jpegq, True, original_size)
    return original_size, size


def compress_fixed(gs_bin: str, qpdf_bin: str | None, src: Path, dst: Path,
                    dpi: int, jpegq: int, grayscale: bool) -> tuple[int, int]:
    total_pages = count_pages(gs_bin, src)
    run_gs(gs_bin, qpdf_bin, src, dst, dpi, jpegq, grayscale, total_pages, f"  {src.name} @ {dpi}dpi/q{jpegq}")
    return src.stat().st_size, dst.stat().st_size


def build_dst(src: Path, input_dir: Path, output_dir: Path | None) -> Path:
    rel = src.relative_to(input_dir)
    if output_dir:
        return output_dir / rel
    return src.with_stem(src.stem + "_compressed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch PDF compressor")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path, nargs="?", default=None)
    parser.add_argument("--target-size", type=float, default=5, metavar="MB",
                         help="Target max output size in MB (default 5). Ignored if --dpi given.")
    parser.add_argument("--dpi", type=int, default=None, metavar="DPI",
                         help="Fixed-quality mode instead of size targeting "
                              "(image downsample DPI, no size guarantee)")
    parser.add_argument("--quality", type=int, default=DEFAULT_JPEGQ, metavar="0-100",
                         help=f"JPEG quality for color/gray images (default {DEFAULT_JPEGQ}). "
                              "In --target-size mode this is just the starting point.")
    parser.add_argument("--grayscale", action="store_true",
                         help="Drop color entirely (DeviceGray output) — big win for scans that don't need color")
    args = parser.parse_args()

    gs_bin = find_gs()
    qpdf_bin = find_qpdf()
    if not qpdf_bin:
        print("note: qpdf not found, skipping the extra structural-optimization pass "
              "(install for smaller output: winget install QPDF.QPDF)\n")

    if not args.input_dir.is_dir():
        sys.exit(f"Not a directory: {args.input_dir}")

    files = [p for p in args.input_dir.rglob("*") if p.suffix.lower() in SUPPORTED]
    if not files:
        sys.exit("No PDFs found.")

    target_bytes = args.target_size * 1024 * 1024
    total_before = total_after = 0
    under_target = 0
    for src in sorted(files):
        dst = build_dst(src, args.input_dir, args.output_dir)

        if args.dpi is not None:
            before, after = compress_fixed(gs_bin, qpdf_bin, src, dst, args.dpi, args.quality, args.grayscale)
        else:
            before, after = compress_to_size(gs_bin, qpdf_bin, src, dst, args.target_size,
                                              args.quality, args.grayscale)

        total_before += before
        total_after += after
        pct = (1 - after / before) * 100 if before else 0
        fits = after <= target_bytes
        under_target += args.dpi is None and fits
        over = "  ⚠ over target" if args.dpi is None and not fits else ""
        print(f"{src.name} → {dst.name}  {before/1024/1024:.1f}MB → {after/1024/1024:.1f}MB  "
              f"({pct:.0f}% smaller){over}")

    if args.dpi is None:
        print(f"\n{under_target}/{len(files)} files under {args.target_size:.0f}MB target")
    else:
        overall = (1 - total_after / total_before) * 100 if total_before else 0
        print(f"\nTotal: {total_before/1024/1024:.1f}MB → {total_after/1024/1024:.1f}MB  ({overall:.0f}% smaller)")


if __name__ == "__main__":
    main()

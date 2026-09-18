"""
video-compr.py — compress videos in a folder to fit a target size
Usage:
    python video-compr.py <input_dir> [output_dir] [--target-size 5]
    python video-compr.py <input_dir> [output_dir] --crf 28   # fixed-quality mode instead

Default mode (no --crf): two-pass encode that lands each file at/just-under
--target-size MB (default 5, e.g. a storage/upload cap). Resolution and audio
bitrate are auto-picked per clip to spend the size budget on video quality
first — a long clip gets downscaled instead of starving its video bitrate.
Pass --max-height / --audio-bitrate to pin either one instead of auto-picking.

--crf mode re-encodes at a fixed quality with no size guarantee (original
single-pass behavior) — useful when size doesn't matter but quality/speed does.

- Supports MP4, MOV, MKV, AVI, WebM
- Re-encodes video with libx264/libx265/libvpx-vp9; audio to AAC (Opus for vp9)
- If output_dir is omitted, files are saved alongside originals with a _compressed suffix
- Requires ffmpeg + ffprobe on PATH: https://ffmpeg.org/download.html
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

SUPPORTED = {".mp4", ".mov", ".mkv", ".avi", ".webm"}

CODECS = {
    "h264": "libx264",
    "h265": "libx265",
    "vp9": "libvpx-vp9",
    "h264_nvenc": "h264_nvenc",
    "h265_nvenc": "hevc_nvenc",
}
NVENC_CODECS = {"h264_nvenc", "h265_nvenc"}

# x264-style --preset names have no NVENC equivalent; map onto NVENC's p1 (fastest)..p7 (slowest).
NVENC_PRESET_MAP = {
    "ultrafast": "p1", "superfast": "p1", "veryfast": "p2", "faster": "p3",
    "fast": "p4", "medium": "p4", "slow": "p5", "slower": "p6", "veryslow": "p7",
}

# Safety margin so two-pass VBR (which is an average, not a hard cap) lands
# under --target-size rather than slightly over it.
SAFETY_MARGIN = 0.96

# Rule-of-thumb minimum bitrate (kbps) for a height to still look decent with h264;
# below this a lower resolution generally looks better than a starved higher one.
MIN_BITRATE_KBPS = {1080: 2500, 720: 1200, 480: 600, 360: 350, 240: 200, 144: 100}
HEIGHT_LADDER = sorted(MIN_BITRATE_KBPS, reverse=True)

AUDIO_LADDER = ["128k", "96k", "64k", "48k"]


def parse_bitrate(s: str) -> int:
    s = s.strip().lower()
    if s.endswith("k"):
        return int(float(s[:-1]) * 1_000)
    if s.endswith("m"):
        return int(float(s[:-1]) * 1_000_000)
    return int(s)


def probe(src: Path) -> tuple[float, int]:
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True,
    )
    height = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True,
    )
    if dur.returncode != 0 or not dur.stdout.strip():
        sys.exit(f"Could not read duration of {src.name}:\n{dur.stderr[-500:]}")
    if height.returncode != 0 or not height.stdout.strip():
        sys.exit(f"Could not read resolution of {src.name}:\n{height.stderr[-500:]}")
    return float(dur.stdout.strip()), int(height.stdout.strip())


def pick_height(video_bps: float, original_height: int) -> int:
    candidates = [h for h in HEIGHT_LADDER if h <= original_height] or [original_height]
    for h in candidates:
        if video_bps >= MIN_BITRATE_KBPS.get(h, 0) * 1_000:
            return h
    return candidates[-1]


def pick_audio_bitrate(total_target_bps: float) -> str:
    for label in AUDIO_LADDER:
        if parse_bitrate(label) <= 0.25 * total_target_bps:
            return label
    return AUDIO_LADDER[-1]


def run_ffmpeg(cmd: list[str], src: Path) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed on {src.name}:\n{result.stderr[-2000:]}")


def compress_to_size(src: Path, dst: Path, target_mb: float, max_height: int | None,
                      codec: str, audio_bitrate: str | None, preset: str) -> tuple[int, int]:
    duration, original_height = probe(src)
    target_bytes = target_mb * 1024 * 1024 * SAFETY_MARGIN
    total_target_bps = (target_bytes * 8) / duration

    chosen_audio = audio_bitrate or pick_audio_bitrate(total_target_bps)
    video_bps = max(total_target_bps - parse_bitrate(chosen_audio), 80_000)
    height = max_height if max_height else pick_height(video_bps, original_height)

    vcodec = CODECS[codec]
    acodec = "libopus" if codec == "vp9" else "aac"
    kbps = f"{int(video_bps // 1000)}k"
    print(f"  {src.name}: budget {kbps} video / {chosen_audio} audio @ {height}p "
          f"for {duration:.0f}s")

    dst.parent.mkdir(parents=True, exist_ok=True)
    base = ["ffmpeg", "-y", "-i", str(src), "-c:v", vcodec, "-b:v", kbps,
            "-vf", f"scale=-2:'min({height},ih)'"]

    if codec in NVENC_CODECS:
        # NVENC has its own internal two-pass (-multipass fullres); no external pass/log files.
        cmd = base + ["-preset", NVENC_PRESET_MAP[preset], "-rc", "vbr", "-multipass", "fullres",
                       "-c:a", acodec, "-b:a", chosen_audio, str(dst)]
        run_ffmpeg(cmd, src)
        return src.stat().st_size, dst.stat().st_size

    if codec != "vp9":
        base += ["-preset", preset]

    passlog = dst.parent / f".{dst.stem}_pass"
    try:
        run_ffmpeg(base + ["-pass", "1", "-passlogfile", str(passlog), "-an", "-f", "null", "-"], src)
        run_ffmpeg(base + ["-pass", "2", "-passlogfile", str(passlog),
                            "-c:a", acodec, "-b:a", chosen_audio, str(dst)], src)
    finally:
        for p in dst.parent.glob(f".{dst.stem}_pass*"):
            p.unlink(missing_ok=True)

    return src.stat().st_size, dst.stat().st_size


def compress_crf(src: Path, dst: Path, crf: int, preset: str, max_height: int,
                  codec: str, audio_bitrate: str) -> tuple[int, int]:
    vcodec = CODECS[codec]
    acodec = "libopus" if codec == "vp9" else "aac"

    if codec in NVENC_CODECS:
        # NVENC has no -crf; -cq in VBR mode is the closest equivalent (same 0-51 scale).
        cmd = ["ffmpeg", "-y", "-i", str(src), "-c:v", vcodec, "-preset", NVENC_PRESET_MAP[preset],
               "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    else:
        cmd = ["ffmpeg", "-y", "-i", str(src), "-c:v", vcodec, "-crf", str(crf)]
        if codec == "vp9":
            cmd += ["-b:v", "0"]  # required for CRF-only mode in libvpx-vp9
        else:
            cmd += ["-preset", preset]
    if max_height:
        cmd += ["-vf", f"scale=-2:'min({max_height},ih)'"]
    cmd += ["-c:a", acodec, "-b:a", audio_bitrate, str(dst)]

    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(cmd, src)
    return src.stat().st_size, dst.stat().st_size


def build_dst(src: Path, input_dir: Path, output_dir: Path | None, ext: str) -> Path:
    rel = src.relative_to(input_dir).with_suffix(ext)
    if output_dir:
        return output_dir / rel
    return src.with_stem(src.stem + "_compressed").with_suffix(ext)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch video compressor")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path, nargs="?", default=None)
    parser.add_argument("--target-size", type=float, default=5, metavar="MB",
                         help="Target max output size in MB (default 5). Ignored if --crf given.")
    parser.add_argument("--crf", type=int, default=None, metavar="0-51",
                         help="Fixed-quality mode instead of size targeting "
                              "(lower = better quality/bigger file, no size guarantee)")
    parser.add_argument("--preset", default="medium",
                         choices=["ultrafast", "superfast", "veryfast", "faster", "fast",
                                  "medium", "slow", "slower", "veryslow"],
                         help="Encoder speed/efficiency tradeoff (h264/h265 only)")
    parser.add_argument("--max-height", type=int, default=None, metavar="PX",
                         help="Cap output height in pixels. Default: auto-picked per clip "
                              "in size-targeting mode, no cap in --crf mode.")
    parser.add_argument("--codec", default="h264", choices=list(CODECS),
                         help="Video codec: h264 (compatible), h265 (smaller), vp9 (webm), "
                              "h264_nvenc/h265_nvenc (NVIDIA GPU encode, requires nvenc-capable ffmpeg build)")
    parser.add_argument("--audio-bitrate", default=None, metavar="RATE",
                         help="Default: auto-picked in size-targeting mode, 128k in --crf mode")
    parser.add_argument("--ext", default=None, metavar=".mp4",
                         help="Output container extension (default: same as input, or .webm for vp9)")
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("ffmpeg and ffprobe are required: https://ffmpeg.org/download.html")

    if not args.input_dir.is_dir():
        sys.exit(f"Not a directory: {args.input_dir}")

    files = [p for p in args.input_dir.rglob("*") if p.suffix.lower() in SUPPORTED]
    if not files:
        sys.exit("No supported videos found.")

    target_bytes = args.target_size * 1024 * 1024
    total_before = total_after = 0
    under_target = 0
    for src in sorted(files):
        ext = args.ext or (".webm" if args.codec == "vp9" else src.suffix.lower())
        dst = build_dst(src, args.input_dir, args.output_dir, ext)

        if args.crf is not None:
            before, after = compress_crf(src, dst, args.crf, args.preset, args.max_height or 0,
                                          args.codec, args.audio_bitrate or "128k")
        else:
            before, after = compress_to_size(src, dst, args.target_size, args.max_height,
                                              args.codec, args.audio_bitrate, args.preset)

        total_before += before
        total_after += after
        pct = (1 - after / before) * 100 if before else 0
        fits = after <= target_bytes
        under_target += args.crf is None and fits
        over = "  ⚠ over target" if args.crf is None and not fits else ""
        print(f"{src.name} → {dst.name}  {before/1024/1024:.1f}MB → {after/1024/1024:.1f}MB  "
              f"({pct:.0f}% smaller){over}")

    if args.crf is None:
        print(f"\n{under_target}/{len(files)} files under {args.target_size:.0f}MB target")
    else:
        overall = (1 - total_after / total_before) * 100 if total_before else 0
        print(f"\nTotal: {total_before/1024/1024:.1f}MB → {total_after/1024/1024:.1f}MB  ({overall:.0f}% smaller)")


if __name__ == "__main__":
    main()

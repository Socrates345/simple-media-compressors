# Simple Media Compressors

Four standalone Python scripts for batch-compressing images, PDFs, and videos, plus
converting EPUBs to PDF, from the command line. Point a script at a folder, get smaller
(or converted) files out.

| Tool | Script | Does |
|---|---|---|
| Images | `img-compr/img-compr.py` | Compresses JPEG, PNG, WebP |
| PDFs | `pdf_compr/pdf-compr.py` | Compresses PDF |
| Video | `video_compr/video-compr.py` | Compresses MP4, MOV, MKV, AVI, WebM |
| EPUB → PDF | `epub_pdf/epub-to-pdf.py` | Converts EPUB to PDF |

## Setup

1. Install Python 3.10+
2. `pip install -r requirements.txt` — installs Pillow (image compressor) and
   ebooklib/beautifulsoup4/xhtml2pdf (EPUB→PDF converter).
3. Install the external tool(s) for whichever compressor(s) you're using:
   - **PDFs** — [Ghostscript](https://www.ghostscript.com/releases/gsdnld.html), must be on
     PATH (Windows: `choco install ghostscript`). [qpdf](https://qpdf.sourceforge.io/) is
     optional but recommended (`winget install QPDF.QPDF`) — adds a lossless extra pass.
   - **Video** — [ffmpeg](https://ffmpeg.org/download.html) (includes ffprobe), must be on PATH.
   - **Images** — nothing else needed, Pillow covers it.
   - **EPUB → PDF** — nothing else needed, pure Python (no Calibre or other binary required).

Each script is independent — you only need the external tool for the one you run.

## Images

```
python img-compr/img-compr.py photos/ compressed/
python img-compr/img-compr.py photos/ compressed/ --quality 70 --max-size 1280
python img-compr/img-compr.py photos/ compressed/ --no-webp   # keep PNG as PNG
```

- JPEG/WebP are re-encoded at `--quality` (default 80).
- PNG is converted to WebP by default for much better compression (`--no-webp` to keep PNG).
- `--max-size` caps the longest edge, preserving aspect ratio (default 1920px, 0 = no resize).
- Recurses subdirectories, mirrors folder structure to output.
- If `output_dir` is omitted, files are saved alongside originals with a `_compressed` suffix.

## PDFs

```
python pdf_compr/pdf-compr.py pdfs/ compressed/                  # target 5MB per file (default)
python pdf_compr/pdf-compr.py pdfs/ compressed/ --target-size 8  # different size cap
python pdf_compr/pdf-compr.py pdfs/ compressed/ --dpi 150        # fixed-quality, no size guarantee
python pdf_compr/pdf-compr.py pdfs/ compressed/ --grayscale      # scans/photos that don't need color
```

- Default mode auto-escalates through strategies (image DPI → JPEG quality → grayscale) until
  the file lands under `--target-size` MB (default 5) or every lever is exhausted.
- After each pass, if qpdf is installed, a lossless structural pass runs on top (object
  streams, max Flate recompression) — real extra savings on text/vector-heavy PDFs.
- `--dpi` switches to a single fixed-DPI pass with no size guarantee — quicker.
- Recurses subdirectories, mirrors folder structure to output.
- If `output_dir` is omitted, files are saved alongside originals with a `_compressed` suffix.
- **Note:** for vector/font-heavy PDFs (e.g. LaTeX output with little raster content),
  [PDF24](https://github.com/PDF24/PDF24-Creator) beats this and every open CLI combo we've
  tried, almost certainly via font-subset deduplication. This tool matches or beats it on
  scanned/image-heavy PDFs, the case it's built for, but isn't a full replacement.

## Video

```
python video_compr/video-compr.py clips/ compressed/                       # target 5MB per file (default)
python video_compr/video-compr.py clips/ compressed/ --target-size 8       # different size cap
python video_compr/video-compr.py clips/ compressed/ --crf 28              # fixed-quality, no size guarantee
python video_compr/video-compr.py clips/ compressed/ --codec h264_nvenc    # NVIDIA GPU encode, much faster
python video_compr/video-compr.py clips/ compressed/ --codec h265         # smaller, less compatible
```

- Default mode two-pass encodes to land at/just-under `--target-size` MB (default 5).
  Resolution and audio bitrate are auto-picked per clip so a long/high-motion clip gets
  downscaled rather than starving its video bitrate. Pass `--max-height` / `--audio-bitrate`
  to pin either instead of auto-picking.
- `--crf` switches to a fixed-quality single-pass encode with no size guarantee — quicker.
  CRF scale: 18 ≈ visually lossless, 23 = x264 default, 28 = smaller but still watchable.
- `--codec`: `h264` (compatible, default), `h265` (smaller, less compatible), `vp9` (WebM),
  `h264_nvenc`/`h265_nvenc` (NVIDIA GPU encode, requires an nvenc-capable ffmpeg build).
- Recurses subdirectories, mirrors folder structure to output.
- If `output_dir` is omitted, files are saved alongside originals with a `_compressed` suffix.

## EPUB → PDF

```
python epub_pdf/epub-to-pdf.py epubs/mybook.epub   # single file, PDF saved alongside it
python epub_pdf/epub-to-pdf.py epubs/ pdfs/         # batch a folder, recurses
```

- Chapters are read from the epub's spine (its reading order) and concatenated, each
  starting on a new PDF page, with a generated title page (title + author from the
  epub's metadata).
- Embedded images are extracted from the epub archive and re-linked so they render in
  the output PDF.
- The epub's original CSS is dropped and replaced with a plain, readable stylesheet —
  xhtml2pdf only supports a subset of CSS (no flexbox/floats/etc.), so carrying over
  arbitrary book stylesheets tends to break more than it helps. Expect solid text +
  image fidelity, not a pixel-perfect reproduction of fancy/illustrated layouts.
- Recurses subdirectories, mirrors folder structure to output.
- If `output_dir` is omitted, the PDF is saved alongside the source `.epub`.
- Pure Python — no Calibre or other external binary required. If you hit an epub that
  renders badly (heavy custom CSS, SVG-based layouts, fixed-layout comics/manga),
  Calibre's `ebook-convert` is the more capable fallback: https://calibre-ebook.com/

## License

MIT — see [LICENSE](LICENSE).

"""
epub-to-pdf.py — convert .epub files in a folder to .pdf
Usage:
    python epub-to-pdf.py <input_dir_or_file> [output_dir]

- input can be a single .epub file or a directory (recurses, converts every .epub found)
- Chapters are concatenated in spine order, each starting on a new page, with a
  generated title page (title + author read from the epub's metadata)
- Embedded images are extracted and re-linked so they render in the PDF
- Original CSS is not carried over (xhtml2pdf's CSS support is limited); a plain
  readable stylesheet is applied instead
- If output_dir is omitted, PDFs are saved alongside the source .epub files
- Pure Python — no external binary required
- Requires: pip install ebooklib beautifulsoup4 xhtml2pdf
"""

import argparse
import posixpath
import sys
import tempfile
from pathlib import Path

try:
    import ebooklib
    from ebooklib import epub
except ImportError:
    sys.exit("ebooklib is required: pip install ebooklib")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("beautifulsoup4 is required: pip install beautifulsoup4")

try:
    from xhtml2pdf import pisa
except ImportError:
    sys.exit("xhtml2pdf is required: pip install xhtml2pdf")

SUPPORTED = {".epub"}

STYLE = """
<style>
  body { font-family: "Times New Roman", serif; font-size: 12pt; line-height: 1.4; }
  h1, h2, h3 { font-family: Helvetica, sans-serif; }
  img { max-width: 100%; }
  .chapter { page-break-before: always; }
  .titlepage { page-break-after: always; text-align: center; margin-top: 35%; }
  .titlepage h1 { font-size: 26pt; margin-bottom: 0.3em; }
  .titlepage .author { font-size: 16pt; color: #444; }
</style>
"""


def build_image_map(book: "epub.EpubBook") -> dict[str, "epub.EpubItem"]:
    return {item.get_name(): item for item in book.get_items() if item.get_type() == ebooklib.ITEM_IMAGE}


def resolve_images(soup: BeautifulSoup, doc_name: str, image_map: dict, extracted: dict, tmp_dir: Path) -> None:
    """Rewrite <img src> in place to point at real files extracted from the epub."""
    doc_dir = posixpath.dirname(doc_name)
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src or src.startswith(("http://", "https://", "data:")):
            continue
        internal_path = posixpath.normpath(posixpath.join(doc_dir, src))
        item = image_map.get(internal_path)
        if item is None:
            # fall back to matching by basename, in case of an unusual relative path
            base = posixpath.basename(internal_path)
            item = next((i for name, i in image_map.items() if posixpath.basename(name) == base), None)
        if item is None:
            img.decompose()
            continue
        if item.get_name() not in extracted:
            local_path = tmp_dir / f"img_{len(extracted)}{Path(item.get_name()).suffix}"
            local_path.write_bytes(item.get_content())
            extracted[item.get_name()] = local_path
        img["src"] = str(extracted[item.get_name()])


def build_html(book: "epub.EpubBook", tmp_dir: Path) -> str:
    image_map = build_image_map(book)
    extracted: dict[str, Path] = {}

    title = (book.get_metadata("DC", "title") or [("Untitled", {})])[0][0]
    creators = book.get_metadata("DC", "creator")
    author = ", ".join(c[0] for c in creators) if creators else ""

    parts = [f'<div class="titlepage"><h1>{title}</h1><p class="author">{author}</p></div>']

    for item_id, _ in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content(), "html.parser")
        resolve_images(soup, item.get_name(), image_map, extracted, tmp_dir)
        body = soup.find("body")
        inner = body.decode_contents() if body else str(soup)
        parts.append(f'<div class="chapter">{inner}</div>')

    return f"<html><head>{STYLE}</head><body>{''.join(parts)}</body></html>"


def convert(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    book = epub.read_epub(str(src))
    with tempfile.TemporaryDirectory(prefix="epub2pdf_") as tmp:
        html = build_html(book, Path(tmp))
        with open(dst, "wb") as f:
            result = pisa.CreatePDF(html, dest=f)
        if result.err:
            sys.exit(f"Conversion failed for {src.name} ({result.err} error(s))")


def build_dst(src: Path, input_root: Path, output_dir: Path | None) -> Path:
    if output_dir:
        rel = src.relative_to(input_root) if input_root.is_dir() else src.name
        return (output_dir / rel).with_suffix(".pdf")
    return src.with_suffix(".pdf")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert EPUB files to PDF")
    parser.add_argument("input", type=Path, help="A .epub file, or a directory to search recursively")
    parser.add_argument("output_dir", type=Path, nargs="?", default=None)
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"Not found: {args.input}")

    if args.input.is_file():
        files = [args.input]
        input_root = args.input.parent
    else:
        files = sorted(p for p in args.input.rglob("*") if p.suffix.lower() in SUPPORTED)
        input_root = args.input
        if not files:
            sys.exit("No .epub files found.")

    for src in files:
        dst = build_dst(src, input_root, args.output_dir)
        print(f"{src.name} -> {dst}")
        convert(src, dst)

    print(f"\nDone: {len(files)} file(s) converted.")


if __name__ == "__main__":
    main()

"""Offline screenshot OCR with one original-pixel JSON contract.

Vision/custom helpers accept one image path and emit JSON. Tesseract 5 emits
word-level TSV; its sparse-text lines become our labels, with the weakest
word's measured confidence. Neither backend sees expected migration values.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .browser import BrowserError


def add_ocr_arguments(parser):
    parser.add_argument("--ocr-backend", choices=("auto", "vision", "tesseract", "external"),
                        default="auto", help="Offline OCR: auto uses Vision on macOS, Tesseract elsewhere")
    parser.add_argument("--ocr-executable",
                        help="Executable path (spaces allowed) or PATH name; by itself selects the legacy JSON helper")


def _executable(value):
    # This is one executable, never a shell command or a string to shlex.split.
    value = os.fspath(value)
    path = Path(value).expanduser()
    if path.is_file():
        resolved = str(path.resolve())
    else:
        resolved = shutil.which(value)
    if not resolved:
        raise BrowserError("OCR executable not found. Install the selected backend and pass --ocr-executable or add it to PATH")
    if Path(resolved).suffix.lower() in (".bat", ".cmd", ".ps1"):
        raise BrowserError("OCR requires a native executable, not a Windows shell script")
    if not os.access(resolved, os.X_OK):
        raise BrowserError("OCR executable is not executable; check its permissions")
    return str(Path(resolved).resolve())


def resolve_ocr(backend="auto", executable=None):
    """Resolve only for execution; planning must never require an OCR install."""
    if backend == "auto":
        backend = "external" if executable is not None else ("vision" if sys.platform == "darwin" else "tesseract")
    if backend not in ("vision", "tesseract", "external"):
        raise BrowserError("Unknown OCR backend")
    if backend == "vision" and sys.platform != "darwin":
        raise BrowserError("Vision OCR requires macOS; use --ocr-backend tesseract on Windows")
    if executable is None:
        if backend == "vision":
            executable = "output/tools/ocr-screenshot"
        elif backend == "tesseract":
            executable = shutil.which("tesseract")
            if executable is None and sys.platform == "win32":
                executable = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"
            executable = executable or "tesseract"
        else:
            raise BrowserError("The external OCR backend requires --ocr-executable")
    return OcrEngine(backend, _executable(executable))


def _run(arguments, *, input_bytes=None):
    try:
        # Binary pipes avoid Windows code-page conversion, including for PNG
        # stdin. Only stdout is decoded: native diagnostic text can use a
        # different locale and must never be echoed with private OCR data.
        result = subprocess.run(arguments, input=input_bytes, capture_output=True,
                                timeout=30, check=False, shell=False)
    except subprocess.TimeoutExpired:
        raise BrowserError("Local OCR timed out after 30 seconds; preserve the journal and inspect the saved evidence") from None
    except OSError:
        raise BrowserError("Cannot start local OCR; check the executable, permissions and native runtime dependencies") from None
    if result.returncode:
        # stderr may contain private screenshot text/paths. Do not echo it.
        raise BrowserError(f"Local OCR failed (exit {result.returncode}); check the selected engine's installation, runtime and language data")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeError:
        raise BrowserError("Local OCR output is not valid UTF-8") from None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_document(document):
    """Fail closed on malformed confidence/geometry before any consumer uses it."""
    if not isinstance(document, dict):
        raise BrowserError("Invalid OCR document")
    width, height = document.get("image_width"), document.get("image_height")
    if not all(type(v) is int and v > 0 for v in (width, height)) or not isinstance(document.get("lines"), list):
        raise BrowserError("Invalid OCR image dimensions or lines")
    lines = []
    for line in document["lines"]:
        if not isinstance(line, dict) or not isinstance(line.get("text"), str) or not line["text"].strip():
            raise BrowserError("Invalid OCR text line")
        confidence = line.get("confidence")
        if not _number(confidence) or not 0 <= confidence <= 1:
            raise BrowserError("Invalid OCR confidence")
        x, y, w, h = (line.get(k) for k in ("x", "y", "width", "height"))
        if (not all(_number(v) for v in (x, y, w, h)) or w <= 0 or h <= 0
                or x < -.5 or y < -.5 or x + w > width + .5 or y + h > height + .5):
            raise BrowserError("Invalid OCR text geometry")
        lines.append(dict(text=line["text"], confidence=confidence, x=x, y=y, width=w, height=h))
    return dict(image_width=width, image_height=height, lines=lines)


def parse_tesseract_tsv(content, width, height, *, scale=2):
    """Convert TSV pixels back to screenshot pixels without confidence inflation.

    Keep Tesseract's block/paragraph/line boundaries. Large gaps within a line
    separate UI columns (e.g. a field label and its value); never merge across
    lines or discard a low-confidence word that could make a click ambiguous.
    """
    required = {"level", "page_num", "block_num", "par_num", "line_num", "word_num",
                "left", "top", "width", "height", "conf", "text"}
    groups = {}
    try:
        reader = csv.DictReader(io.StringIO(content), delimiter="\t", quoting=csv.QUOTE_NONE)
        if reader.fieldnames is None or set(reader.fieldnames) != required:
            raise ValueError("header")
        page_seen = False
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("columns")
            level = int(row["level"])
            if int(row["page_num"]) != 1 or not 1 <= level <= 5:
                raise ValueError("page")
            if level == 1:
                if page_seen or (int(row["width"]), int(row["height"])) != (width * scale, height * scale):
                    raise ValueError("dimensions")
                page_seen = True
            if level != 5 or not row["text"].strip():
                continue
            confidence = float(row["conf"]) / 100
            word = dict(text=row["text"].strip(), confidence=confidence,
                        **{out: int(row[src]) / scale for src, out in
                           (("left", "x"), ("top", "y"), ("width", "width"), ("height", "height"))})
            # Validate words too: taking a union must not hide bad geometry.
            validate_document(dict(image_width=width, image_height=height, lines=[word]))
            key = tuple(int(row[k]) for k in ("block_num", "par_num", "line_num"))
            groups.setdefault(key, []).append(word)
        if not page_seen:
            raise ValueError("missing page")
    except (ValueError, TypeError, csv.Error):
        raise BrowserError("Invalid Tesseract TSV output or image dimensions") from None
    lines = []
    for words in groups.values():
        segments = [[]]
        for word in sorted(words, key=lambda item: item["x"]):
            if segments[-1]:
                previous = segments[-1][-1]
                gap = word["x"] - previous["x"] - previous["width"]
                if gap > max(24, 2 * max(previous["height"], word["height"])):
                    segments.append([])
            segments[-1].append(word)
        for segment in segments:
            x, y = min(w["x"] for w in segment), min(w["y"] for w in segment)
            lines.append(dict(text=" ".join(w["text"] for w in segment),
                              confidence=min(w["confidence"] for w in segment), x=x, y=y,
                              width=max(w["x"] + w["width"] for w in segment) - x,
                              height=max(w["y"] + w["height"] for w in segment) - y))
    return validate_document(dict(image_width=width, image_height=height, lines=lines))


def _pillow():
    try:
        from PIL import Image
    except ImportError:
        raise BrowserError('Tesseract OCR needs Pillow: python -m pip install -e ".[browser,ocr]"') from None
    return Image


@dataclass(frozen=True)
class OcrEngine:
    backend: str
    executable: str

    def check(self):
        """Check native dependencies before Chrome opens or any Save is possible."""
        if self.backend == "tesseract":
            _pillow()
            version = _run([self.executable, "--version"])
            if not version.lower().startswith("tesseract 5."):
                raise BrowserError("The Tesseract OCR backend requires Tesseract 5.x")
            languages = _run([self.executable, "--list-langs"])
            if "eng" not in languages.splitlines():
                raise BrowserError("Install Tesseract's English (eng) language data before execution")

    def recognize(self, path):
        path = Path(path).resolve()
        if self.backend == "tesseract":
            Image = _pillow()
            # Pillow opens Unicode paths; send PNG bytes over stdin so native
            # Windows image libraries never have to reopen a Unicode filename
            # or a locked temporary file. No additional screenshot is stored.
            try:
                with Image.open(path) as original, io.BytesIO() as scaled:
                    width, height = original.size
                    original.convert("RGB").resize((width * 2, height * 2), Image.Resampling.LANCZOS).save(scaled, format="PNG")
                    png = scaled.getvalue()
            except (OSError, ValueError):
                raise BrowserError("Cannot prepare the local OCR screenshot") from None
            content = _run([self.executable, "stdin", "stdout", "-l", "eng",
                            "--oem", "1", "--psm", "11", "tsv"], input_bytes=png)
            return parse_tesseract_tsv(content, width, height)
        try:
            document = json.loads(_run([self.executable, str(path)]))
        except ValueError:
            raise BrowserError("Local OCR helper did not return valid JSON") from None
        return validate_document(document)


def recognize_image(engine, path):
    # Python callers and existing profiles may still supply the legacy path.
    if not isinstance(engine, OcrEngine):
        engine = resolve_ocr(executable=engine)
    return engine.recognize(path)

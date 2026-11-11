from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from pptx import Presentation


@dataclass
class Slide:
    number: int
    title: str
    text: str
    notes: str
    image: str


@dataclass
class Deck:
    sha256: str
    filename: str
    directory: Path
    slides: list[Slide]

    @property
    def source(self):
        return self.directory / "source.pptx"


def slide_text(shapes):
    parts = []
    for shape in shapes:
        if shape.has_text_frame:
            parts.append(shape.text_frame.text)
        if shape.has_table:
            parts.extend(" | ".join(c.text for c in row.cells) for row in shape.table.rows)
        if hasattr(shape, "shapes"):
            parts.append(slide_text(shape.shapes))
    return "\n".join(p for p in parts if p)


def validate_pptx(content: bytes):
    if len(content) > 80 * 1024 * 1024:
        raise ValueError("Prototype upload limit is 80 MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            info = z.infolist()
            if "ppt/presentation.xml" not in z.namelist():
                raise ValueError("This is not a PowerPoint .pptx file.")
            if len(info) > 10000 or sum(i.file_size for i in info) > 300 * 1024 * 1024:
                raise ValueError("Presentation expands beyond the prototype's 300 MB limit.")
    except zipfile.BadZipFile:
        raise ValueError("Invalid or damaged .pptx file.") from None


def render_pdf(source: Path, directory: Path, soffice: str = "") -> Path:
    executable = soffice or shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        candidates = ["/Applications/LibreOffice.app/Contents/MacOS/soffice",
                      "C:/Program Files/LibreOffice/program/soffice.exe"]
        executable = next((p for p in candidates if Path(p).exists()), None)
    if not executable:
        raise RuntimeError("Install LibreOffice and set SOFFICE_PATH to its soffice executable.")
    # An isolated profile avoids stealing or hanging an existing LibreOffice session.
    with tempfile.TemporaryDirectory(prefix="slides-render-") as profile:
        cmd = [executable, "-env:UserInstallation=" + Path(profile).as_uri(),
               "--headless", "--convert-to", "pdf:impress_pdf_Export", "--outdir", str(directory), str(source)]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=150)
        except subprocess.TimeoutExpired:
            raise RuntimeError("LibreOffice rendering timed out after 150 seconds.") from None
    pdf = directory / (source.stem + ".pdf")
    if result.returncode or not pdf.is_file():
        raise RuntimeError("LibreOffice could not render this deck. Check its installation and the .pptx file.")
    return pdf


def prepare_deck(content: bytes, filename: str, data_root: Path, soffice: str = "") -> Deck:
    validate_pptx(content)
    sha = hashlib.sha256(content).hexdigest()
    directory = data_root / sha
    manifest = directory / "manifest.json"
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        slides = [Slide(**s) for s in saved["slides"]]
        if saved.get("schema_version") == 2 and saved["sha256"] == sha and all((directory / s.image).is_file() for s in slides):
            return Deck(sha, Path(filename).name, directory, slides)
    presentation = Presentation(io.BytesIO(content))
    if not 1 <= len(presentation.slides) <= 60:
        raise ValueError("This prototype supports 1–60 slides per presentation.")
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "source.pptx"
    source.write_bytes(content)
    slides = []
    for i, s in enumerate(presentation.slides, 1):
        notes = s.notes_slide.notes_text_frame.text if s.has_notes_slide else ""
        text = slide_text(s.shapes)
        title = s.shapes.title.text if s.shapes.title else next((line for line in text.splitlines() if line.strip()), f"Slide {i}")[:200]
        slides.append(Slide(i, title, text, notes, f"slides/{i:03d}.png"))
    pdf = render_pdf(source, directory, soffice)
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(pdf)
    try:
        if len(document) != len(slides):
            raise ValueError("Rendered slide count differs from .pptx. Unhide slides and try again; switching is disabled.")
        (directory / "slides").mkdir(exist_ok=True)
        for i, slide in enumerate(slides):
            page = document[i]
            bitmap = page.render(scale=1440 / page.get_width())
            image = bitmap.to_pil()
            image.save(directory / slide.image)
            image.close()
            bitmap.close()
            page.close()
    finally:
        document.close()
    manifest.write_text(json.dumps({"schema_version": 2, "sha256": sha, "slides": [asdict(s) for s in slides]}, ensure_ascii=False, indent=2))
    return Deck(sha, Path(filename).name, directory, slides)

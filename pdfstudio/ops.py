"""Operaciones sobre PDFs, sin dependencia de la interfaz (se pueden probar solas).

Convención de coordenadas: todo lo que entra y sale de aquí está en el "espacio
de página" de PyMuPDF (sin rotar). La interfaz se encarga de convertir desde y
hacia lo que ve el usuario.
"""
from __future__ import annotations

import collections
import html as _html
import os
import re
import secrets
from typing import Callable, Optional, Sequence

import pymupdf

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}

Progress = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]


class Cancelled(Exception):
    pass


class NeedsPassword(Exception):
    pass


class BadPassword(Exception):
    pass


# --------------------------------------------------------------------------
# Utilidades generales
# --------------------------------------------------------------------------
def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def parse_ranges(text: str, n: int) -> list[list[int]]:
    """'1-3, 5, 7-9' -> [[0,1,2],[4],[6,7,8]] (índices base 0). Lanza ValueError."""
    groups: list[list[int]] = []
    for part in re.split(r"[;,]", text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"Rango no válido: «{part}»")
        if a < 1 or b > n or a > b:
            raise ValueError(f"«{part}» está fuera del documento (1-{n})")
        groups.append(list(range(a - 1, b)))
    if not groups:
        raise ValueError("No has indicado ninguna página")
    return groups


def flatten(groups: Sequence[Sequence[int]]) -> list[int]:
    seen, out = set(), []
    for g in groups:
        for i in g:
            if i not in seen:
                seen.add(i)
                out.append(i)
    return out


def open_pdf(path: str, password: Optional[str] = None):
    """Abre un PDF (o una imagen, convirtiéndola) desde memoria, sin bloquear el
    archivo en disco. Devuelve (doc, password_usada). El doc devuelto siempre
    está descifrado; la contraseña se guarda aparte para volver a aplicarla al
    guardar."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        with pymupdf.open(path) as img:
            data = img.convert_to_pdf()
        return pymupdf.open(stream=data, filetype="pdf"), None
    with open(path, "rb") as f:
        data = f.read()
    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        if password is None:
            doc.close()
            raise NeedsPassword(path)
        if not doc.authenticate(password):
            doc.close()
            raise BadPassword(path)
        plain = doc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)
        doc.close()
        return pymupdf.open(stream=plain, filetype="pdf"), password
    return doc, None


def snapshot(doc) -> bytes:
    return doc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)


def from_snapshot(data: bytes):
    return pymupdf.open(stream=data, filetype="pdf")


def save_doc(doc, path: str, password: Optional[str] = None, compact: bool = True) -> None:
    """Guarda de forma segura (archivo temporal + reemplazo)."""
    tmp = path + ".tmp~"
    kw: dict = dict(garbage=3 if compact else 0, deflate=True)
    if password:
        kw.update(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    else:
        kw.update(encryption=pymupdf.PDF_ENCRYPT_NONE)
    try:
        doc.save(tmp, **kw)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Unir, dividir, extraer
# --------------------------------------------------------------------------
def merge_pdfs(items: Sequence[tuple[str, Optional[str]]], out_path: str,
               add_bookmarks: bool = True, progress: Progress = None) -> int:
    """items: [(ruta, contraseña|None)]. Devuelve nº de páginas del resultado."""
    out = pymupdf.open()
    toc: list = []
    for n, (path, pw) in enumerate(items):
        if progress:
            progress(n, len(items))
        d, _ = open_pdf(path, pw)
        start = out.page_count
        out.insert_pdf(d)
        if add_bookmarks:
            toc.append([1, os.path.splitext(os.path.basename(path))[0], start + 1])
            for lvl, title, pg, *_ in d.get_toc(simple=True):
                if pg >= 1:
                    toc.append([lvl + 1, title, pg + start])
        d.close()
    if toc:
        try:
            out.set_toc(toc)
        except Exception:
            pass
    out.save(out_path, garbage=3, deflate=True)
    pages = out.page_count
    out.close()
    return pages


def _save_range(doc, a: int, b: int, path: str) -> None:
    new = pymupdf.open()
    new.insert_pdf(doc, from_page=a, to_page=b)
    new.save(path, garbage=3, deflate=True)
    new.close()


def _name(base: str, a: int, b: int) -> str:
    return f"{base}_p{a + 1}.pdf" if a == b else f"{base}_p{a + 1}-{b + 1}.pdf"


def split_by_groups(doc, groups: Sequence[Sequence[int]], out_dir: str, base: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for g in groups:
        path = os.path.join(out_dir, _name(base, g[0], g[-1]))
        _save_range(doc, g[0], g[-1], path)
        paths.append(path)
    return paths


def split_every(doc, n: int, out_dir: str, base: str) -> list[str]:
    n = max(1, n)
    groups = [list(range(a, min(a + n, doc.page_count))) for a in range(0, doc.page_count, n)]
    return split_by_groups(doc, groups, out_dir, base)


def extract_pages(doc, pages: Sequence[int], out_path: str) -> None:
    new = pymupdf.open()
    for i in pages:
        new.insert_pdf(doc, from_page=i, to_page=i)
    new.save(out_path, garbage=3, deflate=True)
    new.close()


def reorder_pages(doc, order: Sequence[int]) -> None:
    doc.select(list(order))


def delete_pages(doc, pages: Sequence[int]) -> None:
    keep = [i for i in range(doc.page_count) if i not in set(pages)]
    if not keep:
        raise ValueError("No se pueden borrar todas las páginas")
    doc.select(keep)


def rotate_pages(doc, pages: Sequence[int], delta: int) -> None:
    for i in pages:
        pg = doc[i]
        pg.set_rotation((pg.rotation + delta) % 360)


def duplicate_page(doc, i: int) -> None:
    doc.fullcopy_page(i, i + 1)


def insert_blank_page(doc, after: int) -> None:
    ref = doc[min(max(after, 0), doc.page_count - 1)]
    doc.new_page(pno=after + 1, width=ref.rect.width, height=ref.rect.height)


def insert_pdf_at(doc, src_path: str, at: int, password: Optional[str] = None) -> int:
    src, _ = open_pdf(src_path, password)
    n = src.page_count
    doc.insert_pdf(src, start_at=at)
    src.close()
    return n


# --------------------------------------------------------------------------
# Compresión, protección, metadatos
# --------------------------------------------------------------------------
COMPRESS_LEVELS = {
    0: ("Sin pérdida (solo limpiar y comprimir estructura)", None),
    1: ("Equilibrada (imágenes a ~150 ppp)", dict(dpi_threshold=170, dpi_target=150, quality=80)),
    2: ("Fuerte (imágenes a ~110 ppp)", dict(dpi_threshold=130, dpi_target=110, quality=65)),
    3: ("Máxima (imágenes a ~72 ppp, se nota)", dict(dpi_threshold=90, dpi_target=72, quality=50)),
}


def compress_pdf(doc, out_path: str, level: int = 1) -> tuple[int, int]:
    """Guarda una copia comprimida. Devuelve (bytes_antes, bytes_después)."""
    before = len(snapshot(doc))
    d = from_snapshot(snapshot(doc))
    opts = COMPRESS_LEVELS[level][1]
    if opts:
        d.rewrite_images(**opts)
    try:
        d.subset_fonts()
    except Exception:
        pass  # necesita fontTools; es opcional
    d.save(out_path, garbage=4, deflate=True, clean=True, use_objstms=1)
    d.close()
    return before, os.path.getsize(out_path)


def protect_pdf(doc, out_path: str, user_pw: str, owner_pw: Optional[str] = None,
                allow_print: bool = True, allow_copy: bool = True, allow_modify: bool = False) -> None:
    perm = pymupdf.PDF_PERM_ACCESSIBILITY
    if allow_print:
        perm |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
    if allow_copy:
        perm |= pymupdf.PDF_PERM_COPY
    if allow_modify:
        perm |= (pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ANNOTATE
                 | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ASSEMBLE)
    # Si el propietario tuviera la misma contraseña que el usuario, quien abre el
    # archivo sería "propietario" y las restricciones no valdrían nada. Por eso, sin
    # contraseña de permisos explícita se genera una aleatoria.
    owner = owner_pw or secrets.token_urlsafe(18)
    d = from_snapshot(snapshot(doc))
    d.save(out_path, garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_AES_256,
           user_pw=user_pw, owner_pw=owner, permissions=perm)
    d.close()


def get_metadata(doc) -> dict:
    m = doc.metadata or {}
    return {k: m.get(k) or "" for k in ("title", "author", "subject", "keywords", "creator", "producer")}


def set_metadata(doc, meta: dict) -> None:
    cur = dict(doc.metadata or {})
    cur.update({k: v for k, v in meta.items()})
    doc.set_metadata(cur)


def bake(doc) -> None:
    """Incrusta anotaciones y campos de formulario en el contenido (ya no son editables)."""
    doc.bake(annots=True, widgets=True)


# --------------------------------------------------------------------------
# Exportar / importar imágenes
# --------------------------------------------------------------------------
def export_images(doc, pages: Sequence[int], out_dir: str, dpi: int = 150, fmt: str = "png",
                  base: str = "pagina", progress: Progress = None) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    fmt = fmt.lower().lstrip(".")
    paths = []
    for n, i in enumerate(pages):
        if progress:
            progress(n, len(pages))
        pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
        path = os.path.join(out_dir, f"{base}_{i + 1:03d}.{fmt}")
        pix.save(path, jpg_quality=90) if fmt in ("jpg", "jpeg") else pix.save(path)
        paths.append(path)
    return paths


def images_to_pdf(paths: Sequence[str], out_path: str, progress: Progress = None) -> int:
    out = pymupdf.open()
    for n, p in enumerate(paths):
        if progress:
            progress(n, len(paths))
        with pymupdf.open(p) as img:
            data = img.convert_to_pdf()
        with pymupdf.open(stream=data, filetype="pdf") as ip:
            out.insert_pdf(ip)
    out.save(out_path, garbage=3, deflate=True)
    n = out.page_count
    out.close()
    return n


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------
def tessdata_dir() -> Optional[str]:
    cands = []
    try:
        from .paths import resource
        cands.append(resource("tessdata"))  # incluida con la versión instalable
    except Exception:
        pass
    try:
        cands.append(pymupdf.get_tessdata())
    except Exception:
        pass
    cands += [
        os.environ.get("TESSDATA_PREFIX"),
        r"C:\Program Files\Tesseract-OCR\tessdata",
        r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/usr/share/tessdata",
        "/usr/local/share/tessdata",
        "/opt/homebrew/share/tessdata",
    ]
    for c in cands:
        if not c:
            continue
        if os.path.isdir(os.path.join(c, "tessdata")):
            c = os.path.join(c, "tessdata")
        if os.path.isdir(c) and any(f.endswith(".traineddata") for f in os.listdir(c)):
            return c
    return None


def ocr_languages() -> list[str]:
    d = tessdata_dir()
    if not d:
        return []
    return sorted(f[:-12] for f in os.listdir(d) if f.endswith(".traineddata") and f[:-12] != "osd")


def page_has_text(page) -> bool:
    return len(page.get_text("text").strip()) > 20


def ocr_pdf(doc, out_path: str, lang: str = "spa+eng", dpi: int = 300, skip_text_pages: bool = True,
            progress: Progress = None, cancel: CancelFn = None) -> int:
    """Crea un PDF con capa de texto buscable. Devuelve nº de páginas procesadas con OCR."""
    tess = tessdata_dir()
    if not tess:
        raise RuntimeError("No se encuentra Tesseract (carpeta tessdata). Consulta el README.")
    out = pymupdf.open()
    done = 0
    for i in range(doc.page_count):
        if cancel and cancel():
            out.close()
            raise Cancelled()
        if progress:
            progress(i, doc.page_count)
        page = doc[i]
        if skip_text_pages and page_has_text(page):
            out.insert_pdf(doc, from_page=i, to_page=i)
            continue
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        data = pix.pdfocr_tobytes(language=lang, tessdata=tess)
        with pymupdf.open(stream=data, filetype="pdf") as ocr_doc:
            out.insert_pdf(ocr_doc)
        done += 1
    try:
        out.set_toc(doc.get_toc())
    except Exception:
        pass
    out.save(out_path, garbage=3, deflate=True)
    out.close()
    return done


# --------------------------------------------------------------------------
# Búsqueda en carpeta
# --------------------------------------------------------------------------
def search_folder(folder: str, term: str, recursive: bool = True,
                  on_result: Optional[Callable[[str, int, str], None]] = None,
                  progress: Optional[Callable[[str], None]] = None,
                  cancel: CancelFn = None) -> int:
    """Busca `term` en todos los PDFs de la carpeta. Devuelve nº de coincidencias."""
    term_l = term.lower()
    count = 0
    files = []
    for root, _dirs, names in os.walk(folder):
        for n in sorted(names):
            if n.lower().endswith(".pdf"):
                files.append(os.path.join(root, n))
        if not recursive:
            break
    for path in files:
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(path)
        try:
            with pymupdf.open(path) as d:
                if d.needs_pass:
                    continue
                for i, page in enumerate(d):
                    text = page.get_text("text")
                    low = text.lower()
                    pos = low.find(term_l)
                    if pos < 0:
                        continue
                    n_hits = low.count(term_l)
                    snippet = " ".join(text[max(0, pos - 45): pos + len(term) + 45].split())
                    count += n_hits
                    if on_result:
                        on_result(path, i, f"({n_hits}) …{snippet}…")
        except Exception:
            continue
    return count


# --------------------------------------------------------------------------
# Anotaciones
# --------------------------------------------------------------------------
Color = tuple


def _rect_page(r) -> pymupdf.Rect:
    return pymupdf.Rect(r).normalize()


def add_markup(page, kind: str, rects: Sequence, color: Color = (1, 0.92, 0)) -> None:
    quads = [_rect_page(r).quad for r in rects]
    if not quads:
        return
    fn = {
        "highlight": page.add_highlight_annot,
        "underline": page.add_underline_annot,
        "strike": page.add_strikeout_annot,
        "squiggly": page.add_squiggly_annot,
    }[kind]
    a = fn(quads=quads)
    a.set_colors(stroke=color)
    a.update()


def add_shape(page, kind: str, p1, p2, color: Color, width: float) -> None:
    p1, p2 = pymupdf.Point(p1), pymupdf.Point(p2)
    if kind in ("rect", "ellipse"):
        r = pymupdf.Rect(p1, p2).normalize()
        if r.width < 2 or r.height < 2:
            return
        a = page.add_rect_annot(r) if kind == "rect" else page.add_circle_annot(r)
        a.set_colors(stroke=color)
        a.set_border(width=width)
    else:
        if (p2 - p1).abs_unit == 0 and p1 == p2:
            return
        a = page.add_line_annot(p1, p2)
        a.set_colors(stroke=color)
        a.set_border(width=width)
        if kind == "arrow":
            a.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
    a.update()


def add_ink(page, strokes: Sequence[Sequence], color: Color, width: float) -> None:
    strokes = [[tuple(p) for p in s] for s in strokes if len(s) > 1]
    if not strokes:
        return
    a = page.add_ink_annot(strokes)
    a.set_colors(stroke=color)
    a.set_border(width=width)
    a.update()


def add_note(page, point, text: str, color: Color = (1, 0.85, 0)) -> None:
    a = page.add_text_annot(pymupdf.Point(point), text, icon="Note")
    a.set_colors(stroke=color)
    a.update()


def add_textbox(page, rect, text: str, fontsize: float, color: Color) -> None:
    r = _rect_page(rect)
    lines = text.split("\n")
    need_w = max(pymupdf.get_text_length(l, fontname="helv", fontsize=fontsize) for l in lines) + 10
    need_h = len(lines) * fontsize * 1.35 + 6
    if r.width < 24 or r.height < 12:  # clic sin arrastrar: tamaño automático
        r = pymupdf.Rect(r.x0, r.y0, r.x0 + need_w, r.y0 + need_h)
    else:
        r.y1 = max(r.y1, r.y0 + fontsize * 1.35 + 6)
    page.add_freetext_annot(r, text, fontsize=fontsize, fontname="helv", text_color=color,
                            fill_color=None, border_color=None, align=0)


def insert_image(page, rect, *, filename: Optional[str] = None, stream: Optional[bytes] = None) -> None:
    r = _rect_page(rect)
    if r.width < 4 or r.height < 4:
        r = pymupdf.Rect(r.x0, r.y0, r.x0 + 150, r.y0 + 60)
    page.insert_image(r, filename=filename, stream=stream, keep_proportion=True, overlay=True)


def add_redaction(page, rect) -> None:
    r = _rect_page(rect)
    if r.width < 2 or r.height < 2:
        return
    page.add_redact_annot(r, fill=(0, 0, 0))


def mark_search_for_redaction(doc, term: str) -> int:
    n = 0
    for page in doc:
        for r in page.search_for(term):
            page.add_redact_annot(r, fill=(0, 0, 0))
            n += 1
    return n


def count_redactions(doc) -> int:
    return sum(1 for page in doc for a in page.annots() if a.type[0] == pymupdf.PDF_ANNOT_REDACT)


def apply_redactions(doc) -> int:
    n = 0
    for page in doc:
        c = sum(1 for a in page.annots() if a.type[0] == pymupdf.PDF_ANNOT_REDACT)
        if c:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
            n += c
    return n


def annot_at(page, pt, tol: float = 3.0) -> Optional[dict]:
    """Anotación bajo el punto, como dict (xref, type, rect, content, stroke).
    Se copian los datos aquí porque un Annot deja de ser válido si se libera su página."""
    pt = pymupdf.Point(pt)
    hit = None
    for a in page.annots():
        if a.type[0] in (pymupdf.PDF_ANNOT_POPUP,):
            continue
        r = pymupdf.Rect(a.rect)
        r.x0 -= tol; r.y0 -= tol; r.x1 += tol; r.y1 += tol
        if r.contains(pt):
            hit = annot_data(a)
    return hit


def annot_data(a) -> dict:
    return dict(xref=a.xref, type=a.type[0], type_name=a.type[1], rect=pymupdf.Rect(a.rect),
                content=a.info.get("content", "") or "", stroke=a.colors.get("stroke"))


def annot_by_xref(page, xref: int) -> Optional[dict]:
    for a in page.annots():
        if a.xref == xref:
            return annot_data(a)
    return None


def delete_annot_xref(page, xref: int) -> bool:
    for a in page.annots():
        if a.xref == xref:
            page.delete_annot(a)
            return True
    return False


# --------------------------------------------------------------------------
# Edición de texto existente
# --------------------------------------------------------------------------
def _base14(font: str, flags: int) -> str:
    name = font.lower()
    bold = bool(flags & 16) or any(k in name for k in ("bold", "black", "heavy", "semibold"))
    italic = bool(flags & 2) or any(k in name for k in ("italic", "oblique"))
    mono = bool(flags & 8) or any(k in name for k in ("courier", "mono", "consol", "typewriter"))
    serif = (bool(flags & 4) or any(k in name for k in (
        "times", "serif", "georgia", "garamond", "minion", "palatino", "cambria", "book"))) and "sans" not in name
    table = ("cour", "cobo", "coit", "cobi") if mono else (
        ("tiro", "tibo", "tiit", "tibi") if serif else ("helv", "hebo", "heit", "hebi"))
    return table[(1 if bold else 0) + (2 if italic else 0)]


def line_at(page, pt) -> Optional[dict]:
    """Línea de texto bajo el punto (espacio de página) con su formato."""
    pt = pymupdf.Point(pt)
    d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            if not l["spans"]:
                continue
            r = pymupdf.Rect(l["bbox"])
            r.x0 -= 2; r.y0 -= 2; r.x1 += 2; r.y1 += 2
            if not r.contains(pt):
                continue
            dom = max(l["spans"], key=lambda s: len(s["text"]))
            first = l["spans"][0]
            return dict(
                bbox=tuple(l["bbox"]),
                text="".join(s["text"] for s in l["spans"]),
                origin=tuple(first["origin"]),
                size=float(dom["size"]),
                color=tuple(pymupdf.sRGB_to_pdf(dom["color"])),
                font=dom["font"],
                fontname=_base14(dom["font"], dom["flags"]),
            )
    return None


def sample_background(page, bbox) -> tuple:
    r = pymupdf.Rect(bbox) & page.rect
    if r.is_empty:
        return (1, 1, 1)
    pix = page.get_pixmap(clip=r, dpi=72, alpha=False)
    if pix.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    s, n = pix.samples, pix.n
    cnt = collections.Counter(s[i:i + 3] for i in range(0, len(s) - 2, n))
    rgb = cnt.most_common(1)[0][0]
    return tuple(c / 255 for c in rgb)


def _stash_redactions(page) -> list:
    keep = []
    for a in list(page.annots()):
        if a.type[0] == pymupdf.PDF_ANNOT_REDACT:
            keep.append((pymupdf.Rect(a.rect), a.colors.get("fill")))
            page.delete_annot(a)
    return keep


# Distancia (en múltiplos del tamaño de fuente) entre el borde superior de la caja
# HTML y la línea base del texto; medida empíricamente con las fuentes integradas.
_HTML_BASELINE = {"sans-serif": 1.899, "serif": 1.90, "monospace": 1.97}


def _needs_unicode(text: str) -> bool:
    """Las fuentes base-14 solo codifican Latin-1: €, – “ ” … necesitan una fuente Unicode."""
    return any(ord(c) > 255 for c in text)


def _insert_unicode_line(page, origin, text: str, fontname: str, size: float, color) -> None:
    family = "monospace" if fontname.startswith("co") else ("serif" if fontname.startswith("ti") else "sans-serif")
    bold = fontname in ("hebo", "hebi", "tibo", "tibi", "cobo", "cobi")
    italic = fontname in ("heit", "hebi", "tiit", "tibi", "coit", "cobi")
    hexcol = "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in color)
    ox, oy = origin
    top = oy - _HTML_BASELINE[family] * size
    pad = max(0.0, page.rect.y0 - top)
    rect = pymupdf.Rect(ox - 1.0, max(top, page.rect.y0), max(page.rect.x1 - 4, ox + 50), oy + size * 2.2)
    rect = rect & page.rect
    html = (f'<p style="font-family:{family};font-size:{size}pt;color:{hexcol};white-space:nowrap;'
            f'padding-top:{pad}pt;font-weight:{"bold" if bold else "normal"};'
            f'font-style:{"italic" if italic else "normal"}">{_html.escape(text)}</p>')
    page.insert_htmlbox(rect, html, css="* {margin:0;padding:0;}")


def replace_text(page, info: dict, new_text: str) -> None:
    """Sustituye una línea de texto: borra la original y escribe la nueva con la
    fuente estándar más parecida (Helvetica/Times/Courier). Si el texto lleva
    caracteres fuera de Latin-1 (€, –, “ ” …) usa una fuente Unicode integrada."""
    keep = _stash_redactions(page)
    bg = sample_background(page, info["bbox"])
    page.add_redact_annot(pymupdf.Rect(info["bbox"]), fill=bg)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    for r, fill in keep:
        page.add_redact_annot(r, fill=fill or (0, 0, 0))
    if not new_text.strip():
        return
    if _needs_unicode(new_text):
        _insert_unicode_line(page, info["origin"], new_text, info["fontname"], info["size"], info["color"])
    else:
        page.insert_text(pymupdf.Point(info["origin"]), new_text, fontname=info["fontname"],
                         fontsize=info["size"], color=info["color"])


def images_at(page, pt) -> list[dict]:
    pt = pymupdf.Point(pt)
    out = []
    for im in page.get_image_info(xrefs=True):
        if pymupdf.Rect(im["bbox"]).contains(pt):
            out.append(im)
    return out


def remove_image_area(page, bbox) -> None:
    keep = _stash_redactions(page)
    page.add_redact_annot(pymupdf.Rect(bbox), fill=None)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_NONE)
    for r, fill in keep:
        page.add_redact_annot(r, fill=fill or (0, 0, 0))


def replace_image(page, xref: int, path: str) -> None:
    page.replace_image(xref, filename=path)


def save_image_xref(doc, xref: int, path_no_ext: str) -> str:
    img = doc.extract_image(xref)
    out = f"{path_no_ext}.{img['ext']}"
    with open(out, "wb") as f:
        f.write(img["image"])
    return out


# --------------------------------------------------------------------------
# Formularios
# --------------------------------------------------------------------------
def widget_at(page, pt):
    pt = pymupdf.Point(pt)
    hit = None
    for w in page.widgets() or []:
        if pymupdf.Rect(w.rect).contains(pt):
            hit = w
    return hit


def widget_kind(w) -> str:
    t = w.field_type
    return {
        pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
        pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
        pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "combo",
        pymupdf.PDF_WIDGET_TYPE_LISTBOX: "list",
        pymupdf.PDF_WIDGET_TYPE_BUTTON: "button",
        pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "signature",
    }.get(t, "other")


def widget_rects(page) -> list:
    return [pymupdf.Rect(w.rect) for w in (page.widgets() or [])]


def is_form(doc) -> bool:
    try:
        return bool(doc.is_form_pdf)
    except Exception:
        return False

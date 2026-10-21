"""Visor de PDF con herramientas de anotación y edición (una instancia por pestaña)."""
from __future__ import annotations

import enum
import os
from collections import OrderedDict
from typing import Callable, Optional

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QImage, QPainter, QPen)
from PySide6.QtWidgets import (QAbstractScrollArea, QFileDialog, QInputDialog, QMenu,
                               QMessageBox)

from . import ops

GAP = 14
MARGIN = 20
PX = 96 / 72            # 100 % = 96 ppp
MIN_ZOOM, MAX_ZOOM = 0.15, 4.0
MAX_UNDO = 15
UNDO_BUDGET = 500 * 1024 * 1024
MAX_RENDER_PX = 7000


class Tool(enum.Enum):
    SELECT = "select"
    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKE = "strike"
    RECT = "rect"
    ELLIPSE = "ellipse"
    LINE = "line"
    ARROW = "arrow"
    INK = "ink"
    NOTE = "note"
    TEXTBOX = "textbox"
    SIGN = "sign"
    IMAGE = "image"
    REDACT = "redact"
    EDIT_TEXT = "edit_text"
    EDIT_IMAGE = "edit_image"
    FORM = "form"


TEXT_TOOLS = {Tool.SELECT, Tool.HIGHLIGHT, Tool.UNDERLINE, Tool.STRIKE}
MARKUP_KIND = {Tool.HIGHLIGHT: "highlight", Tool.UNDERLINE: "underline", Tool.STRIKE: "strike"}
DRAG_TOOLS = {Tool.RECT, Tool.ELLIPSE, Tool.LINE, Tool.ARROW, Tool.TEXTBOX, Tool.SIGN,
              Tool.IMAGE, Tool.REDACT}


def _rgb(c: QColor) -> tuple:
    return (c.redF(), c.greenF(), c.blueF())


class DocView(QAbstractScrollArea):
    modified = Signal()              # el contenido cambió (título, miniaturas...)
    structureChanged = Signal()      # cambió nº/orden/rotación de páginas
    pagesChanged = Signal(list)      # solo cambió el contenido de estas páginas
    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    selectionChanged = Signal(bool)
    message = Signal(str)

    def __init__(self, doc, path: Optional[str] = None, password: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.viewport().setMouseTracking(True)
        self.viewport().setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().setSingleStep(60)
        self.horizontalScrollBar().setSingleStep(40)

        self.doc = doc
        self.path = path
        self.password = password
        self.dirty = False

        self.zoom = 1.0
        self.fit_mode: Optional[str] = "width"
        self.tool = Tool.SELECT
        self.color = QColor(220, 30, 30)
        self.pen_width = 2.0
        self.font_size = 12.0

        # proveedores que rellena la ventana principal
        self.signature_getter: Optional[Callable[[], Optional[bytes]]] = None
        self.image_path: Optional[str] = None

        self._undo: list[bytes] = []
        self._redo: list[bytes] = []
        self._imgs: "OrderedDict[int, QImage]" = OrderedDict()
        self._words: dict[int, list] = {}
        self._wrects: dict[int, list] = {}
        self._rects: list[QRectF] = []
        self._sizes: list[tuple[float, float]] = []
        self._content_w = 0.0
        self._total_h = 0.0

        self._sel: Optional[dict] = None           # {'page', 'a', 'b'}
        self._sel_annot: Optional[tuple] = None    # (page, xref)
        self._text_drag = False
        self._press_pos: Optional[QPointF] = None
        self._drag: Optional[dict] = None
        self._hits: list[tuple[int, pymupdf.Rect]] = []
        self._hit_idx = -1
        self._last_page = -1

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self.viewport().update())
        self._rebuild_sizes()
        self.relayout()

    # ------------------------------------------------------------------ #
    # Documento / deshacer
    # ------------------------------------------------------------------ #
    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def _rebuild_sizes(self) -> None:
        self._sizes = []
        for i in range(self.doc.page_count):
            r = self.doc[i].rect
            self._sizes.append((r.width, r.height))

    def _drop_caches(self, pages=None) -> None:
        if pages is None:
            self._imgs.clear()
            self._words.clear()
            self._wrects.clear()
        else:
            for i in pages:
                self._imgs.pop(i, None)
                self._words.pop(i, None)
                self._wrects.pop(i, None)

    def replace_document(self, doc) -> None:
        old = self.doc
        self.doc = doc
        try:
            old.close()
        except Exception:
            pass
        self._drop_caches()
        self._sel = None
        self._sel_annot = None
        self._hits, self._hit_idx = [], -1
        self._rebuild_sizes()
        self.relayout()
        self.structureChanged.emit()
        self.modified.emit()

    def begin_edit(self) -> None:
        self._undo.append(ops.snapshot(self.doc))
        self._redo.clear()
        while len(self._undo) > MAX_UNDO or (len(self._undo) > 2 and sum(map(len, self._undo)) > UNDO_BUDGET):
            self._undo.pop(0)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(ops.snapshot(self.doc))
        data = self._undo.pop()
        self.dirty = True
        self.replace_document(ops.from_snapshot(data))

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(ops.snapshot(self.doc))
        data = self._redo.pop()
        self.dirty = True
        self.replace_document(ops.from_snapshot(data))

    def run_edit(self, fn: Callable[[], object], pages=None, structure: bool = False,
                 quiet_errors: bool = False):
        """Ejecuta `fn` (que modifica self.doc) con copia de seguridad para deshacer.
        Si falla, restaura el documento. Devuelve (ok, resultado)."""
        self.begin_edit()
        try:
            res = fn()
        except Exception as ex:  # noqa: BLE001
            data = self._undo.pop()
            self.replace_document(ops.from_snapshot(data))
            if not quiet_errors:
                QMessageBox.warning(self, "No se pudo completar", str(ex))
            return False, None
        self.dirty = True
        if structure or pages is None:
            self._drop_caches()
            self._rebuild_sizes()
            self._sel = None
            self._sel_annot = None
            self.relayout()
            self.structureChanged.emit()
        else:
            self._drop_caches(pages)
            self.viewport().update()
            if pages:
                self.pagesChanged.emit(list(pages))
        self.modified.emit()
        return True, res

    # ------------------------------------------------------------------ #
    # Disposición y zoom
    # ------------------------------------------------------------------ #
    def relayout(self) -> None:
        k = self.zoom * PX
        vw = self.viewport().width()
        vh = self.viewport().height()
        max_w = max((w for w, _h in self._sizes), default=0) * k
        self._content_w = max(max_w + 2 * MARGIN, vw)
        y = float(MARGIN)
        self._rects = []
        for w, h in self._sizes:
            self._rects.append(QRectF((self._content_w - w * k) / 2, y, w * k, h * k))
            y += h * k + GAP
        self._total_h = y - GAP + MARGIN
        self.horizontalScrollBar().setRange(0, int(max(0, self._content_w - vw)))
        self.horizontalScrollBar().setPageStep(vw)
        self.verticalScrollBar().setRange(0, int(max(0, self._total_h - vh)))
        self.verticalScrollBar().setPageStep(int(vh * 0.9))
        self.viewport().update()

    def set_zoom(self, z: float, keep_fit: bool = False) -> None:
        z = min(max(z, MIN_ZOOM), MAX_ZOOM)
        if not keep_fit:
            self.fit_mode = None
        if abs(z - self.zoom) < 1e-4:
            return
        anchor = self._top_anchor()
        self.zoom = z
        self._imgs.clear()
        self.relayout()
        self._restore_anchor(anchor)
        self.zoomChanged.emit(self.zoom)

    def _top_anchor(self) -> tuple[int, float]:
        """(página, fracción de su altura) que está en el borde superior de la vista."""
        if not self._rects:
            return 0, 0.0
        y = self.verticalScrollBar().value()
        for i, r in enumerate(self._rects):
            if y <= r.bottom() + GAP / 2:
                return i, max(-0.2, (y - r.top()) / r.height()) if r.height() else 0.0
        return len(self._rects) - 1, 0.0

    def _restore_anchor(self, anchor: tuple[int, float]) -> None:
        i, frac = anchor
        if 0 <= i < len(self._rects):
            r = self._rects[i]
            self.verticalScrollBar().setValue(int(r.top() + frac * r.height()))

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * 1.2)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / 1.2)

    def fit_width(self) -> None:
        self.fit_mode = "width"
        self._apply_fit()

    def fit_page(self) -> None:
        self.fit_mode = "page"
        self._apply_fit()

    def _apply_fit(self) -> None:
        if not self._sizes:
            return
        i = max(0, self.current_page())
        w, h = self._sizes[i]
        avail_w = self.viewport().width() - 2 * MARGIN
        avail_h = self.viewport().height() - 2 * MARGIN
        z = avail_w / (w * PX)
        if self.fit_mode == "page":
            z = min(z, avail_h / (h * PX))
        self.set_zoom(z, keep_fit=True)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        anchor = self._top_anchor()
        if self.fit_mode:
            self._apply_fit()
        self.relayout()
        self._restore_anchor(anchor)

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if e.angleDelta().y() > 0:
                self.zoom_in()
            elif e.angleDelta().y() < 0:
                self.zoom_out()
            e.accept()
        else:
            super().wheelEvent(e)

    def keyPressEvent(self, e) -> None:
        vs = self.verticalScrollBar()
        k = e.key()
        if k == Qt.Key.Key_Delete or k == Qt.Key.Key_Backspace:
            self.delete_selected_annotation()
        elif k == Qt.Key.Key_Escape:
            self.clear_selection()
        elif k == Qt.Key.Key_PageDown:
            vs.setValue(vs.value() + vs.pageStep())
        elif k == Qt.Key.Key_PageUp:
            vs.setValue(vs.value() - vs.pageStep())
        elif k == Qt.Key.Key_Down:
            vs.setValue(vs.value() + 60)
        elif k == Qt.Key.Key_Up:
            vs.setValue(vs.value() - 60)
        elif k == Qt.Key.Key_Home:
            vs.setValue(0)
        elif k == Qt.Key.Key_End:
            vs.setValue(vs.maximum())
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------ #
    # Navegación
    # ------------------------------------------------------------------ #
    def current_page(self) -> int:
        if not self._rects:
            return -1
        y = self.verticalScrollBar().value() + self.viewport().height() * 0.35
        cur = 0
        for i, r in enumerate(self._rects):
            if r.top() <= y:
                cur = i
            else:
                break
        return cur

    def go_to_page(self, i: int) -> None:
        if 0 <= i < len(self._rects):
            self.verticalScrollBar().setValue(int(self._rects[i].top() - MARGIN / 2))

    def _on_scroll(self, _v: int) -> None:
        self.viewport().update()
        cur = self.current_page()
        if cur != self._last_page:
            self._last_page = cur
            self.pageChanged.emit(cur)

    # ------------------------------------------------------------------ #
    # Conversión de coordenadas
    # ------------------------------------------------------------------ #
    def _scroll_off(self) -> QPointF:
        return QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())

    def _page_at(self, pos: QPointF) -> Optional[int]:
        c = pos + self._scroll_off()
        for i, r in enumerate(self._rects):
            if r.contains(c):
                return i
        return None

    def _nearest_page(self, pos: QPointF) -> int:
        c = pos + self._scroll_off()
        best, bd = 0, 1e18
        for i, r in enumerate(self._rects):
            d = 0 if r.top() <= c.y() <= r.bottom() else min(abs(c.y() - r.top()), abs(c.y() - r.bottom()))
            if d < bd:
                best, bd = i, d
        return best

    def _to_page_pt(self, i: int, pos: QPointF) -> pymupdf.Point:
        c = pos + self._scroll_off()
        r = self._rects[i]
        k = self.zoom * PX
        vis = pymupdf.Point((c.x() - r.left()) / k, (c.y() - r.top()) / k)
        return vis * self.doc[i].derotation_matrix

    def _pt_to_view(self, i: int, pt) -> QPointF:
        v = pymupdf.Point(pt) * self.doc[i].rotation_matrix
        r = self._rects[i]
        k = self.zoom * PX
        return QPointF(r.left() + v.x * k, r.top() + v.y * k) - self._scroll_off()

    def _rect_to_view(self, i: int, rect) -> QRectF:
        v = (pymupdf.Rect(rect) * self.doc[i].rotation_matrix).normalize()
        r = self._rects[i]
        k = self.zoom * PX
        return QRectF(r.left() + v.x0 * k, r.top() + v.y0 * k, v.width * k, v.height * k).translated(
            -self._scroll_off())

    # ------------------------------------------------------------------ #
    # Dibujado
    # ------------------------------------------------------------------ #
    def _render(self, i: int) -> QImage:
        img = self._imgs.get(i)
        if img is not None:
            self._imgs.move_to_end(i)
            return img
        page = self.doc[i]
        dpr = self.devicePixelRatioF()
        scale = self.zoom * PX * dpr
        w, h = self._sizes[i]
        biggest = max(w, h) * scale
        if biggest > MAX_RENDER_PX:
            scale *= MAX_RENDER_PX / biggest
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGB888).copy()
        self._imgs[i] = img
        budget = 0
        for key in reversed(list(self._imgs.keys())):
            budget += self._imgs[key].sizeInBytes()
            if budget > 350 * 1024 * 1024 and key != i:
                self._imgs.pop(key, None)
        while len(self._imgs) > 24:
            self._imgs.popitem(last=False)
        return img

    def _get_words(self, i: int) -> list:
        w = self._words.get(i)
        if w is None:
            w = self.doc[i].get_text("words", sort=True)
            self._words[i] = w
        return w

    def _get_wrects(self, i: int) -> list:
        r = self._wrects.get(i)
        if r is None:
            r = ops.widget_rects(self.doc[i])
            self._wrects[i] = r
        return r

    def paintEvent(self, ev) -> None:
        p = QPainter(self.viewport())
        p.fillRect(ev.rect(), QColor(82, 86, 89))
        if not self._rects:
            return
        off = self._scroll_off()
        top = off.y()
        bottom = top + self.viewport().height()
        for i, r in enumerate(self._rects):
            if r.bottom() < top:
                continue
            if r.top() > bottom:
                break
            target = r.translated(-off)
            p.fillRect(target.adjusted(2, 3, 3, 4), QColor(0, 0, 0, 70))
            p.drawImage(target, self._render(i))
            self._paint_overlays(p, i)
        if self._drag:
            self._paint_drag(p)

    def _paint_overlays(self, p: QPainter, i: int) -> None:
        for pi, rect in self._hits:
            if pi == i:
                cur = self._hits[self._hit_idx] == (pi, rect) if self._hit_idx >= 0 else False
                p.fillRect(self._rect_to_view(i, rect),
                           QColor(255, 120, 0, 140) if cur else QColor(255, 210, 0, 110))
        if self._sel and self._sel["page"] == i:
            for r in self._sel_line_rects():
                p.fillRect(self._rect_to_view(i, r), QColor(51, 144, 255, 90))
        if self.tool == Tool.FORM:
            for r in self._get_wrects(i):
                vr = self._rect_to_view(i, r)
                p.fillRect(vr, QColor(80, 150, 255, 45))
                p.setPen(QPen(QColor(60, 120, 230, 200), 1))
                p.drawRect(vr)
        if self._sel_annot and self._sel_annot[0] == i:
            a = self._annot_data(i, self._sel_annot[1])
            if a is not None:
                pen = QPen(QColor(30, 120, 255), 1.5, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(self._rect_to_view(i, a["rect"]).adjusted(-2, -2, 2, 2))

    def _paint_drag(self, p: QPainter) -> None:
        d = self._drag
        i = d["page"]
        col = QColor(self.color)
        pen = QPen(col, max(1.0, self.pen_width * self.zoom * PX))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        t = d["tool"]
        if t == Tool.INK:
            p.setPen(pen)
            pts = [self._pt_to_view(i, q) for q in d["stroke"]]
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
            return
        a, b = self._pt_to_view(i, d["p0"]), self._pt_to_view(i, d["p1"])
        if t in (Tool.LINE, Tool.ARROW):
            p.setPen(pen)
            p.drawLine(a, b)
            return
        r = QRectF(a, b).normalized()
        if t == Tool.RECT:
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
        elif t == Tool.ELLIPSE:
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)
        elif t == Tool.REDACT:
            p.setPen(QPen(QColor(200, 0, 0), 1.5))
            p.setBrush(QColor(0, 0, 0, 140))
            p.drawRect(r)
        else:
            p.setPen(QPen(QColor(30, 120, 255), 1.2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(30, 120, 255, 30))
            p.drawRect(r)

    # ------------------------------------------------------------------ #
    # Selección de texto
    # ------------------------------------------------------------------ #
    def _nearest_word(self, i: int, pt: pymupdf.Point) -> Optional[int]:
        words = self._get_words(i)
        best, bd = None, 1e18
        for n, w in enumerate(words):
            dx = max(w[0] - pt.x, 0, pt.x - w[2])
            dy = max(w[1] - pt.y, 0, pt.y - w[3])
            d = dx * dx + dy * dy * 4
            if d < bd:
                best, bd = n, d
        return best

    def _sel_words(self) -> list:
        if not self._sel:
            return []
        lo, hi = sorted((self._sel["a"], self._sel["b"]))
        return self._get_words(self._sel["page"])[lo:hi + 1]

    def _sel_lines(self) -> "OrderedDict[tuple, list]":
        groups: "OrderedDict[tuple, list]" = OrderedDict()
        for w in self._sel_words():
            groups.setdefault((w[5], w[6]), []).append(w)
        return groups

    def _sel_line_rects(self) -> list:
        out = []
        for ws in self._sel_lines().values():
            out.append(pymupdf.Rect(min(w[0] for w in ws), min(w[1] for w in ws),
                                    max(w[2] for w in ws), max(w[3] for w in ws)))
        return out

    def has_selection(self) -> bool:
        return bool(self._sel_words())

    def selected_text(self) -> str:
        return "\n".join(" ".join(w[4] for w in ws) for ws in self._sel_lines().values())

    def copy_selection(self) -> None:
        t = self.selected_text()
        if t:
            QGuiApplication.clipboard().setText(t)
            self.message.emit(f"Copiado: {len(t)} caracteres")

    def clear_selection(self) -> None:
        had = self._sel is not None or self._sel_annot is not None
        self._sel = None
        self._sel_annot = None
        if had:
            self.selectionChanged.emit(False)
            self.viewport().update()

    def apply_markup_to_selection(self, kind: str) -> bool:
        rects = self._sel_line_rects()
        if not rects:
            return False
        i = self._sel["page"]
        col = _rgb(self.color)
        ok, _ = self.run_edit(lambda: ops.add_markup(self.doc[i], kind, rects, col), pages=[i])
        if ok:
            self._sel = None
            self.selectionChanged.emit(False)
        return ok

    def redact_selection(self) -> bool:
        rects = self._sel_line_rects()
        if not rects:
            return False
        i = self._sel["page"]
        ok, _ = self.run_edit(lambda: [ops.add_redaction(self.doc[i], r) for r in rects], pages=[i])
        if ok:
            self._sel = None
            self.selectionChanged.emit(False)
            self.message.emit("Marcado para redactar. Usa Documento ▸ Aplicar redacciones para eliminarlo de verdad.")
        return ok

    # ------------------------------------------------------------------ #
    # Anotaciones
    # ------------------------------------------------------------------ #
    def _annot_data(self, i: int, xref: int) -> Optional[dict]:
        return ops.annot_by_xref(self.doc[i], xref)

    def delete_selected_annotation(self) -> bool:
        if not self._sel_annot:
            return False
        i, xref = self._sel_annot
        ok, _ = self.run_edit(lambda: ops.delete_annot_xref(self.doc[i], xref), pages=[i])
        self._sel_annot = None
        return ok

    def _edit_annot_text(self, i: int, xref: int) -> None:
        a = self._annot_data(i, xref)
        if a is None or a["type"] not in (pymupdf.PDF_ANNOT_TEXT, pymupdf.PDF_ANNOT_FREE_TEXT):
            return
        kind, cur = a["type"], a["content"]
        rect, color = a["rect"], a["stroke"] or (0, 0, 0)
        text, ok = QInputDialog.getMultiLineText(self, "Editar texto", "Texto:", cur)
        if not ok or text == cur:
            return

        def apply():
            pg = self.doc[i]
            an = next(x for x in pg.annots() if x.xref == xref)
            if kind == pymupdf.PDF_ANNOT_TEXT:
                an.set_info(content=text)
                an.update()
            else:  # texto libre: se recrea con el mismo rectángulo
                pg.delete_annot(an)
                ops.add_textbox(pg, rect, text, self.font_size, color)

        self.run_edit(apply, pages=[i])

    # ------------------------------------------------------------------ #
    # Ratón
    # ------------------------------------------------------------------ #
    def _update_cursor(self, pos: QPointF) -> None:
        i = self._page_at(pos)
        if i is None:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            return
        t = self.tool
        if t in TEXT_TOOLS:
            c = Qt.CursorShape.IBeamCursor
        elif t in (Tool.EDIT_TEXT,):
            c = Qt.CursorShape.IBeamCursor
        elif t == Tool.FORM:
            c = Qt.CursorShape.ArrowCursor
            pt = self._to_page_pt(i, pos)
            if any(r.contains(pt) for r in self._get_wrects(i)):
                c = Qt.CursorShape.PointingHandCursor
        elif t == Tool.EDIT_IMAGE:
            c = Qt.CursorShape.PointingHandCursor
        else:
            c = Qt.CursorShape.CrossCursor
        self.viewport().setCursor(c)

    def mousePressEvent(self, e) -> None:
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton or not self._rects:
            return
        pos = e.position()
        i = self._page_at(pos)
        if i is None:
            self.clear_selection()
            return
        pt = self._to_page_pt(i, pos)
        t = self.tool
        self._press_pos = pos

        if t in TEXT_TOOLS:
            self._sel_annot = None
            if t == Tool.SELECT:
                a = ops.annot_at(self.doc[i], pt)
                if a is not None:
                    self._sel_annot = (i, a["xref"])
            n = self._nearest_word(i, pt)
            self._sel = {"page": i, "a": n, "b": n} if n is not None else None
            self._text_drag = n is not None
            self.viewport().update()
        elif t in DRAG_TOOLS:
            if t == Tool.SIGN and not self.signature_getter:
                return
            self._drag = {"tool": t, "page": i, "p0": pt, "p1": pt}
        elif t == Tool.INK:
            self._drag = {"tool": t, "page": i, "stroke": [pt], "p0": pt, "p1": pt}
        elif t == Tool.NOTE:
            text, ok = QInputDialog.getMultiLineText(self, "Nota", "Texto de la nota:")
            if ok and text.strip():
                col = (1, 0.85, 0)
                self.run_edit(lambda: ops.add_note(self.doc[i], pt, text, col), pages=[i])
        elif t == Tool.EDIT_TEXT:
            self._edit_text_at(i, pt)
        elif t == Tool.EDIT_IMAGE:
            self._edit_image_at(i, pt, e.globalPosition().toPoint())
        elif t == Tool.FORM:
            self._edit_form_at(i, pt)

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self._text_drag and self._sel:
            i = self._sel["page"]
            pt = self._to_page_pt(i, pos)
            n = self._nearest_word(i, pt)
            if n is not None and n != self._sel["b"]:
                self._sel["b"] = n
                self.viewport().update()
        elif self._drag:
            i = self._drag["page"]
            pt = self._to_page_pt(i, pos)
            self._drag["p1"] = pt
            if self._drag["tool"] == Tool.INK:
                self._drag["stroke"].append(pt)
            self.viewport().update()
        else:
            self._update_cursor(pos)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        moved = self._press_pos is not None and (pos - self._press_pos).manhattanLength() > 4
        if self._text_drag:
            self._text_drag = False
            if self._sel and self.tool in MARKUP_KIND and self.has_selection():
                self.apply_markup_to_selection(MARKUP_KIND[self.tool])
            elif self._sel and not moved:
                self._sel = None  # un simple clic no deja texto seleccionado
            if moved:
                self._sel_annot = None
            self.selectionChanged.emit(self.has_selection())
            self.viewport().update()
        elif self._drag:
            d, self._drag = self._drag, None
            self._finish_drag(d)
            self.viewport().update()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self._rects:
            return
        i = self._page_at(e.position())
        if i is None:
            return
        pt = self._to_page_pt(i, e.position())
        if self.tool == Tool.SELECT:
            a = ops.annot_at(self.doc[i], pt)
            if a is not None and a["type"] in (pymupdf.PDF_ANNOT_TEXT, pymupdf.PDF_ANNOT_FREE_TEXT):
                self._edit_annot_text(i, a["xref"])
                return
            n = self._nearest_word(i, pt)
            if n is not None:
                self._sel = {"page": i, "a": n, "b": n}
                self.selectionChanged.emit(True)
                self.viewport().update()

    def contextMenuEvent(self, e) -> None:
        if not self._rects:
            return
        pos = QPointF(e.pos())
        i = self._page_at(pos)
        menu = QMenu(self)
        if i is not None:
            pt = self._to_page_pt(i, pos)
            a = ops.annot_at(self.doc[i], pt)
            if self.has_selection():
                menu.addAction("Copiar", self.copy_selection)
                menu.addSeparator()
                menu.addAction("Resaltar", lambda: self.apply_markup_to_selection("highlight"))
                menu.addAction("Subrayar", lambda: self.apply_markup_to_selection("underline"))
                menu.addAction("Tachar", lambda: self.apply_markup_to_selection("strike"))
                menu.addAction("Marcar para redactar", self.redact_selection)
            elif a is not None:
                xref = a["xref"]
                self._sel_annot = (i, xref)
                self.viewport().update()
                if a["type"] in (pymupdf.PDF_ANNOT_TEXT, pymupdf.PDF_ANNOT_FREE_TEXT):
                    menu.addAction("Editar texto…", lambda: self._edit_annot_text(i, xref))
                menu.addAction("Eliminar anotación", self.delete_selected_annotation)
        if menu.actions():
            menu.exec(e.globalPos())

    # ------------------------------------------------------------------ #
    # Finalizar gestos
    # ------------------------------------------------------------------ #
    def _finish_drag(self, d: dict) -> None:
        t, i, p0, p1 = d["tool"], d["page"], d["p0"], d["p1"]
        col = _rgb(self.color)
        w = self.pen_width
        if t == Tool.INK:
            stroke = [tuple(q) for q in d["stroke"]]
            self.run_edit(lambda: ops.add_ink(self.doc[i], [stroke], col, w), pages=[i])
        elif t in (Tool.RECT, Tool.ELLIPSE, Tool.LINE, Tool.ARROW):
            kind = {Tool.RECT: "rect", Tool.ELLIPSE: "ellipse", Tool.LINE: "line", Tool.ARROW: "arrow"}[t]
            if abs(p1.x - p0.x) < 2 and abs(p1.y - p0.y) < 2:
                return
            self.run_edit(lambda: ops.add_shape(self.doc[i], kind, p0, p1, col, w), pages=[i])
        elif t == Tool.TEXTBOX:
            text, ok = QInputDialog.getMultiLineText(self, "Texto", "Escribe el texto:")
            if ok and text.strip():
                fs = self.font_size
                self.run_edit(lambda: ops.add_textbox(self.doc[i], pymupdf.Rect(p0, p1), text, fs, col),
                              pages=[i])
        elif t == Tool.REDACT:
            self.run_edit(lambda: ops.add_redaction(self.doc[i], pymupdf.Rect(p0, p1)), pages=[i])
            n = ops.count_redactions(self.doc)
            self.message.emit(f"{n} redacción(es) pendiente(s). Documento ▸ Aplicar redacciones para eliminarlas.")
        elif t == Tool.SIGN:
            png = self.signature_getter() if self.signature_getter else None
            if png:
                self._place_image(i, p0, p1, stream=png, default_w=150)
        elif t == Tool.IMAGE:
            if not self.image_path or not os.path.exists(self.image_path):
                self.message.emit("Elige primero una imagen (vuelve a pulsar la herramienta Imagen).")
                return
            self._place_image(i, p0, p1, filename=self.image_path, default_w=180)

    def _place_image(self, i: int, p0, p1, *, stream=None, filename=None, default_w: float = 150) -> None:
        rect = pymupdf.Rect(p0, p1).normalize()
        if rect.width < 6 or rect.height < 6:  # clic: tamaño por defecto con la proporción de la imagen
            qi = QImage.fromData(stream) if stream else QImage(filename)
            ratio = (qi.height() / qi.width()) if qi.width() else 0.4
            rect = pymupdf.Rect(p0.x, p0.y, p0.x + default_w, p0.y + default_w * ratio)
        self.run_edit(lambda: ops.insert_image(self.doc[i], rect, stream=stream, filename=filename),
                      pages=[i])

    # ------------------------------------------------------------------ #
    # Edición de texto, imágenes y formularios existentes
    # ------------------------------------------------------------------ #
    def _edit_text_at(self, i: int, pt) -> None:
        page = self.doc[i]
        if page.rotation != 0:
            QMessageBox.information(self, "Página rotada",
                                    "Para editar texto, deja la página con rotación 0° "
                                    "(Páginas ▸ Rotar) y vuelve a intentarlo.")
            return
        info = ops.line_at(page, pt)
        if not info:
            self.message.emit("No hay texto editable ahí. Si es un escaneo, usa Documento ▸ OCR primero.")
            return
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Editar texto")
        dlg.setLabelText(f"Línea de texto (fuente {info['font']}, {info['size']:.1f} pt → se usará "
                         f"{info['fontname']}):")
        dlg.setTextValue(info["text"])
        dlg.resize(640, 120)
        if dlg.exec() != QInputDialog.DialogCode.Accepted:
            return
        new = dlg.textValue()
        if new == info["text"]:
            return
        self.run_edit(lambda: ops.replace_text(self.doc[i], info, new), pages=[i])

    def _exec_menu(self, menu: QMenu, global_pos):
        return menu.exec(global_pos)

    def _edit_image_at(self, i: int, pt, global_pos) -> None:
        ims = ops.images_at(self.doc[i], pt)
        if not ims:
            self.message.emit("No hay ninguna imagen en ese punto.")
            return
        im = ims[-1]
        xref, bbox = im["xref"], im["bbox"]
        menu = QMenu(self)
        act_rep = menu.addAction("Reemplazar imagen…")
        act_del = menu.addAction("Eliminar imagen")
        act_save = menu.addAction("Guardar imagen como…")
        chosen = self._exec_menu(menu, global_pos)
        if chosen is act_del:
            self.run_edit(lambda: ops.remove_image_area(self.doc[i], bbox), pages=[i])
        elif chosen is act_rep:
            if not xref:
                QMessageBox.information(self, "Imagen", "Esta imagen no se puede reemplazar directamente.")
                return
            path, _ = QFileDialog.getOpenFileName(self, "Nueva imagen", "",
                                                  "Imágenes (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)")
            if path:
                self.run_edit(lambda: ops.replace_image(self.doc[i], xref, path), pages=[i])
        elif chosen is act_save:
            path, _ = QFileDialog.getSaveFileName(self, "Guardar imagen", "imagen")
            if path and xref:
                out = ops.save_image_xref(self.doc, xref, os.path.splitext(path)[0])
                self.message.emit(f"Imagen guardada: {out}")

    def _edit_form_at(self, i: int, pt) -> None:
        page = self.doc[i]
        w = ops.widget_at(page, pt)
        if w is None:
            return
        kind = ops.widget_kind(w)
        xref = w.xref
        name = w.field_name

        def setter(value):
            def apply():
                pg = self.doc[i]
                ww = next(x for x in pg.widgets() if x.xref == xref)
                ww.field_value = value
                ww.update()
            self.run_edit(apply, pages=[i])

        if kind == "text":
            cur = w.field_value or ""
            if w.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
                text, ok = QInputDialog.getMultiLineText(self, "Campo", f"{name}:", str(cur))
            else:
                text, ok = QInputDialog.getText(self, "Campo", f"{name}:", text=str(cur))
            if ok:
                setter(text)
        elif kind == "checkbox":
            val = w.field_value
            on = val not in (False, "Off", "", None, 0)
            setter(False if on else w.on_state())
        elif kind == "radio":
            setter(w.on_state())
        elif kind in ("combo", "list"):
            choices = [c if isinstance(c, str) else c[-1] for c in (w.choice_values or [])]
            if not choices:
                return
            cur = str(w.field_value) if w.field_value in choices else choices[0]
            item, ok = QInputDialog.getItem(self, "Campo", f"{name}:", choices, choices.index(cur), False)
            if ok:
                setter(item)
        elif kind == "signature":
            self.message.emit("Para firmar usa la herramienta Firma (dibujada o con imagen).")
        else:
            self.message.emit("Este campo no se puede editar aquí.")

    # ------------------------------------------------------------------ #
    # Búsqueda
    # ------------------------------------------------------------------ #
    def find(self, term: str) -> int:
        self._hits, self._hit_idx = [], -1
        if term:
            for i in range(self.doc.page_count):
                for r in self.doc[i].search_for(term):
                    self._hits.append((i, pymupdf.Rect(r)))
        self.viewport().update()
        if self._hits:
            self.find_step(1)
        return len(self._hits)

    def find_step(self, step: int) -> None:
        if not self._hits:
            return
        self._hit_idx = (self._hit_idx + step) % len(self._hits)
        i, r = self._hits[self._hit_idx]
        vr = self._rect_to_view(i, r).translated(self._scroll_off())
        vs, hs = self.verticalScrollBar(), self.horizontalScrollBar()
        vs.setValue(int(vr.center().y() - self.viewport().height() * 0.3))
        if vr.left() < hs.value() or vr.right() > hs.value() + self.viewport().width():
            hs.setValue(int(vr.center().x() - self.viewport().width() / 2))
        self.viewport().update()

    def clear_find(self) -> None:
        self._hits, self._hit_idx = [], -1
        self.viewport().update()

    @property
    def hit_info(self) -> tuple[int, int]:
        return (self._hit_idx + 1, len(self._hits))

    # ------------------------------------------------------------------ #
    # Herramienta activa
    # ------------------------------------------------------------------ #
    def set_tool(self, tool: Tool) -> None:
        self.tool = tool
        self._drag = None
        self._text_drag = False
        if tool not in TEXT_TOOLS:
            self._sel = None
            self.selectionChanged.emit(False)
        self.viewport().update()

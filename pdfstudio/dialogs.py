"""Diálogos de la aplicación y utilidades para ejecutar tareas largas sin congelar la ventana."""
from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6.QtCore import QBuffer, QEventLoop, QIODevice, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressDialog,
                               QPushButton, QRadioButton, QSpinBox, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget, QInputDialog)

from . import ops

PDF_FILTER = "PDF (*.pdf)"
IMG_FILTER = "Imágenes (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)"


# ---------------------------------------------------------------------------
# Tareas en segundo plano
# ---------------------------------------------------------------------------
class _Worker(QThread):
    progress = Signal(int, int)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.result = None
        self.error: Optional[BaseException] = None
        self.cancelled = False
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            self.result = self.fn(lambda i, n: self.progress.emit(i, n), lambda: self._cancel)
        except ops.Cancelled:
            self.cancelled = True
        except BaseException as ex:  # noqa: BLE001
            self.error = ex


def run_task(parent, title: str, fn: Callable, label: str = "Procesando…"):
    """Ejecuta fn(progress, cancel) en un hilo mostrando una barra. Devuelve (ok, resultado).
    Si hay error muestra un aviso y devuelve (False, None)."""
    dlg = QProgressDialog(label, "Cancelar", 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    w = _Worker(fn, parent)
    loop = QEventLoop()

    def on_progress(i: int, n: int) -> None:
        dlg.setMaximum(max(n, 1))
        dlg.setValue(min(i, n))

    w.progress.connect(on_progress)
    w.finished.connect(loop.quit)
    dlg.canceled.connect(w.cancel)
    w.start()
    dlg.show()
    loop.exec()
    w.wait()
    dlg.close()
    if w.cancelled:
        return False, None
    if w.error is not None:
        QMessageBox.warning(parent, title, f"No se pudo completar:\n{w.error}")
        return False, None
    return True, w.result


def ask_password(parent, path: str, wrong: bool = False) -> Optional[str]:
    msg = f"«{os.path.basename(path)}» está protegido con contraseña."
    if wrong:
        msg = "Contraseña incorrecta. " + msg
    text, ok = QInputDialog.getText(parent, "Contraseña", msg, QLineEdit.EchoMode.Password)
    return text if ok else None


def open_with_password(parent, path: str):
    """Abre un PDF pidiendo contraseña si hace falta. Devuelve (doc, pw) o None si se cancela."""
    pw: Optional[str] = None
    wrong = False
    while True:
        try:
            return ops.open_pdf(path, pw)
        except ops.NeedsPassword:
            pass
        except ops.BadPassword:
            wrong = True
        pw = ask_password(parent, path, wrong)
        if pw is None:
            return None


def pick_save(parent, title: str, default: str, flt: str = PDF_FILTER) -> Optional[str]:
    path, _ = QFileDialog.getSaveFileName(parent, title, default, flt)
    return path or None


# ---------------------------------------------------------------------------
# Lista de archivos reordenable (unir PDFs / imágenes a PDF)
# ---------------------------------------------------------------------------
class FileList(QWidget):
    def __init__(self, flt: str, parent=None):
        super().__init__(parent)
        self.flt = flt
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        b_add, b_del = QPushButton("Añadir…"), QPushButton("Quitar")
        b_up, b_down = QPushButton("▲ Subir"), QPushButton("▼ Bajar")
        b_add.clicked.connect(self._add)
        b_del.clicked.connect(self._remove)
        b_up.clicked.connect(lambda: self._move(-1))
        b_down.clicked.connect(lambda: self._move(1))
        col = QVBoxLayout()
        for b in (b_add, b_del, b_up, b_down):
            col.addWidget(b)
        col.addStretch(1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.list, 1)
        lay.addLayout(col)

    def add_paths(self, paths) -> None:
        for p in paths:
            it = QListWidgetItem(os.path.basename(p))
            it.setToolTip(p)
            it.setData(Qt.ItemDataRole.UserRole, p)
            self.list.addItem(it)

    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _add(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir archivos", "", self.flt)
        self.add_paths(files)

    def _remove(self) -> None:
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def _move(self, d: int) -> None:
        rows = sorted(self.list.row(i) for i in self.list.selectedItems())
        if not rows:
            return
        if d > 0:
            rows = rows[::-1]
        for r in rows:
            t = r + d
            if 0 <= t < self.list.count():
                it = self.list.takeItem(r)
                self.list.insertItem(t, it)
                it.setSelected(True)


def _out_row(edit: QLineEdit, on_browse: Callable[[], None]) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    b = QPushButton("Examinar…")
    b.clicked.connect(on_browse)
    row.addWidget(b)
    return row


# ---------------------------------------------------------------------------
class MergeDialog(QDialog):
    def __init__(self, parent=None, initial: Optional[list[str]] = None, images: bool = False):
        super().__init__(parent)
        self.images = images
        self.setWindowTitle("Imágenes a PDF" if images else "Unir PDFs")
        self.resize(560, 420)
        self.files = FileList(IMG_FILTER if images else PDF_FILTER)
        if initial:
            self.files.add_paths(initial)
        self.chk = QCheckBox("Crear un marcador por cada archivo")
        self.chk.setChecked(True)
        self.out = QLineEdit()
        self.out.setPlaceholderText("Archivo de salida…")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Arrastra para reordenar. El resultado respeta este orden."))
        lay.addWidget(self.files, 1)
        if not images:
            lay.addWidget(self.chk)
        lay.addLayout(_out_row(self.out, self._browse))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.passwords: dict[str, Optional[str]] = {}

    def _browse(self) -> None:
        p = pick_save(self, "Guardar como", self.out.text() or "unido.pdf")
        if p:
            self.out.setText(p if p.lower().endswith(".pdf") else p + ".pdf")

    def _ok(self) -> None:
        paths = self.files.paths()
        if len(paths) < (1 if self.images else 2):
            QMessageBox.information(self, "Faltan archivos",
                                    "Añade al menos " + ("una imagen." if self.images else "dos PDFs."))
            return
        if not self.out.text().strip():
            self._browse()
            if not self.out.text().strip():
                return
        if not self.images:
            for p in paths:
                if p in self.passwords:
                    continue
                r = open_with_password(self, p)
                if r is None:
                    return
                doc, pw = r
                doc.close()
                self.passwords[p] = pw
        self.accept()

    def items(self) -> list[tuple[str, Optional[str]]]:
        return [(p, self.passwords.get(p)) for p in self.files.paths()]

    def out_path(self) -> str:
        return self.out.text().strip()

    def bookmarks(self) -> bool:
        return self.chk.isChecked()


# ---------------------------------------------------------------------------
class SplitDialog(QDialog):
    def __init__(self, parent, n_pages: int, base: str):
        super().__init__(parent)
        self.n = n_pages
        self.setWindowTitle("Dividir PDF")
        self.r_ranges = QRadioButton("Por rangos (un archivo por rango):")
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText(f"Ej.: 1-3, 4-6, 9   (el documento tiene {n_pages} páginas)")
        self.r_every = QRadioButton("Cada")
        self.every = QSpinBox()
        self.every.setRange(1, max(1, n_pages))
        self.every.setValue(1 if n_pages < 2 else min(5, n_pages))
        self.r_each = QRadioButton("Una página por archivo")
        self.r_ranges.setChecked(True)
        self.folder = QLineEdit()
        self.base = QLineEdit(base)
        lay = QVBoxLayout(self)
        lay.addWidget(self.r_ranges)
        lay.addWidget(self.ranges)
        row = QHBoxLayout()
        row.addWidget(self.r_every)
        row.addWidget(self.every)
        row.addWidget(QLabel("páginas"))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(self.r_each)
        form = QFormLayout()
        form.addRow("Nombre base:", self.base)
        form.addRow("Carpeta de salida:", self._folder_row())
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _folder_row(self) -> QHBoxLayout:
        def browse() -> None:
            d = QFileDialog.getExistingDirectory(self, "Carpeta de salida", self.folder.text())
            if d:
                self.folder.setText(d)
        return _out_row(self.folder, browse)

    def _ok(self) -> None:
        if not self.folder.text().strip():
            QMessageBox.information(self, "Carpeta", "Elige una carpeta de salida.")
            return
        if self.r_ranges.isChecked():
            try:
                ops.parse_ranges(self.ranges.text(), self.n)
            except ValueError as ex:
                QMessageBox.warning(self, "Rangos", str(ex))
                return
        self.accept()

    def groups(self) -> list[list[int]]:
        if self.r_ranges.isChecked():
            return ops.parse_ranges(self.ranges.text(), self.n)
        step = self.every.value() if self.r_every.isChecked() else 1
        return [list(range(a, min(a + step, self.n))) for a in range(0, self.n, step)]

    def out_dir(self) -> str:
        return self.folder.text().strip()

    def base_name(self) -> str:
        return self.base.text().strip() or "parte"


# ---------------------------------------------------------------------------
class SignatureCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 190)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.strokes: list[list[QPointF]] = []
        self.ink = QColor(10, 20, 90)
        self.loaded: Optional[QImage] = None

    def clear(self) -> None:
        self.strokes, self.loaded = [], None
        self.update()

    def set_image(self, img: QImage) -> None:
        self.loaded = img
        self.strokes = []
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), Qt.GlobalColor.white)
        p.setPen(QPen(QColor(200, 200, 200), 1, Qt.PenStyle.DashLine))
        y = self.height() * 0.72
        p.drawLine(16, int(y), self.width() - 16, int(y))
        if self.loaded is not None:
            sc = min((self.width() - 20) / self.loaded.width(), (self.height() - 20) / self.loaded.height())
            w, h = self.loaded.width() * sc, self.loaded.height() * sc
            p.drawImage(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.loaded)
        pen = QPen(self.ink, 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        for s in self.strokes:
            if len(s) == 1:
                p.drawPoint(s[0])
            for a, b in zip(s, s[1:]):
                p.drawLine(a, b)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.strokes.append([e.position()])
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self.strokes and e.buttons() & Qt.MouseButton.LeftButton:
            self.strokes[-1].append(e.position())
            self.update()

    def is_empty(self) -> bool:
        return not self.strokes and self.loaded is None

    def to_png(self) -> Optional[bytes]:
        if self.is_empty():
            return None
        scale = 3
        img = QImage(self.width() * scale, self.height() * scale, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(scale, scale)
        if self.loaded is not None:
            sc = min((self.width() - 20) / self.loaded.width(), (self.height() - 20) / self.loaded.height())
            w, h = self.loaded.width() * sc, self.loaded.height() * sc
            p.drawImage(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.loaded)
        pen = QPen(self.ink, 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        for s in self.strokes:
            if len(s) == 1:
                p.drawPoint(s[0])
            for a, b in zip(s, s[1:]):
                p.drawLine(a, b)
        p.end()
        box = _alpha_bbox(img)
        if box is None:
            return None
        pad = 6 * scale
        box = box.adjusted(-pad, -pad, pad, pad).intersected(img.rect())
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.copy(box).save(buf, "PNG")
        return bytes(buf.data())


def _alpha_bbox(img: QImage):
    from PySide6.QtCore import QRect
    w, h = img.width(), img.height()
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if (img.pixel(x, y) >> 24) & 0xFF > 10:
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
    if x1 < 0:
        return None
    return QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def image_to_signature(path: str, max_w: int = 700) -> Optional[QImage]:
    """Convierte una foto/escaneo de una firma en imagen con fondo transparente."""
    src = QImage(path)
    if src.isNull():
        return None
    if src.width() > max_w:
        src = src.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)
    src = src.convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(src.height()):
        for x in range(src.width()):
            c = QColor(src.pixel(x, y))
            lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            alpha = max(0, min(255, int((235 - lum) * 2.2)))
            src.setPixelColor(x, y, QColor(min(c.red(), 40), min(c.green(), 50), min(c.blue(), 110), alpha))
    return src


class SignatureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tu firma")
        self.canvas = SignatureCanvas()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Dibuja tu firma con el ratón/lápiz o carga una foto de tu firma sobre papel blanco."))
        lay.addWidget(self.canvas)
        row = QHBoxLayout()
        b_clear = QPushButton("Borrar")
        b_load = QPushButton("Cargar imagen…")
        b_clear.clicked.connect(self.canvas.clear)
        b_load.clicked.connect(self._load)
        row.addWidget(b_clear)
        row.addWidget(b_load)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Es una firma visual (imagen). No es una firma digital con certificado."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.png: Optional[bytes] = None

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Imagen de la firma", "", IMG_FILTER)
        if not path:
            return
        img = image_to_signature(path)
        if img is None:
            QMessageBox.warning(self, "Imagen", "No se pudo leer la imagen.")
            return
        self.canvas.set_image(img)

    def _ok(self) -> None:
        self.png = self.canvas.to_png()
        if not self.png:
            QMessageBox.information(self, "Firma", "Dibuja o carga una firma primero.")
            return
        self.accept()


# ---------------------------------------------------------------------------
class CompressDialog(QDialog):
    def __init__(self, parent, size: int, base: str):
        super().__init__(parent)
        self.setWindowTitle("Comprimir PDF")
        self.level = QComboBox()
        for k, (label, _o) in ops.COMPRESS_LEVELS.items():
            self.level.addItem(label, k)
        self.level.setCurrentIndex(1)
        self.out = QLineEdit(base + "_comprimido.pdf")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Tamaño actual (con tus cambios): {ops.human_size(size)}"))
        form = QFormLayout()
        form.addRow("Nivel:", self.level)
        form.addRow("Guardar como:", _out_row(self.out, self._browse))
        lay.addLayout(form)
        lay.addWidget(QLabel("Se crea una copia nueva; el original no se toca."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self) -> None:
        p = pick_save(self, "Guardar copia comprimida", self.out.text())
        if p:
            self.out.setText(p)

    def _ok(self) -> None:
        if not os.path.dirname(self.out.text()):
            self._browse()
        if os.path.dirname(self.out.text()):
            self.accept()

    def values(self) -> tuple[int, str]:
        return self.level.currentData(), self.out.text().strip()


# ---------------------------------------------------------------------------
class ProtectDialog(QDialog):
    def __init__(self, parent, base: str):
        super().__init__(parent)
        self.setWindowTitle("Proteger con contraseña")
        self.pw1, self.pw2 = QLineEdit(), QLineEdit()
        for e in (self.pw1, self.pw2):
            e.setEchoMode(QLineEdit.EchoMode.Password)
        self.owner = QLineEdit()
        self.owner.setEchoMode(QLineEdit.EchoMode.Password)
        self.owner.setPlaceholderText("(opcional; vacía = aleatoria, nadie podrá quitar las restricciones)")
        self.c_print = QCheckBox("Permitir imprimir")
        self.c_copy = QCheckBox("Permitir copiar texto")
        self.c_mod = QCheckBox("Permitir modificar / anotar")
        self.c_print.setChecked(True)
        self.c_copy.setChecked(True)
        self.out = QLineEdit(base + "_protegido.pdf")
        form = QFormLayout()
        form.addRow("Contraseña para abrir:", self.pw1)
        form.addRow("Repetir:", self.pw2)
        form.addRow("Contraseña de permisos:", self.owner)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        for c in (self.c_print, self.c_copy, self.c_mod):
            lay.addWidget(c)
        f2 = QFormLayout()
        f2.addRow("Guardar como:", _out_row(self.out, self._browse))
        lay.addLayout(f2)
        note = QLabel("Cifrado AES-256. Si olvidas la contraseña no hay forma de recuperarla.\n"
                      "Las restricciones de imprimir/copiar/modificar son orientativas: algunos programas las ignoran.")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self) -> None:
        p = pick_save(self, "Guardar PDF protegido", self.out.text())
        if p:
            self.out.setText(p)

    def _ok(self) -> None:
        if not self.pw1.text():
            QMessageBox.information(self, "Contraseña", "Escribe una contraseña.")
        elif self.pw1.text() != self.pw2.text():
            QMessageBox.warning(self, "Contraseña", "Las contraseñas no coinciden.")
        elif not os.path.dirname(self.out.text()):
            self._browse()
            if os.path.dirname(self.out.text()):
                self.accept()
        else:
            self.accept()

    def values(self) -> dict:
        return dict(out_path=self.out.text().strip(), user_pw=self.pw1.text(),
                    owner_pw=self.owner.text() or None, allow_print=self.c_print.isChecked(),
                    allow_copy=self.c_copy.isChecked(), allow_modify=self.c_mod.isChecked())


# ---------------------------------------------------------------------------
class MetadataDialog(QDialog):
    FIELDS = (("title", "Título"), ("author", "Autor"), ("subject", "Asunto"), ("keywords", "Palabras clave"))

    def __init__(self, parent, meta: dict):
        super().__init__(parent)
        self.setWindowTitle("Propiedades del documento")
        self.edits = {}
        form = QFormLayout()
        for key, label in self.FIELDS:
            e = QLineEdit(meta.get(key, ""))
            self.edits[key] = e
            form.addRow(label + ":", e)
        for key, label in (("creator", "Creado con"), ("producer", "Productor")):
            form.addRow(label + ":", QLabel(meta.get(key, "") or "—"))
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(440, self.sizeHint().height())

    def values(self) -> dict:
        return {k: e.text() for k, e in self.edits.items()}


# ---------------------------------------------------------------------------
class ExportImagesDialog(QDialog):
    def __init__(self, parent, n_pages: int, base: str, current: int):
        super().__init__(parent)
        self.n = n_pages
        self.setWindowTitle("Exportar páginas como imágenes")
        self.pages = QLineEdit()
        self.pages.setPlaceholderText(f"vacío = todas · ej.: 1-3, {current + 1}")
        self.dpi = QSpinBox()
        self.dpi.setRange(36, 600)
        self.dpi.setValue(150)
        self.fmt = QComboBox()
        self.fmt.addItems(["png", "jpg"])
        self.folder = QLineEdit()
        self.base = QLineEdit(base)
        form = QFormLayout()
        form.addRow("Páginas:", self.pages)
        form.addRow("Resolución (ppp):", self.dpi)
        form.addRow("Formato:", self.fmt)
        form.addRow("Nombre base:", self.base)
        form.addRow("Carpeta:", _out_row(self.folder, self._browse))
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(460, self.sizeHint().height())

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Carpeta de salida", self.folder.text())
        if d:
            self.folder.setText(d)

    def _ok(self) -> None:
        if not self.folder.text().strip():
            QMessageBox.information(self, "Carpeta", "Elige una carpeta.")
            return
        try:
            self.page_list()
        except ValueError as ex:
            QMessageBox.warning(self, "Páginas", str(ex))
            return
        self.accept()

    def page_list(self) -> list[int]:
        t = self.pages.text().strip()
        if not t:
            return list(range(self.n))
        return ops.flatten(ops.parse_ranges(t, self.n))

    def values(self) -> dict:
        return dict(pages=self.page_list(), out_dir=self.folder.text().strip(), dpi=self.dpi.value(),
                    fmt=self.fmt.currentText(), base=self.base.text().strip() or "pagina")


# ---------------------------------------------------------------------------
class OcrDialog(QDialog):
    def __init__(self, parent, base: str):
        super().__init__(parent)
        self.setWindowTitle("OCR: hacer el texto buscable")
        langs = ops.ocr_languages()
        lay = QVBoxLayout(self)
        self.ok_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.langs = QListWidget()
        self.langs.setMaximumHeight(130)
        if not langs:
            warn = QLabel("No se encuentra Tesseract (carpeta «tessdata»).\nInstálalo y vuelve a probar; "
                          "mira el README, sección OCR.")
            warn.setStyleSheet("color:#c0392b")
            lay.addWidget(warn)
            self.ok_btn.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        else:
            lay.addWidget(QLabel("Idiomas del documento (puedes marcar varios):"))
            default = [l for l in ("spa", "eng") if l in langs] or langs[:1]
            for l in langs:
                it = QListWidgetItem(l)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if l in default else Qt.CheckState.Unchecked)
                self.langs.addItem(it)
            lay.addWidget(self.langs)
            if "spa" not in langs:
                note = QLabel("Falta el idioma «spa» (español): instala el paquete de datos de Tesseract para español.")
                note.setStyleSheet("color:#b9770e")
                note.setWordWrap(True)
                lay.addWidget(note)
        self.dpi = QSpinBox()
        self.dpi.setRange(100, 600)
        self.dpi.setValue(300)
        self.skip = QCheckBox("Saltar páginas que ya tienen texto")
        self.skip.setChecked(True)
        self.out = QLineEdit(base + "_ocr.pdf")
        form = QFormLayout()
        form.addRow("Resolución del OCR (ppp):", self.dpi)
        form.addRow("Guardar como:", _out_row(self.out, self._browse))
        lay.addLayout(form)
        lay.addWidget(self.skip)
        self.ok_btn.accepted.connect(self._ok)
        self.ok_btn.rejected.connect(self.reject)
        lay.addWidget(self.ok_btn)
        self.resize(480, self.sizeHint().height())

    def _browse(self) -> None:
        p = pick_save(self, "Guardar PDF con OCR", self.out.text())
        if p:
            self.out.setText(p)

    def _ok(self) -> None:
        if not any(self.langs.item(i).checkState() == Qt.CheckState.Checked for i in range(self.langs.count())):
            QMessageBox.information(self, "Idioma", "Marca al menos un idioma.")
            return
        if not os.path.dirname(self.out.text()):
            self._browse()
            if not os.path.dirname(self.out.text()):
                return
        self.accept()

    def values(self) -> dict:
        langs = "+".join(self.langs.item(i).text() for i in range(self.langs.count())
                         if self.langs.item(i).checkState() == Qt.CheckState.Checked)
        return dict(lang=langs, dpi=self.dpi.value(), skip=self.skip.isChecked(),
                    out_path=self.out.text().strip())


# ---------------------------------------------------------------------------
class _SearchWorker(QThread):
    found = Signal(str, int, str)
    status = Signal(str)
    finished_count = Signal(int)

    def __init__(self, folder: str, term: str, recursive: bool):
        super().__init__()
        self.folder, self.term, self.recursive = folder, term, recursive
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            n = ops.search_folder(self.folder, self.term, self.recursive,
                                  on_result=lambda p, i, s: self.found.emit(p, i, s),
                                  progress=lambda p: self.status.emit(os.path.basename(p)),
                                  cancel=lambda: self._stop)
        except ops.Cancelled:
            n = -1
        self.finished_count.emit(n)


class FolderSearchDialog(QDialog):
    openRequested = Signal(str, int)

    def __init__(self, parent=None, folder: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Buscar en una carpeta de PDFs")
        self.resize(760, 520)
        self.folder = QLineEdit(folder)
        self.term = QLineEdit()
        self.term.returnPressed.connect(self._start)
        self.rec = QCheckBox("Incluir subcarpetas")
        self.rec.setChecked(True)
        self.status = QLabel("")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Archivo", "Pág.", "Coincidencias"])
        self.tree.setColumnWidth(0, 230)
        self.tree.setColumnWidth(1, 50)
        self.tree.itemDoubleClicked.connect(self._open)
        self.b_go = QPushButton("Buscar")
        self.b_go.clicked.connect(self._start)
        self.b_stop = QPushButton("Detener")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._stop)
        form = QFormLayout()
        form.addRow("Carpeta:", _out_row(self.folder, self._browse))
        form.addRow("Texto:", self.term)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(self.rec)
        row.addStretch(1)
        row.addWidget(self.b_go)
        row.addWidget(self.b_stop)
        lay.addLayout(row)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.status)
        lay.addWidget(QLabel("Doble clic en un resultado para abrirlo en esa página. "
                             "Los PDFs escaneados sin OCR no tienen texto que buscar."))
        self.worker: Optional[_SearchWorker] = None

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Carpeta", self.folder.text())
        if d:
            self.folder.setText(d)

    def _start(self) -> None:
        folder, term = self.folder.text().strip(), self.term.text().strip()
        if not os.path.isdir(folder) or not term:
            QMessageBox.information(self, "Búsqueda", "Indica una carpeta válida y el texto a buscar.")
            return
        self.tree.clear()
        self.b_go.setEnabled(False)
        self.b_stop.setEnabled(True)
        self.worker = _SearchWorker(folder, term, self.rec.isChecked())
        self.worker.found.connect(self._found)
        self.worker.status.connect(lambda n: self.status.setText(f"Buscando en {n}…"))
        self.worker.finished_count.connect(self._done)
        self.worker.start()

    def _stop(self) -> None:
        if self.worker:
            self.worker.stop()

    def _found(self, path: str, page: int, snippet: str) -> None:
        it = QTreeWidgetItem([os.path.relpath(path, self.folder.text().strip()), str(page + 1), snippet])
        it.setData(0, Qt.ItemDataRole.UserRole, (path, page))
        self.tree.addTopLevelItem(it)

    def _done(self, n: int) -> None:
        self.b_go.setEnabled(True)
        self.b_stop.setEnabled(False)
        self.status.setText("Búsqueda detenida." if n < 0 else
                            f"{n} coincidencia(s) en {self.tree.topLevelItemCount()} página(s).")

    def _open(self, item, _col) -> None:
        path, page = item.data(0, Qt.ItemDataRole.UserRole)
        self.openRequested.emit(path, page)

    def closeEvent(self, e) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
        super().closeEvent(e)

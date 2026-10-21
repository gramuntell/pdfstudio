"""Ventana principal de PDF Studio."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QRectF, QSettings, QStandardPaths, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QImage, QKeySequence, QPageLayout, QPainter
from PySide6.QtWidgets import (QApplication, QColorDialog, QDockWidget, QDoubleSpinBox, QFileDialog,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QSpinBox, QStackedWidget, QTabWidget, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from . import dialogs, ops, paths
from .version import __version__
from .sidebar import AnnotationPanel, BookmarkPanel, ThumbnailPanel
from .viewer import DocView, MARKUP_KIND, Tool

APP_NAME = "PDF Studio"

TOOL_LABELS = [
    (Tool.SELECT, "Seleccionar", "Seleccionar y copiar texto; clic en una anotación para elegirla (Supr la borra)"),
    (Tool.HIGHLIGHT, "Resaltar", "Arrastra sobre el texto para resaltarlo"),
    (Tool.UNDERLINE, "Subrayar", "Arrastra sobre el texto para subrayarlo"),
    (Tool.STRIKE, "Tachar", "Arrastra sobre el texto para tacharlo"),
    (None, None, None),
    (Tool.RECT, "Rectángulo", "Dibuja un rectángulo"),
    (Tool.ELLIPSE, "Elipse", "Dibuja una elipse"),
    (Tool.LINE, "Línea", "Dibuja una línea"),
    (Tool.ARROW, "Flecha", "Dibuja una flecha"),
    (Tool.INK, "Lápiz", "Dibujo a mano alzada"),
    (Tool.NOTE, "Nota", "Haz clic para añadir una nota"),
    (Tool.TEXTBOX, "Añadir texto", "Arrastra un recuadro (o haz clic) y escribe el texto"),
    (None, None, None),
    (Tool.FORM, "Formulario", "Haz clic en un campo del formulario para rellenarlo"),
    (Tool.SIGN, "Firma", "Haz clic (o arrastra) donde quieras poner tu firma"),
    (Tool.IMAGE, "Insertar imagen", "Elige una imagen y colócala en la página"),
    (None, None, None),
    (Tool.EDIT_TEXT, "Editar texto", "Haz clic en una línea de texto para modificarla"),
    (Tool.EDIT_IMAGE, "Editar imagen", "Haz clic en una imagen para reemplazarla, borrarla o guardarla"),
    (Tool.REDACT, "Redactar", "Marca zonas para eliminar su contenido de forma permanente"),
]

DEFAULT_COLORS = {
    Tool.HIGHLIGHT: QColor(255, 235, 59),
    Tool.UNDERLINE: QColor(30, 100, 220),
    Tool.STRIKE: QColor(220, 30, 30),
    Tool.INK: QColor(20, 60, 200),
    Tool.TEXTBOX: QColor(15, 15, 15),
}


class MainWindow(QMainWindow):
    def __init__(self, files: Optional[list[str]] = None):
        super().__init__()
        self.settings = QSettings("PdfStudio", "PdfStudio")
        self.tool = Tool.SELECT
        self.tool_colors: dict[Tool, QColor] = dict(DEFAULT_COLORS)
        self.pen_width = 2.0
        self.font_size = 12.0
        self.image_path: Optional[str] = None
        self._search_dialog: Optional[dialogs.FolderSearchDialog] = None
        self._sidebar_stale = False

        self.setAcceptDrops(True)
        self.resize(1320, 860)
        self.setWindowTitle(APP_NAME)

        self._build_central()
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_sidebar()
        self._build_status()
        self._update_state()

        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        for f in files or []:
            self.open_path(f)

    # ------------------------------------------------------------------ #
    # Construcción de la interfaz
    # ------------------------------------------------------------------ #
    def _build_central(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        welcome = QWidget()
        lay = QVBoxLayout(welcome)
        lay.addStretch(1)
        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:28px;font-weight:600;")
        sub = QLabel("Arrastra aquí un PDF o ábrelo con Ctrl+O")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn = QPushButton("Abrir PDF…")
        btn.setFixedWidth(180)
        btn.clicked.connect(self.open_dialog)
        self.recent_box = QVBoxLayout()
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(10)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(10)
        lay.addLayout(self.recent_box)
        lay.addStretch(2)

        self.stack = QStackedWidget()
        self.stack.addWidget(welcome)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)
        self._fill_welcome_recents()

    def _fill_welcome_recents(self) -> None:
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        recents = [p for p in self._recents() if os.path.exists(p)][:6]
        if recents:
            lab = QLabel("Recientes")
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.recent_box.addWidget(lab)
        for p in recents:
            b = QPushButton(os.path.basename(p))
            b.setToolTip(p)
            b.setFlat(True)
            b.setFixedWidth(360)
            b.clicked.connect(lambda _=False, path=p: self.open_path(path))
            self.recent_box.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)

    def _act(self, text: str, slot, shortcut=None, tip: Optional[str] = None, checkable: bool = False) -> QAction:
        a = QAction(text, self)
        a.setCheckable(checkable)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(lambda _checked=False: slot())
        self.addAction(a)
        return a

    def _build_actions(self) -> None:
        A = self._act
        # archivo
        self.a_open = A("Abrir…", self.open_dialog, "Ctrl+O")
        self.a_save = A("Guardar", self.save, "Ctrl+S")
        self.a_saveas = A("Guardar como…", self.save_as, "Ctrl+Shift+S")
        self.a_print = A("Imprimir…", self.print_document, "Ctrl+P")
        self.a_close = A("Cerrar pestaña", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self.a_quit = A("Salir", self.close, "Ctrl+Q")
        # edición
        self.a_undo = A("Deshacer", self._undo, "Ctrl+Z")
        self.a_redo = A("Rehacer", self._redo, ["Ctrl+Y", "Ctrl+Shift+Z"])
        self.a_copy = A("Copiar texto", self._copy, "Ctrl+C")
        self.a_find = A("Buscar…", self.show_find, "Ctrl+F")
        self.a_find_next = A("Siguiente coincidencia", lambda: self._find_step(1), "F3")
        self.a_find_prev = A("Coincidencia anterior", lambda: self._find_step(-1), "Shift+F3")
        self.a_goto = A("Ir a la página…", self.goto_page, "Ctrl+G")
        # ver
        self.a_zoom_in = A("Acercar", lambda: self._with_view(lambda v: v.zoom_in()), ["Ctrl++", "Ctrl+="])
        self.a_zoom_out = A("Alejar", lambda: self._with_view(lambda v: v.zoom_out()), "Ctrl+-")
        self.a_zoom_100 = A("Tamaño real (100 %)", lambda: self._with_view(lambda v: v.set_zoom(1.0)), "Ctrl+0")
        self.a_fit_w = A("Ajustar al ancho", lambda: self._with_view(lambda v: v.fit_width()), "Ctrl+1")
        self.a_fit_p = A("Ajustar a la página", lambda: self._with_view(lambda v: v.fit_page()), "Ctrl+2")
        self.a_sidebar = A("Panel lateral", self._toggle_sidebar, "F4")
        # páginas
        self.a_rot_l = A("Girar a la izquierda", lambda: self.rotate_pages(-90), "Ctrl+Shift+L")
        self.a_rot_r = A("Girar a la derecha", lambda: self.rotate_pages(90), "Ctrl+Shift+R")
        self.a_pg_del = A("Eliminar páginas", self.delete_pages)
        self.a_pg_extract = A("Extraer páginas a un PDF nuevo…", self.extract_pages)
        self.a_pg_dup = A("Duplicar páginas", self.duplicate_pages)
        self.a_pg_blank = A("Insertar página en blanco", self.insert_blank)
        self.a_pg_insert = A("Insertar páginas de otro PDF…", self.insert_pdf)
        self.a_pg_up = A("Mover arriba", lambda: self.move_pages(-1))
        self.a_pg_down = A("Mover abajo", lambda: self.move_pages(1))
        # documento
        self.a_meta = A("Propiedades…", self.edit_metadata)
        self.a_merge = A("Unir PDFs…", self.merge_pdfs)
        self.a_split = A("Dividir…", self.split_pdf)
        self.a_compress = A("Comprimir…", self.compress)
        self.a_ocr = A("OCR (texto buscable en escaneos)…", self.ocr)
        self.a_protect = A("Proteger con contraseña…", self.protect)
        self.a_unprotect = A("Quitar contraseña (al guardar)", self.unprotect)
        self.a_redact_find = A("Marcar para redactar por texto…", self.redact_by_text)
        self.a_redact_apply = A("Aplicar redacciones…", self.apply_redactions)
        self.a_bake = A("Aplanar anotaciones y campos…", self.bake)
        self.a_export = A("Exportar páginas como imágenes…", self.export_images)
        self.a_img2pdf = A("Crear PDF a partir de imágenes…", self.images_to_pdf)
        self.a_folder_search = A("Buscar texto en una carpeta…", self.search_folder)
        self.a_signature = A("Mi firma…", self.edit_signature)
        self.a_about = A("Acerca de / atajos", self.about)

        # herramientas (grupo exclusivo)
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: dict[Tool, QAction] = {}
        for tool, label, tip in TOOL_LABELS:
            if tool is None:
                continue
            a = QAction(label, self, checkable=True)
            a.setToolTip(tip)
            a.setStatusTip(tip)
            a.triggered.connect(lambda _c=False, t=tool: self.set_tool(t))
            self.tool_group.addAction(a)
            self.tool_actions[tool] = a
        self.tool_actions[Tool.SELECT].setChecked(True)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&Archivo")
        m.addAction(self.a_open)
        self.recent_menu = m.addMenu("Abrir reciente")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addSeparator()
        m.addActions([self.a_save, self.a_saveas, self.a_print, self.a_close])
        m.addSeparator()
        m.addAction(self.a_quit)

        m = mb.addMenu("&Editar")
        m.addActions([self.a_undo, self.a_redo])
        m.addSeparator()
        m.addActions([self.a_copy, self.a_find, self.a_find_next, self.a_find_prev, self.a_goto])

        m = mb.addMenu("&Ver")
        m.addActions([self.a_zoom_in, self.a_zoom_out, self.a_zoom_100, self.a_fit_w, self.a_fit_p])
        m.addSeparator()
        m.addAction(self.a_sidebar)

        m = mb.addMenu("&Herramientas")
        for tool, label, _tip in TOOL_LABELS:
            if tool is None:
                m.addSeparator()
            else:
                m.addAction(self.tool_actions[tool])
        m.addSeparator()
        m.addAction(self.a_signature)

        m = mb.addMenu("&Páginas")
        m.addActions([self.a_rot_l, self.a_rot_r])
        m.addSeparator()
        m.addActions([self.a_pg_up, self.a_pg_down])
        m.addSeparator()
        m.addActions([self.a_pg_dup, self.a_pg_blank, self.a_pg_insert])
        m.addSeparator()
        m.addActions([self.a_pg_extract, self.a_pg_del])

        m = mb.addMenu("&Documento")
        m.addAction(self.a_meta)
        m.addSeparator()
        m.addActions([self.a_merge, self.a_split, self.a_img2pdf])
        m.addSeparator()
        m.addActions([self.a_ocr, self.a_compress])
        m.addSeparator()
        m.addActions([self.a_protect, self.a_unprotect])
        m.addSeparator()
        m.addActions([self.a_redact_find, self.a_redact_apply, self.a_bake])
        m.addSeparator()
        m.addActions([self.a_export, self.a_folder_search])

        m = mb.addMenu("A&yuda")
        m.addAction(self.a_about)

    def _build_toolbars(self) -> None:
        tb = QToolBar("Principal")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        tb.addActions([self.a_open, self.a_save, self.a_print])
        tb.addSeparator()
        tb.addActions([self.a_undo, self.a_redo])
        tb.addSeparator()
        tb.addActions([self.a_zoom_out, self.a_zoom_in, self.a_fit_w, self.a_fit_p])
        tb.addSeparator()
        tb.addAction(self.a_find)
        tb.addSeparator()
        tb.addWidget(QLabel(" Color: "))
        self.color_btn = QToolButton()
        self.color_btn.setFixedSize(30, 22)
        self.color_btn.setToolTip("Color de la herramienta activa")
        self.color_btn.clicked.connect(self.pick_color)
        tb.addWidget(self.color_btn)
        tb.addWidget(QLabel("  Grosor: "))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 30)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setValue(self.pen_width)
        self.width_spin.valueChanged.connect(self._params_changed)
        tb.addWidget(self.width_spin)
        tb.addWidget(QLabel("  Tamaño de texto: "))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(5, 96)
        self.font_spin.setValue(int(self.font_size))
        self.font_spin.valueChanged.connect(self._params_changed)
        tb.addWidget(self.font_spin)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        # barra de búsqueda (oculta hasta Ctrl+F)
        self.find_bar = QToolBar("Buscar")
        self.find_bar.setMovable(False)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Buscar en el documento…")
        self.find_edit.setMinimumWidth(260)
        self.find_edit.returnPressed.connect(self._find_enter)
        self.find_info = QLabel("")
        b_prev, b_next, b_close = QPushButton("▲"), QPushButton("▼"), QPushButton("✕")
        for b in (b_prev, b_next, b_close):
            b.setFixedWidth(34)
        b_prev.clicked.connect(lambda: self._find_step(-1))
        b_next.clicked.connect(lambda: self._find_step(1))
        b_close.clicked.connect(self.hide_find)
        self.find_bar.addWidget(QLabel(" Buscar: "))
        self.find_bar.addWidget(self.find_edit)
        self.find_bar.addWidget(b_prev)
        self.find_bar.addWidget(b_next)
        self.find_bar.addWidget(self.find_info)
        self.find_bar.addWidget(b_close)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.find_bar)
        self.find_bar.hide()
        self._last_term = ""

        # herramientas a la izquierda
        lt = QToolBar("Herramientas")
        lt.setObjectName("tools_toolbar")
        lt.setMovable(False)
        lt.setOrientation(Qt.Orientation.Vertical)
        lt.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for tool, _label, _tip in TOOL_LABELS:
            if tool is None:
                lt.addSeparator()
            else:
                lt.addAction(self.tool_actions[tool])
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, lt)

    def _build_sidebar(self) -> None:
        self.thumbs = ThumbnailPanel()
        self.bookmarks = BookmarkPanel()
        self.annots = AnnotationPanel()
        self.thumbs.set_context_actions([
            self.a_rot_l, self.a_rot_r, None, self.a_pg_up, self.a_pg_down, None, self.a_pg_dup,
            self.a_pg_blank, self.a_pg_insert, None, self.a_pg_extract, self.a_pg_del])
        self.thumbs.deleteRequested.connect(self.delete_pages)
        self.side_tabs = QTabWidget()
        self.side_tabs.addTab(self.thumbs, "Páginas")
        self.side_tabs.addTab(self.bookmarks, "Marcadores")
        self.side_tabs.addTab(self.annots, "Anotaciones")
        self.side_tabs.currentChanged.connect(self._side_tab_changed)
        self.dock = QDockWidget("Panel", self)
        self.dock.setObjectName("sidebar")
        self.dock.setWidget(self.side_tabs)
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.dock.setTitleBarWidget(QWidget())
        self.dock.setMinimumWidth(190)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock)

    def _build_status(self) -> None:
        self.page_label = QLabel("")
        self.zoom_label = QLabel("")
        self.statusBar().addPermanentWidget(self.page_label)
        self.statusBar().addPermanentWidget(self.zoom_label)

    # ------------------------------------------------------------------ #
    # Acceso a la vista actual
    # ------------------------------------------------------------------ #
    def view(self) -> Optional[DocView]:
        w = self.tabs.currentWidget()
        return w if isinstance(w, DocView) else None

    def _with_view(self, fn) -> None:
        v = self.view()
        if v:
            fn(v)

    def _need_view(self) -> Optional[DocView]:
        v = self.view()
        if v is None:
            self.statusBar().showMessage("Abre un PDF primero.", 4000)
        return v

    # ------------------------------------------------------------------ #
    # Abrir / guardar / cerrar
    # ------------------------------------------------------------------ #
    def _last_dir(self) -> str:
        return self.settings.value("last_dir", "") or ""

    def _remember_dir(self, path: str) -> None:
        self.settings.setValue("last_dir", os.path.dirname(path))

    def _recents(self) -> list[str]:
        r = self.settings.value("recents", [])
        return list(r) if isinstance(r, (list, tuple)) else ([r] if r else [])

    def _add_recent(self, path: str) -> None:
        r = [p for p in self._recents() if p != path]
        r.insert(0, path)
        self.settings.setValue("recents", r[:10])
        self._fill_welcome_recents()

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        rec = [p for p in self._recents() if os.path.exists(p)]
        if not rec:
            self.recent_menu.addAction("(vacío)").setEnabled(False)
        for p in rec:
            self.recent_menu.addAction(os.path.basename(p), lambda path=p: self.open_path(path))

    def open_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Abrir", self._last_dir(),
            "PDF e imágenes (*.pdf *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;PDF (*.pdf)")
        for f in files:
            self.open_path(f)

    def open_path(self, path: str, page: Optional[int] = None) -> None:
        path = os.path.abspath(path)
        for i in range(self.tabs.count()):
            v = self.tabs.widget(i)
            if getattr(v, "path", None) == path:
                self.tabs.setCurrentIndex(i)
                if page is not None:
                    v.go_to_page(page)
                return
        try:
            r = dialogs.open_with_password(self, path)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "No se pudo abrir", f"{os.path.basename(path)}\n\n{ex}")
            return
        if r is None:
            return
        doc, pw = r
        is_img = os.path.splitext(path)[1].lower() in ops.IMAGE_EXTS
        view = DocView(doc, None if is_img else path, pw)
        if is_img:
            view.dirty = True  # una imagen convertida debe guardarse como PDF
        self._attach(view, os.path.basename(path))
        if not is_img:
            self._remember_dir(path)
            self._add_recent(path)
        if page is not None:
            view.go_to_page(page)

    def _attach(self, view: DocView, title: str) -> None:
        view.signature_getter = self._signature_png
        view.image_path = self.image_path
        view.modified.connect(lambda v=view: self._on_modified(v))
        view.structureChanged.connect(lambda v=view: self._on_structure(v))
        view.pagesChanged.connect(lambda pages, v=view: self._on_pages(v, pages))
        view.pageChanged.connect(lambda i, v=view: self._on_page_changed(v, i))
        view.zoomChanged.connect(lambda z, v=view: self._on_zoom(v, z))
        view.selectionChanged.connect(lambda _s, v=view: self._update_state())
        view.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        idx = self.tabs.addTab(view, title)
        self.tabs.setCurrentIndex(idx)
        view.set_tool(self.tool)
        self._apply_params(view)
        view.fit_width()
        self._set_tab_title(view)

    def _tab_index(self, view: DocView) -> int:
        return self.tabs.indexOf(view)

    def _set_tab_title(self, view: DocView) -> None:
        i = self._tab_index(view)
        if i < 0:
            return
        name = os.path.basename(view.path) if view.path else "Sin guardar"
        self.tabs.setTabText(i, name + (" •" if view.dirty else ""))
        self.tabs.setTabToolTip(i, view.path or "")
        if view is self.view():
            self.setWindowTitle(f"{name}{'*' if view.dirty else ''} — {APP_NAME}")

    def save(self) -> bool:
        v = self._need_view()
        if not v:
            return False
        if not v.path:
            return self.save_as()
        try:
            ops.save_doc(v.doc, v.path, v.password)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "No se pudo guardar", str(ex))
            return False
        v.dirty = False
        self._set_tab_title(v)
        self._update_state()
        self.statusBar().showMessage(f"Guardado: {v.path}", 5000)
        return True

    def save_as(self) -> bool:
        v = self._need_view()
        if not v:
            return False
        default = v.path or os.path.join(self._last_dir(), self.tabs.tabText(self.tabs.currentIndex()).rstrip(" •"))
        if not default.lower().endswith(".pdf"):
            default = os.path.splitext(default)[0] + ".pdf"
        path = dialogs.pick_save(self, "Guardar como", default)
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            ops.save_doc(v.doc, path, v.password)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "No se pudo guardar", str(ex))
            return False
        v.path = os.path.abspath(path)
        v.dirty = False
        self._remember_dir(path)
        self._add_recent(v.path)
        self._set_tab_title(v)
        self._update_state()
        self.statusBar().showMessage(f"Guardado: {v.path}", 5000)
        return True

    def print_document(self) -> None:
        v = self._need_view()
        if not v:
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        dlg.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, True)
        dlg.setMinMax(1, v.page_count)
        dlg.setWindowTitle("Imprimir")
        if dlg.exec() != QPrintDialog.DialogCode.Accepted:
            return
        first, last = 1, v.page_count
        if printer.printRange() == QPrinter.PrintRange.PageRange:
            first, last = printer.fromPage(), printer.toPage()
        elif printer.printRange() == QPrinter.PrintRange.CurrentPage:
            first = last = max(0, v.current_page()) + 1
        self.print_to(printer, v, first, last)

    def print_to(self, printer, v: DocView, first: int, last: int) -> int:
        """Dibuja las páginas first..last (1-based) en `printer`. Devuelve cuántas imprimió."""
        pages = list(range(max(1, first) - 1, min(last, v.page_count)))
        if not pages:
            return 0

        def orient(i: int) -> None:
            r = v.doc[i].rect
            printer.setPageOrientation(QPageLayout.Orientation.Landscape if r.width > r.height
                                       else QPageLayout.Orientation.Portrait)

        orient(pages[0])
        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.warning(self, "Imprimir", "No se pudo iniciar la impresión.")
            return 0
        done = 0
        try:
            for n, i in enumerate(pages):
                if n:
                    orient(i)
                    printer.newPage()
                page = v.doc[i]
                dpi = max(72, min(printer.resolution(), 300))
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                area = printer.pageRect(printer.Unit.DevicePixel)
                sc = min(area.width() / img.width(), area.height() / img.height())
                w, h = img.width() * sc, img.height() * sc
                painter.drawImage(QRectF(area.x() + (area.width() - w) / 2, area.y() + (area.height() - h) / 2, w, h), img)
                done += 1
                self.statusBar().showMessage(f"Imprimiendo página {n + 1} de {len(pages)}…")
                QApplication.processEvents()
        finally:
            painter.end()
        self.statusBar().showMessage(f"{done} página(s) enviadas a la impresora.", 6000)
        return done

    def _confirm_close(self, v: DocView) -> bool:
        if not v.dirty:
            return True
        name = os.path.basename(v.path) if v.path else "este documento"
        self.tabs.setCurrentIndex(self._tab_index(v))
        r = QMessageBox.question(
            self, "Cambios sin guardar", f"«{name}» tiene cambios sin guardar.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return True

    def close_tab(self, index: int) -> None:
        v = self.tabs.widget(index)
        if not isinstance(v, DocView) or not self._confirm_close(v):
            return
        self.tabs.removeTab(index)
        try:
            v.doc.close()
        except Exception:
            pass
        v.deleteLater()
        self._on_tab_changed(self.tabs.currentIndex())

    def closeEvent(self, e) -> None:
        for i in range(self.tabs.count()):
            v = self.tabs.widget(i)
            if isinstance(v, DocView) and not self._confirm_close(v):
                e.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        e.accept()

    # arrastrar y soltar
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.splitext(p)[1].lower() == ".pdf" or os.path.splitext(p)[1].lower() in ops.IMAGE_EXTS:
                self.open_path(p)

    # ------------------------------------------------------------------ #
    # Reacciones a cambios
    # ------------------------------------------------------------------ #
    def _on_tab_changed(self, _idx: int) -> None:
        v = self.view()
        self.stack.setCurrentIndex(1 if v else 0)
        self.thumbs.set_view(v)
        self.bookmarks.set_view(v)
        self._sidebar_stale = True
        self._side_tab_changed(self.side_tabs.currentIndex())
        if v:
            v.set_tool(self.tool)
            self._apply_params(v)
            self._on_zoom(v, v.zoom)
            self._on_page_changed(v, v.current_page())
            self._set_tab_title(v)
        else:
            self.setWindowTitle(APP_NAME)
            self.page_label.setText("")
            self.zoom_label.setText("")
        self._update_state()

    def _side_tab_changed(self, i: int) -> None:
        if self.side_tabs.widget(i) is self.annots and self._sidebar_stale:
            self.annots.set_view(self.view())
            self._sidebar_stale = False

    def _on_modified(self, v: DocView) -> None:
        self._set_tab_title(v)
        if v is self.view():
            self._update_state()
            self._sidebar_stale = True
            if self.side_tabs.currentWidget() is self.annots:
                self._side_tab_changed(self.side_tabs.currentIndex())

    def _on_structure(self, v: DocView) -> None:
        if v is self.view():
            self.thumbs.rebuild()
            self.bookmarks.rebuild()
            self._on_page_changed(v, v.current_page())

    def _on_pages(self, v: DocView, pages: list) -> None:
        if v is self.view():
            for i in pages:
                self.thumbs.refresh_page(i)

    def _on_page_changed(self, v: DocView, i: int) -> None:
        if v is self.view():
            self.page_label.setText(f"Pág. {i + 1} / {v.page_count}  " if i >= 0 else "")
            self.thumbs.sync_current(i)

    def _on_zoom(self, v: DocView, z: float) -> None:
        if v is self.view():
            self.zoom_label.setText(f"{z * 100:.0f} %  ")

    def _update_state(self) -> None:
        v = self.view()
        has = v is not None
        for a in (self.a_save, self.a_saveas, self.a_print, self.a_find, self.a_goto, self.a_zoom_in, self.a_zoom_out,
                  self.a_zoom_100, self.a_fit_w, self.a_fit_p, self.a_rot_l, self.a_rot_r, self.a_pg_del,
                  self.a_pg_extract, self.a_pg_dup, self.a_pg_blank, self.a_pg_insert, self.a_pg_up,
                  self.a_pg_down, self.a_meta, self.a_split, self.a_compress, self.a_ocr, self.a_protect,
                  self.a_redact_find, self.a_redact_apply, self.a_bake, self.a_export, self.a_close):
            a.setEnabled(has)
        for a in self.tool_actions.values():
            a.setEnabled(has)
        self.a_undo.setEnabled(bool(v and v.can_undo()))
        self.a_redo.setEnabled(bool(v and v.can_redo()))
        self.a_copy.setEnabled(bool(v and v.has_selection()) or isinstance(QApplication.focusWidget(), QLineEdit))
        self.a_unprotect.setEnabled(bool(v and v.password))

    # ------------------------------------------------------------------ #
    # Herramientas y parámetros
    # ------------------------------------------------------------------ #
    def color_for(self, tool: Tool) -> QColor:
        return self.tool_colors.get(tool, QColor(220, 30, 30))

    def set_tool(self, tool: Tool) -> None:
        v = self.view()
        if tool == Tool.IMAGE:
            path, _ = QFileDialog.getOpenFileName(self, "Imagen a insertar", self._last_dir(), dialogs.IMG_FILTER)
            if not path:
                self.tool_actions[self.tool].setChecked(True)
                return
            self.image_path = path
            for i in range(self.tabs.count()):
                self.tabs.widget(i).image_path = path
        if tool == Tool.SIGN and self._signature_png() is None:
            self.tool_actions[self.tool].setChecked(True)
            return
        self.tool = tool
        self.tool_actions[tool].setChecked(True)
        if v:
            v.set_tool(tool)
            self._apply_params(v)
            if tool in MARKUP_KIND and v.has_selection():
                v.apply_markup_to_selection(MARKUP_KIND[tool])
        hints = {
            Tool.HIGHLIGHT: "Arrastra sobre el texto para resaltarlo.",
            Tool.SIGN: "Haz clic donde quieras la firma (o arrastra para elegir el tamaño).",
            Tool.IMAGE: "Haz clic (o arrastra un recuadro) donde quieras la imagen.",
            Tool.EDIT_TEXT: "Haz clic en una línea de texto. Solo se sustituye la línea completa.",
            Tool.FORM: "Los campos están marcados en azul. Haz clic en uno para rellenarlo.",
        }
        if tool in hints:
            self.statusBar().showMessage(hints[tool], 8000)
        self._refresh_color_button()

    def _refresh_color_button(self) -> None:
        c = self.color_for(self.tool)
        self.color_btn.setStyleSheet(f"background:{c.name()};border:1px solid #666;")

    def pick_color(self) -> None:
        c = QColorDialog.getColor(self.color_for(self.tool), self, "Color")
        if c.isValid():
            self.tool_colors[self.tool] = c
            self._refresh_color_button()
            v = self.view()
            if v:
                v.color = c

    def _params_changed(self) -> None:
        self.pen_width = self.width_spin.value()
        self.font_size = float(self.font_spin.value())
        v = self.view()
        if v:
            self._apply_params(v)

    def _apply_params(self, v: DocView) -> None:
        v.color = self.color_for(self.tool)
        v.pen_width = self.pen_width
        v.font_size = self.font_size
        self._refresh_color_button()

    # ------------------------------------------------------------------ #
    # Firma
    # ------------------------------------------------------------------ #
    def _sig_path(self) -> str:
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "firma.png")

    def _signature_png(self) -> Optional[bytes]:
        p = self._sig_path()
        if os.path.exists(p):
            with open(p, "rb") as f:
                return f.read()
        return self.edit_signature()

    def edit_signature(self) -> Optional[bytes]:
        dlg = dialogs.SignatureDialog(self)
        if dlg.exec() == dialogs.SignatureDialog.DialogCode.Accepted and dlg.png:
            with open(self._sig_path(), "wb") as f:
                f.write(dlg.png)
            self.statusBar().showMessage("Firma guardada.", 4000)
            return dlg.png
        return None

    # ------------------------------------------------------------------ #
    # Edición, búsqueda, navegación
    # ------------------------------------------------------------------ #
    def _undo(self) -> None:
        self._with_view(lambda v: v.undo())

    def _redo(self) -> None:
        self._with_view(lambda v: v.redo())

    def _copy(self) -> None:
        fw = QApplication.focusWidget()
        if isinstance(fw, QLineEdit):
            fw.copy()
        else:
            self._with_view(lambda v: v.copy_selection())

    def show_find(self) -> None:
        if not self._need_view():
            return
        self.find_bar.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def hide_find(self) -> None:
        self.find_bar.hide()
        self._with_view(lambda v: v.clear_find())
        self._last_term = ""
        self.find_info.setText("")

    def _find_enter(self) -> None:
        v = self.view()
        if not v:
            return
        term = self.find_edit.text().strip()
        if term and term == self._last_term:
            v.find_step(1)
        else:
            self._last_term = term
            n = v.find(term)
            if term and n == 0:
                self.find_info.setText("  Sin resultados")
                return
        cur, tot = v.hit_info
        self.find_info.setText(f"  {cur} de {tot}" if tot else "")

    def _find_step(self, step: int) -> None:
        v = self.view()
        if not v:
            return
        if not v.hit_info[1]:
            self.show_find()
            return
        v.find_step(step)
        cur, tot = v.hit_info
        self.find_info.setText(f"  {cur} de {tot}")

    def goto_page(self) -> None:
        v = self._need_view()
        if not v:
            return
        n, ok = QInputDialog.getInt(self, "Ir a la página", f"Página (1-{v.page_count}):",
                                    max(0, v.current_page()) + 1, 1, v.page_count)
        if ok:
            v.go_to_page(n - 1)

    def _toggle_sidebar(self) -> None:
        self.dock.setVisible(not self.dock.isVisible())

    # ------------------------------------------------------------------ #
    # Páginas
    # ------------------------------------------------------------------ #
    def _target_pages(self, v: DocView) -> list[int]:
        sel = self.thumbs.selected_pages()
        return sel or [max(0, v.current_page())]

    def rotate_pages(self, delta: int) -> None:
        v = self._need_view()
        if not v:
            return
        pages = self._target_pages(v)
        ok, _ = v.run_edit(lambda: ops.rotate_pages(v.doc, pages, delta), structure=True)
        if ok:
            self.thumbs.select_pages(pages)

    def delete_pages(self) -> None:
        v = self._need_view()
        if not v:
            return
        pages = self._target_pages(v)
        if len(pages) >= v.page_count:
            QMessageBox.information(self, "Eliminar", "No se pueden eliminar todas las páginas.")
            return
        txt = ", ".join(str(p + 1) for p in pages[:10]) + ("…" if len(pages) > 10 else "")
        r = QMessageBox.question(self, "Eliminar páginas", f"¿Eliminar la(s) página(s) {txt}?\n"
                                 "Puedes deshacerlo con Ctrl+Z.")
        if r == QMessageBox.StandardButton.Yes:
            v.run_edit(lambda: ops.delete_pages(v.doc, pages), structure=True)

    def extract_pages(self) -> None:
        v = self._need_view()
        if not v:
            return
        pages = self._target_pages(v)
        base = os.path.splitext(os.path.basename(v.path or "documento"))[0]
        path = dialogs.pick_save(self, "Extraer páginas a…",
                                 os.path.join(os.path.dirname(v.path or self._last_dir() or "."),
                                              f"{base}_extracto.pdf"))
        if not path:
            return
        try:
            ops.extract_pages(v.doc, pages, path)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Extraer", str(ex))
            return
        self.statusBar().showMessage(f"{len(pages)} página(s) extraídas a {path}", 6000)

    def duplicate_pages(self) -> None:
        v = self._need_view()
        if not v:
            return
        pages = self._target_pages(v)

        def apply() -> None:
            for i in sorted(pages, reverse=True):
                ops.duplicate_page(v.doc, i)
        v.run_edit(apply, structure=True)

    def insert_blank(self) -> None:
        v = self._need_view()
        if not v:
            return
        after = max(self._target_pages(v))
        v.run_edit(lambda: ops.insert_blank_page(v.doc, after), structure=True)
        v.go_to_page(after + 1)

    def insert_pdf(self) -> None:
        v = self._need_view()
        if not v:
            return
        path, _ = QFileDialog.getOpenFileName(self, "PDF a insertar", self._last_dir(), dialogs.PDF_FILTER)
        if not path:
            return
        r = dialogs.open_with_password(self, path)
        if r is None:
            return
        r[0].close()
        pw = r[1]
        at = max(self._target_pages(v)) + 1
        ok, n = v.run_edit(lambda: ops.insert_pdf_at(v.doc, path, at, pw), structure=True)
        if ok:
            self.statusBar().showMessage(f"{n} página(s) insertadas tras la página {at}", 6000)
            v.go_to_page(at)

    def move_pages(self, d: int) -> None:
        v = self._need_view()
        if not v:
            return
        sel = set(self._target_pages(v))
        items = [(i, i in sel) for i in range(v.page_count)]
        n = len(items)
        if d < 0:
            for k in range(1, n):
                if items[k][1] and not items[k - 1][1]:
                    items[k - 1], items[k] = items[k], items[k - 1]
        else:
            for k in range(n - 2, -1, -1):
                if items[k][1] and not items[k + 1][1]:
                    items[k + 1], items[k] = items[k], items[k + 1]
        order = [p for p, _f in items]
        if order == list(range(n)):
            return
        new_pos = [k for k, (_p, f) in enumerate(items) if f]
        ok, _ = v.run_edit(lambda: ops.reorder_pages(v.doc, order), structure=True)
        if ok:
            self.thumbs.select_pages(new_pos)
            v.go_to_page(new_pos[0])

    # ------------------------------------------------------------------ #
    # Documento
    # ------------------------------------------------------------------ #
    def _base_name(self, v: DocView) -> str:
        return os.path.splitext(os.path.basename(v.path or "documento"))[0]

    def _out_in_same_dir(self, v: DocView, name: str) -> str:
        d = os.path.dirname(v.path) if v.path else (self._last_dir() or os.path.expanduser("~"))
        return os.path.join(d, name)

    def edit_metadata(self) -> None:
        v = self._need_view()
        if not v:
            return
        dlg = dialogs.MetadataDialog(self, ops.get_metadata(v.doc))
        if dlg.exec() == dialogs.MetadataDialog.DialogCode.Accepted:
            vals = dlg.values()
            v.run_edit(lambda: ops.set_metadata(v.doc, vals), pages=[])

    def merge_pdfs(self) -> None:
        v = self.view()
        initial = [v.path] if v and v.path and not v.dirty else []
        dlg = dialogs.MergeDialog(self, initial)
        if dlg.exec() != dialogs.MergeDialog.DialogCode.Accepted:
            return
        items, out, bm = dlg.items(), dlg.out_path(), dlg.bookmarks()
        ok, pages = dialogs.run_task(self, "Unir PDFs",
                                     lambda prog, cancel: ops.merge_pdfs(items, out, bm, prog),
                                     "Uniendo archivos…")
        if ok:
            self.statusBar().showMessage(f"Unidos {len(items)} archivos ({pages} páginas): {out}", 8000)
            self.open_path(out)

    def split_pdf(self) -> None:
        v = self._need_view()
        if not v:
            return
        dlg = dialogs.SplitDialog(self, v.page_count, self._base_name(v))
        if dlg.exec() != dialogs.SplitDialog.DialogCode.Accepted:
            return
        data, groups, out_dir, base = ops.snapshot(v.doc), dlg.groups(), dlg.out_dir(), dlg.base_name()

        def job(_prog, _cancel):
            d = ops.from_snapshot(data)
            try:
                return ops.split_by_groups(d, groups, out_dir, base)
            finally:
                d.close()

        ok, files = dialogs.run_task(self, "Dividir PDF", job, "Dividiendo…")
        if ok:
            QMessageBox.information(self, "Dividir", f"Se han creado {len(files)} archivo(s) en:\n{out_dir}")

    def compress(self) -> None:
        v = self._need_view()
        if not v:
            return
        data = ops.snapshot(v.doc)
        dlg = dialogs.CompressDialog(self, len(data), os.path.join(
            os.path.dirname(v.path) if v.path else (self._last_dir() or os.path.expanduser("~")),
            self._base_name(v)))
        if dlg.exec() != dialogs.CompressDialog.DialogCode.Accepted:
            return
        level, out = dlg.values()

        def job(_prog, _cancel):
            d = ops.from_snapshot(data)
            try:
                return ops.compress_pdf(d, out, level)
            finally:
                d.close()

        ok, res = dialogs.run_task(self, "Comprimir", job, "Comprimiendo…")
        if ok:
            before, after = res
            pct = (1 - after / before) * 100 if before else 0
            QMessageBox.information(self, "Comprimir",
                                    f"De {ops.human_size(before)} a {ops.human_size(after)} "
                                    f"({pct:.0f} % menos).\n\n{out}")

    def ocr(self) -> None:
        v = self._need_view()
        if not v:
            return
        dlg = dialogs.OcrDialog(self, os.path.join(
            os.path.dirname(v.path) if v.path else (self._last_dir() or os.path.expanduser("~")),
            self._base_name(v)))
        if dlg.exec() != dialogs.OcrDialog.DialogCode.Accepted:
            return
        val = dlg.values()
        data = ops.snapshot(v.doc)

        def job(prog, cancel):
            d = ops.from_snapshot(data)
            try:
                return ops.ocr_pdf(d, val["out_path"], val["lang"], val["dpi"], val["skip"], prog, cancel)
            finally:
                d.close()

        ok, n = dialogs.run_task(self, "OCR", job, "Reconociendo texto (puede tardar)…")
        if ok:
            self.statusBar().showMessage(f"OCR aplicado a {n} página(s): {val['out_path']}", 8000)
            self.open_path(val["out_path"])

    def protect(self) -> None:
        v = self._need_view()
        if not v:
            return
        dlg = dialogs.ProtectDialog(self, os.path.join(
            os.path.dirname(v.path) if v.path else (self._last_dir() or os.path.expanduser("~")),
            self._base_name(v)))
        if dlg.exec() != dialogs.ProtectDialog.DialogCode.Accepted:
            return
        val = dlg.values()
        try:
            ops.protect_pdf(v.doc, **val)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Proteger", str(ex))
            return
        QMessageBox.information(self, "Proteger", f"Copia protegida guardada en:\n{val['out_path']}")

    def unprotect(self) -> None:
        v = self._need_view()
        if not v or not v.password:
            return
        v.password = None
        v.dirty = True
        self._on_modified(v)
        self.statusBar().showMessage("La contraseña se quitará al guardar este documento.", 8000)

    def redact_by_text(self) -> None:
        v = self._need_view()
        if not v:
            return
        term, ok = QInputDialog.getText(self, "Marcar para redactar", "Texto a buscar (se marcarán todas las apariciones):")
        if not ok or not term.strip():
            return
        ok, n = v.run_edit(lambda: ops.mark_search_for_redaction(v.doc, term.strip()), pages=None)
        if ok:
            self.statusBar().showMessage(
                f"{n} coincidencia(s) marcadas. Revisa y usa Documento ▸ Aplicar redacciones." if n
                else "No se encontró ese texto.", 8000)

    def apply_redactions(self) -> None:
        v = self._need_view()
        if not v:
            return
        n = ops.count_redactions(v.doc)
        if not n:
            QMessageBox.information(self, "Redacciones", "No hay zonas marcadas para redactar.\n"
                                    "Usa la herramienta «Redactar» o Documento ▸ Marcar para redactar por texto.")
            return
        r = QMessageBox.warning(
            self, "Aplicar redacciones",
            f"Se eliminará de forma permanente el contenido bajo {n} zona(s) marcada(s) (texto, imagen y "
            "gráficos). Puedes deshacerlo con Ctrl+Z hasta que guardes.\n\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Yes:
            ok, done = v.run_edit(lambda: ops.apply_redactions(v.doc), pages=None)
            if ok:
                self.statusBar().showMessage(f"{done} redacción(es) aplicadas.", 6000)

    def bake(self) -> None:
        v = self._need_view()
        if not v:
            return
        r = QMessageBox.question(
            self, "Aplanar",
            "Las anotaciones y los campos de formulario pasarán a ser parte del contenido de la página "
            "y ya no se podrán editar ni borrar individualmente (sí con Deshacer).\n\n¿Continuar?")
        if r == QMessageBox.StandardButton.Yes:
            v.run_edit(lambda: ops.bake(v.doc), pages=None)

    def export_images(self) -> None:
        v = self._need_view()
        if not v:
            return
        dlg = dialogs.ExportImagesDialog(self, v.page_count, self._base_name(v), max(0, v.current_page()))
        if dlg.exec() != dialogs.ExportImagesDialog.DialogCode.Accepted:
            return
        val = dlg.values()
        data = ops.snapshot(v.doc)

        def job(prog, _cancel):
            d = ops.from_snapshot(data)
            try:
                return ops.export_images(d, val["pages"], val["out_dir"], val["dpi"], val["fmt"],
                                         val["base"], prog)
            finally:
                d.close()

        ok, files = dialogs.run_task(self, "Exportar", job, "Exportando páginas…")
        if ok:
            QMessageBox.information(self, "Exportar", f"{len(files)} imagen(es) guardadas en:\n{val['out_dir']}")

    def images_to_pdf(self) -> None:
        dlg = dialogs.MergeDialog(self, None, images=True)
        if dlg.exec() != dialogs.MergeDialog.DialogCode.Accepted:
            return
        paths, out = dlg.files.paths(), dlg.out_path()
        ok, n = dialogs.run_task(self, "Imágenes a PDF",
                                 lambda prog, cancel: ops.images_to_pdf(paths, out, prog), "Creando PDF…")
        if ok:
            self.statusBar().showMessage(f"PDF de {n} página(s) creado: {out}", 8000)
            self.open_path(out)

    def search_folder(self) -> None:
        v = self.view()
        folder = os.path.dirname(v.path) if v and v.path else self._last_dir()
        if self._search_dialog is None:
            self._search_dialog = dialogs.FolderSearchDialog(self, folder)
            self._search_dialog.setWindowModality(Qt.WindowModality.NonModal)
            self._search_dialog.openRequested.connect(lambda p, pg: self.open_path(p, pg))
        self._search_dialog.show()
        self._search_dialog.raise_()

    # ------------------------------------------------------------------ #
    def about(self) -> None:
        QMessageBox.information(
            self, "Acerca de PDF Studio",
            f"<b>PDF Studio</b> {__version__} — visor y editor de PDF.<br>"
            "Software libre (AGPL-3.0). Usa PyMuPDF, Qt/PySide6 y Tesseract.<br>"
            f"Registro de errores: {paths.log_path()}<br><br>"
            "<b>Atajos</b><br>"
            "Ctrl+O abrir · Ctrl+S guardar · Ctrl+Z / Ctrl+Y deshacer / rehacer<br>"
            "Ctrl+F buscar (F3 siguiente) · Ctrl+G ir a página · Ctrl+rueda zoom<br>"
            "Ctrl+Shift+L / R girar páginas · Supr borra la anotación elegida<br>"
            "F4 muestra u oculta el panel lateral<br><br>"
            "<b>Límites a tener en cuenta</b><br>"
            "· «Editar texto» sustituye líneas completas y usa fuentes estándar "
            "(Helvetica/Times/Courier); no es un procesador de texto.<br>"
            "· La firma es una imagen, no una firma digital con certificado.<br>"
            "· El OCR usa los idiomas incluidos (español e inglés).")

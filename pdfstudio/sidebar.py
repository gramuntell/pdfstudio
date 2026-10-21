"""Paneles laterales: miniaturas (con reordenación), marcadores y anotaciones."""
from __future__ import annotations

from typing import Optional

import pymupdf
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QInputDialog,
                               QListView, QListWidget, QListWidgetItem, QMenu, QPushButton, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import ops

THUMB_W = 120
THUMB_ROLE = Qt.ItemDataRole.UserRole + 1


class ThumbDelegate(QStyledItemDelegate):
    """Miniatura centrada con el número de página debajo."""

    def sizeHint(self, option, index):
        return index.data(Qt.ItemDataRole.SizeHintRole) or QSize(THUMB_W + 20, int(THUMB_W * 1.42) + 36)

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = type(opt.icon)()
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, option.widget)
        pm = index.data(THUMB_ROLE)
        r = option.rect
        painter.save()
        if pm is not None:
            dpr = pm.devicePixelRatio() or 1.0
            w, h = pm.width() / dpr, pm.height() / dpr
            target = QRectF(r.left() + (r.width() - w) / 2, r.top() + 6, w, h)
            painter.drawPixmap(target, pm, QRectF(pm.rect()))
            painter.setPen(QPen(QColor(120, 120, 120), 1))
            painter.drawRect(target)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(option.palette.highlightedText().color() if selected else option.palette.text().color())
        painter.drawText(QRectF(r.left(), r.bottom() - 24, r.width(), 20),
                         Qt.AlignmentFlag.AlignCenter, str(index.data(Qt.ItemDataRole.DisplayRole)))
        painter.restore()


class ThumbnailPanel(QListWidget):
    deleteRequested = Signal()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Delete:
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(e)

    def select_pages(self, pages) -> None:
        self.clearSelection()
        for i in pages:
            if 0 <= i < self.count():
                self.item(i).setSelected(True)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = None
        self._gen = 0
        self._syncing = False
        self._context_actions: list = []
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setMovement(QListView.Movement.Snap)
        self.setItemDelegate(ThumbDelegate(self))
        self.setSpacing(4)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.currentRowChanged.connect(self._row_changed)
        self.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._reordered))

    def set_context_actions(self, actions) -> None:
        self._context_actions = list(actions)

    def set_view(self, view) -> None:
        self.view = view
        self.rebuild()

    # -- contenido -------------------------------------------------------
    def rebuild(self) -> None:
        self._gen += 1
        gen = self._gen
        self._syncing = True
        self.clear()
        if self.view is None:
            self._syncing = False
            return
        n = self.view.page_count
        for i in range(n):
            pw, ph = self.view._sizes[i]
            h = int(THUMB_W * ph / pw) if pw else int(THUMB_W * 1.42)
            it = QListWidgetItem(str(i + 1))
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setSizeHint(QSize(THUMB_W + 20, h + 36))
            blank = QPixmap(THUMB_W, h)
            blank.fill(Qt.GlobalColor.white)
            it.setData(THUMB_ROLE, blank)
            self.addItem(it)
        self._syncing = False
        self.sync_current(self.view.current_page())
        QTimer.singleShot(0, lambda: self._fill(gen, 0))

    def _fill(self, gen: int, start: int) -> None:
        if gen != self._gen or self.view is None:
            return
        doc = self.view.doc
        end = min(start + 6, doc.page_count)
        for i in range(start, end):
            try:
                page = doc[i]
                s = THUMB_W * self.devicePixelRatioF() / page.rect.width
                pix = page.get_pixmap(matrix=pymupdf.Matrix(s, s), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                             QImage.Format.Format_RGB888).copy()
                pm = QPixmap.fromImage(img)
                pm.setDevicePixelRatio(self.devicePixelRatioF())
                if i < self.count():
                    self.item(i).setData(THUMB_ROLE, pm)
            except Exception:
                pass
        self.viewport().update()
        if end < doc.page_count:
            QTimer.singleShot(0, lambda: self._fill(gen, end))

    def refresh_page(self, i: int) -> None:
        """Vuelve a pintar una sola miniatura."""
        if self.view is None or i >= self.count():
            return
        page = self.view.doc[i]
        s = THUMB_W * self.devicePixelRatioF() / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(s, s), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        self.item(i).setData(THUMB_ROLE, pm)
        self.viewport().update()

    # -- sincronización --------------------------------------------------
    def sync_current(self, i: int) -> None:
        if 0 <= i < self.count() and self.currentRow() != i:
            self._syncing = True
            self.setCurrentRow(i, QItemSelectionModelFlag())
            self._syncing = False
            self.scrollToItem(self.item(i))

    def _row_changed(self, row: int) -> None:
        if self._syncing or self.view is None or row < 0:
            return
        self.view.go_to_page(row)

    def selected_pages(self) -> list[int]:
        return sorted(self.row(it) for it in self.selectedItems())

    def _reordered(self) -> None:
        if self.view is None:
            return
        order = [self.item(r).data(Qt.ItemDataRole.UserRole) for r in range(self.count())]
        if order == list(range(len(order))):
            return
        self.view.run_edit(lambda: ops.reorder_pages(self.view.doc, order), structure=True)

    def _menu(self, pos) -> None:
        if not self._context_actions:
            return
        m = QMenu(self)
        for a in self._context_actions:
            if a is None:
                m.addSeparator()
            else:
                m.addAction(a)
        m.exec(self.viewport().mapToGlobal(pos))


def QItemSelectionModelFlag():
    from PySide6.QtCore import QItemSelectionModel
    return QItemSelectionModel.SelectionFlag.ClearAndSelect


# ---------------------------------------------------------------------------
class BookmarkPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = None
        self._toc: list = []
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._clicked)
        row = QHBoxLayout()
        self.b_add = QPushButton("＋ Aquí")
        self.b_add.setToolTip("Añadir marcador en la página actual (como hijo del seleccionado, si lo hay)")
        self.b_ren = QPushButton("Renombrar")
        self.b_del = QPushButton("Borrar")
        for b in (self.b_add, self.b_ren, self.b_del):
            row.addWidget(b)
        self.b_add.clicked.connect(self._add)
        self.b_ren.clicked.connect(self._rename)
        self.b_del.clicked.connect(self._delete)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tree)
        lay.addLayout(row)

    def set_view(self, view) -> None:
        self.view = view
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        self._toc = []
        if self.view is None:
            return
        self._toc = [list(e[:3]) for e in self.view.doc.get_toc(simple=True)]
        stack: list[tuple[int, Optional[QTreeWidgetItem]]] = []
        for idx, (lvl, title, page) in enumerate(self._toc):
            it = QTreeWidgetItem([f"{title}   ·  p. {page}"])
            it.setData(0, Qt.ItemDataRole.UserRole, idx)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            if stack and stack[-1][1] is not None:
                stack[-1][1].addChild(it)
            else:
                self.tree.addTopLevelItem(it)
            stack.append((lvl, it))
        self.tree.expandAll()

    def _clicked(self, item, _col) -> None:
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        page = self._toc[idx][2]
        if self.view and page >= 1:
            self.view.go_to_page(page - 1)

    def _selected_index(self) -> Optional[int]:
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _subtree_end(self, idx: int) -> int:
        lvl = self._toc[idx][0]
        j = idx + 1
        while j < len(self._toc) and self._toc[j][0] > lvl:
            j += 1
        return j

    def _commit(self, new_toc: list) -> None:
        if not self.view:
            return
        self.view.run_edit(lambda: self.view.doc.set_toc(new_toc), pages=[])
        self.rebuild()

    def _add(self) -> None:
        if not self.view:
            return
        page = max(0, self.view.current_page()) + 1
        title, ok = QInputDialog.getText(self, "Nuevo marcador", f"Título (página {page}):")
        if not ok or not title.strip():
            return
        toc = [list(e) for e in self._toc]
        sel = self._selected_index()
        if sel is None:
            toc.append([1, title.strip(), page])
        else:
            pos = self._subtree_end(sel)
            toc.insert(pos, [toc[sel][0] + 1, title.strip(), page])
        self._commit(toc)

    def _rename(self) -> None:
        sel = self._selected_index()
        if sel is None:
            return
        title, ok = QInputDialog.getText(self, "Renombrar", "Título:", text=self._toc[sel][1])
        if ok and title.strip():
            toc = [list(e) for e in self._toc]
            toc[sel][1] = title.strip()
            self._commit(toc)

    def _delete(self) -> None:
        sel = self._selected_index()
        if sel is None:
            return
        toc = [list(e) for e in self._toc]
        del toc[sel:self._subtree_end(sel)]
        self._commit(toc)


# ---------------------------------------------------------------------------
class AnnotationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = None
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._open)
        self.b_del = QPushButton("Eliminar seleccionada")
        self.b_del.clicked.connect(self._delete)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.list)
        lay.addWidget(self.b_del)

    def set_view(self, view) -> None:
        self.view = view
        self.rebuild()

    def rebuild(self) -> None:
        self.list.clear()
        if self.view is None:
            return
        for i in range(self.view.page_count):
            for a in self.view.doc[i].annots():
                kind = a.type[1]
                if kind == "Popup":
                    continue
                txt = (a.info.get("content") or "").strip().replace("\n", " ")
                it = QListWidgetItem(f"p. {i + 1} · {kind}" + (f" · {txt[:40]}" if txt else ""))
                it.setData(Qt.ItemDataRole.UserRole, (i, a.xref))
                self.list.addItem(it)

    def _open(self, item) -> None:
        i, xref = item.data(Qt.ItemDataRole.UserRole)
        if self.view:
            self.view.go_to_page(i)
            self.view._sel_annot = (i, xref)
            self.view.viewport().update()

    def _delete(self) -> None:
        it = self.list.currentItem()
        if not it or not self.view:
            return
        i, xref = it.data(Qt.ItemDataRole.UserRole)
        self.view.run_edit(lambda: ops.delete_annot_xref(self.view.doc[i], xref), pages=[i])
        self.rebuild()

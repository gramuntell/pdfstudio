"""PDF Studio — punto de entrada.  Uso:  python main.py [archivo.pdf ...]"""
import os
import sys
import traceback
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

from pdfstudio import paths  # noqa: E402

# Con pythonw / exe sin consola stdout y stderr son None: redirigir al log.
if sys.stdout is None or sys.stderr is None:
    try:
        _f = open(paths.log_path(), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or _f
        sys.stderr = sys.stderr or _f
    except Exception:
        pass


def _crash(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(paths.log_path(), "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass
    if sys.platform == "win32" and "--selftest" not in sys.argv:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f"PDF Studio ha tenido un error.\n\nDetalles guardados en:\n{paths.log_path()}\n\n{text[-600:]}",
                "PDF Studio", 0x10)
        except Exception:
            pass


sys.excepthook = _crash

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pdfstudio.window import APP_NAME, MainWindow  # noqa: E402

APP_ID = "PdfStudio.PdfStudio.1"
SERVER = "PdfStudio-" + os.environ.get("USERNAME", os.environ.get("USER", "u"))


def _to_front(win) -> None:
    win.setWindowState((win.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
    win.show()
    win.raise_()
    win.activateWindow()
    if sys.platform == "win32":
        try:
            import ctypes
            u = ctypes.windll.user32
            u.keybd_event(0x12, 0, 0, 0)       # truco ALT: permite robar el foco
            hwnd = int(win.winId())
            u.ShowWindow(hwnd, 9)
            u.SetForegroundWindow(hwnd)
            u.keybd_event(0x12, 0, 2, 0)
        except Exception:
            pass


def _send_to_running(files: list[str]) -> bool:
    """Si ya hay una instancia abierta, le pasa los archivos y devuelve True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER)
    if not sock.waitForConnected(300):
        return False
    sock.write(("\n".join(files) or "-").encode("utf-8"))
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def _selftest(out: str) -> int:
    """Comprobación para CI: abre un PDF, renderiza y lista idiomas de OCR."""
    import pymupdf
    from pdfstudio import ops
    lines = []
    try:
        d = pymupdf.open()
        d.new_page().insert_text((72, 72), "hola")
        pix = d[0].get_pixmap()
        lines.append(f"render ok {pix.width}x{pix.height}")
        lines.append("ocr langs: " + ",".join(ops.ocr_languages()))
        ok = "spa" in ops.ocr_languages() and "eng" in ops.ocr_languages()
        lines.append("RESULT " + ("OK" if ok else "FAIL-OCR"))
    except Exception as e:  # noqa: BLE001
        lines.append(f"RESULT FAIL {e!r}")
        ok = False
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        return _selftest(sys.argv[i + 1] if i + 1 < len(sys.argv) else "selftest.txt")
    if sys.platform == "win32":
        try:  # identidad propia en la barra de tareas
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    files = [os.path.abspath(a) for a in sys.argv[1:] if os.path.exists(a)]
    app = QApplication(sys.argv)
    if _send_to_running(files):
        return 0
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("PdfStudio")
    app.setStyle("Fusion")
    icon = QIcon(paths.resource("assets", "pdfstudio.ico"))
    app.setWindowIcon(icon)

    win = MainWindow(files)
    win.setWindowIcon(icon)

    QLocalServer.removeServer(SERVER)
    server = QLocalServer()
    server.listen(SERVER)

    def on_conn() -> None:
        s = server.nextPendingConnection()
        if s is None:
            return
        s.waitForReadyRead(500)
        data = bytes(s.readAll()).decode("utf-8", "ignore")
        for p in data.split("\n"):
            if p and p != "-" and os.path.exists(p):
                win.open_path(p)
        _to_front(win)

    server.newConnection.connect(on_conn)
    win._ipc_server = server  # evita que se destruya

    win.show()
    _to_front(win)
    QTimer.singleShot(250, lambda: _to_front(win))
    QTimer.singleShot(900, lambda: _to_front(win))
    return app.exec()


if __name__ == "__main__":
    if not getattr(sys, "frozen", False):
        os.chdir(paths.app_dir())
        sys.path.insert(0, paths.app_dir())
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        _crash(*sys.exc_info())
        sys.exit(1)

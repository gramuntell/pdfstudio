"""Rutas de recursos y de datos de usuario (funciona en código fuente y empaquetado)."""
import os
import sys


def app_dir() -> str:
    """Carpeta de la aplicación: junto al .exe si está empaquetada, o la raíz del proyecto."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource(*parts: str) -> str:
    return os.path.join(app_dir(), *parts)


def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "PDF Studio")
    os.makedirs(d, exist_ok=True)
    return d


def log_path() -> str:
    return os.path.join(data_dir(), "error.log")

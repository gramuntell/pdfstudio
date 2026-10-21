# -*- mode: python ; coding: utf-8 -*-
# Compilar:  pyinstaller --noconfirm PDFStudio.spec
import os
datas = [("assets", "assets")]
if os.path.isdir("tessdata"):
    datas.append(("tessdata", "tessdata"))

a = Analysis(
    ["main.py"], pathex=["."], datas=datas, hiddenimports=[],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "unittest", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PDF Studio",
          console=False, icon="assets/pdfstudio.ico", upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="PDF Studio", upx=False)

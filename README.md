# PDF Studio

Visor y editor de PDF de escritorio para uso personal. Python + PySide6 (Qt) + PyMuPDF.
Todo funciona en local; no envía nada a internet.

## Instalar y ejecutar

Necesitas Python 3.10 o superior.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate        Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
python main.py                 # o:  python main.py documento.pdf
```

Probado con PySide6 6.12 y PyMuPDF 2.0 en Linux (ver «Qué se ha probado»).

## OCR (opcional)

El OCR usa los datos de idioma de Tesseract. No hace falta instalar nada más para el resto de funciones.

- **Instalador de Windows:** ya incluye español e inglés.
- **Desde el código en Windows:** instala Tesseract (UB Mannheim) con *Spanish*, o define `TESSDATA_PREFIX` con la carpeta `tessdata`.
- **Ubuntu/Debian:** `sudo apt install tesseract-ocr tesseract-ocr-spa`
- **macOS:** `brew install tesseract tesseract-lang`

## Qué incluye

| Área | Funciones |
|---|---|
| Visor | Pestañas, zoom (Ctrl+rueda), ajustar al ancho/página, búsqueda (Ctrl+F), ir a página, miniaturas, marcadores, arrastrar PDFs a la ventana |
| Anotaciones | Resaltar, subrayar, tachar, rectángulo, elipse, línea, flecha, lápiz, notas, cuadros de texto, color/grosor/tamaño; seleccionar y borrar (Supr) |
| Páginas | Girar, eliminar, duplicar, insertar en blanco o de otro PDF, extraer, mover (arrastrando miniaturas o con Mover arriba/abajo) |
| Documento | Unir, dividir (por rangos / cada N / una por archivo), comprimir (4 niveles), proteger con contraseña (AES-256), quitar contraseña, propiedades, marcadores, exportar a PNG/JPG, imágenes a PDF, buscar en una carpeta de PDFs, imprimir |
| Formularios y firma | Rellenar campos (texto, casillas, desplegables), firma dibujada o desde foto (se guarda para reutilizarla), insertar imágenes |
| Edición | Editar texto (línea completa), reemplazar/eliminar/guardar imágenes existentes |
| Privacidad | Redacción real (marcar zonas o buscar texto → aplicar elimina el contenido), aplanar anotaciones |
| OCR | Convierte escaneos en PDFs con texto buscable (español/inglés u otros idiomas instalados) |

Todo se puede deshacer con Ctrl+Z hasta 15 pasos por pestaña. **Guardar sobrescribe el archivo original**; usa «Guardar como» si quieres conservarlo.

## Atajos

Ctrl+O abrir · Ctrl+S guardar · Ctrl+Shift+S guardar como · Ctrl+P imprimir · Ctrl+Z / Ctrl+Y deshacer / rehacer · Ctrl+F buscar (F3 siguiente) · Ctrl+G ir a página · Ctrl+1 / Ctrl+2 ajustar ancho / página · Ctrl+Shift+L / R girar · F4 panel lateral · Supr borrar anotación elegida.

## Límites que conviene conocer

- **Editar texto** sustituye una línea completa. La original se borra y la nueva se escribe con Helvetica, Times o Courier (la más parecida), o con una fuente Unicode integrada si llevas €, –, “ ” u otros símbolos. No conserva la fuente original, no reajusta párrafos ni funciona en páginas rotadas. Para cambiar texto en un escaneo, haz OCR primero (el texto reconocido queda como capa invisible; editar el escaneo en sí no es posible).
- **La firma es una imagen**, no una firma digital con certificado.
- **Formularios:** se rellenan campos AcroForm estándar. No se ejecutan los scripts de JavaScript de los formularios (cálculos automáticos) ni se soportan formularios XFA.
- **Las restricciones de un PDF protegido** (imprimir/copiar/modificar) son orientativas: algunos programas las ignoran. Lo que protege de verdad es la contraseña para abrir.
- Los PDFs con contraseña se guardan de nuevo con la misma contraseña, salvo que uses «Quitar contraseña».
- No incluye: comparar documentos, Word/Excel ↔ PDF, comentarios compartidos, firma con certificado, PDF/A, accesibilidad.

## Qué se ha probado

Tres baterías automáticas (en `tests/`): operaciones sobre PDFs (`test_ops.py`), interfaz simulando ratón y teclado sobre la ventana real (`test_gui.py`) y diálogos/flujos de documento (`test_dialogs.py`).

```bash
python tests/test_ops.py
QT_QPA_PLATFORM=offscreen python tests/test_gui.py       # en Windows: set QT_QPA_PLATFORM=offscreen
QT_QPA_PLATFORM=offscreen python tests/test_dialogs.py
```

Se ejecutaron en Linux sin pantalla real. **No** se han probado en Windows/macOS, con una impresora física (solo imprimiendo a PDF) ni el gesto real de arrastrar miniaturas con el ratón (sí la lógica de reordenación y los botones Mover arriba/abajo).

## Instalador para Windows

Cada vez que se sube una etiqueta `vX.Y.Z` al repositorio, GitHub Actions (`.github/workflows/build.yml`) compila con PyInstaller, comprueba el `.exe` (`--selftest`), genera `PDFStudio-Setup-X.Y.Z.exe` con Inno Setup y lo publica en *Releases* junto a su SHA-256. El instalador no pide permisos de administrador, incluye OCR en español e inglés y crea accesos directos.

Compilar a mano en Windows: `pip install -r requirements-build.txt`, copiar `spa/eng/osd.traineddata` a `tessdata/`, `pyinstaller --noconfirm PDFStudio.spec` e `iscc installer\PDFStudio.iss`.

El instalador no está firmado: Windows SmartScreen mostrará «Más información → Ejecutar de todas formas».

## Licencia

AGPL-3.0 (ver `LICENSE` y `THIRD_PARTY.md`), por usar PyMuPDF. Si repartes la app, debes ofrecer el código fuente.

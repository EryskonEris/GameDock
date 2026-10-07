# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'main.py')],
    pathex=[str(root)], binaries=[], datas=[],
    hiddenimports=['win32timezone'] + collect_submodules('win32com.shell'),
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
# The widget launcher does not use PDF, QML, Quick or the virtual keyboard.
# Their default Qt plugins otherwise pull large unrelated modules into the ZIP.
unused_libraries = {
    'qt6pdf.dll', 'qt6qml.dll', 'qt6qmlmeta.dll', 'qt6qmlmodels.dll',
    'qt6qmlworkerscript.dll', 'qt6quick.dll', 'qt6virtualkeyboard.dll',
}
# Qt uses Windows' unversioned ICU interface. A same-named ICU from unrelated
# programs on the build machine's PATH has a different ABI and must not ship.
system_icu = {'icuuc.dll', 'icudt78.dll'}
a.binaries = [entry for entry in a.binaries if
    Path(entry[0]).name.lower() not in unused_libraries | system_icu
    and '/platforminputcontexts/' not in entry[0].replace('\\', '/').lower()
    and Path(entry[0]).name.lower() != 'qpdf.dll']
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='GameDock', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(root / 'assets' / 'gamedock.ico'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='GameDock')

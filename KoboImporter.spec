# -*- mode: python ; coding: utf-8 -*-
#
# Points 25, 26, 27 :
#   - construction en mode dossier (COLLECT) et non plus en fichier unique.
#     Le mono-fichier se decompressait dans %TEMP% a chaque lancement, soit
#     5 a 15 secondes d'attente sur un poste modeste, pendant lesquelles
#     l'utilisateur croit que rien ne se passe et double-clique a nouveau ;
#   - upx desactive : la compression UPX est une cause classique de faux
#     positif antivirus, bloquante sur des postes d'organisation ;
#   - icone et proprietes de version renseignees.

block_cipher = None

a = Analysis(
    ['kobo_importer_app.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[
        'openpyxl',
        'openpyxl.cell._writer',   # importe dynamiquement par openpyxl 3.1
        'pandas._libs.tslibs.base',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'scipy', 'IPython', 'jupyter', 'notebook',
        'pytest', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'tkinter.test', 'test',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # mode dossier
    name='KoboImporter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app.ico',
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='KoboImporter',
)

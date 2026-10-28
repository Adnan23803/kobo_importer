"""Coherence du numero de version entre les fichiers qui le portent.

Lancement :  python tests/test_versions.py

La version est ecrite a trois endroits : le programme (koboimp/__init__.py),
l'installeur (installer.iss) et les proprietes de l'executable
(version_info.txt). Un oubli produit un installeur qui s'annonce 3.3.0 en
installant une 3.2.3, et une verification de mise a jour qui propose sans fin
la version deja installee.

Le manifeste derniere_version.json n'est pas controle ici : la construction
automatique le met a jour apres publication de l'installeur, avec son empreinte.
"""

import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

from koboimp import __version__  # noqa: E402

PASSED = []
FAILED = []


def check(label, condition, detail=""):
    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} :: {detail}")


def lire(nom):
    with open(os.path.join(RACINE, nom), encoding="utf-8") as handle:
        return handle.read()


print("\n--- Numero de version ---")
attendue = __version__
parties = tuple(int(part) for part in attendue.split("."))
check("version du programme au format X.Y.Z", len(parties) == 3, attendue)

iss = re.search(r'#define\s+MaVersion\s+"([^"]+)"', lire("installer.iss"))
check("installer.iss", iss and iss.group(1) == attendue, iss and iss.group(1))

info = lire("version_info.txt")
quadruplet = "(" + ", ".join(str(part) for part in parties + (0,)) + ")"
for cle in ("filevers", "prodvers"):
    trouve = re.search(rf"{cle}=(\([^)]*\))", info)
    check(f"version_info.txt : {cle}", trouve and trouve.group(1) == quadruplet,
          trouve and trouve.group(1))
for cle in ("FileVersion", "ProductVersion"):
    trouve = re.search(rf"StringStruct\('{cle}', '([^']+)'\)", info)
    check(f"version_info.txt : {cle}", trouve and trouve.group(1) == attendue,
          trouve and trouve.group(1))

# Etiquette de publication (construction automatique) : v3.3.0 pour 3.3.0.
etiquette = os.environ.get("GITHUB_REF_NAME", "")
if os.environ.get("GITHUB_REF_TYPE") == "tag":
    check("etiquette git", etiquette == f"v{attendue}", etiquette)

print("\n--- Resultat ---")
print(f"\n{len(PASSED)} verification(s) reussie(s), {len(FAILED)} echec(s).")
for echec in FAILED:
    print(f"  ECHEC  {echec}")
sys.exit(1 if FAILED else 0)

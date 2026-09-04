"""Emplacements de fichiers de l'application.

Point 22 : toutes les donnees inscriptibles vivent dans %LOCALAPPDATA%\\KoboImporter
et non plus a cote de l'executable, qui peut se trouver dans un dossier protege
comme C:\\Program Files.
"""

import os
import sys

APP_FOLDER_NAME = "KoboImporter"


def is_frozen():
    return getattr(sys, "frozen", False)


def install_dir():
    """Dossier ou se trouve l'executable (ou le code source en developpement)."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts):
    """Chemin d'une ressource embarquee (assets/...)."""
    base = getattr(sys, "_MEIPASS", install_dir())
    return os.path.join(base, *parts)


def data_dir():
    """Dossier de donnees inscriptible de l'utilisateur."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not base:
        base = os.path.expanduser("~")
    path = os.path.join(base, APP_FOLDER_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def data_path(*parts):
    path = os.path.join(data_dir(), *parts)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


CONFIG_FILE = os.path.join(data_dir(), "config.json")
REGISTRY_FILE = os.path.join(data_dir(), "registry.db")
LOGO_FILE = resource_path("assets", "logo.png")
ICON_FILE = resource_path("assets", "app.ico")


def default_work_paths():
    """Dossiers de travail par defaut, tous sous le dossier de donnees."""
    return {
        "output_dir": os.path.join(data_dir(), "xml_echecs"),
        "log_file": os.path.join(data_dir(), "journal_import.csv"),
        "report_dir": os.path.join(data_dir(), "rapports"),
    }


TEMP_MARKERS = ["_MEI", os.path.join("AppData", "Local", "Temp")]


def is_temp_runtime_path(path):
    """Detecte un chemin herite d'un ancien lancement PyInstaller --onefile."""
    if not path:
        return False
    normalized = str(path).replace("/", "\\").lower()
    return any(marker.lower() in normalized for marker in TEMP_MARKERS)


def legacy_config_file():
    """Ancien emplacement de config.json (a cote de l'executable)."""
    return os.path.join(install_dir(), "config.json")


def open_in_explorer(path):
    """Ouvre un dossier (ou le dossier parent d'un fichier) dans l'explorateur."""
    target = path if os.path.isdir(path) else os.path.dirname(path)
    if not target or not os.path.isdir(target):
        target = data_dir()
    try:
        os.startfile(target)  # noqa: S606 - Windows uniquement
        return True, target
    except (AttributeError, OSError):
        return False, target

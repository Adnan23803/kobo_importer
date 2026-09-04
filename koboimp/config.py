"""Chargement / sauvegarde de la configuration.

Points couverts :
  22 - la configuration vit dans %LOCALAPPDATA%, avec reprise automatique d'un
       ancien config.json trouve a cote de l'executable ;
  23 - le token est chiffre (DPAPI) et l'export de configuration peut l'omettre ;
   8 - l'UID et la version du formulaire sont renseignes par l'application,
       plus par l'utilisateur ; aucune valeur codee en dur ;
   9 - la configuration est rangee dans un profil nomme ; load_config et
       save_config operent sur le profil actif, ce qui laisse inchange tout le
       reste de l'application.
"""

import json
import os
from urllib.parse import urlparse, urlunparse

from . import paths, profiles, security

DEFAULT_CONFIG = {
    # Connexion
    "server_base_url": "https://kf.kobotoolbox.org",
    "kpi_base_url": "",           # vide = derive de server_base_url
    "submission_base_url": "",    # vide = derive de server_base_url
    "api_token_enc": "",
    # Formulaire selectionne (rempli par l'application)
    "asset_uid": "",
    "form_title": "",
    "form_version": "",
    # Source de donnees
    "excel_file": "",
    "excel_sheet": "",
    # Dossiers de travail
    "output_dir": "",
    "log_file": "",
    "report_dir": "",
    # Options d'execution
    "dry_run": False,
    "resume_mode": "new",         # new | retry | force
    "max_workers": 5,
    "request_timeout": 30,
    "max_attempts": 4,
    # Interface
    "appearance": "Light",
    # Point 13 : adresse d'un manifeste JSON annoncant la derniere version.
    # Vide = aucune verification, aucun appel reseau.
    "update_url": "",
}

RESUME_MODES = {
    "new": "Envoyer uniquement les lignes jamais envoyees",
    "retry": "Reprendre uniquement les lignes en echec",
    "force": "Tout renvoyer (ignore l'historique)",
}

_PATH_KEYS = ("output_dir", "log_file", "report_dir")
_SECRET_KEYS = ("api_token_enc",)


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

def normalize_base_url(url):
    value = (url or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/")


def derive_kpi_url(server_base_url):
    """Deduit l'adresse KPI (API v2 /assets) de l'adresse saisie.

    KoboToolbox expose historiquement deux serveurs : KPI (kf.*, l'interface web)
    et KoboCAT (kc.*, la reception des soumissions). L'utilisateur ne connait en
    general que l'adresse de son navigateur : on devine l'autre.
    """
    base = normalize_base_url(server_base_url)
    if not base:
        return ""
    parsed = urlparse(base)
    host = parsed.netloc
    for prefix, replacement in (("kc.", "kf."), ("kobocat.", "kf."), ("api.", "kf.")):
        if host.startswith(prefix):
            host = replacement + host[len(prefix):]
            break
    return urlunparse((parsed.scheme, host, parsed.path.rstrip("/"), "", "", ""))


def derive_submission_url(server_base_url):
    """Adresse de reception des soumissions.

    L'endpoint OpenRosa /submission repond sur le meme hote que l'interface web
    (kf.*, eu.*) comme sur l'ancien hote kc.*. On tente donc l'adresse saisie
    telle quelle ; le client HTTP bascule sur kc.* si elle refuse l'envoi.
    """
    return normalize_base_url(server_base_url)


def alternate_submission_url(server_base_url):
    """Variante kc.* utilisee en repli si l'adresse principale refuse l'envoi."""
    base = normalize_base_url(server_base_url)
    if not base:
        return ""
    parsed = urlparse(base)
    host = parsed.netloc
    if host.startswith("kf."):
        host = "kc." + host[3:]
    elif host.startswith("kobo."):
        host = "kc." + host[5:]
    else:
        return ""
    return urlunparse((parsed.scheme, host, parsed.path.rstrip("/"), "", "", ""))


def resolved_endpoints(config):
    """Retourne (kpi_base, submission_base, submission_fallback)."""
    server = normalize_base_url(config.get("server_base_url"))
    kpi = normalize_base_url(config.get("kpi_base_url")) or derive_kpi_url(server)
    submission = normalize_base_url(config.get("submission_base_url")) or derive_submission_url(server)
    fallback = "" if config.get("submission_base_url") else alternate_submission_url(server)
    return kpi, submission, fallback


# --------------------------------------------------------------------------
# Chargement / sauvegarde
# --------------------------------------------------------------------------

def _migrate_legacy_keys(data):
    """Convertit un config.json des versions 1.x / 2.x."""
    changed = False

    if "assets_uid" in data:
        data.setdefault("asset_uid", data.pop("assets_uid"))
        changed = True

    if "api_token" in data:
        legacy_token = data.pop("api_token")
        if legacy_token and not data.get("api_token_enc"):
            data["api_token_enc"] = security.encrypt_token(legacy_token)
        changed = True

    if "resume_only_failed" in data:
        if data.pop("resume_only_failed"):
            data["resume_mode"] = "retry"
        changed = True

    # Ces dossiers ne servent plus : les XML ne transitent plus par le disque
    # en fonctionnement normal (point 16).
    for obsolete in ("success_dir", "failed_dir"):
        if obsolete in data:
            data.pop(obsolete)
            changed = True

    return changed


def normalize(raw):
    """Complete un dictionnaire brut avec les valeurs par defaut et les bornes."""
    data = DEFAULT_CONFIG.copy()
    if isinstance(raw, dict):
        loaded = dict(raw)
        _migrate_legacy_keys(loaded)
        data.update({k: v for k, v in loaded.items() if k in DEFAULT_CONFIG})
    return _finalize(data)


def load_config(profile=None):
    """Configuration du profil demande, ou du profil actif (point 9)."""
    return normalize(profiles.read(profile))


def _finalize(data):
    # Chemins de travail : on remplace tout chemin vide ou herite d'un dossier
    # temporaire PyInstaller par la valeur par defaut.
    defaults = paths.default_work_paths()
    for key in _PATH_KEYS:
        current = data.get(key) or ""
        if not current or paths.is_temp_runtime_path(current):
            data[key] = defaults[key]

    data["max_workers"] = _clamp_int(data.get("max_workers"), 5, 1, 16)
    data["request_timeout"] = _clamp_int(data.get("request_timeout"), 30, 5, 300)
    data["max_attempts"] = _clamp_int(data.get("max_attempts"), 4, 1, 10)
    if data.get("resume_mode") not in RESUME_MODES:
        data["resume_mode"] = "new"

    return data


def _clamp_int(value, fallback, low, high):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def as_payload(config):
    return {key: config.get(key, DEFAULT_CONFIG[key]) for key in DEFAULT_CONFIG}


def save_config(config, profile=None):
    """Enregistre dans le profil actif, ou dans celui demande (point 9)."""
    return profiles.write(as_payload(config), profile)


def save_config_to_file(config, path):
    """Ecriture directe dans un fichier : export, sauvegarde, tests."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(as_payload(config), handle, indent=2, ensure_ascii=False)
    return path


def export_config(config, path, include_token=False):
    """Point 23 : export partageable, sans secret par defaut.

    Le token chiffre par la DPAPI est de toute facon illisible sur un autre
    poste : l'exporter n'aurait aucun interet et donnerait une fausse impression
    de fonctionnement.
    """
    payload = as_payload(config)
    if not include_token:
        for key in _SECRET_KEYS:
            payload[key] = ""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return path


def import_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError("Le fichier ne contient pas une configuration valide.")
    _migrate_legacy_keys(loaded)
    data = DEFAULT_CONFIG.copy()
    data.update({k: v for k, v in loaded.items() if k in DEFAULT_CONFIG})

    defaults = paths.default_work_paths()
    for key in _PATH_KEYS:
        if not data.get(key) or paths.is_temp_runtime_path(data[key]):
            data[key] = defaults[key]
    return data


def get_token(config):
    return security.decrypt_token(config.get("api_token_enc", ""))


def set_token(config, token):
    config["api_token_enc"] = security.encrypt_token(token)
    return config

"""Point 9 : plusieurs configurations nommees.

Une seule configuration obligeait a ressaisir serveur, jeton et formulaire des
qu'on alternait entre deux projets. Un profil rassemble tout ce qui caracterise
un contexte de travail ; on bascule de l'un a l'autre en une selection.

Ce module ne connait que le stockage : la normalisation des valeurs et les
valeurs par defaut restent dans config.py, qui s'appuie sur lui. La dependance
va dans ce sens uniquement, pour eviter tout import circulaire.
"""

import json
import os
import re
import shutil

from . import paths

STORE_FILE = os.path.join(paths.data_dir(), "profiles.json")
DEFAULT_NAME = "Par defaut"
MAX_NAME_LENGTH = 60

_INVALID_NAME = re.compile(r"[\x00-\x1f]")


class ProfileError(Exception):
    """Erreur formulee pour l'utilisateur."""


def clean_name(name):
    cleaned = _INVALID_NAME.sub("", str(name or "")).strip()
    return cleaned[:MAX_NAME_LENGTH]


def _blank_store():
    return {"active": DEFAULT_NAME, "profiles": {}}


def _read_store():
    """Lit le magasin de profils, en reprenant l'ancien config.json au besoin."""
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, "r", encoding="utf-8") as handle:
                store = json.load(handle)
            if isinstance(store, dict) and isinstance(store.get("profiles"), dict):
                if not store["profiles"]:
                    store["profiles"] = {DEFAULT_NAME: {}}
                if store.get("active") not in store["profiles"]:
                    store["active"] = next(iter(store["profiles"]))
                return store
        except (OSError, ValueError):
            # Fichier corrompu : on le met de cote plutot que de le perdre.
            try:
                shutil.copyfile(STORE_FILE, STORE_FILE + ".corrompu")
            except OSError:
                pass

    store = _blank_store()

    # Migration depuis la configuration unique des versions precedentes.
    legacy = paths.CONFIG_FILE
    if not os.path.exists(legacy):
        legacy = paths.legacy_config_file()
    if os.path.exists(legacy):
        try:
            with open(legacy, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                store["profiles"][DEFAULT_NAME] = loaded
        except (OSError, ValueError):
            pass

    store["profiles"].setdefault(DEFAULT_NAME, {})
    store["active"] = DEFAULT_NAME
    return store


def _write_store(store):
    parent = os.path.dirname(STORE_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Ecriture en deux temps : une coupure ne laisse pas un fichier tronque
    # qui ferait perdre tous les profils.
    temporary = STORE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, STORE_FILE)


# --------------------------------------------------------------------------
# Consultation
# --------------------------------------------------------------------------

def list_names():
    return sorted(_read_store()["profiles"], key=str.lower)


def active_name():
    return _read_store()["active"]


def exists(name):
    return clean_name(name) in _read_store()["profiles"]


def read(name=None):
    """Contenu brut d'un profil (dict, eventuellement vide)."""
    store = _read_store()
    target = clean_name(name) or store["active"]
    return dict(store["profiles"].get(target) or {})


# --------------------------------------------------------------------------
# Modification
# --------------------------------------------------------------------------

def write(payload, name=None):
    store = _read_store()
    target = clean_name(name) or store["active"]
    store["profiles"][target] = dict(payload)
    store["active"] = target
    _write_store(store)
    return target


def set_active(name):
    store = _read_store()
    target = clean_name(name)
    if target not in store["profiles"]:
        raise ProfileError(f"Le profil « {target} » n'existe pas.")
    store["active"] = target
    _write_store(store)
    return target


def create(name, payload=None):
    store = _read_store()
    target = clean_name(name)
    if not target:
        raise ProfileError("Donnez un nom au profil.")
    if target in store["profiles"]:
        raise ProfileError(f"Un profil « {target} » existe deja.")
    store["profiles"][target] = dict(payload or {})
    store["active"] = target
    _write_store(store)
    return target


def duplicate(source, new_name):
    return create(new_name, read(source))


def rename(old, new):
    store = _read_store()
    source = clean_name(old)
    target = clean_name(new)
    if source not in store["profiles"]:
        raise ProfileError(f"Le profil « {source} » n'existe pas.")
    if not target:
        raise ProfileError("Donnez un nom au profil.")
    if target != source and target in store["profiles"]:
        raise ProfileError(f"Un profil « {target} » existe deja.")

    store["profiles"][target] = store["profiles"].pop(source)
    if store["active"] == source:
        store["active"] = target
    _write_store(store)
    return target


def delete(name):
    """Supprime un profil. Le dernier restant ne peut pas etre supprime."""
    store = _read_store()
    target = clean_name(name)
    if target not in store["profiles"]:
        raise ProfileError(f"Le profil « {target} » n'existe pas.")
    if len(store["profiles"]) <= 1:
        raise ProfileError(
            "Ce profil est le seul existant : il ne peut pas etre supprime.\n\n"
            "Creez-en un autre d'abord, ou modifiez celui-ci."
        )

    store["profiles"].pop(target)
    if store["active"] == target:
        store["active"] = next(iter(store["profiles"]))
    _write_store(store)
    return store["active"]

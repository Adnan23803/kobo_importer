"""Point 13 : verification de la disponibilite d'une version plus recente.

L'episode du 410 a montre le risque : quand KoboToolbox modifie son API, un
executable deja distribue cesse de fonctionner et rien n'indique a l'utilisateur
qu'un correctif existe. Il appelle, ou renonce.

La verification exige une adresse de manifeste, cherchee dans cet ordre :
le reglage du profil (« Parametres avances »), puis DEFAULT_UPDATE_URL, inscrite
dans le programme a la construction. Sans aucune des deux, aucun appel reseau
n'est emis et la fonction reste inerte.

Deux formats de manifeste sont acceptes :

  format simple           {"version": "3.1.0",
                           "url": "https://.../KoboImporter_3.1.0.exe",
                           "notes": "Correction de ..."}

  API GitHub Releases     {"tag_name": "v3.1.0",
                           "html_url": "https://github.com/.../releases/...",
                           "body": "...", "prerelease": false, "draft": false}
"""

import re
from dataclasses import dataclass

import requests

from . import __version__

CHECK_TIMEOUT = 10

# Adresse par defaut, inscrite dans le programme au moment de la construction.
#
# Elle est indispensable a une distribution reelle : `update_url` est range dans
# le profil, donc dans %LOCALAPPDATA%, qu'une installation neuve ne possede pas.
# Sans valeur ici, chaque poste devrait etre configure a la main et la
# verification ne servirait a rien.
#
# Renseignez-la avant de construire l'executable, par exemple :
#   DEFAULT_UPDATE_URL = "https://api.github.com/repos/MonCompte/kobo-importer/releases/latest"
# ou, pour ne pas dependre du quota de l'API GitHub :
#   DEFAULT_UPDATE_URL = "https://raw.githubusercontent.com/MonCompte/kobo-importer/main/derniere_version.json"
DEFAULT_UPDATE_URL = "https://api.github.com/repos/Adnan23803/kobo_importer/releases/latest"

_VERSION_PART = re.compile(r"\d+")


def resolve_url(config):
    """Adresse a interroger : celle du profil, sinon celle du programme.

    Un reglage saisi dans les parametres avances l'emporte, ce qui permet a une
    organisation de pointer vers son propre serveur sans reconstruire l'outil.
    """
    return str((config or {}).get("update_url") or "").strip() or DEFAULT_UPDATE_URL


@dataclass
class UpdateInfo:
    available: bool = False
    current: str = __version__
    latest: str = ""
    url: str = ""
    notes: str = ""
    error: str = ""

    def headline(self):
        if self.error:
            return f"Verification impossible : {self.error}"
        if self.available:
            return f"Version {self.latest} disponible (vous utilisez la {self.current})."
        if self.latest:
            return f"Vous utilisez la version la plus recente ({self.current})."
        return "Verification des mises a jour non configuree."


def parse_version(text):
    """'v3.1.0' -> (3, 1, 0). Les parties non numeriques sont ignorees.

    Comparer des chaines donnerait '3.10.0' < '3.9.0' : il faut comparer des
    nombres, partie par partie.
    """
    numbers = _VERSION_PART.findall(str(text or ""))
    if not numbers:
        return ()
    return tuple(int(part) for part in numbers[:4])


def is_newer(candidate, reference=__version__):
    left, right = parse_version(candidate), parse_version(reference)
    if not left or not right:
        return False
    # Egalise les longueurs : 3.1 et 3.1.0 designent la meme version.
    size = max(len(left), len(right))
    left = left + (0,) * (size - len(left))
    right = right + (0,) * (size - len(right))
    return left > right


def _extract(payload):
    """Retourne (version, url, notes) quel que soit le format du manifeste."""
    if isinstance(payload, list):
        # API GitHub : /releases renvoie une liste, la plus recente en tete.
        payload = next(
            (item for item in payload
             if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease")),
            payload[0] if payload else {},
        )
    if not isinstance(payload, dict):
        return "", "", ""

    if payload.get("draft") or payload.get("prerelease"):
        return "", "", ""

    version = str(payload.get("version") or payload.get("tag_name") or "").strip()

    # html_url avant url : dans une reponse GitHub, « url » designe l'entree
    # d'API (https://api.github.com/repos/.../releases/123), inexploitable pour
    # un utilisateur, tandis que « html_url » ouvre la page de telechargement.
    url = str(payload.get("html_url") or payload.get("url") or "").strip()

    notes = str(payload.get("notes") or payload.get("body") or "").strip()
    return version, url, notes


def check_for_update(update_url, current=__version__, timeout=CHECK_TIMEOUT):
    """Interroge le manifeste. N'echoue jamais : renvoie l'erreur dans l'objet.

    Une verification de mise a jour ne doit jamais empecher de travailler ; en
    cas de coupure reseau ou de manifeste absent, l'application continue.
    """
    target = str(update_url or "").strip()
    if not target:
        return UpdateInfo(current=current)

    try:
        response = requests.get(
            target,
            timeout=timeout,
            headers={
                "User-Agent": f"KoboImporter/{current}",
                "Accept": "application/json",
            },
        )
    except requests.RequestException as exc:
        return UpdateInfo(current=current, error=f"{exc.__class__.__name__}")

    if response.status_code != 200:
        return UpdateInfo(current=current, error=f"HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        return UpdateInfo(current=current, error="reponse illisible (JSON attendu)")

    version, url, notes = _extract(payload)
    if not version:
        return UpdateInfo(current=current, error="manifeste sans numero de version")

    return UpdateInfo(
        available=is_newer(version, current),
        current=current,
        latest=version,
        url=url,
        notes=notes[:2000],
    )

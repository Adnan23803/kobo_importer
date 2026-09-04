"""Client HTTP KoboToolbox.

Points couverts :
   4 - le code 202 (soumission deja recue) est un succes idempotent, pas un echec ;
   8 - liste des formulaires du compte et lecture de leur schema ;
  14 - les reponses d'erreur brutes sont traduites en francais ;
  15 - une session HTTP par thread, connexions maintenues ouvertes ;
  20 - repli exponentiel, respect de Retry-After, aucun reessai sur une erreur
       de donnees (400/401/403/404) qui se reproduirait a l'identique.
"""

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter

from . import __version__

# Endpoint de soumission OpenRosa.
#
# KoboToolbox a supprime toute l'API v1 dans la version de juin 2026 :
# /api/v1/submissions renvoie desormais 410 Gone sur kf.* comme sur kc.*.
# Le fichier REMOVALS.md du depot kobotoolbox/kpi designe son remplacant :
# « Use the OpenRosa submission endpoints: /submission, /{username}/submission,
#   or /collector/{token}/submission. »
# /submission repond bien (401 sans authentification) sur kf.*, kc.* et eu.*.
#
# Le protocole ne change pas : envoi multipart du champ xml_submission_file,
# le formulaire cible etant identifie par l'attribut id de la racine du XML.
SUBMISSION_PATH = "/submission"
LEGACY_SUBMISSION_PATH = "/api/v1/submissions"   # supprime, conserve pour diagnostic

ASSETS_PATH = "/api/v2/assets.json"
ASSET_PATH = "/api/v2/assets/{uid}.json"

# Champs suffisants pour savoir si le formulaire a bouge, sans retelecharger
# toute sa definition XLSForm (point 11).
STATUS_FIELDS = (
    "uid,name,version_id,deployed_version_id,has_deployment,"
    "deployment__active,deployment__submission_count"
)

# Resultats possibles d'un envoi.
SUCCESS = "SUCCESS"
DUPLICATE = "DUPLICATE"
REJECTED = "REJECTED"
AUTH = "AUTH"
NOT_FOUND = "NOT_FOUND"
GONE = "GONE"
SERVER = "SERVER"
NETWORK = "NETWORK"
RATE = "RATE"
STOPPED = "STOPPED"

SENT_STATUSES = {SUCCESS, DUPLICATE}

# Codes pour lesquels reessayer est inutile : la reponse serait identique.
NO_RETRY_CODES = {400, 401, 403, 404, 405, 409, 410, 413, 422}

BACKOFF_BASE = 2.0
BACKOFF_CAP = 30.0

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_OPENROSA_MESSAGE = ".//{http://openrosa.org/http/response}message"


class KoboError(Exception):
    """Erreur deja formulee pour l'utilisateur."""


@dataclass
class SubmitResult:
    status: str
    http_status: int = None
    message: str = ""
    attempts: int = 0

    @property
    def sent(self):
        return self.status in SENT_STATUSES

    @property
    def retryable_later(self):
        """Echec probablement temporaire : la ligne merite une reprise."""
        return self.status in {SERVER, NETWORK, RATE}


# --------------------------------------------------------------------------
# Point 14 : messages lisibles
# --------------------------------------------------------------------------

def _extract_body_message(body):
    text = (body or "").strip()
    if not text:
        return ""

    if text.startswith("<"):
        try:
            node = ET.fromstring(text)
            found = node.find(_OPENROSA_MESSAGE)
            if found is None and node.tag.endswith("message"):
                found = node
            if found is not None and found.text:
                return found.text.strip()
        except ET.ParseError:
            pass
        text = _HTML_TAG_RE.sub(" ", text)

    elif text.startswith(("{", "[")):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                for key in ("detail", "error", "message", "reason"):
                    if payload.get(key):
                        return str(payload[key]).strip()
                return "; ".join(f"{k} : {v}" for k, v in list(payload.items())[:4])
            if isinstance(payload, list) and payload:
                return str(payload[0])
        except ValueError:
            pass

    collapsed = " ".join(text.split())
    return collapsed[:400] + ("..." if len(collapsed) > 400 else "")


def humanize_http_error(status_code, body):
    """Traduit une reponse serveur en phrase comprehensible."""
    detail = _extract_body_message(body)

    explanations = {
        400: "Donnees refusees par le serveur : une valeur ne correspond pas au formulaire.",
        401: "Token invalide ou expire.",
        403: "Acces refuse : ce compte n'a pas le droit d'envoyer sur ce formulaire.",
        404: "Formulaire introuvable, ou adresse de serveur incorrecte.",
        405: "Cette adresse n'accepte pas les envois de donnees.",
        409: "Conflit signale par le serveur.",
        410: "L'adresse d'envoi utilisee a ete definitivement supprimee par KoboToolbox. "
             "Mettez a jour Kobo Importer, ou corrigez « Adresse de reception des "
             "soumissions » dans les parametres avances.",
        413: "Soumission trop volumineuse.",
        422: "Soumission incoherente avec la version du formulaire deployee.",
        429: "Trop de requetes : le serveur limite le debit.",
        500: "Erreur interne du serveur Kobo.",
        502: "Serveur Kobo momentanement injoignable (passerelle).",
        503: "Serveur Kobo momentanement indisponible.",
        504: "Le serveur Kobo n'a pas repondu a temps.",
    }
    head = explanations.get(status_code, f"Reponse inattendue du serveur (HTTP {status_code}).")
    return f"{head} {detail}".strip() if detail else head


def _status_category(status_code):
    if status_code == 201:
        return SUCCESS
    if status_code == 202:
        return DUPLICATE
    if status_code in (400, 409, 413, 422):
        return REJECTED
    if status_code in (401, 403):
        return AUTH
    if status_code == 404:
        return NOT_FOUND
    if status_code in (405, 410):
        return GONE
    if status_code == 429:
        return RATE
    if 500 <= status_code < 600:
        return SERVER
    return SERVER


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class KoboClient:
    """Client partage entre threads : chacun obtient sa propre session."""

    def __init__(
        self,
        token,
        kpi_base_url="",
        submission_base_url="",
        fallback_submission_base_url="",
        timeout=30,
        max_attempts=4,
        pool_size=10,
    ):
        self.token = (token or "").strip()
        self.kpi_base_url = (kpi_base_url or "").rstrip("/")
        self.submission_base_url = (submission_base_url or "").rstrip("/")
        self.fallback_submission_base_url = (fallback_submission_base_url or "").rstrip("/")
        self.timeout = int(timeout)
        self.max_attempts = max(1, int(max_attempts))
        self.pool_size = max(1, int(pool_size))

        self._local = threading.local()
        self._sessions = []
        self._sessions_lock = threading.Lock()
        self._endpoint_lock = threading.Lock()
        self._active_submission_base = self.submission_base_url

    # -- point 15 : reutilisation des connexions ---------------------------

    @property
    def session(self):
        """Session propre au thread appelant, avec connexions persistantes.

        Sans cela, chaque soumission refait une poignee de main TLS complete :
        sur une liaison a forte latence, c'est l'essentiel du temps d'import.
        """
        existing = getattr(self._local, "session", None)
        if existing is not None:
            return existing

        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.pool_size,
            pool_maxsize=self.pool_size,
            max_retries=0,  # la politique de reessai est geree ici (point 20)
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "Authorization": f"Token {self.token}",
            "User-Agent": f"KoboImporter/{__version__}",
            "X-OpenRosa-Version": "1.0",
            "Accept-Encoding": "gzip, deflate",
        })

        self._local.session = session
        with self._sessions_lock:
            self._sessions.append(session)
        return session

    def close(self):
        with self._sessions_lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - fermeture best effort
                pass
        self._local = threading.local()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    # -- point 8 : decouverte des formulaires ------------------------------

    def list_forms(self, max_pages=10):
        """Formulaires de type enquete accessibles au compte."""
        if not self.kpi_base_url:
            raise KoboError("Adresse du serveur KoboToolbox non renseignee.")

        url = f"{self.kpi_base_url}{ASSETS_PATH}?q=asset_type%3Asurvey&limit=200"
        forms = []
        for _ in range(max_pages):
            payload = self._get_json(url)
            results = payload.get("results") if isinstance(payload, dict) else None
            if not results:
                break
            forms.extend(results)
            url = payload.get("next")
            if not url:
                break

        from . import schema as schema_mod
        entries = [schema_mod.summarize_form_entry(item) for item in forms if item.get("uid")]
        entries.sort(key=lambda item: (not item["deployed"], item["title"].lower()))
        return entries

    def get_asset(self, uid):
        if not self.kpi_base_url:
            raise KoboError("Adresse du serveur KoboToolbox non renseignee.")
        if not uid:
            raise KoboError("Aucun formulaire selectionne.")
        return self._get_json(f"{self.kpi_base_url}{ASSET_PATH.format(uid=uid)}")

    def get_form_status(self, uid):
        """Etat courant du formulaire : version deployee, activite, volume.

        Point 11 : sert de controle juste avant l envoi. Entre le moment ou
        l utilisateur verifie son fichier et celui ou il lance l import, le
        formulaire peut avoir ete redeploye avec des questions renommees ou
        supprimees ; les soumissions partiraient alors dans le vide.
        """
        if not self.kpi_base_url:
            raise KoboError("Adresse du serveur KoboToolbox non renseignee.")
        if not uid:
            raise KoboError("Aucun formulaire selectionne.")

        url = f"{self.kpi_base_url}{ASSET_PATH.format(uid=uid)}?fields={STATUS_FIELDS}"
        payload = self._get_json(url)
        return {
            "uid": str(payload.get("uid") or "").strip(),
            "title": str(payload.get("name") or "").strip(),
            "version": str(
                payload.get("deployed_version_id") or payload.get("version_id") or ""
            ).strip(),
            "deployed": bool(
                payload.get("has_deployment") and payload.get("deployment__active")
            ),
            "submissions": int(payload.get("deployment__submission_count") or 0),
        }

    def get_schema(self, uid):
        from . import schema as schema_mod
        return schema_mod.parse_asset(self.get_asset(uid))

    def _get_json(self, url):
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.exceptions.SSLError as exc:
            raise KoboError(f"Certificat du serveur refuse :\n{exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise KoboError(
                "Serveur injoignable. Verifiez l'adresse saisie et votre connexion Internet."
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise KoboError("Le serveur n'a pas repondu dans le delai imparti.") from exc
        except requests.RequestException as exc:
            raise KoboError(f"Echec de la requete :\n{exc}") from exc

        if response.status_code != 200:
            raise KoboError(humanize_http_error(response.status_code, response.text))

        try:
            return response.json()
        except ValueError as exc:
            raise KoboError(
                "Reponse illisible du serveur. L'adresse pointe peut-etre vers une "
                "page de connexion plutot que vers l'API KoboToolbox."
            ) from exc

    # -- test de connexion -------------------------------------------------

    def test_connection(self, asset_uid=""):
        """Retourne (succes, message). Verifie le compte puis le formulaire."""
        if not self.token:
            return False, "Aucun token API renseigne."
        try:
            payload = self._get_json(f"{self.kpi_base_url}/api/v2/assets.json?limit=1")
        except KoboError as exc:
            return False, str(exc)

        count = payload.get("count", 0) if isinstance(payload, dict) else 0
        message = f"Connexion reussie. {count} formulaire(s) accessible(s) sur ce compte."

        if asset_uid:
            try:
                asset = self.get_asset(asset_uid)
            except KoboError as exc:
                return False, f"Compte valide mais formulaire inaccessible :\n{exc}"
            if not (asset.get("has_deployment") and asset.get("deployment__active")):
                return True, message + "\n\nAttention : ce formulaire n'est pas deploye. " \
                                       "Les envois seront refuses tant qu'il ne l'est pas."
            message += f"\nFormulaire « {asset.get('name')} » accessible et deploye."

        return True, message

    # -- points 4 et 20 : envoi --------------------------------------------

    def submission_url(self, base=None):
        return f"{(base or self._active_submission_base).rstrip('/')}{SUBMISSION_PATH}"

    def _switch_to_fallback(self):
        """Bascule une seule fois vers l'adresse kc.* si la principale ne recoit pas."""
        with self._endpoint_lock:
            if self.fallback_submission_base_url and \
                    self._active_submission_base != self.fallback_submission_base_url:
                self._active_submission_base = self.fallback_submission_base_url
                return True
            return False

    @staticmethod
    def _retry_delay(attempt, response=None):
        if response is not None:
            header = response.headers.get("Retry-After")
            if header:
                try:
                    return min(BACKOFF_CAP, max(1.0, float(header)))
                except (TypeError, ValueError):
                    pass
        return min(BACKOFF_CAP, BACKOFF_BASE ** attempt)

    def submit(self, xml_bytes, filename="submission.xml", stop_event=None):
        """Envoie une soumission deja serialisee en memoire (point 16)."""
        last = SubmitResult(status=NETWORK, message="Aucune tentative effectuee.")

        for attempt in range(1, self.max_attempts + 1):
            if stop_event is not None and stop_event.is_set():
                return SubmitResult(status=STOPPED, message="Arret demande.", attempts=attempt - 1)

            try:
                response = self.session.post(
                    self.submission_url(),
                    files={"xml_submission_file": (filename, xml_bytes, "text/xml")},
                    timeout=self.timeout,
                )
            except requests.exceptions.RequestException as exc:
                last = SubmitResult(
                    status=NETWORK,
                    message=f"Probleme reseau : {exc.__class__.__name__}. "
                            "Verifiez la connexion Internet.",
                    attempts=attempt,
                )
                if attempt < self.max_attempts and not self._sleep(self._retry_delay(attempt), stop_event):
                    return SubmitResult(status=STOPPED, message="Arret demande.", attempts=attempt)
                continue

            category = _status_category(response.status_code)

            if category in SENT_STATUSES:
                message = "Soumission enregistree." if category == SUCCESS else \
                    "Soumission deja presente sur le serveur (doublon ignore)."
                return SubmitResult(
                    status=category,
                    http_status=response.status_code,
                    message=message,
                    attempts=attempt,
                )

            message = humanize_http_error(response.status_code, response.text)

            # Une adresse d'envoi erronee se manifeste par un 404 (inconnue) ou
            # un 410 (supprimee) : on tente l'autre hote connu (kf.* <-> kc.*)
            # avant de conclure.
            if response.status_code in (404, 410) and self._switch_to_fallback():
                last = SubmitResult(
                    status=_status_category(response.status_code),
                    http_status=response.status_code,
                    message=message,
                    attempts=attempt,
                )
                continue

            if response.status_code in NO_RETRY_CODES:
                return SubmitResult(
                    status=category,
                    http_status=response.status_code,
                    message=message,
                    attempts=attempt,
                )

            last = SubmitResult(
                status=category,
                http_status=response.status_code,
                message=message,
                attempts=attempt,
            )
            if attempt < self.max_attempts:
                delay = self._retry_delay(attempt, response)
                if not self._sleep(delay, stop_event):
                    return SubmitResult(status=STOPPED, message="Arret demande.", attempts=attempt)

        return last

    @staticmethod
    def _sleep(seconds, stop_event):
        """Attente interruptible : retourne False si un arret a ete demande."""
        if stop_event is None:
            time.sleep(seconds)
            return True
        return not stop_event.wait(seconds)

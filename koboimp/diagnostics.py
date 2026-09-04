"""Point 2 : diagnostic de l'installation et du serveur.

Motivation concrete : la suppression de l'API v1 par KoboToolbox a fait echouer
tous les envois avec un message illisible (« reponse inattendue du serveur »).
Il a fallu sonder les adresses a la main pour comprendre. Cet ecran met ce
sondage a portee de l'utilisateur : il repond en quelques secondes a « d'ou
vient le probleme ? » sans qu'il ait a lancer un import pour le decouvrir.

Chaque controle est independant : un echec n'interrompt pas la serie, car
c'est justement la combinaison des resultats qui situe la panne.
"""

import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from . import __version__
from . import config as config_mod
from . import kobo_api, paths

OK = "ok"
WARN = "warning"
FAIL = "error"
INFO = "info"

# Ecart d'horloge au-dela duquel TLS commence a echouer sur certains serveurs.
CLOCK_TOLERANCE_SECONDS = 300

PROBE_TIMEOUT = 15


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    hint: str = ""
    elapsed: float = 0.0

    @property
    def failed(self):
        return self.status == FAIL


@dataclass
class DiagnosticReport:
    results: list = field(default_factory=list)
    started_at: str = ""

    def add(self, result):
        self.results.append(result)
        return result

    @property
    def failures(self):
        return [item for item in self.results if item.status == FAIL]

    @property
    def warnings(self):
        return [item for item in self.results if item.status == WARN]

    def verdict(self):
        if self.failures:
            return FAIL, f"{len(self.failures)} probleme(s) bloquant(s) detecte(s)."
        if self.warnings:
            return WARN, f"Fonctionnel, avec {len(self.warnings)} point(s) de vigilance."
        return OK, "Tout est operationnel."

    def as_text(self):
        """Rapport copiable, a joindre a une demande d'assistance."""
        symbols = {OK: "[ OK ]", WARN: "[ ! ]", FAIL: "[FAIL]", INFO: "[ i ]"}
        lines = [
            f"Diagnostic Kobo Importer {__version__} - {self.started_at}",
            "=" * 64,
        ]
        for item in self.results:
            lines.append(f"{symbols.get(item.status, '[ ? ]')} {item.name}")
            if item.detail:
                lines.append(f"        {item.detail}")
            if item.hint and item.status in (WARN, FAIL):
                lines.append(f"        -> {item.hint}")
        status, summary = self.verdict()
        lines.append("=" * 64)
        lines.append(summary)
        return "\n".join(lines)


def _timed(function):
    start = time.monotonic()
    try:
        return function(), time.monotonic() - start
    except Exception as exc:  # noqa: BLE001 - converti en resultat de controle
        return exc, time.monotonic() - start


# --------------------------------------------------------------------------
# Controles
# --------------------------------------------------------------------------

def check_storage():
    """Le dossier de donnees est-il reellement inscriptible ?"""
    directory = paths.data_dir()
    probe = os.path.join(directory, ".ecriture_test")
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
    except OSError as exc:
        return CheckResult(
            "Dossier de donnees", FAIL, f"{directory} : {exc}",
            "Verifiez que votre profil Windows n'est pas en lecture seule, "
            "ou que l'antivirus ne bloque pas ce dossier.",
        )
    return CheckResult("Dossier de donnees", OK, directory)


def check_configuration(config):
    server = config_mod.normalize_base_url(config.get("server_base_url"))
    token = config_mod.get_token(config)

    if not server:
        return CheckResult(
            "Configuration", FAIL, "Aucune adresse de serveur.",
            "Renseignez-la a l'etape Connexion.",
        )
    if not token:
        return CheckResult(
            "Configuration", FAIL, f"Serveur {server}, aucun jeton d'acces.",
            "Collez votre jeton a l'etape Connexion.",
        )
    kpi, submission, fallback = config_mod.resolved_endpoints(config)
    detail = f"Serveur {server} | API {kpi} | Envois {submission}"
    if fallback:
        detail += f" (repli {fallback})"
    return CheckResult("Configuration", OK, detail)


def check_network(config):
    """Resolution DNS : distingue une panne Internet d'une adresse fautive."""
    kpi, _submission, _fallback = config_mod.resolved_endpoints(config)
    host = urlparse(kpi).hostname
    if not host:
        return CheckResult("Reseau", FAIL, "Adresse de serveur illisible.",
                           "Verifiez l'adresse saisie a l'etape Connexion.")

    outcome, elapsed = _timed(lambda: socket.getaddrinfo(host, 443))
    if isinstance(outcome, Exception):
        return CheckResult(
            "Reseau", FAIL, f"Nom « {host} » non resolu : {outcome}",
            "Soit vous n'avez pas de connexion Internet, soit l'adresse du "
            "serveur comporte une faute de frappe.",
            elapsed,
        )
    addresses = sorted({item[4][0] for item in outcome})
    return CheckResult("Reseau", OK, f"{host} -> {', '.join(addresses[:3])}", "", elapsed)


def check_clock(config):
    """Une horloge decalee fait echouer TLS avec un message trompeur."""
    kpi, _s, _f = config_mod.resolved_endpoints(config)
    try:
        response = requests.head(kpi, timeout=PROBE_TIMEOUT)
        server_date = response.headers.get("Date")
    except requests.RequestException:
        return CheckResult("Horloge du poste", INFO,
                           "Non verifiable : serveur injoignable.")
    if not server_date:
        return CheckResult("Horloge du poste", INFO, "Le serveur n'annonce pas d'heure.")

    try:
        reference = parsedate_to_datetime(server_date)
    except (TypeError, ValueError):
        return CheckResult("Horloge du poste", INFO, "Heure du serveur illisible.")

    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    drift = abs((datetime.now(timezone.utc) - reference).total_seconds())

    if drift > CLOCK_TOLERANCE_SECONDS:
        return CheckResult(
            "Horloge du poste", WARN,
            f"Ecart de {int(drift // 60)} minute(s) avec le serveur.",
            "Un decalage important provoque des erreurs de certificat. "
            "Activez la synchronisation automatique de l'heure dans Windows.",
        )
    return CheckResult("Horloge du poste", OK, f"Ecart de {int(drift)} seconde(s).")


def check_api(client):
    """Le compte est-il valide et l'API v2 accessible ?"""
    outcome, elapsed = _timed(lambda: client.list_forms())
    if isinstance(outcome, Exception):
        return CheckResult(
            "API KoboToolbox (formulaires)", FAIL, str(outcome),
            "Si le message parle de jeton, regenerez-le dans votre compte "
            "KoboToolbox. S'il parle d'adresse, verifiez l'etape Connexion.",
            elapsed,
        )
    deployed = sum(1 for item in outcome if item.get("deployed"))
    return CheckResult(
        "API KoboToolbox (formulaires)", OK,
        f"{len(outcome)} formulaire(s) accessible(s), dont {deployed} deploye(s).",
        "", elapsed,
    )


def check_submission_endpoint(client):
    """Sonde l'adresse d'envoi sans rien soumettre.

    C'est le controle qui aurait immediatement revele la suppression de
    /api/v1/submissions : une adresse morte repond 404 ou 410, une adresse
    vivante repond 204 (OpenRosa) ou 401 si le jeton n'est pas accepte.
    """
    url = client.submission_url()

    def probe():
        return client.session.head(url, timeout=PROBE_TIMEOUT)

    outcome, elapsed = _timed(probe)
    if isinstance(outcome, Exception):
        return CheckResult(
            "Adresse d'envoi des donnees", FAIL, f"{url} : {outcome}",
            "Le serveur est injoignable sur cette adresse.", elapsed,
        )

    code = outcome.status_code
    if code in (404, 410):
        return CheckResult(
            "Adresse d'envoi des donnees", FAIL,
            f"{url} repond {code} : cette adresse n'existe plus.",
            "KoboToolbox a probablement modifie son API. Mettez a jour "
            "Kobo Importer, ou corrigez l'adresse dans Parametres avances.",
            elapsed,
        )
    if code in (200, 204):
        return CheckResult("Adresse d'envoi des donnees", OK,
                           f"{url} repond {code} (OpenRosa).", "", elapsed)
    if code in (401, 403):
        return CheckResult(
            "Adresse d'envoi des donnees", WARN,
            f"{url} repond {code} sur une requete de sondage.",
            "L'adresse existe. Ce code peut etre normal pour un sondage sans "
            "donnees ; si les envois echouent aussi, verifiez le jeton.",
            elapsed,
        )
    if code == 405:
        return CheckResult("Adresse d'envoi des donnees", OK,
                           f"{url} existe (sondage refuse, envoi possible).", "", elapsed)
    return CheckResult(
        "Adresse d'envoi des donnees", WARN, f"{url} repond {code}.",
        "Code inattendu : lancez un import en simulation pour confirmer.", elapsed,
    )


def check_form(client, config):
    uid = config.get("asset_uid")
    if not uid:
        return CheckResult("Formulaire selectionne", INFO, "Aucun formulaire choisi.")

    outcome, elapsed = _timed(lambda: client.get_form_status(uid))
    if isinstance(outcome, Exception):
        return CheckResult(
            "Formulaire selectionne", FAIL, f"{uid} : {outcome}",
            "Le formulaire a peut-etre ete supprime, ou votre compte a perdu "
            "l'acces. Rechoisissez-le a l'etape Formulaire.",
            elapsed,
        )

    detail = (f"« {outcome['title']} » ({outcome['uid']}) version "
              f"{outcome['version'] or 'inconnue'}, "
              f"{outcome['submissions']} soumission(s)")
    if not outcome["deployed"]:
        return CheckResult(
            "Formulaire selectionne", FAIL, detail + " - NON DEPLOYE",
            "Un formulaire non deploye refuse tous les envois. Deployez-le "
            "depuis l'interface web de KoboToolbox.",
            elapsed,
        )

    known = str(config.get("form_version") or "")
    if known and outcome["version"] and known != outcome["version"]:
        return CheckResult(
            "Formulaire selectionne", WARN,
            detail + f" - votre copie locale est en version {known}",
            "Le formulaire a ete redeploye. Rechargez-le a l'etape Formulaire "
            "puis revalidez votre fichier.",
            elapsed,
        )
    return CheckResult("Formulaire selectionne", OK, detail, "", elapsed)


def check_registry():
    from . import registry as registry_mod
    try:
        store = registry_mod.Registry(paths.REGISTRY_FILE)
        try:
            runs = store.recent_runs(limit=1)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - converti en resultat de controle
        return CheckResult(
            "Historique des imports", FAIL, str(exc),
            "Le fichier registry.db est peut-etre verrouille par une autre "
            "instance de l'application, ou endommage.",
        )
    dernier = runs[0]["started_at"] if runs else "aucun import enregistre"
    return CheckResult("Historique des imports", OK,
                       f"{paths.REGISTRY_FILE} (dernier : {dernier})")


# --------------------------------------------------------------------------
# Enchainement
# --------------------------------------------------------------------------

def run_diagnostic(config, client_factory=None, progress=None):
    """Execute la serie complete et retourne un DiagnosticReport.

    progress(nom_du_controle) permet a l'interface d'afficher l'avancement :
    les sondages reseau peuvent prendre plusieurs secondes.
    """
    report = DiagnosticReport(started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    announce = progress or (lambda _name: None)

    announce("Dossier de donnees")
    report.add(check_storage())
    announce("Historique des imports")
    report.add(check_registry())
    announce("Configuration")
    configuration = report.add(check_configuration(config))

    if configuration.failed:
        # Sans adresse ni jeton, tous les controles reseau echoueraient pour la
        # meme raison : mieux vaut une conclusion nette qu'une avalanche.
        return report

    announce("Reseau")
    network = report.add(check_network(config))
    if network.failed:
        return report

    announce("Horloge du poste")
    report.add(check_clock(config))

    client = None
    if client_factory is not None:
        try:
            client = client_factory()
        except Exception as exc:  # noqa: BLE001
            report.add(CheckResult("Client HTTP", FAIL, str(exc)))
            return report
    else:
        kpi, submission, fallback = config_mod.resolved_endpoints(config)
        client = kobo_api.KoboClient(
            token=config_mod.get_token(config),
            kpi_base_url=kpi,
            submission_base_url=submission,
            fallback_submission_base_url=fallback,
            timeout=PROBE_TIMEOUT,
            max_attempts=1,
        )

    announce("API KoboToolbox")
    report.add(check_api(client))
    announce("Adresse d'envoi")
    report.add(check_submission_endpoint(client))
    announce("Formulaire")
    report.add(check_form(client, config))
    return report

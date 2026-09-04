"""Moteur d'import.

Points couverts :
  16 - le XML est construit en memoire ; seul un echec est ecrit sur disque
       (auparavant : une ecriture, une relecture et un deplacement par ligne) ;
  17 - le journal CSV utilise un seul descripteur ouvert et csv.writer, au lieu
       d'un DataFrame pandas cree et d'un fichier rouvert a chaque ligne ;
  18 - les notifications vers l'interface sont regroupees et limitees en debit ;
  19 - envoi par lots et annulation effective des taches en attente ;
  21 - chaque ligne passe par le registre (instanceID stable, reprise fiable) ;
  24 - la validation unitaire remplace le validate_row qui retournait toujours True.
"""

import csv
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

from . import excel, kobo_api, registry as registry_mod, validation, xmlbuild

# Regroupement des notifications vers l'interface (point 18).
PROGRESS_INTERVAL = 0.15      # secondes entre deux rafraichissements
LOG_MILESTONE = 250           # une ligne de journal tous les N envois reussis
REGISTRY_FLUSH = 100          # ecritures registre / journal groupees


class FormVersionChanged(Exception):
    """Point 11 : le formulaire a ete redeploye depuis la verification.

    Envoyer malgre tout produirait des refus en masse ou, pire, des soumissions
    silencieusement amputees des questions renommees.
    """

    def __init__(self, expected, current, title=""):
        self.expected = expected
        self.current = current
        self.title = title
        super().__init__(
            f"Le formulaire « {title} » a ete redeploye depuis votre verification "
            f"(version {expected or 'inconnue'} puis {current or 'inconnue'}).\n\n"
            "Revenez a l etape Formulaire pour le recharger, puis revalidez votre "
            "fichier : des questions ont pu etre renommees, ajoutees ou supprimees."
        )


@dataclass
class RunResult:
    total: int = 0
    selected: int = 0
    processed: int = 0
    sent: int = 0
    duplicates: int = 0
    failed: int = 0
    invalid: int = 0
    skipped: int = 0
    stopped: bool = False
    dry_run: bool = False
    elapsed: float = 0.0
    report_path: str = ""
    failures: list = field(default_factory=list)

    @property
    def rate(self):
        return self.processed / self.elapsed if self.elapsed > 0 else 0.0

    def summary_text(self):
        if self.dry_run:
            return (f"Simulation : {self.processed} ligne(s) generee(s), "
                    f"{self.invalid} refusee(s) au controle.")
        parts = [f"{self.sent} envoyee(s)"]
        if self.duplicates:
            parts.append(f"{self.duplicates} deja presente(s)")
        if self.failed:
            parts.append(f"{self.failed} en echec")
        if self.invalid:
            parts.append(f"{self.invalid} invalide(s)")
        if self.skipped:
            parts.append(f"{self.skipped} ignoree(s)")
        return " | ".join(parts)


@dataclass
class _Outcome:
    position: int
    status: str
    http_status: int = None
    message: str = ""


class _CsvLog:
    """Point 17 : un seul fichier ouvert pour toute l'execution.

    Separateur point-virgule et BOM UTF-8 : le fichier s'ouvre correctement d'un
    double-clic dans un Excel configure en francais.

    Si le journal n'est pas ouvrable (fichier deja ouvert dans Excel, dossier en
    lecture seule), l'objet se met en sommeil : perdre la trace ecrite ne doit
    pas empecher l'import lui-meme.
    """

    HEADER = ["horodatage", "ligne_excel", "statut", "code_http", "message"]

    def __init__(self, path):
        self.path = path
        self.error = ""
        self._handle = None
        self._writer = None

        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            is_new = not os.path.exists(path) or os.path.getsize(path) == 0
            self._handle = open(path, "a", newline="", encoding="utf-8-sig")
            self._writer = csv.writer(self._handle, delimiter=";")
            if is_new:
                self._writer.writerow(self.HEADER)
        except (OSError, ValueError) as exc:
            # ValueError couvre les chemins malformes (caractere interdit) que
            # l'utilisateur peut saisir dans les parametres avances.
            self.error = str(exc)
            self._handle = None
            self._writer = None

    @property
    def active(self):
        return self._writer is not None

    def write_many(self, rows):
        if self._writer is not None:
            self._writer.writerows(rows)

    def flush(self):
        if self._handle is not None:
            self._handle.flush()

    def close(self):
        if self._handle is None:
            return
        try:
            self._handle.flush()
        finally:
            self._handle.close()
            self._handle = None
            self._writer = None


class ImportEngine:
    def __init__(
        self,
        config,
        dataframe,
        form_schema,
        column_statuses,
        source_id,
        source_name,
        client,
        registry,
        progress_callback=None,
        log_callback=None,
        stop_event=None,
        validation_report=None,
        verify_form_version=True,
    ):
        self.config = config
        self.dataframe = dataframe
        self.schema = form_schema
        self.source_id = source_id
        self.source_name = source_name
        self.client = client
        self.registry = registry
        self.stop_event = stop_event
        self.validation_report = validation_report
        self.verify_form_version = verify_form_version

        self._progress = progress_callback or (lambda *args, **kwargs: None)
        self._log = log_callback or (lambda *args, **kwargs: None)

        # Colonnes retenues : seules celles reconnues par le formulaire (point 10).
        mapped = [status for status in column_statuses if status.is_mapped]
        if not mapped:
            raise ValueError("Aucune colonne du fichier ne correspond au formulaire.")
        self._columns = [status.column for status in mapped]
        self._paths = [status.path for status in mapped]
        self._types = {status.path: status.question.type for status in mapped}

        # Selection par position : un tableau peut contenir deux en-tetes
        # devenus identiques apres nettoyage, que la selection par nom
        # confondrait silencieusement.
        positions = [status.index for status in mapped]
        self._values = list(
            dataframe.iloc[:, positions].itertuples(index=False, name=None)
        )

        self.row_keys = registry_mod.compute_row_keys(dataframe, source_id)
        self.states = {}
        self._last_progress = 0.0
        self._started = time.monotonic()

    # -- boucle principale -------------------------------------------------

    def run(self):
        started = self._started = time.monotonic()
        config = self.config
        dry_run = bool(config.get("dry_run"))
        result = RunResult(total=len(self.dataframe), dry_run=dry_run)

        self._log(f"Fichier : {self.source_name} ({result.total} ligne(s))")
        self._check_form_unchanged()

        self.states = self.registry.register_rows(
            self.source_id, self.source_name, self.schema.uid, self.row_keys
        )
        selected, skipped = registry_mod.select_rows_to_send(
            self.states, self.row_keys, config.get("resume_mode", "new")
        )
        result.selected = len(selected)
        result.skipped = skipped

        if skipped:
            self._log(f"{skipped} ligne(s) deja envoyee(s) precedemment : ignoree(s).", "info")
        if not selected:
            self._log("Rien a envoyer : tout le fichier est deja passe.", "success")
            result.elapsed = time.monotonic() - started
            self._emit_progress(result, force=True)
            return result

        run_id = self.registry.start_run(
            self.source_id, self.source_name, self.schema.uid, self.schema.title,
            result.selected, dry_run,
        )

        journal = _CsvLog(config["log_file"])
        if not journal.active:
            self._log(
                f"Journal CSV indisponible ({journal.error}) : l'import continue, "
                "seul le fichier de trace ne sera pas ecrit.",
                "warning",
            )
        pending_registry = []
        pending_journal = []
        workers = max(1, int(config.get("max_workers", 5)))
        batch_size = max(32, workers * 16)

        mode = "Simulation (aucun envoi)" if dry_run else f"Envoi avec {workers} connexion(s) simultanee(s)"
        self._log(f"{mode} - {result.selected} ligne(s) a traiter.")

        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kobo")
        try:
            for start in range(0, len(selected), batch_size):
                if self._stopped():
                    break

                batch = selected[start:start + batch_size]
                futures = {executor.submit(self._process_row, position): position for position in batch}

                for future in as_completed(futures):
                    outcome = future.result()
                    self._record(outcome, result, pending_registry, pending_journal)

                    if len(pending_registry) >= REGISTRY_FLUSH:
                        self._flush(pending_registry, pending_journal, journal)

                    self._emit_progress(result)

                if self._stopped():
                    for future in futures:
                        future.cancel()
                    break

            result.stopped = self._stopped()
        finally:
            # Point 19 : les taches non demarrees sont annulees au lieu d'etre
            # attendues une par une.
            executor.shutdown(wait=not self._stopped(), cancel_futures=True)
            self._flush(pending_registry, pending_journal, journal)
            journal.close()

        result.elapsed = time.monotonic() - started
        self.registry.finish_run(run_id, result.sent + result.duplicates,
                                 result.failed + result.invalid, result.skipped, result.stopped)

        if result.stopped:
            self._log("Import interrompu a la demande de l'utilisateur.", "warning")

        result.report_path = self._write_report(result)
        self._emit_progress(result, force=True)
        return result

    def _check_form_unchanged(self):
        """Point 11 : refuse de partir si le formulaire n est plus le meme."""
        if not self.verify_form_version:
            return
        probe = getattr(self.client, "get_form_status", None)
        if probe is None:
            return
        try:
            status = probe(self.schema.uid)
        except Exception as exc:  # noqa: BLE001 - un reseau capricieux ne bloque pas
            self._log(f"Version du formulaire non verifiee : {exc}", "warning")
            return

        current = status.get("version") or ""
        if current and self.schema.version and current != self.schema.version:
            raise FormVersionChanged(self.schema.version, current, self.schema.title)

        if not status.get("deployed", True):
            self._log(
                "Attention : ce formulaire n est plus deploye, les envois seront refuses.",
                "warning",
            )

    # -- traitement d'une ligne -------------------------------------------

    def _process_row(self, position):
        if self._stopped():
            return _Outcome(position, registry_mod.STOPPED, message="Arret demande.")

        try:
            values = dict(zip(self._paths, self._values[position]))

            # Point 24 : dernier rempart si l'utilisateur a saute la verification.
            acceptable, reason = validation.validate_row_values(values, self.schema)
            if not acceptable:
                return _Outcome(position, registry_mod.INVALID, message=reason)

            pairs = xmlbuild.row_to_pairs(values, self._types)
            if not pairs:
                return _Outcome(position, registry_mod.INVALID, message="Ligne entierement vide.")

            instance_id = self.states[self.row_keys[position]]["instance_id"]
            payload, _ = xmlbuild.build_submission_xml(
                pairs,
                root_name=self.schema.uid,
                form_version=self.schema.version,
                instance_id=instance_id,
            )

            if self.config.get("dry_run"):
                self._dump_xml(position, payload, prefix="simulation")
                return _Outcome(position, registry_mod.DRY_RUN, message="XML genere, aucun envoi.")

            outcome = self.client.submit(
                payload,
                filename=f"ligne_{position + 2}.xml",
                stop_event=self.stop_event,
            )

            # Point 16 : on ne conserve sur disque que ce qui a echoue, pour
            # permettre un diagnostic. Le cas nominal ne touche pas le disque.
            if not outcome.sent and outcome.status != kobo_api.STOPPED:
                self._dump_xml(position, payload, prefix="echec")

            return _Outcome(position, outcome.status, outcome.http_status, outcome.message)

        except Exception as exc:  # noqa: BLE001 - une ligne fautive ne doit pas tuer l'import
            return _Outcome(position, "ERROR", message=f"{exc.__class__.__name__} : {exc}")

    def _dump_xml(self, position, payload, prefix):
        directory = self.config.get("output_dir")
        if not directory:
            return
        try:
            os.makedirs(directory, exist_ok=True)
            target = os.path.join(directory, f"{prefix}_ligne_{position + 2}.xml")
            with open(target, "wb") as handle:
                handle.write(payload)
        except OSError:
            pass  # l'echec d'ecriture du diagnostic ne doit pas masquer l'erreur reelle

    # -- comptabilite ------------------------------------------------------

    def _record(self, outcome, result, pending_registry, pending_journal):
        status = outcome.status

        # Une ligne interrompue avant son envoi n'a pas ete traitee : elle ne
        # doit ni compter dans la progression, ni laisser de trace, afin d'etre
        # reprise telle quelle a la prochaine execution.
        if status == registry_mod.STOPPED:
            return

        result.processed += 1

        if status == kobo_api.SUCCESS:
            result.sent += 1
        elif status == kobo_api.DUPLICATE:
            result.duplicates += 1
        elif status == registry_mod.INVALID:
            result.invalid += 1
        elif status == registry_mod.DRY_RUN:
            pass
        else:
            result.failed += 1

        if status not in (kobo_api.SUCCESS, kobo_api.DUPLICATE, registry_mod.DRY_RUN):
            result.failures.append({
                "row_index": outcome.position,
                "status": status,
                "http_status": outcome.http_status,
                "message": outcome.message,
            })
            self._log(f"Ligne {outcome.position + 2} : {outcome.message}", "error")
        elif result.processed % LOG_MILESTONE == 0:
            # Point 18 : une trace de progression regulierement, pas une par ligne.
            self._log(f"{result.processed} ligne(s) traitee(s)...", "info")

        # Une simulation ne laisse aucune trace dans l'historique : sinon, un
        # essai a blanc lance apres un import reussi remplacerait le statut
        # « envoyee » par « simulee », et l'envoi suivant creerait un doublon.
        if status != registry_mod.DRY_RUN:
            pending_registry.append((self.row_keys[outcome.position], status,
                                     outcome.http_status, outcome.message))

        pending_journal.append([
            datetime.now().isoformat(timespec="seconds"),
            outcome.position + 2,
            status,
            outcome.http_status or "",
            (outcome.message or "").replace("\n", " ")[:500],
        ])

    def _flush(self, pending_registry, pending_journal, journal):
        if pending_registry:
            self.registry.mark_many(pending_registry)
            pending_registry.clear()
        if pending_journal:
            journal.write_many(pending_journal)
            journal.flush()
            pending_journal.clear()

    def _emit_progress(self, result, force=False):
        """Point 18 : au plus quelques rafraichissements par seconde."""
        now = time.monotonic()
        if not force and (now - self._last_progress) < PROGRESS_INTERVAL:
            return
        self._last_progress = now
        result.elapsed = now - self._started
        self._progress(result)

    def _stopped(self):
        return self.stop_event is not None and self.stop_event.is_set()

    # -- point 11 : rapport d'erreurs --------------------------------------

    def _write_report(self, result):
        if not result.failures:
            return ""
        directory = self.config.get("report_dir")
        if not directory:
            return ""
        try:
            os.makedirs(directory, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = os.path.splitext(os.path.basename(self.source_name))[0][:40]
            target = os.path.join(directory, f"a_corriger_{base}_{stamp}.xlsx")
            excel.write_error_report(
                target,
                self.dataframe,
                result.failures,
                self.validation_report,
                context={
                    "Fichier source": self.source_name,
                    "Formulaire": f"{self.schema.title} ({self.schema.uid})",
                    "Version du formulaire": self.schema.version,
                    "Date de l'import": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Lignes envoyees": result.sent + result.duplicates,
                    "Lignes en echec": result.failed + result.invalid,
                },
            )
            self._log(f"Rapport des lignes a corriger : {target}", "warning")
            return target
        except Exception as exc:  # noqa: BLE001 - le rapport est un bonus
            self._log(f"Rapport non genere : {exc}", "warning")
            return ""

"""Point 12 : pilotage en ligne de commande.

Le moteur ne recoit de l'interface que deux fonctions de rappel : il est donc
utilisable tel quel sans fenetre. Cela ouvre l'import planifie (tache Windows
nocturne), la verification d'un fichier dans une chaine de traitement, et le
diagnostic a distance sans faire manipuler l'interface a l'utilisateur.

Exemples :
    KoboImporter.exe --diagnostic
    KoboImporter.exe --list-forms
    KoboImporter.exe --check donnees.xlsx --form aBcDeF123
    KoboImporter.exe --import donnees.csv --form aBcDeF123 --mode new
    KoboImporter.exe --import donnees.xlsx --form aBcDeF123 --dry-run --profile "Projet B"
"""

import argparse
import os
import sys
import threading

from . import __version__
from . import config as config_mod
from . import diagnostics, engine as engine_mod, excel, kobo_api
from . import paths, profiles, registry as registry_mod, updates, validation

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------

def attach_console():
    """Rattache la sortie a la console appelante.

    L'executable est construit en mode fenetre (console=False) pour ne pas
    afficher de terminal noir au double-clic. Lance depuis cmd ou PowerShell,
    il n'a donc aucune sortie : on se rattache a la console du parent, sinon
    l'utilisateur ne verrait rien du tout.
    """
    if sys.platform != "win32" or not paths.is_frozen():
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return
        for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
            if stream is None or stream.fileno() < 0:
                setattr(sys, name, open("CONOUT$", "w", encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - sans console, on ecrira dans --log
        pass


class Reporter:
    """Sortie texte, avec copie facultative dans un fichier."""

    def __init__(self, log_path="", quiet=False):
        self.quiet = quiet
        self._handle = None
        if log_path:
            try:
                parent = os.path.dirname(log_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                self._handle = open(log_path, "a", encoding="utf-8")
            except OSError as exc:
                self.write(f"Journal non ouvrable ({exc}), sortie console uniquement.")

    def write(self, message=""):
        text = str(message)
        if not self.quiet:
            try:
                print(text, flush=True)
            except (OSError, ValueError):
                pass
        if self._handle is not None:
            self._handle.write(text + "\n")
            self._handle.flush()

    def close(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# --------------------------------------------------------------------------
# Preparation commune
# --------------------------------------------------------------------------

def _load_config(profile_name, reporter):
    if profile_name:
        if not profiles.exists(profile_name):
            disponibles = ", ".join(profiles.list_names())
            reporter.write(f"Profil « {profile_name} » introuvable. Profils : {disponibles}")
            return None
        return config_mod.load_config(profile_name)
    return config_mod.load_config()


def _build_client(config):
    kpi, submission, fallback = config_mod.resolved_endpoints(config)
    return kobo_api.KoboClient(
        token=config_mod.get_token(config),
        kpi_base_url=kpi,
        submission_base_url=submission,
        fallback_submission_base_url=fallback,
        timeout=config.get("request_timeout", 30),
        max_attempts=config.get("max_attempts", 4),
        pool_size=max(4, int(config.get("max_workers", 5)) + 2),
    )


def _prepare(args, config, reporter):
    """Charge formulaire, fichier, correspondance et controle. Retourne un tuple
    (client, schema, frame, statuses, report, signature) ou None."""
    client = _build_client(config)

    uid = args.form or config.get("asset_uid")
    if not uid:
        reporter.write("Aucun formulaire indique. Utilisez --form UID ou --list-forms.")
        return None

    reporter.write(f"Lecture du formulaire {uid}...")
    try:
        form_schema = client.get_schema(uid)
    except kobo_api.KoboError as exc:
        reporter.write(f"Formulaire inaccessible : {exc}")
        return None
    reporter.write(f"  « {form_schema.title} » version {form_schema.version or 'inconnue'}"
                   f"{'' if form_schema.deployed else '  [NON DEPLOYE]'}")

    path = args.file
    if not os.path.exists(path):
        reporter.write(f"Fichier introuvable : {path}")
        return None

    reporter.write(f"Lecture de {os.path.basename(path)}...")
    try:
        frame, sheet = excel.read_table(path, args.sheet)
        signature = excel.file_signature(path)
    except excel.ExcelError as exc:
        reporter.write(str(exc))
        return None
    reporter.write(f"  {len(frame)} ligne(s), {len(frame.columns)} colonne(s)"
                   + (f", feuille « {sheet} »" if sheet else ""))

    # Correspondance manuelle enregistree depuis l'interface (point 3).
    store = registry_mod.Registry(paths.REGISTRY_FILE)
    try:
        overrides = store.load_mapping(form_schema.uid)
    finally:
        store.close()
    if overrides:
        reporter.write(f"  {len(overrides)} correspondance(s) manuelle(s) appliquee(s).")

    statuses = validation.map_columns(frame.columns, form_schema, overrides)
    report = validation.validate_dataframe(frame, form_schema, overrides=overrides)
    return client, form_schema, frame, statuses, report, signature, sheet


def _print_report(report, reporter, limit=25):
    reporter.write("")
    reporter.write(f"Controle : {report.headline()}")
    reporter.write(f"  colonnes reconnues : {len(report.mapped_columns)}"
                   f" / ignorees : {len(report.unmapped_columns)}")
    for warning in report.warnings:
        reporter.write(f"  ! {warning}")
    for question in report.missing_required:
        reporter.write(f"  MANQUANT : question obligatoire « {question.path} » absente du fichier")
    if report.row_issues:
        reporter.write(f"  {report.issue_count} valeur(s) a corriger :")
        for issue in report.row_issues[:limit]:
            reporter.write(f"    ligne {issue.row_number} - {issue.column} : {issue.message}")
        if report.issue_count > limit:
            reporter.write(f"    ... et {report.issue_count - limit} autre(s)")


# --------------------------------------------------------------------------
# Commandes
# --------------------------------------------------------------------------

def command_diagnostic(args, reporter):
    config = _load_config(args.profile, reporter)
    if config is None:
        return EXIT_USAGE
    report = diagnostics.run_diagnostic(
        config, progress=lambda name: reporter.write(f"  ... {name}") if args.verbose else None
    )
    reporter.write(report.as_text())
    return EXIT_FAILURES if report.failures else EXIT_OK


def command_list_forms(args, reporter):
    config = _load_config(args.profile, reporter)
    if config is None:
        return EXIT_USAGE
    try:
        entries = _build_client(config).list_forms()
    except kobo_api.KoboError as exc:
        reporter.write(f"Echec : {exc}")
        return EXIT_FAILURES

    reporter.write(f"{len(entries)} formulaire(s) :")
    for entry in entries:
        marque = " " if entry["deployed"] else "*"
        reporter.write(f"  {marque} {entry['uid']:24} {entry['submissions']:>7} soum.  {entry['title']}")
    if any(not entry["deployed"] for entry in entries):
        reporter.write("  (* = non deploye : les envois y seront refuses)")
    return EXIT_OK


def command_profiles(args, reporter):
    actif = profiles.active_name()
    reporter.write("Profils enregistres :")
    for name in profiles.list_names():
        reporter.write(f"  {'>' if name == actif else ' '} {name}")
    return EXIT_OK


def command_check(args, reporter):
    config = _load_config(args.profile, reporter)
    if config is None:
        return EXIT_USAGE
    prepared = _prepare(args, config, reporter)
    if prepared is None:
        return EXIT_USAGE

    _client, _schema, _frame, _statuses, report, _signature, _sheet = prepared
    _print_report(report, reporter)
    if report.has_blocking_errors:
        return EXIT_FAILURES
    return EXIT_FAILURES if report.invalid_rows else EXIT_OK


def command_import(args, reporter):
    config = _load_config(args.profile, reporter)
    if config is None:
        return EXIT_USAGE

    if args.mode:
        config["resume_mode"] = args.mode
    if args.workers:
        config["max_workers"] = max(1, min(16, args.workers))
    if args.report_dir:
        config["report_dir"] = args.report_dir
    config["dry_run"] = bool(args.dry_run)

    prepared = _prepare(args, config, reporter)
    if prepared is None:
        return EXIT_USAGE
    client, form_schema, frame, statuses, report, signature, sheet = prepared

    _print_report(report, reporter, limit=10)
    if report.has_blocking_errors:
        reporter.write("")
        reporter.write("Import annule : le fichier ne peut pas etre envoye en l'etat.")
        return EXIT_USAGE
    if not any(status.is_mapped for status in statuses):
        reporter.write("Import annule : aucune colonne ne correspond au formulaire.")
        return EXIT_USAGE

    source_id = f"{signature}:{form_schema.uid}:{sheet or ''}"
    store = registry_mod.Registry(paths.REGISTRY_FILE)
    reporter.write("")
    reporter.write("Envoi en cours..." if not args.dry_run else "Simulation en cours...")

    try:
        worker = engine_mod.ImportEngine(
            config=config,
            dataframe=frame,
            form_schema=form_schema,
            column_statuses=statuses,
            source_id=source_id,
            source_name=os.path.basename(args.file),
            client=client,
            registry=store,
            log_callback=lambda message, level="info": (
                reporter.write(f"  [{level}] {message}")
                if (args.verbose or level in ("error", "warning")) else None
            ),
            stop_event=threading.Event(),
            validation_report=report,
        )
        result = worker.run()
    except engine_mod.FormVersionChanged as exc:
        reporter.write("")
        reporter.write(str(exc))
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - toute panne doit produire un code de sortie
        reporter.write(f"Import interrompu : {exc.__class__.__name__} : {exc}")
        return EXIT_FAILURES
    finally:
        store.close()
        client.close()

    reporter.write("")
    reporter.write("=" * 60)
    reporter.write(result.summary_text())
    reporter.write(f"Duree : {result.elapsed:.0f} s"
                   + (f" ({result.rate:.1f} ligne/s)" if result.rate else ""))
    if result.report_path:
        reporter.write(f"Lignes a corriger : {result.report_path}")
    return EXIT_FAILURES if (result.failed or result.invalid) else EXIT_OK


def command_update(args, reporter):
    config = _load_config(args.profile, reporter)
    if config is None:
        return EXIT_USAGE
    info = updates.check_for_update(updates.resolve_url(config))
    reporter.write(info.headline())
    if info.available and info.url:
        reporter.write(f"Telechargement : {info.url}")
    return EXIT_OK


# --------------------------------------------------------------------------
# Analyse des arguments
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="KoboImporter",
        description="Import de fichiers Excel ou CSV vers KoboToolbox. "
                    "Sans argument, l'interface graphique s'ouvre.",
        epilog="Codes de sortie : 0 succes, 1 echecs rencontres, 2 erreur d'utilisation.",
    )
    parser.add_argument("--version", action="version", version=f"Kobo Importer {__version__}")

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--diagnostic", action="store_true",
                        help="verifie installation, reseau, compte et formulaire")
    action.add_argument("--list-forms", action="store_true",
                        help="liste les formulaires accessibles au compte")
    action.add_argument("--profiles", action="store_true",
                        help="liste les profils de configuration enregistres")
    action.add_argument("--check", metavar="FICHIER",
                        help="controle un fichier sans rien envoyer")
    action.add_argument("--import", dest="import_file", metavar="FICHIER",
                        help="controle puis envoie un fichier")
    action.add_argument("--check-update", action="store_true",
                        help="interroge le manifeste de mise a jour")

    parser.add_argument("--form", metavar="UID", help="formulaire de destination")
    parser.add_argument("--sheet", metavar="NOM", help="feuille du classeur (Excel uniquement)")
    parser.add_argument("--profile", metavar="NOM", help="profil de configuration a utiliser")
    parser.add_argument("--mode", choices=("new", "retry", "force"),
                        help="new : lignes jamais envoyees (defaut) | "
                             "retry : uniquement les echecs | force : tout renvoyer")
    parser.add_argument("--workers", type=int, metavar="N", help="envois simultanes (1-16)")
    parser.add_argument("--dry-run", action="store_true",
                        help="genere les fichiers sans rien envoyer")
    parser.add_argument("--report-dir", metavar="DOSSIER",
                        help="ou deposer le classeur des lignes a corriger")
    parser.add_argument("--log", metavar="FICHIER", help="copie la sortie dans un fichier")
    parser.add_argument("--quiet", action="store_true", help="n'ecrit que dans --log")
    parser.add_argument("--verbose", action="store_true", help="detaille chaque etape")
    return parser


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    attach_console()

    parser = build_parser()
    args = parser.parse_args(arguments)

    # Uniformise : --check et --import portent tous deux un chemin de fichier.
    args.file = args.check or args.import_file

    reporter = Reporter(args.log or "", args.quiet)
    reporter.write(f"Kobo Importer {__version__}")
    try:
        if args.diagnostic:
            return command_diagnostic(args, reporter)
        if args.list_forms:
            return command_list_forms(args, reporter)
        if args.profiles:
            return command_profiles(args, reporter)
        if args.check_update:
            return command_update(args, reporter)
        if args.check:
            return command_check(args, reporter)
        if args.import_file:
            return command_import(args, reporter)
        parser.print_help()
        return EXIT_USAGE
    except KeyboardInterrupt:
        reporter.write("Interrompu par l'utilisateur.")
        return EXIT_USAGE
    finally:
        reporter.close()

"""Fenetre principale : assistant en quatre etapes.

Points couverts :
  12 - un parcours guide remplace les quatre onglets et la barre laterale ;
       tout le reglage fin part dans « Parametres avances » ;
  13 - la fenetre tient sur un ecran d'ordinateur portable 1366 x 768 ;
  14 - les boutons refletent l'etat reel (rien d'actif hors contexte) ;
  18 - les messages venant du moteur sont regroupes avant affichage ;
   2 - diagnostic accessible depuis l'en-tete ;
   3 - correspondance manuelle des colonnes, memorisee par formulaire ;
   8 - historique des imports consultable ;
   9 - profils de configuration nommes ;
  13 - annonce discrete d'une version plus recente.
"""

import dataclasses
import os
import queue
import threading
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from .. import __version__
from .. import config as config_mod
from .. import engine as engine_mod
from .. import kobo_api, paths, profiles, registry as registry_mod, updates
from . import theme
from .dialogs import DiagnosticDialog, HistoryDialog, ProfileDialog
from .steps import APP_TITLE, ConnectionStep, FileStep, FormStep, ImportStep
from .widgets import Card, StepIndicator


# ==========================================================================
# Etat partage entre les etapes
# ==========================================================================

class Session:
    def __init__(self):
        self.config = config_mod.load_config()
        self.registry = registry_mod.Registry(paths.REGISTRY_FILE)

        self.forms = []
        self.schema = None
        self.connection_ok = False

        self.dataframe = None
        self.source_path = ""
        self.source_name = ""
        self.source_id = ""
        self.sheet = ""
        self.column_statuses = []
        self.validation_report = None
        self.column_overrides = {}      # point 3 : colonne -> chemin de question

        self._client = None
        self._client_signature = None

    # -- client HTTP -------------------------------------------------------

    def build_client(self):
        """Recree le client si la connexion a change ; le reutilise sinon,
        pour conserver les connexions HTTP ouvertes (point 15)."""
        kpi, submission, fallback = config_mod.resolved_endpoints(self.config)
        token = config_mod.get_token(self.config)
        signature = (kpi, submission, fallback, token,
                     self.config.get("request_timeout"), self.config.get("max_attempts"),
                     self.config.get("max_workers"))

        if self._client is not None and signature == self._client_signature:
            return self._client

        if self._client is not None:
            self._client.close()

        self._client = kobo_api.KoboClient(
            token=token,
            kpi_base_url=kpi,
            submission_base_url=submission,
            fallback_submission_base_url=fallback,
            timeout=self.config.get("request_timeout", 30),
            max_attempts=self.config.get("max_attempts", 4),
            pool_size=max(4, int(self.config.get("max_workers", 5)) + 2),
        )
        self._client_signature = signature
        return self._client

    # -- formulaire et donnees --------------------------------------------

    def set_schema(self, form_schema):
        self.schema = form_schema
        self.config["asset_uid"] = form_schema.uid
        self.config["form_title"] = form_schema.title
        self.config["form_version"] = form_schema.version
        # La correspondance est rattachee au formulaire : on la recharge des
        # qu'il change (point 3).
        self.column_overrides = self.registry.load_mapping(form_schema.uid)

    def set_data(self, dataframe, path, sheet, signature, column_statuses, report):
        self.dataframe = dataframe
        self.source_path = path
        self.source_name = os.path.basename(path)
        self.sheet = sheet or ""
        # Le formulaire fait partie de l'identite : le meme fichier envoye vers
        # deux formulaires differents constitue bien deux imports distincts.
        self.source_id = f"{signature}:{self.schema.uid if self.schema else ''}:{self.sheet}"
        self.column_statuses = column_statuses
        self.validation_report = report

    def reset_validation(self):
        self.dataframe = None
        self.column_statuses = []
        self.validation_report = None
        self.source_id = ""

    def set_overrides(self, overrides):
        """Enregistre la correspondance manuelle pour le formulaire courant."""
        self.column_overrides = dict(overrides or {})
        if self.schema:
            self.registry.save_mapping(self.schema.uid, self.column_overrides)

    def clear_overrides(self):
        self.column_overrides = {}
        if self.schema:
            self.registry.clear_mapping(self.schema.uid)

    def reset_client(self):
        """Ferme le client courant : les sessions HTTP ouvertes sont liberees."""
        if self._client is not None:
            self._client.close()
        self._client = None
        self._client_signature = None

    def history_summary(self):
        if not self.source_id:
            return None
        return self.registry.summary(self.source_id)

    def history_moment(self):
        if not self.source_id:
            return None
        return self.registry.last_seen(self.source_id)

    def ready_to_import(self):
        if not config_mod.get_token(self.config):
            return False, "Renseignez votre jeton d'acces a l'etape Connexion."
        if not self.schema:
            return False, "Choisissez le formulaire de destination."
        if self.dataframe is None:
            return False, "Choisissez le fichier a importer."
        if self.validation_report is not None and self.validation_report.has_blocking_errors:
            return False, self.validation_report.headline()
        if not any(status.is_mapped for status in self.column_statuses):
            return False, "Aucune colonne du fichier ne correspond au formulaire."
        return True, ""

    def close(self):
        if self._client is not None:
            self._client.close()
        self.registry.close()


# ==========================================================================
# Parametres avances (point 12)
# ==========================================================================

class AdvancedDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.session = app.session

        self.title("Parametres avances")
        self.geometry("760x640")
        self.minsize(680, 560)
        self.transient(app)
        self.configure(fg_color=theme.PAGE_BG)

        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=16)

        config = self.session.config

        # --- reseau -------------------------------------------------------
        reseau = Card(container, title="Reseau",
                      subtitle="Reduisez le nombre d'envois simultanes si la connexion est instable.")
        reseau.pack(fill="x")
        body = ctk.CTkFrame(reseau, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        self.workers_var = ctk.StringVar(value=str(config.get("max_workers", 5)))
        self.timeout_var = ctk.StringVar(value=str(config.get("request_timeout", 30)))
        self.attempts_var = ctk.StringVar(value=str(config.get("max_attempts", 4)))

        self._number_row(body, "Envois simultanes (1 a 16)", self.workers_var)
        self._number_row(body, "Delai d'attente par envoi (secondes)", self.timeout_var)
        self._number_row(body, "Tentatives en cas de coupure (1 a 10)", self.attempts_var)

        # --- adresses -----------------------------------------------------
        kpi, submission, _fallback = config_mod.resolved_endpoints(config)
        adresses = Card(
            container, title="Adresses techniques",
            subtitle="Laissez vide pour que l'application les deduise de l'adresse du serveur. "
                     "A renseigner uniquement pour un serveur auto-heberge inhabituel.",
        )
        adresses.pack(fill="x", pady=(14, 0))
        body = ctk.CTkFrame(adresses, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        self.kpi_var = ctk.StringVar(value=config.get("kpi_base_url", ""))
        self.submission_var = ctk.StringVar(value=config.get("submission_base_url", ""))
        self._text_row(body, "Adresse de l'API (liste des formulaires)", self.kpi_var,
                       f"deduit : {kpi}")
        self._text_row(body, "Adresse de reception des soumissions", self.submission_var,
                       f"deduit : {submission}")

        # --- mises a jour (point 13) --------------------------------------
        maj = Card(
            container, title="Mises a jour",
            subtitle="Adresse d'un fichier JSON annoncant la derniere version publiee. "
                     "Laissez vide pour desactiver : aucune requete ne sera alors emise. "
                     "Renseignez-la si votre organisation publie les mises a jour de "
                     "Kobo Importer a une adresse fixe.",
        )
        maj.pack(fill="x", pady=(14, 0))
        body = ctk.CTkFrame(maj, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        self.update_var = ctk.StringVar(value=config.get("update_url", ""))
        self._text_row(
            body, "Adresse du manifeste de version", self.update_var,
            "https://exemple.org/koboimporter/derniere_version.json",
        )

        ctk.CTkButton(
            body, text="Verifier maintenant", height=36, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG,
            command=self.check_update_now,
        ).pack(fill="x", pady=(10, 0))

        self.update_label = ctk.CTkLabel(
            body, text=f"Version installee : {__version__}", font=theme.small_font(),
            text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=640,
        )
        self.update_label.pack(fill="x", pady=(8, 0))

        # --- dossiers -----------------------------------------------------
        dossiers = Card(
            container, title="Dossiers de travail",
            subtitle=f"Par defaut, tout est range dans {paths.data_dir()}",
        )
        dossiers.pack(fill="x", pady=(14, 0))
        body = ctk.CTkFrame(dossiers, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        self.output_var = ctk.StringVar(value=config.get("output_dir", ""))
        self.report_var = ctk.StringVar(value=config.get("report_dir", ""))
        self.log_var = ctk.StringVar(value=config.get("log_file", ""))

        self._path_row(body, "XML des lignes en echec", self.output_var, directory=True)
        self._path_row(body, "Rapports « a corriger »", self.report_var, directory=True)
        self._path_row(body, "Journal CSV", self.log_var, directory=False)

        ctk.CTkButton(
            body, text="Ouvrir le dossier de donnees", height=36, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG,
            command=lambda: paths.open_in_explorer(paths.data_dir()),
        ).pack(fill="x", pady=(12, 0))

        # --- configuration ------------------------------------------------
        partage = Card(
            container, title="Configuration",
            subtitle="L'export ne contient jamais votre jeton d'acces : il peut etre transmis "
                     "sans risque a un collegue.",
        )
        partage.pack(fill="x", pady=(14, 0))
        body = ctk.CTkFrame(partage, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        for text, command in (
            ("Exporter la configuration", self.export_config),
            ("Importer une configuration", self.import_config),
        ):
            ctk.CTkButton(
                row, text=text, height=36, corner_radius=theme.RADIUS,
                fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
                text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=command,
            ).pack(side="left", fill="x", expand=True, padx=4)

        # --- historique ---------------------------------------------------
        historique = Card(
            container, title="Historique d'import",
            subtitle="L'application retient les lignes deja envoyees pour ne jamais creer de "
                     "doublon. Oublier un fichier permet de le renvoyer integralement.",
        )
        historique.pack(fill="x", pady=(14, 0))
        body = ctk.CTkFrame(historique, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        self.history_label = ctk.CTkLabel(
            body, text=self._history_text(), font=theme.small_font(),
            text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=660,
        )
        self.history_label.pack(fill="x")

        ctk.CTkButton(
            body, text="Oublier l'historique du fichier courant", height=36,
            corner_radius=theme.RADIUS, fg_color=theme.DANGER_BUTTON,
            hover_color=theme.DANGER_BUTTON_HOVER, command=self.forget_history,
        ).pack(fill="x", pady=(10, 0))

        # --- pied ---------------------------------------------------------
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(0, 16))

        ctk.CTkLabel(
            footer, text=f"Version {__version__}", font=theme.small_font(),
            text_color=theme.TEXT_MUTED,
        ).pack(side="left")

        ctk.CTkButton(
            footer, text="Enregistrer et fermer", height=40, width=200,
            corner_radius=theme.RADIUS, fg_color=theme.PRIMARY,
            hover_color=theme.PRIMARY_HOVER, command=self.save_and_close,
        ).pack(side="right")

        self.after(200, self._grab)

    def _grab(self):
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 - la fenetre peut avoir ete fermee
            pass

    # -- lignes de formulaire ---------------------------------------------

    def _number_row(self, parent, label, variable):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text=label, font=theme.body_font(), text_color=theme.TEXT,
                     anchor="w", width=300).pack(side="left")
        ctk.CTkEntry(row, textvariable=variable, width=100, height=34,
                     corner_radius=theme.RADIUS).pack(side="left")

    def _text_row(self, parent, label, variable, placeholder):
        ctk.CTkLabel(parent, text=label, font=theme.font(12, "bold"), text_color=theme.TEXT,
                     anchor="w").pack(fill="x", pady=(8, 2))
        ctk.CTkEntry(parent, textvariable=variable, height=34, corner_radius=theme.RADIUS,
                     placeholder_text=placeholder).pack(fill="x")

    def _path_row(self, parent, label, variable, directory):
        ctk.CTkLabel(parent, text=label, font=theme.font(12, "bold"), text_color=theme.TEXT,
                     anchor="w").pack(fill="x", pady=(8, 2))
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkEntry(row, textvariable=variable, height=34,
                     corner_radius=theme.RADIUS).pack(side="left", fill="x", expand=True)

        def browse():
            if directory:
                chosen = filedialog.askdirectory(initialdir=variable.get() or paths.data_dir())
            else:
                chosen = filedialog.asksaveasfilename(
                    defaultextension=".csv", initialfile=os.path.basename(variable.get() or "journal.csv"),
                    initialdir=os.path.dirname(variable.get() or paths.data_dir()),
                    filetypes=[("CSV", "*.csv")],
                )
            if chosen:
                variable.set(chosen)

        ctk.CTkButton(row, text="...", width=44, height=34, corner_radius=theme.RADIUS,
                      fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
                      text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG,
                      command=browse).pack(side="left", padx=(8, 0))

    # -- actions -----------------------------------------------------------

    def _history_text(self):
        summary = self.session.history_summary()
        if not summary or not summary.get("_total"):
            return "Aucun historique pour le fichier actuellement charge."
        return (f"Fichier « {self.session.source_name} » : {summary['_sent']} ligne(s) deja "
                f"envoyee(s), {summary['_failed']} en echec, {summary['_total']} connue(s).")

    def forget_history(self):
        if not self.session.source_id:
            messagebox.showinfo(APP_TITLE, "Aucun fichier n'est charge.")
            return
        if not messagebox.askyesno(
            APP_TITLE,
            "Oublier l'historique de ce fichier ?\n\nToutes ses lignes seront considerees "
            "comme jamais envoyees. Un nouvel import creera des doublons sur le serveur "
            "si elles y sont deja.",
        ):
            return
        removed = self.session.registry.forget_source(self.session.source_id)
        self.history_label.configure(text=self._history_text())
        self.app.log(f"Historique oublie : {removed} ligne(s).", "warning")

    def export_config(self):
        target = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile="kobo_importer_config.json",
            filetypes=[("JSON", "*.json")], title="Exporter la configuration",
        )
        if not target:
            return
        self.collect()
        try:
            config_mod.export_config(self.session.config, target, include_token=False)
            messagebox.showinfo(
                APP_TITLE,
                f"Configuration exportee :\n{target}\n\nLe jeton d'acces n'y figure pas : "
                "chaque utilisateur doit saisir le sien.",
            )
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Export impossible :\n{exc}")

    def import_config(self):
        source = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")], title="Importer une configuration",
        )
        if not source:
            return
        try:
            loaded = config_mod.import_config(source)
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, f"Fichier illisible :\n{exc}")
            return

        # Un jeton deja saisi sur ce poste est conserve : l'export n'en contient pas.
        existing = self.session.config.get("api_token_enc", "")
        if not loaded.get("api_token_enc"):
            loaded["api_token_enc"] = existing

        self.session.config.update(loaded)
        self.app.save_config()
        messagebox.showinfo(
            APP_TITLE,
            "Configuration importee. L'application va revenir a la premiere etape "
            "pour verifier la connexion.",
        )
        self.app.reload_after_config_change()
        self.destroy()

    def collect(self):
        config = self.session.config
        config["max_workers"] = _clamp(self.workers_var.get(), config.get("max_workers", 5), 1, 16)
        config["request_timeout"] = _clamp(self.timeout_var.get(), config.get("request_timeout", 30), 5, 300)
        config["max_attempts"] = _clamp(self.attempts_var.get(), config.get("max_attempts", 4), 1, 10)
        config["kpi_base_url"] = config_mod.normalize_base_url(self.kpi_var.get())
        config["submission_base_url"] = config_mod.normalize_base_url(self.submission_var.get())
        config["output_dir"] = self.output_var.get().strip() or config["output_dir"]
        config["report_dir"] = self.report_var.get().strip() or config["report_dir"]
        config["log_file"] = self.log_var.get().strip() or config["log_file"]
        config["update_url"] = self.update_var.get().strip()

    def check_update_now(self):
        """Point 13 : verification a la demande, sans attendre un redemarrage."""
        target = self.update_var.get().strip() or updates.DEFAULT_UPDATE_URL
        if not target:
            self.update_label.configure(
                text="Aucune adresse renseignee : la verification est desactivee.",
                text_color=theme.TEXT_MUTED,
            )
            return

        self.update_label.configure(text="Verification en cours...", text_color=theme.TEXT_MUTED)

        def work():
            return updates.check_for_update(target)

        def done(info):
            colors = {True: theme.WARNING, False: theme.SUCCESS}
            self.update_label.configure(
                text=info.headline(),
                text_color=theme.DANGER if info.error else colors[info.available],
            )

        def failed(exc):
            self.update_label.configure(text=str(exc), text_color=theme.DANGER)

        self.app.run_async(work, done, failed)

    def save_and_close(self):
        self.collect()
        self.app.save_config()
        self.app.set_status("Parametres enregistres.", "success")
        self.destroy()


def _clamp(raw, fallback, low, high):
    try:
        return max(low, min(high, int(str(raw).strip())))
    except (TypeError, ValueError):
        return fallback


# ==========================================================================
# Fenetre principale
# ==========================================================================

class App(ctk.CTk):
    STEP_CLASSES = (ConnectionStep, FormStep, FileStep, ImportStep)

    def __init__(self):
        super().__init__()

        self.session = Session()
        ctk.set_appearance_mode(self.session.config.get("appearance", "Light"))
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_TITLE} {__version__}")
        self._fit_to_screen()
        self.configure(fg_color=theme.PAGE_BG)
        self._set_icon()

        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._import_thread = None
        self._navigation_locked = False
        self.current_index = 0

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self._drain)
        self.log(f"{APP_TITLE} {__version__} - donnees dans {paths.data_dir()}")
        self.after(1500, self._check_for_update)

    def _fit_to_screen(self):
        """Point 13 : la fenetre doit tenir sur l'ecran, quel qu'il soit.

        L'ancienne version imposait 1450 x 920 avec un minimum de 1280 x 800 :
        sur un portable 1366 x 768, courant sur le terrain, le bas de la fenetre
        et ses boutons passaient sous la barre des taches.

        Les tailles donnees a customtkinter sont ensuite multipliees par le
        facteur d'echelle de l'affichage (125 %, 150 %...) : on raisonne donc en
        unites logiques, obtenues en divisant la taille reelle de l'ecran par ce
        facteur. Sans cela, un minimum « sur » en pixels devient trop grand des
        que l'utilisateur augmente la taille du texte de Windows.
        """
        try:
            scaling = ctk.ScalingTracker.get_window_scaling(self) or 1.0
        except Exception:  # noqa: BLE001 - repli si l'API evolue
            scaling = 1.0

        marge_barre_taches = 80
        largeur_ecran = int(self.winfo_screenwidth() / scaling)
        hauteur_ecran = int((self.winfo_screenheight() - marge_barre_taches) / scaling)

        largeur = max(820, min(1120, largeur_ecran - 40))
        hauteur = max(520, min(740, hauteur_ecran))
        self.geometry(f"{largeur}x{hauteur}")
        self.minsize(min(880, largeur), min(560, hauteur))

    def _set_icon(self):
        if os.path.exists(paths.ICON_FILE):
            try:
                self.iconbitmap(paths.ICON_FILE)
            except Exception:  # noqa: BLE001 - icone facultative
                pass

    # -- construction ------------------------------------------------------

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()

        self.stepper = StepIndicator(
            self, [cls.title for cls in self.STEP_CLASSES], on_click=self.go_to
        )
        self.stepper.grid(row=1, column=0, sticky="w", padx=24, pady=(0, 12))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="nsew", padx=24)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.steps = []
        for step_class in self.STEP_CLASSES:
            step = step_class(self.body, self)
            step.grid(row=0, column=0, sticky="nsew")
            self.steps.append(step)

        self._build_footer()
        self.show_step(0)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 12))
        header.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")

        if os.path.exists(paths.LOGO_FILE):
            try:
                image = Image.open(paths.LOGO_FILE)
                logo = ctk.CTkImage(light_image=image, dark_image=image, size=(44, 44))
                label = ctk.CTkLabel(left, text="", image=logo)
                label.image = logo
                label.pack(side="left", padx=(0, 12))
            except Exception:  # noqa: BLE001 - logo facultatif
                pass

        titles = ctk.CTkFrame(left, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(
            titles, text=APP_TITLE, font=theme.title_font(), text_color=theme.TEXT, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            titles, text="Import de fichiers Excel vers KoboToolbox",
            font=theme.small_font(), text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e")

        # Point 9 : le profil actif reste visible en permanence. Envoyer des
        # donnees vers le mauvais projet parce qu'on ignorait quel profil etait
        # charge serait une erreur couteuse a rattraper.
        self.profile_button = ctk.CTkButton(
            right, text="", width=170, height=34, corner_radius=theme.RADIUS,
            font=theme.small_font(),
            fg_color="transparent", border_width=1, border_color=theme.PRIMARY,
            text_color=theme.PRIMARY, hover_color=theme.NEUTRAL_BG,
            command=self.open_profiles,
        )
        self.profile_button.pack(side="left", padx=(0, 8))
        self._refresh_profile_button()

        for label, width, command in (
            ("Diagnostic", 110, self.open_diagnostic),      # point 2
            ("Historique", 110, self.open_history),         # point 8
            ("Parametres", 120, self.open_advanced),
        ):
            ctk.CTkButton(
                right, text=label, width=width, height=34, corner_radius=theme.RADIUS,
                fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
                text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=command,
            ).pack(side="left", padx=(0, 8))

        self.appearance_menu = ctk.CTkOptionMenu(
            right, values=["Light", "Dark", "System"], width=100, height=34,
            corner_radius=theme.RADIUS, command=self._change_appearance,
        )
        self.appearance_menu.set(self.session.config.get("appearance", "Light"))
        self.appearance_menu.pack(side="left")

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=24, pady=(12, 8))
        footer.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            footer, text="Pret.", font=theme.body_font(), text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        self.back_button = ctk.CTkButton(
            footer, text="Precedent", width=130, height=40, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=self.go_back,
        )
        self.back_button.grid(row=0, column=1, padx=(12, 8))

        self.next_button = ctk.CTkButton(
            footer, text="Suivant", width=180, height=40, corner_radius=theme.RADIUS,
            font=theme.font(13, "bold"),
            fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER, command=self.go_next,
        )
        self.next_button.grid(row=0, column=2)

        credit = ctk.CTkFrame(self, fg_color="transparent")
        credit.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 10))
        ctk.CTkLabel(
            credit,
            text=f"Concu par Adnan Adamou - via Data Solution - WhatsApp +227 90941410"
                 f"     |     Version {__version__}",
            font=theme.small_font(), text_color=theme.TEXT_MUTED,
        ).pack(side="left")

    # -- navigation --------------------------------------------------------

    def show_step(self, index):
        self.current_index = index
        step = self.steps[index]
        step.tkraise()
        self.stepper.set_current(index)
        step.on_enter()

        self.back_button.configure(state="normal" if index > 0 else "disabled")
        if step.next_label:
            self.next_button.grid()
            self.next_button.configure(text=step.next_label, state="normal")
        else:
            self.next_button.grid_remove()

    def go_to(self, index):
        if self._navigation_locked or index == self.current_index:
            return
        if index > self.current_index:
            for position in range(self.current_index, index):
                ok, message = self.steps[position].can_advance()
                if not ok:
                    self.set_status(message, "warning")
                    return
        self.show_step(index)

    def go_next(self):
        if self._navigation_locked:
            return
        ok, message = self.steps[self.current_index].can_advance()
        if not ok:
            self.set_status(message, "warning")
            messagebox.showwarning(APP_TITLE, message)
            return
        if self.current_index < len(self.steps) - 1:
            self.save_config()
            self.show_step(self.current_index + 1)

    def go_back(self):
        if self._navigation_locked:
            return
        if self.current_index > 0:
            self.show_step(self.current_index - 1)

    def lock_navigation(self, locked):
        self._navigation_locked = locked
        state = "disabled" if locked else "normal"
        self.back_button.configure(state=state if self.current_index > 0 else "disabled")
        self.next_button.configure(state=state)

    def reload_after_config_change(self):
        self.session.forms = []
        self.session.schema = None
        self.session.reset_validation()
        self.session.reset_client()
        self.show_step(0)

    # -- services partages -------------------------------------------------

    def run_async(self, work, on_done=None, on_error=None):
        """Execute une tache longue hors du fil graphique."""

        def runner():
            try:
                outcome = work()
            except Exception as exc:  # noqa: BLE001 - remonte a l'appelant
                self._queue.put(("callback", on_error or self._default_error, exc))
            else:
                if on_done is not None:
                    self._queue.put(("callback", on_done, outcome))

        threading.Thread(target=runner, daemon=True).start()

    def _default_error(self, exc):
        self.set_status(str(exc), "error")
        messagebox.showerror(APP_TITLE, str(exc))

    def log(self, message, level="info"):
        stamp = datetime.now().strftime("%H:%M:%S")
        self._queue.put(("log", f"[{stamp}] {message}", level))

    def set_status(self, text, tone="muted"):
        colors = {
            "success": theme.SUCCESS, "warning": theme.WARNING,
            "error": theme.DANGER, "info": theme.PRIMARY, "muted": theme.TEXT_MUTED,
        }
        first_line = str(text).split("\n")[0]
        self.status_label.configure(text=first_line, text_color=colors.get(tone, theme.TEXT_MUTED))

    def save_config(self):
        self.session.config["appearance"] = self.appearance_menu.get()
        try:
            config_mod.save_config(self.session.config)
        except OSError as exc:
            self.set_status(f"Configuration non enregistree : {exc}", "error")

    def _change_appearance(self, mode):
        ctk.set_appearance_mode(mode)
        self.session.config["appearance"] = mode
        self.save_config()

    def open_advanced(self):
        AdvancedDialog(self).focus()

    def open_diagnostic(self):
        """Point 2 : sonder l'installation avant de soupconner ses donnees."""
        DiagnosticDialog(self).focus()

    def open_history(self):
        HistoryDialog(self).focus()

    def open_profiles(self):
        ProfileDialog(self).focus()

    def _refresh_profile_button(self):
        name = profiles.active_name()
        display = name if len(name) <= 18 else name[:17] + "..."
        self.profile_button.configure(text=f"Profil : {display}")

    def switch_profile(self, name):
        """Point 9 : bascule complete vers un autre profil.

        Tout l'etat derive de la configuration precedente est abandonne :
        formulaire, fichier charge, correspondance. Les conserver ferait
        travailler l'utilisateur avec le formulaire d'un projet et le serveur
        d'un autre.
        """
        self.save_config()
        try:
            profiles.set_active(name)
        except profiles.ProfileError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self.session.config = config_mod.load_config()
        self.session.reset_client()
        self.session.forms = []
        self.session.schema = None
        self.session.column_overrides = {}
        self.session.reset_validation()

        mode = self.session.config.get("appearance", "Light")
        self.appearance_menu.set(mode)
        ctk.set_appearance_mode(mode)
        self._refresh_profile_button()

        for step in self.steps:
            reload_step = getattr(step, "reload_from_config", None)
            if reload_step is not None:
                reload_step()

        self.show_step(0)
        self.set_status(f"Profil « {name} » charge.", "success")
        self.log(f"Bascule sur le profil « {name} ».")

    def _check_for_update(self):
        """Point 13 : signale discretement qu'une version plus recente existe.

        Silencieux tant qu'aucune adresse de manifeste n'est configuree, et
        jamais bloquant : une verification qui echoue n'empeche pas de
        travailler.
        """
        target = updates.resolve_url(self.session.config)
        if not target:
            return

        def work():
            return updates.check_for_update(target)

        def done(info):
            if info.error:
                self.log(f"Verification de mise a jour : {info.error}")
                return
            if not info.available:
                return
            self.log(info.headline(), "warning")
            self.set_status(info.headline(), "warning")
            parts = [info.headline()]
            if info.notes:
                parts.append("Nouveautes :\n" + info.notes[:600])
            if info.url:
                parts.append("Telechargement :\n" + info.url)
            messagebox.showinfo(APP_TITLE, "\n\n".join(parts))

        self.run_async(work, done, lambda exc: None)

    # -- boucle de messages (point 18) -------------------------------------

    def _drain(self):
        """Vide la file en un seul passage et regroupe les lignes de journal."""
        journal_batch = []
        latest_progress = None
        callbacks = []

        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break

            kind = item[0]
            if kind == "log":
                journal_batch.append((item[1], item[2]))
            elif kind == "progress":
                latest_progress = item[1]      # seul le dernier etat compte
            elif kind == "callback":
                callbacks.append((item[1], item[2]))

        if journal_batch:
            self.steps[3].journal.append_many(journal_batch)
        if latest_progress is not None:
            self.steps[3].on_progress(latest_progress)
        for callback, payload in callbacks:
            try:
                callback(payload)
            except Exception as exc:  # noqa: BLE001 - une erreur d'affichage ne ferme pas l'app
                self.set_status(f"Erreur d'affichage : {exc}", "error")

        self.after(120, self._drain)

    # -- import ------------------------------------------------------------

    def start_import(self, step):
        self._stop_event = threading.Event()
        session = self.session

        try:
            os.makedirs(session.config["report_dir"], exist_ok=True)
        except OSError:
            pass

        def work():
            client = session.build_client()
            worker = engine_mod.ImportEngine(
                config=session.config,
                dataframe=session.dataframe,
                form_schema=session.schema,
                column_statuses=session.column_statuses,
                source_id=session.source_id,
                source_name=session.source_name,
                client=client,
                registry=session.registry,
                progress_callback=self._post_progress,
                log_callback=self.log,
                stop_event=self._stop_event,
                validation_report=session.validation_report,
            )
            return worker.run()

        def done(result):
            self.set_status(result.summary_text(),
                            "warning" if (result.failed or result.stopped) else "success")
            step.on_finished(result)

        def failed(exc):
            if isinstance(exc, engine_mod.FormVersionChanged):
                # Point 11 : ce n'est pas une panne mais une action a mener ;
                # on ramene l'utilisateur la ou il peut la mener.
                self.log("Import annule : le formulaire a ete redeploye.", "warning")
                self.set_status("Le formulaire a change : rechargez-le.", "warning")
                step.on_finished(None, error="Formulaire redeploye")
                messagebox.showwarning(APP_TITLE, str(exc))
                self.session.schema = None
                self.session.reset_validation()
                self.show_step(1)
                return
            self.log(f"Import interrompu : {exc}", "error")
            self.set_status(str(exc), "error")
            step.on_finished(None, error=str(exc))

        self.run_async(work, done, failed)

    def _post_progress(self, result):
        # Instantane : le moteur continue de modifier son objet dans son thread.
        self._queue.put(("progress", dataclasses.replace(result, failures=[])))

    def request_stop(self):
        self._stop_event.set()
        self.set_status("Arret demande : fin des envois en cours...", "warning")
        self.log("Arret demande par l'utilisateur.", "warning")

    # -- fermeture ---------------------------------------------------------

    def on_close(self):
        if self._navigation_locked:
            if not messagebox.askyesno(
                APP_TITLE,
                "Un import est en cours. Quitter maintenant l'interrompra.\n\n"
                "Les lignes deja envoyees sont conservees : vous pourrez reprendre.\n\nQuitter ?",
            ):
                return
            self._stop_event.set()

        self.save_config()
        try:
            self.session.close()
        except Exception:  # noqa: BLE001 - fermeture best effort
            pass
        self.destroy()


def main():
    app = App()
    app.mainloop()

"""Les quatre etapes de l'assistant (point 12).

Connexion -> Formulaire -> Fichier et verification -> Import.
Chaque etape ne montre que ce qui est necessaire a la decision suivante ;
tout le reglage fin est renvoye dans la fenetre « Parametres avances ».
"""

import os
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import config as config_mod
from .. import excel, paths, validation
from . import theme
from .widgets import Badge, Card, Journal, LabelledEntry, SummaryTiles

APP_TITLE = "Kobo Importer"

_STATUS_TONE = {
    validation.COL_OK: ("Reconnue", "success"),
    validation.COL_UNKNOWN: ("Ignoree", "muted"),
    validation.COL_INVALID: ("A corriger", "error"),
    validation.COL_DUPLICATE: ("Doublon", "warning"),
    validation.COL_ATTACHMENT: ("Fichier", "warning"),
    validation.COL_REPEAT: ("Repetition", "warning"),
    validation.COL_FORCED_IGNORE: ("Ecartee", "muted"),
}

# Bornes d'affichage : la liste complete passe par l'export Excel, qui reste
# lisible quel que soit le nombre de colonnes ou de problemes.
MAX_MAPPING_ROWS = 80
MAX_ISSUE_ROWS = 30


class Step(ctk.CTkFrame):
    """Base commune : construction, entree, condition de passage."""

    title = ""
    next_label = "Suivant"

    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.session = app.session
        self.build()

    def build(self):
        raise NotImplementedError

    def on_enter(self):
        pass

    def can_advance(self):
        return True, ""


# ==========================================================================
# Etape 1 - Connexion
# ==========================================================================

class ConnectionStep(Step):
    title = "Connexion"

    def build(self):
        self.grid_columnconfigure(0, weight=1)

        card = Card(
            self,
            title="Connexion a votre serveur KoboToolbox",
            subtitle="Ces informations ne sont demandees qu'une fois : elles sont "
                     "conservees sur ce poste pour les prochains imports.",
        )
        card.pack(fill="x")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        self.server_var = ctk.StringVar(value=self.session.config["server_base_url"])
        LabelledEntry(
            body,
            "Adresse du serveur",
            self.server_var,
            placeholder="https://kf.kobotoolbox.org",
            help_text="L'adresse que vous utilisez dans votre navigateur pour ouvrir "
                      "KoboToolbox. L'application determine seule les adresses techniques.",
        ).pack(fill="x", pady=(0, 14))

        self.token_var = ctk.StringVar(value=config_mod.get_token(self.session.config))
        token_block = LabelledEntry(
            body,
            "Jeton d'acces (API token)",
            self.token_var,
            placeholder="Collez ici votre jeton personnel",
            show="*",
        )
        token_block.pack(fill="x")
        self.token_entry = token_block.entry

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", pady=(10, 0))

        self.toggle_button = ctk.CTkButton(
            actions, text="Afficher", width=100, height=34, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG,
            command=self._toggle_token,
        )
        self.toggle_button.pack(side="left")

        ctk.CTkButton(
            actions, text="Ou trouver mon jeton ?", width=190, height=34,
            corner_radius=theme.RADIUS, fg_color="transparent",
            text_color=theme.PRIMARY, hover_color=theme.NEUTRAL_BG,
            command=self._explain_token,
        ).pack(side="left", padx=8)

        self.test_button = ctk.CTkButton(
            actions, text="Tester la connexion", width=180, height=34,
            corner_radius=theme.RADIUS, fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER,
            command=self.test_connection,
        )
        self.test_button.pack(side="right")

        self.result_badge = Badge(body, text="Non teste", tone="muted")
        self.result_badge.pack(anchor="w", pady=(16, 4))

        self.result_label = ctk.CTkLabel(
            body, text="Renseignez l'adresse et le jeton, puis testez la connexion.",
            font=theme.body_font(), text_color=theme.TEXT_MUTED,
            justify="left", anchor="w", wraplength=760,
        )
        self.result_label.pack(fill="x")

        note = Card(
            self,
            title="Securite",
            subtitle="Le jeton est chiffre avec votre session Windows : un autre utilisateur "
                     "de ce poste ne peut pas le lire, et il n'apparait jamais en clair dans "
                     "un fichier. Le partage de configuration l'exclut automatiquement.",
        )
        note.pack(fill="x", pady=(14, 0))

    def _toggle_token(self):
        hidden = self.token_entry.cget("show") == "*"
        self.token_entry.configure(show="" if hidden else "*")
        self.toggle_button.configure(text="Masquer" if hidden else "Afficher")

    def _explain_token(self):
        server = config_mod.normalize_base_url(self.server_var.get()) or "https://kf.kobotoolbox.org"
        messagebox.showinfo(
            APP_TITLE,
            "Pour obtenir votre jeton d'acces :\n\n"
            "1. Connectez-vous a KoboToolbox dans votre navigateur.\n"
            "2. Cliquez sur votre nom en haut a droite, puis « Parametres du compte ».\n"
            "3. Ouvrez l'onglet « Securite » : le jeton API y est affiche.\n\n"
            f"Vous pouvez aussi ouvrir directement :\n{server}/token/?format=json",
        )

    def collect(self):
        self.session.config["server_base_url"] = config_mod.normalize_base_url(self.server_var.get())
        config_mod.set_token(self.session.config, self.token_var.get())

    def reload_from_config(self):
        """Point 9 : recharge les champs apres une bascule de profil."""
        self.server_var.set(self.session.config.get("server_base_url", ""))
        self.token_var.set(config_mod.get_token(self.session.config))
        self.result_badge.update_tone("Non teste", "muted")
        self.result_label.configure(
            text="Renseignez l'adresse et le jeton, puis testez la connexion.",
            text_color=theme.TEXT_MUTED,
        )

    def test_connection(self):
        self.collect()
        if not self.session.config["server_base_url"]:
            self._show_result(False, "Renseignez l'adresse du serveur.")
            return
        if not config_mod.get_token(self.session.config):
            self._show_result(False, "Renseignez votre jeton d'acces.")
            return

        self.test_button.configure(state="disabled", text="Test en cours...")
        self.result_badge.update_tone("Test en cours", "info")
        self.result_label.configure(text="Interrogation du serveur...", text_color=theme.TEXT_MUTED)

        client = self.session.build_client()

        def work():
            return client.test_connection(self.session.config.get("asset_uid", ""))

        def done(outcome):
            ok, message = outcome
            self._show_result(ok, message)
            self.test_button.configure(state="normal", text="Tester la connexion")
            if ok:
                self.session.connection_ok = True
                self.app.save_config()

        def failed(exc):
            self._show_result(False, str(exc))
            self.test_button.configure(state="normal", text="Tester la connexion")

        self.app.run_async(work, done, failed)

    def _show_result(self, ok, message):
        self.result_badge.update_tone(
            "Connexion etablie" if ok else "Echec", "success" if ok else "error"
        )
        self.result_label.configure(
            text=message, text_color=theme.SUCCESS if ok else theme.DANGER
        )
        self.app.log(f"Test de connexion : {'OK' if ok else 'echec'} - {message}",
                     "success" if ok else "error")

    def can_advance(self):
        self.collect()
        if not self.session.config["server_base_url"]:
            return False, "Renseignez l'adresse du serveur KoboToolbox."
        if not config_mod.get_token(self.session.config):
            return False, "Renseignez votre jeton d'acces."
        return True, ""


# ==========================================================================
# Etape 2 - Formulaire (point 8)
# ==========================================================================

class FormStep(Step):
    title = "Formulaire"

    def build(self):
        self.grid_columnconfigure(0, weight=1)

        card = Card(
            self,
            title="Choisissez le formulaire de destination",
            subtitle="La liste provient directement de votre compte : il n'y a plus d'identifiant "
                     "ni de numero de version a saisir a la main.",
        )
        card.pack(fill="x")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        row.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        search = ctk.CTkEntry(
            row, textvariable=self.search_var, height=36, corner_radius=theme.RADIUS,
            placeholder_text="Rechercher un formulaire...",
        )
        search.grid(row=0, column=0, sticky="ew")

        self.reload_button = ctk.CTkButton(
            row, text="Actualiser", width=120, height=36, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG,
            command=lambda: self.load_forms(force=True),
        )
        self.reload_button.grid(row=0, column=1, padx=(10, 0))

        self.form_menu = ctk.CTkOptionMenu(
            body, values=["Chargement..."], height=40, corner_radius=theme.RADIUS,
            font=theme.body_font(), command=self._on_pick,
        )
        self.form_menu.pack(fill="x", pady=(12, 0))

        self.detail = ctk.CTkLabel(
            body, text="", font=theme.body_font(), text_color=theme.TEXT_MUTED,
            justify="left", anchor="w", wraplength=780,
        )
        self.detail.pack(fill="x", pady=(14, 0))

        self.template_button = ctk.CTkButton(
            body, text="Telecharger le modele Excel de ce formulaire",
            height=42, corner_radius=theme.RADIUS,
            fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER,
            state="disabled", command=self.download_template,
        )
        self.template_button.pack(fill="x", pady=(16, 0))

        ctk.CTkLabel(
            body,
            text="Le modele contient les bonnes colonnes, le type attendu de chaque question "
                 "et les listes de choix : le remplir garantit un import sans surprise.",
            font=theme.small_font(), text_color=theme.TEXT_MUTED,
            justify="left", anchor="w", wraplength=780,
        ).pack(fill="x", pady=(6, 0))

        self._entries = []

    def on_enter(self):
        if not self.session.forms:
            self.load_forms()
        elif self.session.schema:
            self._render_detail()

    def load_forms(self, force=False):
        if self.session.forms and not force:
            return
        self.form_menu.configure(values=["Chargement..."], state="disabled")
        self.form_menu.set("Chargement...")
        self.reload_button.configure(state="disabled")
        self.app.set_status("Recuperation de la liste des formulaires...", "info")

        client = self.session.build_client()

        def work():
            return client.list_forms()

        def done(entries):
            self.reload_button.configure(state="normal")
            self.session.forms = entries
            if not entries:
                self.form_menu.configure(values=["Aucun formulaire"], state="disabled")
                self.form_menu.set("Aucun formulaire")
                self.app.set_status("Aucun formulaire accessible avec ce compte.", "warning")
                return
            self.form_menu.configure(state="normal")
            self._apply_filter()
            self.app.set_status(f"{len(entries)} formulaire(s) disponible(s).", "success")

            previous = self.session.config.get("asset_uid")
            if previous:
                for entry in entries:
                    if entry["uid"] == previous:
                        self.form_menu.set(self._label(entry))
                        self._on_pick(self._label(entry))
                        break

        def failed(exc):
            self.reload_button.configure(state="normal")
            self.form_menu.configure(values=["Erreur de chargement"], state="disabled")
            self.form_menu.set("Erreur de chargement")
            self.app.set_status("Impossible de recuperer la liste des formulaires.", "error")
            messagebox.showerror(APP_TITLE, str(exc))

        self.app.run_async(work, done, failed)

    @staticmethod
    def _label(entry):
        mark = "" if entry["deployed"] else "  [non deploye]"
        return f"{entry['title']}{mark}"

    def reload_from_config(self):
        """Point 9 : la liste des formulaires depend du compte, donc du profil."""
        self._entries = []
        self.search_var.set("")
        self.form_menu.configure(values=["Chargement..."], state="disabled")
        self.form_menu.set("Chargement...")
        self.detail.configure(text="", text_color=theme.TEXT_MUTED)
        self.template_button.configure(state="disabled")

    def _apply_filter(self):
        needle = self.search_var.get().strip().lower()
        entries = [
            entry for entry in self.session.forms
            if not needle or needle in entry["title"].lower() or needle in entry["uid"].lower()
        ]
        self._entries = entries
        values = [self._label(entry) for entry in entries] or ["Aucun resultat"]
        self.form_menu.configure(values=values, state="normal" if entries else "disabled")
        if entries and self.form_menu.get() not in values:
            self.form_menu.set(values[0])

    def _on_pick(self, label):
        entry = next((item for item in self._entries if self._label(item) == label), None)
        if not entry:
            return
        self.template_button.configure(state="disabled")
        self.detail.configure(text="Lecture du formulaire...", text_color=theme.TEXT_MUTED)

        client = self.session.build_client()
        uid = entry["uid"]

        def work():
            return client.get_schema(uid)

        def done(form_schema):
            self.session.set_schema(form_schema)
            self.app.save_config()
            self._render_detail()
            # Le formulaire change : la correspondance des colonnes n'est plus valable.
            self.session.reset_validation()

        def failed(exc):
            self.detail.configure(text=str(exc), text_color=theme.DANGER)

        self.app.run_async(work, done, failed)

    def _render_detail(self):
        form_schema = self.session.schema
        if not form_schema:
            return
        importable = form_schema.importable
        required = form_schema.required_paths

        lignes = [
            f"Identifiant : {form_schema.uid}",
            f"Version deployee : {form_schema.version or 'inconnue'}",
            f"{len(importable)} question(s) importable(s), dont {len(required)} obligatoire(s)",
            f"{form_schema.submission_count} soumission(s) deja presente(s) sur le serveur",
        ]
        if not form_schema.deployed:
            lignes.append("Attention : ce formulaire n'est pas deploye. Les envois seront refuses.")
        if form_schema.has_repeats:
            lignes.append("Ce formulaire contient un groupe repete, non importable depuis un tableau.")
        if form_schema.has_attachments:
            lignes.append("Ce formulaire attend des photos ou fichiers, non transmis par l'import.")

        self.detail.configure(
            text="\n".join(lignes),
            text_color=theme.DANGER if not form_schema.deployed else theme.TEXT_MUTED,
        )
        self.template_button.configure(state="normal")

    def download_template(self):
        form_schema = self.session.schema
        if not form_schema:
            return
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in form_schema.title)[:50]
        target = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialfile=f"modele_{safe or form_schema.uid}.xlsx",
            filetypes=[("Classeur Excel", "*.xlsx")],
            title="Enregistrer le modele Excel",
        )
        if not target:
            return

        def work():
            return excel.build_template(form_schema, target)

        def done(path):
            self.app.log(f"Modele Excel genere : {path}", "success")
            if messagebox.askyesno(
                APP_TITLE,
                f"Modele enregistre :\n{path}\n\nOuvrir le dossier ?",
            ):
                paths.open_in_explorer(path)

        def failed(exc):
            messagebox.showerror(APP_TITLE, f"Modele non genere :\n{exc}")

        self.app.run_async(work, done, failed)

    def can_advance(self):
        if not self.session.schema:
            return False, "Choisissez le formulaire de destination."
        return True, ""


# ==========================================================================
# Etape 3 - Fichier et verification (points 9, 10, 11, 14)
# ==========================================================================

class FileStep(Step):
    title = "Fichier"
    next_label = "Preparer l'import"

    def build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Une seule zone defilante pour toute l'etape : c'est la plus dense, et
        # elle doit rester entierement atteignable sur un petit ecran (point 13).
        # Les listes qu'elle contient sont volontairement des cadres simples :
        # imbriquer deux zones defilantes ferait reagir les deux a la molette.
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

        picker = Card(self.scroll, title="Fichier a importer")
        picker.pack(fill="x")

        body = ctk.CTkFrame(picker, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))
        body.grid_columnconfigure(0, weight=1)

        self.file_var = ctk.StringVar(value=self.session.config.get("excel_file", ""))
        entry = ctk.CTkEntry(
            body, textvariable=self.file_var, height=38, corner_radius=theme.RADIUS,
            placeholder_text="Aucun fichier selectionne",
        )
        entry.grid(row=0, column=0, sticky="ew")

        ctk.CTkButton(
            body, text="Parcourir", width=130, height=38, corner_radius=theme.RADIUS,
            fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER, command=self.pick_file,
        ).grid(row=0, column=1, padx=(10, 0))

        sheet_row = ctk.CTkFrame(body, fg_color="transparent")
        sheet_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        ctk.CTkLabel(
            sheet_row, text="Feuille :", font=theme.font(13, "bold"), text_color=theme.TEXT,
        ).pack(side="left")

        self.sheet_menu = ctk.CTkOptionMenu(
            sheet_row, values=["-"], width=240, height=34, corner_radius=theme.RADIUS,
            command=lambda _value: self.load_file(),
        )
        self.sheet_menu.pack(side="left", padx=(10, 0))
        self.sheet_menu.configure(state="disabled")

        self.history_label = ctk.CTkLabel(
            sheet_row, text="", font=theme.small_font(), text_color=theme.WARNING,
            justify="left",
        )
        self.history_label.pack(side="left", padx=(18, 0))

        # --- resume du controle ------------------------------------------
        self.summary_card = Card(self.scroll, title="Verification avant envoi")
        self.summary_card.pack(fill="x", pady=(14, 0))

        summary_body = ctk.CTkFrame(self.summary_card, fg_color="transparent")
        summary_body.pack(fill="x", padx=18, pady=(0, 18))

        self.headline = ctk.CTkLabel(
            summary_body, text="Selectionnez un fichier pour lancer la verification.",
            font=theme.font(14, "bold"), text_color=theme.TEXT_MUTED,
            justify="left", anchor="w", wraplength=820,
        )
        self.headline.pack(fill="x")

        self.tiles = SummaryTiles(summary_body, [
            ("rows", "lignes lues", "info"),
            ("ready", "prets a envoyer", "success"),
            ("issues", "a corriger", "error"),
            ("mapped", "colonnes reconnues", "info"),
            ("ignored", "colonnes ignorees", "muted"),
        ])
        self.tiles.pack(fill="x", pady=(14, 0))

        buttons = ctk.CTkFrame(summary_body, fg_color="transparent")
        buttons.pack(fill="x", pady=(14, 0))

        # Point 3 : la porte de sortie quand les en-tetes ne correspondent pas.
        self.mapping_button = ctk.CTkButton(
            buttons, text="Associer les colonnes a la main...",
            height=38, corner_radius=theme.RADIUS, state="disabled",
            fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER,
            command=self.open_mapping,
        )
        self.mapping_button.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.export_button = ctk.CTkButton(
            buttons, text="Exporter la liste des problemes (Excel)",
            height=38, corner_radius=theme.RADIUS, state="disabled",
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG,
            command=self.export_issues,
        )
        self.export_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # --- detail correspondance ---------------------------------------
        mapping_card = Card(
            self.scroll,
            title="Correspondance des colonnes",
            subtitle="Les colonnes a corriger sont presentees en premier.",
        )
        mapping_card.pack(fill="x", pady=(14, 0))
        self.mapping_list = ctk.CTkFrame(mapping_card, fg_color="transparent")
        self.mapping_list.pack(fill="x", padx=18, pady=(0, 16))

        issues_card = Card(
            self.scroll,
            title="Valeurs a corriger",
            subtitle="Extrait des problemes detectes ; l'export Excel contient la liste complete.",
        )
        issues_card.pack(fill="x", pady=(14, 0))
        self.issues_list = ctk.CTkFrame(issues_card, fg_color="transparent")
        self.issues_list.pack(fill="x", padx=18, pady=(0, 16))

        self._placeholder(self.mapping_list, "Aucun fichier charge.")
        self._placeholder(self.issues_list, "Aucun fichier charge.")

    # -- selection ---------------------------------------------------------

    def pick_file(self):
        path = filedialog.askopenfilename(
            title="Choisir le fichier a importer",
            filetypes=[
                # Point 7 : le CSV est desormais accepte au meme titre qu'Excel.
                ("Tableaux pris en charge", "*.xlsx *.xlsm *.xls *.csv *.txt *.tsv"),
                ("Classeurs Excel", "*.xlsx *.xlsm *.xls"),
                ("Fichiers CSV", "*.csv *.txt *.tsv"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not path:
            return
        self.file_var.set(path)
        self._load_sheets(path)

    def _load_sheets(self, path):
        def work():
            return excel.list_sheets(path)

        def done(sheets):
            if len(sheets) > 1:
                self.sheet_menu.configure(values=sheets, state="normal")
                preferred = excel.DATA_SHEET if excel.DATA_SHEET in sheets else sheets[0]
                self.sheet_menu.set(preferred)
            else:
                self.sheet_menu.configure(values=sheets or ["-"], state="disabled")
                self.sheet_menu.set(sheets[0] if sheets else "-")
            self.load_file()

        def failed(exc):
            messagebox.showerror(APP_TITLE, str(exc))

        self.app.run_async(work, done, failed)

    def on_enter(self):
        path = self.file_var.get().strip()
        if path and self.session.dataframe is None and os.path.exists(path):
            self._load_sheets(path)
        elif self.session.validation_report is not None:
            self._render()

    def load_file(self):
        path = self.file_var.get().strip()
        if not path:
            return
        if not self.session.schema:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord le formulaire de destination.")
            return

        sheet = self.sheet_menu.get()
        sheet = sheet if sheet and sheet != "-" else None
        self.headline.configure(text="Lecture et verification en cours...", text_color=theme.TEXT_MUTED)
        self.app.set_status("Verification du fichier...", "info")

        form_schema = self.session.schema
        overrides = dict(self.session.column_overrides or {})

        def work():
            frame, used = excel.read_table(path, sheet)
            signature = excel.file_signature(path)
            # Point 3 : la correspondance enregistree pour ce formulaire prime
            # sur la reconnaissance par nom de colonne.
            statuses = validation.map_columns(frame.columns, form_schema, overrides)
            report = validation.validate_dataframe(frame, form_schema, overrides=overrides)
            return frame, used, signature, statuses, report

        def done(outcome):
            frame, used, signature, statuses, report = outcome
            self.session.set_data(frame, path, used, signature, statuses, report)
            self.session.config["excel_file"] = path
            self.session.config["excel_sheet"] = used or ""
            self.app.save_config()
            self._render()

        def failed(exc):
            self.headline.configure(text=str(exc), text_color=theme.DANGER)
            self.app.set_status("Fichier illisible.", "error")
            messagebox.showerror(APP_TITLE, str(exc))

        self.app.run_async(work, done, failed)

    # -- affichage ---------------------------------------------------------

    def _render(self):
        report = self.session.validation_report
        if report is None:
            return

        mapped = len(report.mapped_columns)
        ignored = len(report.unmapped_columns)
        self.tiles.set(
            rows=report.total_rows,
            ready=report.valid_rows,
            issues=report.invalid_rows,
            mapped=mapped,
            ignored=ignored,
        )

        if report.has_blocking_errors:
            tone = theme.DANGER
        elif report.invalid_rows:
            tone = theme.WARNING
        else:
            tone = theme.SUCCESS

        lignes = [report.headline()]
        lignes.extend(report.warnings)
        if report.truncated:
            lignes.append(
                f"{report.issue_count} probleme(s) au total ; "
                f"les {len(report.row_issues)} premiers sont detailles."
            )
        self.headline.configure(text="\n".join(lignes), text_color=tone)

        self.export_button.configure(state="normal" if report.row_issues else "disabled")
        self.mapping_button.configure(state="normal")
        if self.session.column_overrides:
            self.mapping_button.configure(
                text=f"Correspondance manuelle active "
                     f"({sum(1 for v in self.session.column_overrides.values() if v)} colonne(s))"
            )
        else:
            self.mapping_button.configure(text="Associer les colonnes a la main...")
        self._render_mapping(report)
        self._render_issues(report)
        self._render_history()

        self.app.set_status(report.headline(), "error" if report.has_blocking_errors else
                            ("warning" if report.invalid_rows else "success"))

    def _render_mapping(self, report):
        self._clear(self.mapping_list)
        ordre = {validation.COL_INVALID: 0, validation.COL_DUPLICATE: 1,
                 validation.COL_ATTACHMENT: 2, validation.COL_REPEAT: 3,
                 validation.COL_UNKNOWN: 4, validation.COL_OK: 5}
        colonnes = sorted(report.columns, key=lambda item: (ordre.get(item.status, 9), item.index))

        for status in colonnes[:MAX_MAPPING_ROWS]:
            libelle, tone = _STATUS_TONE.get(status.status, ("?", "muted"))
            row = ctk.CTkFrame(self.mapping_list, fg_color="transparent")
            row.pack(fill="x", pady=2)

            Badge(row, text=libelle, tone=tone, width=110).pack(side="left")
            texte = status.column
            detail = status.message
            if status.suggestion:
                detail += f"  Nom attendu : « {status.suggestion} »"

            ctk.CTkLabel(
                row, text=texte, font=theme.font(12, "bold"), text_color=theme.TEXT, anchor="w",
            ).pack(side="left", padx=(12, 8))
            ctk.CTkLabel(
                row, text=detail, font=theme.small_font(), text_color=theme.TEXT_MUTED,
                anchor="w", justify="left",
            ).pack(side="left", fill="x", expand=True)

        if len(colonnes) > MAX_MAPPING_ROWS:
            self._placeholder(
                self.mapping_list, f"... et {len(colonnes) - MAX_MAPPING_ROWS} autre(s) colonne(s)."
            )

        for question in report.missing_required:
            row = ctk.CTkFrame(self.mapping_list, fg_color="transparent")
            row.pack(fill="x", pady=2)
            Badge(row, text="Manquante", tone="error", width=110).pack(side="left")
            ctk.CTkLabel(
                row, text=question.path, font=theme.font(12, "bold"),
                text_color=theme.DANGER, anchor="w",
            ).pack(side="left", padx=(12, 8))
            ctk.CTkLabel(
                row,
                text=f"Question obligatoire absente du fichier : {question.display_label()}",
                font=theme.small_font(), text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(side="left", fill="x", expand=True)

    def _render_issues(self, report):
        self._clear(self.issues_list)
        if not report.row_issues:
            self._placeholder(self.issues_list, "Aucune valeur a corriger.")
            return

        for issue in report.row_issues[:MAX_ISSUE_ROWS]:
            row = ctk.CTkFrame(self.issues_list, fg_color="transparent")
            row.pack(fill="x", pady=2)
            Badge(row, text=f"Ligne {issue.row_number}", tone="warning", width=90).pack(side="left")
            valeur = f" (« {issue.value} »)" if issue.value else ""
            ctk.CTkLabel(
                row, text=f"{issue.column}{valeur}", font=theme.font(12, "bold"),
                text_color=theme.TEXT, anchor="w",
            ).pack(side="left", padx=(12, 8))
            ctk.CTkLabel(
                row, text=issue.message, font=theme.small_font(),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left",
            ).pack(side="left", fill="x", expand=True)

        if len(report.row_issues) > MAX_ISSUE_ROWS:
            self._placeholder(
                self.issues_list,
                f"... et {report.issue_count - MAX_ISSUE_ROWS} autre(s). "
                "Utilisez l'export Excel pour la liste complete.",
            )

    def _render_history(self):
        summary = self.session.history_summary()
        if not summary or not summary.get("_total"):
            self.history_label.configure(text="")
            return
        moment = self.session.history_moment() or ""
        moment = moment.replace("T", " a ")
        self.history_label.configure(
            text=f"Deja traite le {moment} : {summary['_sent']} envoyee(s), "
                 f"{summary['_failed']} en echec."
        )

    def open_mapping(self):
        """Point 3 : ouvre l'editeur de correspondance, puis revalide."""
        if self.session.dataframe is None or not self.session.schema:
            messagebox.showwarning(APP_TITLE, "Chargez d'abord un fichier.")
            return

        from .dialogs import MappingDialog

        def apply(overrides):
            self.session.set_overrides(overrides)
            retenues = sum(1 for target in overrides.values() if target)
            self.app.log(
                f"Correspondance enregistree pour « {self.session.schema.title} » : "
                f"{retenues} colonne(s) importee(s).",
                "success",
            )
            self.load_file()

        MappingDialog(self.app, apply).focus()

    def reload_from_config(self):
        """Appele apres un changement de profil (point 9)."""
        self.file_var.set(self.session.config.get("excel_file", ""))
        self.sheet_menu.configure(values=["-"], state="disabled")
        self.sheet_menu.set("-")
        self.history_label.configure(text="")
        self.headline.configure(
            text="Selectionnez un fichier pour lancer la verification.",
            text_color=theme.TEXT_MUTED,
        )
        self.tiles.reset()
        self.export_button.configure(state="disabled")
        self.mapping_button.configure(state="disabled",
                                      text="Associer les colonnes a la main...")
        self._clear(self.mapping_list)
        self._clear(self.issues_list)
        self._placeholder(self.mapping_list, "Aucun fichier charge.")
        self._placeholder(self.issues_list, "Aucun fichier charge.")

    def export_issues(self):
        report = self.session.validation_report
        if report is None or not report.row_issues:
            return
        target = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialfile=f"a_corriger_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            filetypes=[("Classeur Excel", "*.xlsx")],
            title="Enregistrer la liste des problemes",
        )
        if not target:
            return

        frame = self.session.dataframe
        failures = validation.failures_from_report(report)

        def work():
            return excel.write_error_report(
                target, frame, failures, report,
                context={
                    "Fichier source": self.session.source_name,
                    "Formulaire": self.session.schema.title,
                    "Controle effectue le": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Lignes a corriger": report.invalid_rows,
                },
            )

        def done(path):
            self.app.log(f"Liste des problemes exportee : {path}", "success")
            if messagebox.askyesno(APP_TITLE, f"Fichier enregistre :\n{path}\n\nOuvrir le dossier ?"):
                paths.open_in_explorer(path)

        def failed(exc):
            messagebox.showerror(APP_TITLE, f"Export impossible :\n{exc}")

        self.app.run_async(work, done, failed)

    @staticmethod
    def _clear(container):
        for child in container.winfo_children():
            child.destroy()

    @staticmethod
    def _placeholder(container, text):
        ctk.CTkLabel(
            container, text=text, font=theme.small_font(),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(fill="x", pady=6)

    def can_advance(self):
        if self.session.dataframe is None:
            return False, "Choisissez le fichier a importer."
        report = self.session.validation_report
        if report is not None and report.has_blocking_errors:
            return False, report.headline()
        return True, ""


# ==========================================================================
# Etape 4 - Import (points 14, 18)
# ==========================================================================

RESUME_LABELS = {
    "new": "Nouvelles lignes seulement",
    "retry": "Reprendre les echecs",
    "force": "Tout renvoyer",
}
RESUME_BY_LABEL = {value: key for key, value in RESUME_LABELS.items()}


class ImportStep(Step):
    title = "Import"
    next_label = ""

    def build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        options = Card(self, title="Options d'envoi")
        options.grid(row=0, column=0, sticky="ew")

        body = ctk.CTkFrame(options, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(0, 18))

        line = ctk.CTkFrame(body, fg_color="transparent")
        line.pack(fill="x")

        ctk.CTkLabel(
            line, text="Que faut-il envoyer ?", font=theme.font(13, "bold"),
            text_color=theme.TEXT,
        ).pack(side="left")

        current = self.session.config.get("resume_mode", "new")
        self.resume_selector = ctk.CTkSegmentedButton(
            line,
            values=list(RESUME_LABELS.values()),
            command=self._on_resume_change,
        )
        self.resume_selector.set(RESUME_LABELS.get(current, RESUME_LABELS["new"]))
        self.resume_selector.pack(side="left", padx=(14, 0))

        self.dry_run_var = ctk.BooleanVar(value=self.session.config.get("dry_run", False))
        ctk.CTkCheckBox(
            body, text="Simulation : generer les fichiers sans rien envoyer",
            variable=self.dry_run_var, font=theme.body_font(),
            command=self._on_dry_run_change,
        ).pack(anchor="w", pady=(12, 0))

        self.plan_label = ctk.CTkLabel(
            body, text="", font=theme.small_font(), text_color=theme.TEXT_MUTED,
            anchor="w", justify="left", wraplength=820,
        )
        self.plan_label.pack(fill="x", pady=(10, 0))

        # --- progression --------------------------------------------------
        progress = Card(self, title="Progression")
        progress.grid(row=1, column=0, sticky="ew", pady=(14, 0))

        progress_body = ctk.CTkFrame(progress, fg_color="transparent")
        progress_body.pack(fill="x", padx=18, pady=(0, 18))

        self.progressbar = ctk.CTkProgressBar(progress_body, height=14, corner_radius=999)
        self.progressbar.set(0)
        self.progressbar.pack(fill="x")

        self.progress_label = ctk.CTkLabel(
            progress_body, text="En attente de lancement.", font=theme.body_font(),
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.progress_label.pack(fill="x", pady=(8, 0))

        self.tiles = SummaryTiles(progress_body, [
            ("sent", "envoyees", "success"),
            ("duplicates", "deja presentes", "info"),
            ("failed", "en echec", "error"),
            ("invalid", "invalides", "warning"),
            ("skipped", "ignorees", "muted"),
        ])
        self.tiles.pack(fill="x", pady=(14, 0))

        buttons = ctk.CTkFrame(progress_body, fg_color="transparent")
        buttons.pack(fill="x", pady=(16, 0))

        self.start_button = ctk.CTkButton(
            buttons, text="Lancer l'import", height=46, corner_radius=theme.RADIUS,
            font=theme.font(14, "bold"),
            fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER,
            command=self.start,
        )
        self.start_button.pack(side="left", fill="x", expand=True)

        # Point 14 : le bouton s'appelle « Arreter » et reste inactif hors import.
        self.stop_button = ctk.CTkButton(
            buttons, text="Arreter", height=46, width=150, corner_radius=theme.RADIUS,
            font=theme.font(14, "bold"), state="disabled",
            fg_color=theme.DANGER_BUTTON, hover_color=theme.DANGER_BUTTON_HOVER,
            command=self.stop,
        )
        self.stop_button.pack(side="left", padx=(12, 0))

        self.report_button = ctk.CTkButton(
            buttons, text="Ouvrir le rapport", height=46, width=170,
            corner_radius=theme.RADIUS, state="disabled",
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG,
            command=self.open_report,
        )
        self.report_button.pack(side="left", padx=(12, 0))

        # --- journal ------------------------------------------------------
        journal_card = Card(self)
        journal_card.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        self.journal = Journal(journal_card)
        self.journal.pack(fill="both", expand=True, padx=18, pady=18)

        self._report_path = ""

    def reload_from_config(self):
        """Point 9 : remet les options d'envoi a celles du profil charge."""
        current = self.session.config.get("resume_mode", "new")
        self.resume_selector.set(RESUME_LABELS.get(current, RESUME_LABELS["new"]))
        self.dry_run_var.set(bool(self.session.config.get("dry_run", False)))
        self.progressbar.set(0)
        self.progress_label.configure(text="En attente de lancement.",
                                      text_color=theme.TEXT_MUTED)
        self.tiles.reset()
        self.report_button.configure(state="disabled")
        self._report_path = ""
        self.plan_label.configure(text="")

    def _on_resume_change(self, label):
        self.session.config["resume_mode"] = RESUME_BY_LABEL.get(label, "new")
        self._refresh_plan()

    def _on_dry_run_change(self):
        self.session.config["dry_run"] = bool(self.dry_run_var.get())
        self._refresh_plan()

    def on_enter(self):
        self._refresh_plan()

    def _refresh_plan(self):
        """Annonce a l'avance ce que le lancement va faire."""
        if self.session.dataframe is None or not self.session.schema:
            self.plan_label.configure(text="")
            return

        total = len(self.session.dataframe)
        summary = self.session.history_summary() or {}
        deja = summary.get("_sent", 0)
        mode = self.session.config.get("resume_mode", "new")

        if mode == "force":
            prevu = total
            detail = "toutes les lignes seront renvoyees, y compris celles deja passees"
        elif mode == "retry":
            prevu = summary.get("_failed", 0)
            detail = "seules les lignes ayant echoue lors d'un import precedent"
        else:
            prevu = max(0, total - deja)
            detail = "les lignes jamais envoyees avec succes"

        report = self.session.validation_report
        avertissement = ""
        if report is not None and report.invalid_rows:
            avertissement = (f"  {report.invalid_rows} ligne(s) seront refusees au controle "
                             "et listees dans le rapport.")

        action = "generees en simulation" if self.session.config.get("dry_run") else "envoyees"
        self.plan_label.configure(
            text=f"Environ {prevu} ligne(s) sur {total} seront {action} : {detail}.{avertissement}"
        )

    # -- execution ---------------------------------------------------------

    def start(self):
        ok, message = self.session.ready_to_import()
        if not ok:
            messagebox.showwarning(APP_TITLE, message)
            return

        if self.session.schema and not self.session.schema.deployed:
            if not messagebox.askyesno(
                APP_TITLE,
                "Ce formulaire n'est pas deploye sur le serveur : les envois seront "
                "tres probablement refuses.\n\nLancer quand meme ?",
            ):
                return

        self.session.config["dry_run"] = bool(self.dry_run_var.get())
        self.app.save_config()

        self.start_button.configure(state="disabled", text="Import en cours...")
        self.stop_button.configure(state="normal")
        self.report_button.configure(state="disabled")
        self.resume_selector.configure(state="disabled")
        self.tiles.reset()
        self.progressbar.set(0)
        self.progress_label.configure(text="Preparation...")
        self.journal.clear()
        self.app.lock_navigation(True)

        self.app.start_import(self)

    def stop(self):
        self.stop_button.configure(state="disabled", text="Arret...")
        self.app.request_stop()

    def on_progress(self, result):
        total = max(1, result.selected)
        ratio = min(1.0, result.processed / total)
        self.progressbar.set(ratio)

        vitesse = f" - {result.rate:.1f} ligne/s" if result.rate > 0 else ""
        self.progress_label.configure(
            text=f"{result.processed} / {result.selected} traitee(s) ({int(ratio * 100)} %){vitesse}",
            text_color=theme.TEXT,
        )
        self.tiles.set(
            sent=result.sent,
            duplicates=result.duplicates,
            failed=result.failed,
            invalid=result.invalid,
            skipped=result.skipped,
        )

    def on_finished(self, result, error=None):
        self.start_button.configure(state="normal", text="Lancer l'import")
        self.stop_button.configure(state="disabled", text="Arreter")
        self.resume_selector.configure(state="normal")
        self.app.lock_navigation(False)

        if error:
            self.progress_label.configure(text=f"Import interrompu : {error}", text_color=theme.DANGER)
            messagebox.showerror(APP_TITLE, f"L'import s'est arrete sur une erreur :\n\n{error}")
            return

        self.progressbar.set(1.0)
        etat = "Import interrompu" if result.stopped else "Import termine"
        self.progress_label.configure(
            text=f"{etat} en {result.elapsed:.0f} s - {result.summary_text()}",
            text_color=theme.WARNING if result.stopped else theme.SUCCESS,
        )
        self._report_path = result.report_path
        self.report_button.configure(state="normal" if result.report_path else "disabled")
        self._refresh_plan()

        if result.report_path:
            messagebox.showwarning(
                APP_TITLE,
                f"{etat}.\n\n{result.summary_text()}\n\n"
                f"Les lignes non passees ont ete rassemblees dans :\n{result.report_path}\n\n"
                "Corrigez-les dans ce fichier puis reimportez-le directement.",
            )
        else:
            messagebox.showinfo(APP_TITLE, f"{etat}.\n\n{result.summary_text()}")

    def open_report(self):
        if self._report_path and os.path.exists(self._report_path):
            try:
                os.startfile(self._report_path)  # noqa: S606
            except (AttributeError, OSError):
                paths.open_in_explorer(self._report_path)

    def can_advance(self):
        return False, ""

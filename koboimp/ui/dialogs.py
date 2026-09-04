"""Fenetres secondaires.

  #2  DiagnosticDialog - controle installation, reseau, compte, adresses
  #3  MappingDialog    - correspondance manuelle colonne -> question
  #8  HistoryDialog    - historique des imports, jusqu'ici enregistre sans etre montre
  #9  ProfileDialog    - gestion des configurations nommees
"""

import os
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import config as config_mod
from .. import diagnostics, paths, profiles, validation
from . import theme
from .widgets import Badge, Card

APP_TITLE = "Kobo Importer"

_DIAG_TONE = {
    diagnostics.OK: ("Correct", "success"),
    diagnostics.WARN: ("A verifier", "warning"),
    diagnostics.FAIL: ("Probleme", "error"),
    diagnostics.INFO: ("Information", "muted"),
}


class _Dialog(ctk.CTkToplevel):
    """Base commune : dimension, modalite, fond."""

    def __init__(self, app, title, size="820x640", minimum=(720, 520)):
        super().__init__(app)
        self.app = app
        self.session = app.session
        self.title(title)
        self.geometry(size)
        self.minsize(*minimum)
        self.transient(app)
        self.configure(fg_color=theme.PAGE_BG)
        self.after(200, self._grab)

    def _grab(self):
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 - fenetre deja fermee
            pass

    @staticmethod
    def _clear(container):
        for child in container.winfo_children():
            child.destroy()


# ==========================================================================
# #2 - Diagnostic
# ==========================================================================

class DiagnosticDialog(_Dialog):
    def __init__(self, app):
        super().__init__(app, "Diagnostic", "860x680")
        self._report = None

        header = Card(
            self,
            title="Diagnostic de l'installation",
            subtitle="Verifie, dans l'ordre, ce dont l'application a besoin : dossier de "
                     "donnees, reseau, compte KoboToolbox, adresse d'envoi et formulaire. "
                     "A lancer en premier quand un import echoue sans explication claire.",
        )
        header.pack(fill="x", padx=16, pady=(16, 0))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(0, 18))

        self.run_button = ctk.CTkButton(
            actions, text="Lancer le diagnostic", height=40, corner_radius=theme.RADIUS,
            font=theme.font(13, "bold"),
            fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER, command=self.run,
        )
        self.run_button.pack(side="left")

        self.copy_button = ctk.CTkButton(
            actions, text="Copier le rapport", height=40, width=170,
            corner_radius=theme.RADIUS, state="disabled",
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=self.copy_report,
        )
        self.copy_button.pack(side="left", padx=(10, 0))

        self.save_button = ctk.CTkButton(
            actions, text="Enregistrer...", height=40, width=150,
            corner_radius=theme.RADIUS, state="disabled",
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=self.save_report,
        )
        self.save_button.pack(side="left", padx=(10, 0))

        self.verdict = Badge(actions, text="Non lance", tone="muted")
        self.verdict.pack(side="right")

        self.results = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.results.pack(fill="both", expand=True, padx=16, pady=16)
        self._message("Cliquez sur « Lancer le diagnostic ».")

    def _message(self, text):
        ctk.CTkLabel(
            self.results, text=text, font=theme.body_font(),
            text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=760,
        ).pack(fill="x", pady=6)

    def run(self):
        self.run_button.configure(state="disabled", text="Diagnostic en cours...")
        self.copy_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.verdict.update_tone("En cours", "info")
        self._clear(self.results)
        self._message("Controles en cours, certains sondages reseau prennent quelques secondes...")

        config = dict(self.session.config)
        factory = self.session.build_client

        def work():
            return diagnostics.run_diagnostic(config, client_factory=factory)

        def done(report):
            self._report = report
            self.run_button.configure(state="normal", text="Relancer le diagnostic")
            self.copy_button.configure(state="normal")
            self.save_button.configure(state="normal")
            self._render(report)

        def failed(exc):
            self.run_button.configure(state="normal", text="Lancer le diagnostic")
            self.verdict.update_tone("Echec", "error")
            self._clear(self.results)
            self._message(f"Le diagnostic n'a pas pu s'executer :\n{exc}")

        self.app.run_async(work, done, failed)

    def _render(self, report):
        self._clear(self.results)
        status, summary = report.verdict()
        tone = {diagnostics.OK: "success", diagnostics.WARN: "warning",
                diagnostics.FAIL: "error"}.get(status, "muted")
        self.verdict.update_tone(summary.split(".")[0], tone)

        for item in report.results:
            libelle, ton = _DIAG_TONE.get(item.status, ("?", "muted"))
            card = ctk.CTkFrame(
                self.results, corner_radius=theme.RADIUS, fg_color=theme.CARD_BG,
                border_width=1, border_color=theme.CARD_BORDER,
            )
            card.pack(fill="x", pady=4)

            line = ctk.CTkFrame(card, fg_color="transparent")
            line.pack(fill="x", padx=14, pady=(12, 2))
            Badge(line, text=libelle, tone=ton, width=100).pack(side="left")
            ctk.CTkLabel(
                line, text=item.name, font=theme.font(13, "bold"),
                text_color=theme.TEXT, anchor="w",
            ).pack(side="left", padx=(12, 0))
            if item.elapsed:
                ctk.CTkLabel(
                    line, text=f"{item.elapsed:.1f} s", font=theme.small_font(),
                    text_color=theme.TEXT_MUTED,
                ).pack(side="right")

            if item.detail:
                ctk.CTkLabel(
                    card, text=item.detail, font=theme.small_font(),
                    text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=740,
                ).pack(fill="x", padx=(126, 14), pady=(0, 2))

            if item.hint and item.status in (diagnostics.WARN, diagnostics.FAIL):
                ctk.CTkLabel(
                    card, text=item.hint, font=theme.small_font(),
                    text_color=theme.WARNING if item.status == diagnostics.WARN else theme.DANGER,
                    anchor="w", justify="left", wraplength=740,
                ).pack(fill="x", padx=(126, 14), pady=(0, 12))
            else:
                ctk.CTkFrame(card, fg_color="transparent", height=8).pack()

    def copy_report(self):
        if self._report is None:
            return
        self.clipboard_clear()
        self.clipboard_append(self._report.as_text())
        self.app.set_status("Rapport de diagnostic copie dans le presse-papiers.", "success")

    def save_report(self):
        if self._report is None:
            return
        target = filedialog.asksaveasfilename(
            defaultextension=".txt", initialfile="diagnostic_kobo.txt",
            filetypes=[("Texte", "*.txt")], title="Enregistrer le diagnostic",
        )
        if not target:
            return
        try:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(self._report.as_text())
            messagebox.showinfo(APP_TITLE, f"Diagnostic enregistre :\n{target}")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Enregistrement impossible :\n{exc}")


# ==========================================================================
# #3 - Correspondance manuelle des colonnes
# ==========================================================================

class MappingDialog(_Dialog):
    """Associe a la main chaque colonne du fichier a une question du formulaire.

    Sans cet ecran, une colonne mal nommee obligeait a retoucher le fichier dans
    Excel. La correspondance est enregistree par formulaire : elle ne se refait
    donc qu'une fois, meme si le fichier change a chaque collecte.
    """

    IGNORE_LABEL = "— ne pas importer —"

    def __init__(self, app, on_apply):
        super().__init__(app, "Correspondance des colonnes", "980x700", (860, 560))
        self._on_apply = on_apply
        self._rows = {}

        form_schema = self.session.schema
        self._choices = [self.IGNORE_LABEL] + [
            f"{question.path}  —  {question.display_label()}"
            for question in form_schema.importable
        ]
        self._path_by_label = {
            f"{question.path}  —  {question.display_label()}": question.path
            for question in form_schema.importable
        }

        header = Card(
            self,
            title=f"Colonnes du fichier vers « {form_schema.title} »",
            subtitle="Choisissez pour chaque colonne la question de destination. "
                     "Votre choix est enregistre pour ce formulaire et reapplique "
                     "automatiquement aux prochains fichiers.",
        )
        header.pack(fill="x", padx=16, pady=(16, 0))

        tools = ctk.CTkFrame(header, fg_color="transparent")
        tools.pack(fill="x", padx=18, pady=(0, 18))

        ctk.CTkButton(
            tools, text="Proposer automatiquement", height=36, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=self.autofill,
        ).pack(side="left")

        ctk.CTkButton(
            tools, text="Tout reinitialiser", height=36, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=self.reset,
        ).pack(side="left", padx=(10, 0))

        self.counter = ctk.CTkLabel(
            tools, text="", font=theme.small_font(), text_color=theme.TEXT_MUTED,
        )
        self.counter.pack(side="right")

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=16, pady=(16, 0))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=16)

        self.warning = ctk.CTkLabel(
            footer, text="", font=theme.small_font(), text_color=theme.DANGER,
            anchor="w", justify="left", wraplength=560,
        )
        self.warning.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            footer, text="Annuler", height=40, width=120, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=self.destroy,
        ).pack(side="right", padx=(10, 0))

        self.apply_button = ctk.CTkButton(
            footer, text="Appliquer et revalider", height=40, width=210,
            corner_radius=theme.RADIUS, font=theme.font(13, "bold"),
            fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER, command=self.apply,
        )
        self.apply_button.pack(side="right")

        self._build_rows()

    # -- construction ------------------------------------------------------

    def _initial_targets(self):
        """Pre-selection, en garantissant qu'aucune question n'est visee deux fois.

        Deux colonnes proposees vers la meme question bloqueraient le bouton
        « Appliquer » des l'ouverture, en laissant a l'utilisateur un conflit
        qu'il n'a pas cree. Les choix deja enregistres et les colonnes deja
        reconnues font autorite ; seules les suggestions cedent le passage.
        """
        saved = self.session.column_overrides or {}
        form_schema = self.session.schema

        targets = {}
        taken = set()

        # 1. Ce que l'utilisateur a deja decide, et ce que le nom de colonne
        #    designe sans ambiguite.
        for status in self.session.column_statuses:
            column = status.column
            if column in saved:
                target = saved[column]
            elif status.is_mapped:
                target = status.path
            else:
                continue
            targets[column] = target if target and target not in taken else ""
            if targets[column]:
                taken.add(targets[column])

        # 2. Les suggestions, seulement sur une question encore libre.
        for status in self.session.column_statuses:
            column = status.column
            if column in targets:
                continue
            suggestion = validation.suggest_target(column, form_schema)
            if suggestion and suggestion not in taken:
                targets[column] = suggestion
                taken.add(suggestion)
            else:
                targets[column] = ""

        return targets

    def _build_rows(self):
        self._clear(self.list)
        self._rows.clear()

        targets = self._initial_targets()

        for status in self.session.column_statuses:
            column = status.column
            row = ctk.CTkFrame(
                self.list, corner_radius=theme.RADIUS, fg_color=theme.CARD_BG,
                border_width=1, border_color=theme.CARD_BORDER,
            )
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(1, weight=1)

            left = ctk.CTkFrame(row, fg_color="transparent")
            left.grid(row=0, column=0, sticky="w", padx=(14, 10), pady=12)
            ctk.CTkLabel(
                left, text=column, font=theme.font(12, "bold"),
                text_color=theme.TEXT, anchor="w", width=230,
            ).pack(anchor="w")
            apercu = self._preview(status.index)
            if apercu:
                ctk.CTkLabel(
                    left, text=apercu, font=theme.small_font(),
                    text_color=theme.TEXT_MUTED, anchor="w",
                ).pack(anchor="w")

            target = targets.get(column, "")
            variable = ctk.StringVar(
                value=self._label_for(target) if target else self.IGNORE_LABEL
            )

            menu = ctk.CTkOptionMenu(
                row, values=self._choices, variable=variable, height=34,
                corner_radius=theme.RADIUS, font=theme.small_font(),
                command=lambda _value: self._refresh_counter(),
            )
            menu.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=12)
            self._rows[column] = variable

        self._refresh_counter()

    def _preview(self, index):
        """Trois premieres valeurs : aide a reconnaitre une colonne mal nommee."""
        frame = self.session.dataframe
        if frame is None or index < 0 or index >= len(frame.columns):
            return ""
        import pandas as pd
        values = []
        for value in frame.iloc[:6, index]:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                continue
            text = str(value).strip()
            if text:
                values.append(text[:22])
            if len(values) == 3:
                break
        return "ex. " + ", ".join(values) if values else ""

    def _label_for(self, path):
        for label, target in self._path_by_label.items():
            if target == path:
                return label
        return self.IGNORE_LABEL

    # -- actions -----------------------------------------------------------

    def autofill(self):
        """Complete les colonnes restees vides, sans creer de doublon."""
        form_schema = self.session.schema
        taken = {
            self._path_by_label.get(variable.get(), "")
            for variable in self._rows.values()
        }
        taken.discard("")

        for column, variable in self._rows.items():
            if variable.get() != self.IGNORE_LABEL:
                continue
            suggestion = validation.suggest_target(column, form_schema)
            if suggestion and suggestion not in taken:
                variable.set(self._label_for(suggestion))
                taken.add(suggestion)
        self._refresh_counter()

    def reset(self):
        for variable in self._rows.values():
            variable.set(self.IGNORE_LABEL)
        self._refresh_counter()

    def collect(self):
        return {
            column: self._path_by_label.get(variable.get(), "")
            for column, variable in self._rows.items()
        }

    def _refresh_counter(self):
        overrides = self.collect()
        retained = sum(1 for target in overrides.values() if target)
        problems = validation.validate_mapping(overrides, self.session.schema)
        self.counter.configure(text=f"{retained} colonne(s) importee(s) sur {len(overrides)}")
        self.warning.configure(text=problems[0] if problems else "")
        self.apply_button.configure(state="disabled" if problems else "normal")

    def apply(self):
        overrides = self.collect()
        problems = validation.validate_mapping(overrides, self.session.schema)
        if problems:
            messagebox.showwarning(APP_TITLE, "\n".join(problems))
            return
        if not any(overrides.values()):
            messagebox.showwarning(
                APP_TITLE, "Aucune colonne n'est associee a une question : rien ne serait envoye."
            )
            return
        self.destroy()
        self._on_apply(overrides)


# ==========================================================================
# #8 - Historique des imports
# ==========================================================================

class HistoryDialog(_Dialog):
    """Montre enfin la table `runs`, alimentee a chaque import depuis la v3.0
    mais jamais affichee."""

    def __init__(self, app):
        super().__init__(app, "Historique des imports", "900x620")

        header = Card(
            self,
            title="Imports precedents",
            subtitle="Chaque execution est enregistree : ce qui est parti, ce qui a echoue, "
                     "ce qui a ete ignore parce que deja envoye.",
        )
        header.pack(fill="x", padx=16, pady=(16, 0))

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(0, 18))

        ctk.CTkButton(
            actions, text="Actualiser", height=36, width=130, corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=self.refresh,
        ).pack(side="left")

        ctk.CTkButton(
            actions, text="Ouvrir le journal CSV", height=36, width=190,
            corner_radius=theme.RADIUS,
            fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
            text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=self.open_log,
        ).pack(side="left", padx=(10, 0))

        self.summary = ctk.CTkLabel(
            actions, text="", font=theme.small_font(), text_color=theme.TEXT_MUTED,
        )
        self.summary.pack(side="right")

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=16, pady=16)
        self.refresh()

    def refresh(self):
        self._clear(self.list)
        try:
            runs = self.session.registry.recent_runs(limit=50)
        except Exception as exc:  # noqa: BLE001
            ctk.CTkLabel(
                self.list, text=f"Historique illisible : {exc}", font=theme.body_font(),
                text_color=theme.DANGER, anchor="w",
            ).pack(fill="x")
            return

        if not runs:
            ctk.CTkLabel(
                self.list, text="Aucun import enregistre pour l'instant.",
                font=theme.body_font(), text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(fill="x", pady=8)
            self.summary.configure(text="")
            return

        total_envoye = sum(int(run.get("sent") or 0) for run in runs)
        self.summary.configure(
            text=f"{len(runs)} execution(s) - {total_envoye} ligne(s) envoyee(s) au total"
        )

        for run in runs:
            self._render_run(run)

    def _render_run(self, run):
        sent = int(run.get("sent") or 0)
        failed = int(run.get("failed") or 0)
        skipped = int(run.get("skipped") or 0)
        stopped = bool(run.get("stopped"))
        dry_run = bool(run.get("dry_run"))

        if dry_run:
            libelle, tone = "Simulation", "info"
        elif stopped:
            libelle, tone = "Interrompu", "warning"
        elif failed:
            libelle, tone = "Avec echecs", "warning"
        else:
            libelle, tone = "Termine", "success"

        card = ctk.CTkFrame(
            self.list, corner_radius=theme.RADIUS, fg_color=theme.CARD_BG,
            border_width=1, border_color=theme.CARD_BORDER,
        )
        card.pack(fill="x", pady=4)

        line = ctk.CTkFrame(card, fg_color="transparent")
        line.pack(fill="x", padx=14, pady=(12, 2))
        Badge(line, text=libelle, tone=tone, width=110).pack(side="left")

        moment = str(run.get("started_at") or "").replace("T", " a ")
        ctk.CTkLabel(
            line, text=str(run.get("source_name") or "(fichier inconnu)"),
            font=theme.font(13, "bold"), text_color=theme.TEXT, anchor="w",
        ).pack(side="left", padx=(12, 0))
        ctk.CTkLabel(
            line, text=moment, font=theme.small_font(), text_color=theme.TEXT_MUTED,
        ).pack(side="right")

        detail = f"{sent} envoyee(s)"
        if failed:
            detail += f", {failed} en echec"
        if skipped:
            detail += f", {skipped} ignoree(s)"
        detail += f"  |  formulaire : {run.get('form_title') or run.get('form_uid') or '?'}"

        ctk.CTkLabel(
            card, text=detail, font=theme.small_font(), text_color=theme.TEXT_MUTED,
            anchor="w", justify="left", wraplength=760,
        ).pack(fill="x", padx=(140, 14), pady=(0, 12))

    def open_log(self):
        path = self.session.config.get("log_file", "")
        if path and os.path.exists(path):
            try:
                os.startfile(path)  # noqa: S606
                return
            except (AttributeError, OSError):
                pass
        ok, target = paths.open_in_explorer(path or paths.data_dir())
        if not ok:
            messagebox.showinfo(APP_TITLE, f"Journal introuvable.\nDossier : {target}")


# ==========================================================================
# #9 - Profils de configuration
# ==========================================================================

class ProfileDialog(_Dialog):
    def __init__(self, app):
        super().__init__(app, "Profils de configuration", "720x560", (640, 480))

        header = Card(
            self,
            title="Profils",
            subtitle="Un profil rassemble un serveur, un jeton, un formulaire et des "
                     "dossiers de travail. Basculer de l'un a l'autre evite de tout "
                     "ressaisir quand on alterne entre plusieurs projets.",
        )
        header.pack(fill="x", padx=16, pady=(16, 0))

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=16, pady=16)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=(0, 16))

        for text, command in (
            ("Nouveau", self.create),
            ("Dupliquer", self.duplicate),
            ("Renommer", self.rename),
        ):
            ctk.CTkButton(
                actions, text=text, height=38, corner_radius=theme.RADIUS,
                fg_color="transparent", border_width=1, border_color=theme.CARD_BORDER,
                text_color=theme.TEXT, hover_color=theme.NEUTRAL_BG, command=command,
            ).pack(side="left", fill="x", expand=True, padx=3)

        ctk.CTkButton(
            actions, text="Supprimer", height=38, corner_radius=theme.RADIUS,
            fg_color=theme.DANGER_BUTTON, hover_color=theme.DANGER_BUTTON_HOVER,
            command=self.delete,
        ).pack(side="left", fill="x", expand=True, padx=3)

        self.refresh()

    def refresh(self):
        self._clear(self.list)
        actif = profiles.active_name()
        self._selected = ctk.StringVar(value=actif)

        for name in profiles.list_names():
            data = config_mod.normalize(profiles.read(name))
            row = ctk.CTkFrame(
                self.list, corner_radius=theme.RADIUS, fg_color=theme.CARD_BG,
                border_width=1, border_color=theme.CARD_BORDER,
            )
            row.pack(fill="x", pady=3)

            top = ctk.CTkFrame(row, fg_color="transparent")
            top.pack(fill="x", padx=14, pady=(12, 0))

            ctk.CTkRadioButton(
                top, text=name, variable=self._selected, value=name,
                font=theme.font(13, "bold"), command=self._on_select,
            ).pack(side="left")

            if name == actif:
                Badge(top, text="Actif", tone="success").pack(side="right")

            detail = data.get("server_base_url") or "(serveur non renseigne)"
            titre = data.get("form_title")
            if titre:
                detail += f"  |  {titre}"
            if not config_mod.get_token(data):
                detail += "  |  jeton absent"

            ctk.CTkLabel(
                row, text=detail, font=theme.small_font(), text_color=theme.TEXT_MUTED,
                anchor="w", justify="left", wraplength=620,
            ).pack(fill="x", padx=(38, 14), pady=(0, 12))

    def _on_select(self):
        target = self._selected.get()
        if target == profiles.active_name():
            return
        if not messagebox.askyesno(
            APP_TITLE,
            f"Basculer sur le profil « {target} » ?\n\n"
            "L'application revient a la premiere etape et recharge la configuration.",
        ):
            self._selected.set(profiles.active_name())
            return
        self.app.switch_profile(target)
        self.refresh()

    def _ask_name(self, title, initial=""):
        dialog = ctk.CTkInputDialog(title=title, text="Nom du profil :")
        if initial:
            try:
                dialog._entry.insert(0, initial)
            except Exception:  # noqa: BLE001 - detail interne de customtkinter
                pass
        return profiles.clean_name(dialog.get_input())

    def create(self):
        name = self._ask_name("Nouveau profil")
        if not name:
            return
        try:
            # Part des reglages courants : serveur et dossiers sont souvent
            # communs, seul le formulaire change d'un projet a l'autre.
            base = config_mod.as_payload(self.session.config)
            base["asset_uid"] = ""
            base["form_title"] = ""
            base["form_version"] = ""
            base["excel_file"] = ""
            profiles.create(name, base)
        except profiles.ProfileError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.app.switch_profile(name)
        self.refresh()

    def duplicate(self):
        source = self._selected.get()
        name = self._ask_name("Dupliquer le profil", f"{source} (copie)")
        if not name:
            return
        try:
            profiles.duplicate(source, name)
        except profiles.ProfileError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.app.switch_profile(name)
        self.refresh()

    def rename(self):
        source = self._selected.get()
        name = self._ask_name("Renommer le profil", source)
        if not name or name == source:
            return
        try:
            profiles.rename(source, name)
        except profiles.ProfileError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.app.switch_profile(profiles.active_name())
        self.refresh()

    def delete(self):
        target = self._selected.get()
        if not messagebox.askyesno(
            APP_TITLE,
            f"Supprimer le profil « {target} » ?\n\n"
            "Le serveur, le jeton et le formulaire qu'il contient seront perdus. "
            "L'historique des imports, lui, est conserve.",
        ):
            return
        try:
            profiles.delete(target)
        except profiles.ProfileError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.app.switch_profile(profiles.active_name())
        self.refresh()

"""Composants d'interface reutilisables."""

import customtkinter as ctk

from . import theme


class Card(ctk.CTkFrame):
    """Bloc blanc arrondi avec un titre optionnel."""

    def __init__(self, parent, title="", subtitle="", **kwargs):
        kwargs.setdefault("corner_radius", theme.CARD_RADIUS)
        kwargs.setdefault("fg_color", theme.CARD_BG)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.CARD_BORDER)
        super().__init__(parent, **kwargs)

        self.body = self
        if title:
            ctk.CTkLabel(
                self, text=title, font=theme.heading_font(), text_color=theme.TEXT,
                anchor="w",
            ).pack(fill="x", padx=18, pady=(16, 2 if subtitle else 10))
        if subtitle:
            ctk.CTkLabel(
                self, text=subtitle, font=theme.small_font(), text_color=theme.TEXT_MUTED,
                anchor="w", justify="left",
            ).pack(fill="x", padx=18, pady=(0, 10))


class Badge(ctk.CTkLabel):
    """Pastille coloree : etat d'une colonne, d'une etape, d'un resultat."""

    def __init__(self, parent, text="", tone="info", width=0, **kwargs):
        foreground, background = theme.TONES.get(tone, theme.TONES["info"])
        kwargs.setdefault("corner_radius", 999)
        kwargs.setdefault("fg_color", background)
        kwargs.setdefault("text_color", foreground)
        kwargs.setdefault("font", theme.small_font())
        kwargs.setdefault("padx", 10)
        kwargs.setdefault("pady", 3)
        if width:
            kwargs.setdefault("width", width)
        super().__init__(parent, text=text, **kwargs)

    def update_tone(self, text, tone):
        foreground, background = theme.TONES.get(tone, theme.TONES["info"])
        self.configure(text=text, fg_color=background, text_color=foreground)


class StepIndicator(ctk.CTkFrame):
    """Fil d'Ariane numerote de l'assistant (point 12)."""

    def __init__(self, parent, titles, on_click=None):
        super().__init__(parent, fg_color="transparent")
        self._on_click = on_click
        self._chips = []
        self._max_reached = 0

        for index, title in enumerate(titles):
            if index:
                ctk.CTkLabel(
                    self, text="›", font=theme.font(16), text_color=theme.TEXT_MUTED,
                ).pack(side="left", padx=6)

            chip = ctk.CTkButton(
                self,
                text=f"  {index + 1}. {title}  ",
                height=32,
                corner_radius=999,
                font=theme.font(12, "bold"),
                fg_color="transparent",
                text_color=theme.TEXT_MUTED,
                hover_color=theme.NEUTRAL_BG,
                command=lambda position=index: self._clicked(position),
            )
            chip.pack(side="left")
            self._chips.append(chip)

    def _clicked(self, index):
        if self._on_click and index <= self._max_reached:
            self._on_click(index)

    def set_current(self, index):
        self._max_reached = max(self._max_reached, index)
        for position, chip in enumerate(self._chips):
            if position == index:
                chip.configure(fg_color=theme.PRIMARY, text_color=theme.TEXT_ON_ACCENT)
            elif position <= self._max_reached:
                chip.configure(fg_color="transparent", text_color=theme.PRIMARY)
            else:
                chip.configure(fg_color="transparent", text_color=theme.TEXT_MUTED)


class LabelledEntry(ctk.CTkFrame):
    """Libelle, champ de saisie et aide, empiles verticalement."""

    def __init__(self, parent, label, variable, placeholder="", help_text="", show=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        ctk.CTkLabel(
            self, text=label, font=theme.font(13, "bold"), text_color=theme.TEXT, anchor="w",
        ).pack(fill="x", pady=(0, 4))

        self.entry = ctk.CTkEntry(
            self, textvariable=variable, placeholder_text=placeholder, height=38, show=show,
            corner_radius=theme.RADIUS,
        )
        self.entry.pack(fill="x")

        if help_text:
            ctk.CTkLabel(
                self, text=help_text, font=theme.small_font(), text_color=theme.TEXT_MUTED,
                anchor="w", justify="left", wraplength=620,
            ).pack(fill="x", pady=(4, 0))


class Journal(ctk.CTkFrame):
    """Zone de journal a memoire bornee (point 18).

    Sans plafond, un import de 50 000 lignes accumulait autant de lignes de
    texte et rendait la fenetre inutilisable. Les messages arrivent par paquets
    et les plus anciens sont oublies.
    """

    MAX_LINES = 1500
    TRIM_TO = 1000

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text="Journal", font=theme.heading_font(), text_color=theme.TEXT, anchor="w",
        ).pack(side="left")
        self.counter = ctk.CTkLabel(
            header, text="", font=theme.small_font(), text_color=theme.TEXT_MUTED,
        )
        self.counter.pack(side="right")

        # Hauteur naturelle volontairement modeste : la zone s'etire sur un
        # grand ecran, mais ne pousse pas les boutons hors de la fenetre sur un
        # petit (point 13). Le defilement interne prend le relais.
        self.textbox = ctk.CTkTextbox(
            self, wrap="word", corner_radius=theme.RADIUS, font=theme.mono_font(),
            border_width=1, border_color=theme.CARD_BORDER, height=130,
        )
        self.textbox.pack(fill="both", expand=True, pady=(8, 0))
        self.textbox.configure(state="disabled")

        for tone, colors in theme.TONES.items():
            self.textbox.tag_config(tone, foreground=self._pick(colors[0]))

        self._lines = 0
        self._errors = 0

    @staticmethod
    def _pick(pair):
        mode = ctk.get_appearance_mode()
        return pair[1] if mode == "Dark" else pair[0]

    def append_many(self, entries):
        """entries : liste de (message, niveau)."""
        if not entries:
            return
        self.textbox.configure(state="normal")
        for message, level in entries:
            self.textbox.insert("end", message + "\n", level)
            self._lines += 1
            if level == "error":
                self._errors += 1

        if self._lines > self.MAX_LINES:
            excess = self._lines - self.TRIM_TO
            self.textbox.delete("1.0", f"{excess + 1}.0")
            self._lines = self.TRIM_TO
            self.textbox.insert("1.0", "[... lignes anciennes retirees ...]\n", "muted")

        self.textbox.see("end")
        self.textbox.configure(state="disabled")
        self.counter.configure(
            text=f"{self._errors} erreur(s)" if self._errors else ""
        )

    def clear(self):
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")
        self._lines = 0
        self._errors = 0
        self.counter.configure(text="")

    def dump(self):
        return self.textbox.get("1.0", "end").rstrip()


class SummaryTiles(ctk.CTkFrame):
    """Rangee de compteurs (envoyees / echecs / ignorees...)."""

    def __init__(self, parent, keys):
        super().__init__(parent, fg_color="transparent")
        self._labels = {}
        for column, (key, caption, tone) in enumerate(keys):
            self.grid_columnconfigure(column, weight=1)
            tile = ctk.CTkFrame(
                self, corner_radius=theme.RADIUS, fg_color=theme.CARD_BG,
                border_width=1, border_color=theme.CARD_BORDER,
            )
            tile.grid(row=0, column=column, sticky="nsew", padx=4)
            value = ctk.CTkLabel(
                tile, text="0", font=ctk.CTkFont(size=24, weight="bold"),
                text_color=theme.TONES.get(tone, theme.TONES["info"])[0],
            )
            value.pack(padx=14, pady=(12, 0))
            ctk.CTkLabel(
                tile, text=caption, font=theme.small_font(), text_color=theme.TEXT_MUTED,
            ).pack(padx=14, pady=(0, 12))
            self._labels[key] = value

    def set(self, **values):
        for key, value in values.items():
            if key in self._labels:
                self._labels[key].configure(text=str(value))

    def reset(self):
        for label in self._labels.values():
            label.configure(text="0")


def spacer(parent, height=12):
    frame = ctk.CTkFrame(parent, fg_color="transparent", height=height)
    frame.pack(fill="x")
    return frame

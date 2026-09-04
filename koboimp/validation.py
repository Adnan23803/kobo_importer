"""Point 10 et 24 : controle a blanc du fichier avant tout appel reseau.

L'objectif est de repondre en quelques secondes, hors ligne, a la question
"est-ce que mon fichier va passer ?" plutot que de decouvrir 3000 erreurs 400
apres vingt minutes d'envoi.

Les controles sont vectorises colonne par colonne : un fichier de 50 000 lignes
se verifie sans bloquer l'interface.
"""

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import xmlbuild

# Statuts de colonne, dans l'ordre d'affichage de l'ecran de correspondance.
COL_OK = "ok"
COL_UNKNOWN = "unknown"
COL_INVALID = "invalid"
COL_DUPLICATE = "duplicate"
COL_ATTACHMENT = "attachment"
COL_REPEAT = "repeat"
COL_FORCED_IGNORE = "forced_ignore"   # colonne mise de cote par l utilisateur

MAX_REPORTED_ISSUES = 500


@dataclass
class ColumnStatus:
    column: str
    status: str
    index: int = -1          # position dans le tableau : seule reference fiable
    path: str = ""
    question: object = None
    message: str = ""
    suggestion: str = ""

    manual: bool = False     # correspondance choisie par l utilisateur (point 3)

    @property
    def is_mapped(self):
        return self.status == COL_OK


@dataclass
class RowIssue:
    row_number: int      # numero de ligne tel qu'affiche dans Excel
    column: str
    value: str
    message: str


@dataclass
class ValidationReport:
    total_rows: int = 0
    columns: list = field(default_factory=list)
    missing_required: list = field(default_factory=list)
    row_issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    invalid_row_mask: object = None
    issue_count: int = 0

    @property
    def mapped_columns(self):
        return [column for column in self.columns if column.is_mapped]

    @property
    def unmapped_columns(self):
        return [column for column in self.columns if not column.is_mapped]

    @property
    def invalid_rows(self):
        if self.invalid_row_mask is None:
            return 0
        return int(self.invalid_row_mask.sum())

    @property
    def valid_rows(self):
        return max(0, self.total_rows - self.invalid_rows)

    @property
    def has_blocking_errors(self):
        """Erreurs qui rendent l'import impossible, meme partiellement."""
        return bool(self.missing_required) or not self.mapped_columns

    @property
    def truncated(self):
        return self.issue_count > len(self.row_issues)

    def headline(self):
        if self.has_blocking_errors:
            if not self.mapped_columns:
                return "Aucune colonne ne correspond au formulaire."
            noms = ", ".join(question.display_label() for question in self.missing_required[:3])
            extra = "..." if len(self.missing_required) > 3 else ""
            return f"Question(s) obligatoire(s) absente(s) du fichier : {noms}{extra}"
        if self.invalid_rows:
            return f"{self.valid_rows} ligne(s) prete(s), {self.invalid_rows} a corriger."
        return f"{self.valid_rows} ligne(s) prete(s) a etre envoyee(s)."


# --------------------------------------------------------------------------
# Correspondance colonnes <-> questions (point 10)
# --------------------------------------------------------------------------

def map_columns(columns, form_schema, overrides=None):
    """Associe chaque colonne du fichier a une question du formulaire.

    Chaque statut retient la position de la colonne : deux en-tetes devenus
    identiques apres nettoyage des espaces ne doivent pas se confondre.

    Point 3 : `overrides` associe un nom de colonne au chemin de la question
    choisie a la main. La chaine vide met explicitement la colonne de cote.
    Ces choix priment sur la correspondance automatique par nom, ce qui rend
    l application utilisable avec un fichier qu elle n a pas produit : plus
    besoin de renommer les en-tetes dans Excel pour qu elles tombent juste.
    """
    statuses = []
    seen = {}
    overrides = overrides or {}

    for position, raw_column in enumerate(columns):
        column = str(raw_column).strip()

        if column in overrides:
            statuses.append(_apply_override(
                column, position, overrides[column], form_schema,
            ))
            continue

        if not column or column.lower().startswith("unnamed:"):
            statuses.append(ColumnStatus(
                column=str(raw_column),
                status=COL_INVALID,
                index=position,
                message="Colonne sans en-tete : elle sera ignoree.",
            ))
            continue

        if column.lower() in xmlbuild.RESERVED_COLUMNS:
            statuses.append(ColumnStatus(
                column=column,
                status=COL_UNKNOWN,
                index=position,
                message="Colonne technique, ignoree a l'envoi.",
            ))
            continue

        key = column.lower()
        if key in seen:
            statuses.append(ColumnStatus(
                column=column,
                status=COL_DUPLICATE,
                index=position,
                message=f"Colonne en double avec '{seen[key]}' : la seconde est ignoree.",
            ))
            continue
        seen[key] = column

        if not xmlbuild.is_valid_path(column):
            statuses.append(ColumnStatus(
                column=column,
                status=COL_INVALID,
                index=position,
                message="Nom incompatible avec le format d'envoi "
                        "(espace, accent ou caractere special).",
                suggestion=xmlbuild.sanitize_path(column),
            ))
            continue

        question = form_schema.get(column) if form_schema else None
        if question is None:
            statuses.append(ColumnStatus(
                column=column,
                status=COL_UNKNOWN,
                index=position,
                message="Aucune question de ce nom dans le formulaire : colonne ignoree.",
            ))
            continue

        if question.is_attachment:
            statuses.append(ColumnStatus(
                column=column, status=COL_ATTACHMENT, index=position,
                path=question.path, question=question,
                message="Champ de type fichier : l'import Excel ne transporte pas de piece jointe.",
            ))
            continue

        if question.in_repeat:
            statuses.append(ColumnStatus(
                column=column, status=COL_REPEAT, index=position,
                path=question.path, question=question,
                message="Question dans un groupe repete : non pris en charge par un tableau plat.",
            ))
            continue

        statuses.append(ColumnStatus(
            column=column, status=COL_OK, index=position,
            path=question.path, question=question,
            message=question.display_label(),
        ))

    return statuses


def _apply_override(column, position, target_path, form_schema):
    """Construit le statut d une colonne dont l utilisateur a fixe la cible."""
    target = str(target_path or "").strip()

    if not target:
        return ColumnStatus(
            column=column, status=COL_FORCED_IGNORE, index=position, manual=True,
            message="Colonne volontairement ecartee.",
        )

    question = form_schema.get(target) if form_schema else None
    if question is None:
        return ColumnStatus(
            column=column, status=COL_UNKNOWN, index=position, manual=True,
            message=f"La question « {target} » n existe plus dans ce formulaire : "
                    "corrigez la correspondance.",
        )
    if question.is_attachment:
        return ColumnStatus(
            column=column, status=COL_ATTACHMENT, index=position, manual=True,
            path=question.path, question=question,
            message="Champ de type fichier : l import ne transporte pas de piece jointe.",
        )
    if question.in_repeat:
        return ColumnStatus(
            column=column, status=COL_REPEAT, index=position, manual=True,
            path=question.path, question=question,
            message="Question dans un groupe repete : non pris en charge.",
        )
    return ColumnStatus(
        column=column, status=COL_OK, index=position, manual=True,
        path=question.path, question=question,
        message=question.display_label(),
    )


def suggest_target(column, form_schema):
    """Propose la question la plus plausible pour une colonne non reconnue.

    Comparaison sur une forme reduite (sans accent, sans ponctuation, en
    minuscules) du nom technique puis du libelle. Sert a pre-remplir l ecran de
    correspondance, jamais a decider seul : la suggestion reste a confirmer.
    """
    if not form_schema:
        return ""
    needle = _reduce(column)
    if not needle:
        return ""

    for question in form_schema.importable:
        if _reduce(question.path) == needle or _reduce(question.name) == needle:
            return question.path
    for question in form_schema.importable:
        if question.label and _reduce(question.label) == needle:
            return question.path
    # Rapprochement par mot entier : « Age (ans) » propose la question « age »,
    # sans que « Village principal » la propose aussi parce qu il contient les
    # lettres a-g-e. C est ce qu une comparaison par sous-chaine ferait.
    words = _words(column)
    if words:
        for question in form_schema.importable:
            for candidate in (question.name, question.path.split("/")[-1]):
                reduced = _reduce(candidate)
                if reduced and reduced in words:
                    return question.path

    # Enfin, inclusion de chaines, reservee aux noms assez longs pour que la
    # coincidence soit improbable.
    if len(needle) >= 5:
        for question in form_schema.importable:
            for candidate in (question.label, question.name, question.path):
                reduced = _reduce(candidate)
                if len(reduced) >= 5 and (needle in reduced or reduced in needle):
                    return question.path
    return ""


def _words(text):
    """Mots reduits d un en-tete : « Age (ans) » -> {'age', 'ans'}."""
    import re
    return {
        _reduce(part) for part in re.split(r"[^0-9A-Za-zÀ-ɏ]+", str(text or ""))
        if _reduce(part)
    }


def _reduce(text):
    import unicodedata
    raw = unicodedata.normalize("NFKD", str(text or ""))
    ascii_only = "".join(char for char in raw if not unicodedata.combining(char))
    return "".join(char for char in ascii_only.lower() if char.isalnum())


def validate_mapping(overrides, form_schema):
    """Signale les correspondances impossibles : cible inconnue ou en double."""
    problems = []
    used = {}
    for column, target in (overrides or {}).items():
        target = str(target or "").strip()
        if not target:
            continue
        if form_schema and form_schema.get(target) is None:
            problems.append(f"« {column} » vise une question inexistante : {target}")
            continue
        if target in used:
            problems.append(
                f"« {column} » et « {used[target]} » visent la meme question : {target}"
            )
            continue
        used[target] = column
    return problems


def missing_required_questions(statuses, form_schema):
    if not form_schema:
        return []
    mapped = {status.path for status in statuses if status.is_mapped}
    return [
        question for question in form_schema.importable
        if question.required
        and not question.is_metadata
        and question.path not in mapped
    ]


# --------------------------------------------------------------------------
# Controle des valeurs
# --------------------------------------------------------------------------

def _blank_mask(series):
    """Cellules vides : NaN, chaine vide ou uniquement des espaces."""
    mask = series.isna()
    try:
        text = series.astype("string")
        mask = mask | text.str.strip().eq("")
    except (TypeError, ValueError):
        pass
    return mask.fillna(True) if hasattr(mask, "fillna") else mask


def _as_text(series):
    return series.astype("string").str.strip()


def _numeric_errors(series, present):
    numbers = pd.to_numeric(series.where(present), errors="coerce")
    return present & numbers.isna(), numbers


def _datetime_errors(series, present):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            parsed = pd.to_datetime(series.where(present), errors="coerce", dayfirst=True)
        except (TypeError, ValueError):
            parsed = series.where(present).map(
                lambda value: pd.to_datetime(value, errors="coerce", dayfirst=True)
            )
    return present & pd.isna(parsed), parsed


def _select_one_errors(series, present, question):
    valid = question.choice_names
    if not valid:
        return pd.Series(False, index=series.index)
    text = _as_text(series)
    return present & ~text.isin(valid)


def _select_multiple_errors(series, present, question):
    valid = question.choice_names
    if not valid:
        return pd.Series(False, index=series.index)

    def has_unknown(value):
        formatted = xmlbuild.format_select_multiple(value)
        if not formatted:
            return False
        return any(token not in valid for token in formatted.split(" "))

    flags = series.where(present).map(has_unknown)
    return present & flags.fillna(False).astype(bool)


def _time_errors(series, present):
    def unparsable(value):
        return xmlbuild.format_time(value) is None

    flags = series.where(present).map(unparsable)
    return present & flags.fillna(False).astype(bool)


def _geopoint_errors(series, present):
    def bad(value):
        formatted = xmlbuild.format_geopoint(value)
        if not formatted:
            return True
        parts = formatted.split(" ")
        if len(parts) < 2:
            return True
        try:
            float(parts[0]), float(parts[1])
        except ValueError:
            return True
        return False

    flags = series.where(present).map(bad)
    return present & flags.fillna(False).astype(bool)


def _constraint_errors(series, candidates, question):
    """Point 10 : applique la regle de saisie du formulaire.

    N'est evalue que la ou le type est deja correct : signaler « nombre attendu »
    et « valeur hors limites » sur la meme cellule donnerait deux corrections a
    faire la ou il n'y en a qu'une.
    """
    if not question.has_constraint or not candidates.any():
        return pd.Series(False, index=series.index)
    rule = question.constraint
    flags = series.where(candidates).map(lambda value: not rule.check(value))
    return candidates & flags.fillna(False).astype(bool)


def _record_errors(report, status, series, mask, message, invalid, remaining):
    """Enregistre un lot d'erreurs et retourne (masque cumule, quota restant)."""
    if mask is None or not mask.any():
        return invalid, remaining
    count = int(mask.sum())
    report.issue_count += count
    for position, value in _sample_values(series, mask, max(0, remaining)):
        report.row_issues.append(RowIssue(
            row_number=position + 2,
            column=status.column,
            value="" if pd.isna(value) else str(value),
            message=message,
        ))
    return invalid | mask, max(0, remaining - count)


def _sample_values(series, mask, limit):
    positions = np.flatnonzero(np.asarray(mask, dtype=bool))[:limit]
    return [(int(position), series.iloc[position]) for position in positions]


def validate_dataframe(dataframe, form_schema, max_issues=MAX_REPORTED_ISSUES, overrides=None):
    """Verifie le tableau contre le formulaire, sans aucun appel reseau.

    `overrides` transmet la correspondance manuelle (point 3) : sans elle, le
    controle jugerait le fichier sur les noms de colonnes bruts et signalerait
    comme inconnues des colonnes que l utilisateur a pourtant associees.
    """
    report = ValidationReport(total_rows=int(len(dataframe)))
    report.columns = map_columns(dataframe.columns, form_schema, overrides)
    report.missing_required = missing_required_questions(report.columns, form_schema)

    invalid = pd.Series(False, index=dataframe.index)

    if form_schema is None:
        report.warnings.append(
            "Formulaire non charge : seule la validite des noms de colonnes a ete verifiee."
        )
        report.invalid_row_mask = invalid
        return report

    ignorees = [
        question for question in form_schema.importable
        if question.constraint is not None
        and question.constraint.expression
        and not question.constraint.supported
    ]
    if ignorees:
        report.warnings.append(
            f"{len(ignorees)} regle(s) de saisie du formulaire dependent d'autres "
            "reponses ou de la date du jour : elles ne peuvent pas etre verifiees "
            "ici et seront controlees par le serveur."
        )

    if form_schema.has_repeats:
        report.warnings.append(
            "Ce formulaire contient un groupe repete : ces questions ne peuvent pas "
            "etre renseignees depuis un tableau Excel."
        )
    if form_schema.has_attachments:
        report.warnings.append(
            "Ce formulaire attend des photos ou fichiers : ils ne sont pas transmis par l'import."
        )

    remaining = max_issues

    for status in report.columns:
        if not status.is_mapped:
            continue

        question = status.question
        series = dataframe.iloc[:, status.index]
        blank = _blank_mask(series)
        present = ~blank
        errors = None
        message = ""

        if question.required and not question.is_metadata:
            invalid, remaining = _record_errors(
                report, status, series, blank,
                "Reponse obligatoire manquante.", invalid, remaining,
            )

        kind = question.type
        if kind == "integer":
            errors, numbers = _numeric_errors(series, present)
            non_integer = present & ~errors & (numbers != numbers.round())
            errors = errors | non_integer
            message = "Nombre entier attendu."
        elif kind in ("decimal", "range"):
            errors, _ = _numeric_errors(series, present)
            message = "Nombre attendu."
        elif kind in ("date", "today"):
            errors, _ = _datetime_errors(series, present)
            message = "Date illisible (format attendu : AAAA-MM-JJ)."
        elif kind in ("datetime", "start", "end"):
            errors, _ = _datetime_errors(series, present)
            message = "Date et heure illisibles."
        elif kind == "time":
            errors = _time_errors(series, present)
            message = "Heure illisible (format attendu : HH:MM:SS)."
        elif kind == "select_one":
            errors = _select_one_errors(series, present, question)
            message = "Valeur absente de la liste de choix du formulaire."
        elif kind in ("select_multiple", "select_multiple_from_file"):
            errors = _select_multiple_errors(series, present, question)
            message = "Un ou plusieurs choix n'existent pas dans le formulaire."
        elif kind in ("geopoint", "geoshape", "geotrace"):
            errors = _geopoint_errors(series, present)
            message = "Coordonnees illisibles (latitude puis longitude attendues)."

        if errors is not None and errors.any():
            detail = message
            if question.is_select and question.choices:
                apercu = ", ".join(choice.name for choice in question.choices[:6])
                if len(question.choices) > 6:
                    apercu += ", ..."
                detail = f"{message} Choix acceptes : {apercu}"
            invalid, remaining = _record_errors(
                report, status, series, errors, detail, invalid, remaining,
            )

        # Regle de saisie du formulaire, sur les cellules de type correct.
        well_typed = present if errors is None else (present & ~errors)
        breaches = _constraint_errors(series, well_typed, question)
        invalid, remaining = _record_errors(
            report, status, series, breaches,
            question.constraint.explain() if question.has_constraint else "",
            invalid, remaining,
        )

    report.invalid_row_mask = invalid
    report.row_issues.sort(key=lambda issue: (issue.row_number, issue.column))
    return report


def failures_from_report(report):
    """Convertit un controle a blanc en liste d'echecs exportable (point 11).

    Permet de produire, avant tout envoi, le meme classeur « a corriger » que
    celui genere apres un import.
    """
    grouped = {}
    for issue in report.row_issues:
        position = issue.row_number - 2
        grouped.setdefault(position, []).append(f"{issue.column} : {issue.message}")
    return [
        {
            "row_index": position,
            "status": "CONTROLE",
            "http_status": None,
            "message": " | ".join(messages),
        }
        for position, messages in sorted(grouped.items())
    ]


def validate_row_values(row_mapping, form_schema):
    """Point 24 : controle unitaire, utilise par le moteur en dernier rempart.

    Retourne (True, "") ou (False, motif). Le gros du travail est deja fait par
    validate_dataframe ; cette fonction rattrape les cas ou l'utilisateur lance
    l'import sans avoir verifie le fichier.
    """
    if form_schema is None:
        return True, ""

    for question in form_schema.importable:
        if not question.required or question.is_metadata:
            continue
        value = row_mapping.get(question.path)
        if value is None:
            value = row_mapping.get(question.name)
        if xmlbuild.format_value(value, question.type) is None:
            return False, f"Reponse obligatoire manquante : {question.display_label()}"
    return True, ""

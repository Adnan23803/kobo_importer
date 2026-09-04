"""Lecture du schema d'un formulaire KoboToolbox.

L'API /api/v2/assets/<uid> renvoie la definition XLSForm complete du formulaire :
questions, types, listes de choix, groupes, champs obligatoires. C'est ce qui
permet les points 8 (plus de saisie manuelle de l'UID et de la version),
9 (modele Excel genere), 10 (correspondance colonnes et controle a blanc).
"""

from dataclasses import dataclass, field

from . import constraints as constraints_mod

# Elements de structure : ils ne portent pas de reponse.
STRUCTURE_OPEN = {
    "begin_group", "begin group", "begin_repeat", "begin repeat",
    "begin_kobomatrix", "begin_score", "begin_rank",
}
STRUCTURE_CLOSE = {
    "end_group", "end group", "end_repeat", "end repeat",
    "end_kobomatrix", "end_score", "end_rank",
}
REPEAT_OPEN = {"begin_repeat", "begin repeat"}

# Types presents dans le formulaire mais jamais soumis.
NON_SUBMITTED_TYPES = {"note"}

# Types de metadonnees ajoutes automatiquement par Kobo : jamais obligatoires
# pour un import Excel.
METADATA_TYPES = {
    "start", "end", "today", "deviceid", "subscriberid", "simserial",
    "phonenumber", "username", "audit", "start-geopoint",
}

# Types qui referencent un fichier joint : hors perimetre d'un import Excel.
ATTACHMENT_TYPES = {"image", "audio", "video", "file", "background-audio"}

NUMERIC_TYPES = {"integer", "decimal", "range"}
TEMPORAL_TYPES = {"date", "datetime", "time", "start", "end", "today"}


@dataclass
class Choice:
    name: str
    label: str = ""

    def display(self):
        return f"{self.name} - {self.label}" if self.label else self.name


@dataclass
class Question:
    name: str
    path: str
    type: str
    label: str = ""
    required: bool = False
    list_name: str = ""
    choices: list = field(default_factory=list)
    in_repeat: bool = False
    group_path: str = ""
    hint: str = ""
    constraint: object = None      # constraints.Constraint (point 10)

    @property
    def has_constraint(self):
        return self.constraint is not None and self.constraint.supported

    @property
    def is_metadata(self):
        return self.type in METADATA_TYPES

    @property
    def is_attachment(self):
        return self.type in ATTACHMENT_TYPES

    @property
    def is_select(self):
        return self.type.startswith("select_")

    @property
    def choice_names(self):
        return {choice.name for choice in self.choices}

    def display_label(self):
        return self.label or self.name


@dataclass
class FormSchema:
    uid: str = ""
    title: str = ""
    version: str = ""
    submission_count: int = 0
    deployed: bool = False
    questions: list = field(default_factory=list)
    repeat_groups: list = field(default_factory=list)

    def __post_init__(self):
        self._by_path = {question.path: question for question in self.questions}
        self._by_lower = {question.path.lower(): question for question in self.questions}
        self._by_name = {}
        for question in self.questions:
            # Une question dans un groupe reste trouvable par son nom court,
            # tant que ce nom n'est pas ambigu.
            self._by_name.setdefault(question.name.lower(), []).append(question)

    def get(self, path):
        """Retrouve une question par chemin complet, sans casse, ou par nom court."""
        key = str(path or "").strip()
        if not key:
            return None
        if key in self._by_path:
            return self._by_path[key]
        lowered = key.lower()
        if lowered in self._by_lower:
            return self._by_lower[lowered]
        candidates = self._by_name.get(lowered, [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    @property
    def importable(self):
        """Questions qu'un import Excel peut renseigner."""
        return [
            question for question in self.questions
            if not question.is_attachment and not question.in_repeat
        ]

    @property
    def required_paths(self):
        return [
            question.path for question in self.importable
            if question.required and not question.is_metadata
        ]

    def types_by_path(self):
        return {question.path: question.type for question in self.questions}

    @property
    def constrained_questions(self):
        """Questions dont la regle de saisie est verifiable hors ligne."""
        return [question for question in self.importable if question.has_constraint]

    @property
    def has_repeats(self):
        return bool(self.repeat_groups)

    @property
    def has_attachments(self):
        return any(question.is_attachment for question in self.questions)


# --------------------------------------------------------------------------
# Analyse du JSON renvoye par l'API
# --------------------------------------------------------------------------

def _first_label(value):
    """Le libelle est une liste (une entree par traduction) ou une chaine."""
    if isinstance(value, list):
        for item in value:
            if item:
                return str(item).strip()
        return ""
    if value in (None, False):
        return ""
    return str(value).strip()


def _row_name(row):
    for key in ("name", "$autoname", "$autovalue"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


def _split_type(raw_type):
    """'select_one oui_non' -> ('select_one', 'oui_non')."""
    parts = str(raw_type or "").strip().split()
    if not parts:
        return "", ""
    base = parts[0]
    list_name = parts[1] if len(parts) > 1 else ""
    return base, list_name


def _parse_choices(content):
    """Regroupe les choix par nom de liste."""
    grouped = {}
    for row in content.get("choices") or []:
        if not isinstance(row, dict):
            continue
        list_name = str(row.get("list_name") or row.get("$list_name") or "").strip()
        name = _row_name(row)
        if not list_name or not name:
            continue
        grouped.setdefault(list_name, []).append(Choice(name=name, label=_first_label(row.get("label"))))
    return grouped


def parse_asset(asset):
    """Construit un FormSchema a partir de la reponse /api/v2/assets/<uid>."""
    if not isinstance(asset, dict):
        raise ValueError("Reponse de formulaire inattendue.")

    content = asset.get("content") or {}
    choices_by_list = _parse_choices(content)

    questions = []
    repeat_groups = []
    stack = []          # chemins des groupes ouverts
    repeat_depth = 0

    for row in content.get("survey") or []:
        if not isinstance(row, dict):
            continue

        raw_type = str(row.get("type") or "").strip()
        base_type, inline_list = _split_type(raw_type)
        name = _row_name(row)

        if base_type in STRUCTURE_OPEN:
            if name:
                stack.append(name)
            if base_type in REPEAT_OPEN:
                repeat_depth += 1
                repeat_groups.append("/".join(stack))
            continue

        if base_type in STRUCTURE_CLOSE:
            if stack:
                stack.pop()
            if base_type in {"end_repeat", "end repeat"} and repeat_depth > 0:
                repeat_depth -= 1
            continue

        if not name or not base_type or base_type in NON_SUBMITTED_TYPES:
            continue

        list_name = str(row.get("select_from_list_name") or inline_list or "").strip()
        group_path = "/".join(stack)
        path = f"{group_path}/{name}" if group_path else name

        questions.append(Question(
            name=name,
            path=path,
            type=base_type,
            label=_first_label(row.get("label")),
            required=bool(row.get("required")),
            list_name=list_name,
            choices=list(choices_by_list.get(list_name, [])),
            in_repeat=repeat_depth > 0,
            group_path=group_path,
            hint=_first_label(row.get("hint")),
            # Point 10 : la regle de saisie du formulaire est desormais lue et,
            # quand elle est comprise sans ambiguite, verifiee hors ligne.
            constraint=constraints_mod.compile_constraint(
                row.get("constraint"),
                _first_label(row.get("constraint_message")),
            ),
        ))

    version = (
        asset.get("deployed_version_id")
        or asset.get("version_id")
        or ""
    )

    return FormSchema(
        uid=str(asset.get("uid") or "").strip(),
        title=str(asset.get("name") or "").strip(),
        version=str(version).strip(),
        submission_count=int(asset.get("deployment__submission_count") or 0),
        deployed=bool(asset.get("has_deployment") and asset.get("deployment__active")),
        questions=questions,
        repeat_groups=repeat_groups,
    )


def summarize_form_entry(asset):
    """Resume court d'un formulaire pour la liste deroulante (point 8)."""
    return {
        "uid": str(asset.get("uid") or "").strip(),
        "title": str(asset.get("name") or "(sans titre)").strip(),
        "deployed": bool(asset.get("has_deployment") and asset.get("deployment__active")),
        "submissions": int(asset.get("deployment__submission_count") or 0),
    }

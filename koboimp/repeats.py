"""Groupes repetes : rattachement des feuilles enfants aux lignes principales.

Un tableau plat ne peut pas porter une question posee plusieurs fois. La
convention retenue est celle des **exports Excel de KoboToolbox eux-memes** :

    feuille principale        une ligne par soumission, colonne `_index`
    feuille « membres »       une ligne par repetition, colonne `_parent_index`

`_parent_index` d'une ligne enfant vaut le `_index` de la ligne principale a
laquelle elle se rattache. Choisir cette convention plutot qu'une invention
maison a une consequence pratique : un export Kobo corrige dans Excel se
reimporte tel quel, sans retouche de structure.

Si la feuille principale ne porte pas de colonne `_index`, le numero de ligne
(1, 2, 3...) en tient lieu : c'est exactement ce que Kobo y met, et cela rend le
modele genere remplissable a la main sans rien comprendre a la mecanique.
"""

from dataclasses import dataclass, field

import pandas as pd

# Colonnes de liaison, telles que nommees par les exports KoboToolbox.
PARENT_KEY = "_index"
CHILD_KEY = "_parent_index"

# Autres graphies rencontrees selon l'outil qui a produit le classeur.
# Cote principal, seuls des identifiants de ligne. « parent_id » et
# « id_parent » en sont volontairement exclus : ce sont des noms cote enfant,
# et une colonne metier ainsi nommee dans la feuille principale serait prise
# pour une cle de structure, faussant tout le rattachement.
PARENT_ALIASES = ("_index", "index", "_id")
CHILD_ALIASES = ("_parent_index", "parent_index", "_parent_id", "parent_id", "id_parent")


@dataclass
class RepeatData:
    """Une feuille de repetition, lue, rattachee et verifiee."""

    group: object                       # schema.RepeatGroup
    sheet: str = ""
    frame: object = None
    column_statuses: list = field(default_factory=list)
    by_parent: dict = field(default_factory=dict)   # cle parent -> [positions]
    orphans: list = field(default_factory=list)     # positions sans parent
    parent_column: str = ""
    child_column: str = ""
    positional: bool = False            # rattachement par numero de ligne
    report: object = None               # validation.ValidationReport de la feuille
    invalid_parents: dict = field(default_factory=dict)  # cle parent -> [motifs]

    @property
    def path(self):
        return self.group.path

    @property
    def total_rows(self):
        return 0 if self.frame is None else int(len(self.frame))

    @property
    def mapped_columns(self):
        return [status for status in self.column_statuses if status.is_mapped]

    @property
    def linked_rows(self):
        return sum(len(positions) for positions in self.by_parent.values())

    def instances_for(self, parent_key):
        return self.by_parent.get(parent_key, [])

    def problems_for(self, parent_key):
        """Motifs empechant d'envoyer la ligne principale, s'il y en a."""
        return self.invalid_parents.get(parent_key, [])


def _find_column(columns, aliases):
    """Retrouve une colonne de liaison, sans tenir compte de la casse."""
    lowered = {str(column).strip().lower(): str(column).strip() for column in columns}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return ""


def parent_keys(frame):
    """Cle de chaque ligne principale, dans l'ordre du tableau.

    Retourne (liste_de_cles, nom_de_colonne, positionnel).
    """
    column = _find_column(frame.columns, PARENT_ALIASES)
    if column:
        colonne = frame[column]
        if isinstance(colonne, pd.DataFrame):       # en-tete en double
            colonne = colonne.iloc[:, 0]
        return [_normalize_key(value) for value in colonne], column, False
    # Kobo numerote _index a partir de 1 : on reproduit la meme convention.
    return [str(position + 1) for position in range(len(frame))], "", True


def _normalize_key(value):
    """'1', 1 et 1.0 doivent designer la meme ligne principale.

    Excel relit volontiers un entier en flottant ; sans normalisation, la
    liaison echouerait silencieusement et toutes les repetitions seraient
    perdues sans message.
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and float(value).is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    # « 3.0 » saisi comme texte doit rejoindre « 3 ».
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return str(int(number)) if number.is_integer() else text


def link_children(child_frame, parent_key_list):
    """Associe chaque ligne enfant a une ligne principale.

    Retourne (par_parent, orphelines, colonne_enfant) ou `par_parent` associe la
    cle du parent a la liste des positions enfants, dans l'ordre du tableau.
    """
    known = {}
    for position, key in enumerate(parent_key_list):
        known.setdefault(key, position)

    column = _find_column(child_frame.columns, CHILD_ALIASES)
    by_parent = {}
    orphans = []

    if not column:
        # Sans colonne de liaison, on ne devine pas : rattacher au hasard
        # creerait des donnees fausses, ce qui est pire que de ne rien faire.
        return by_parent, list(range(len(child_frame))), ""

    colonne = child_frame[column]
    if isinstance(colonne, pd.DataFrame):
        colonne = colonne.iloc[:, 0]

    for position, value in enumerate(colonne):
        key = _normalize_key(value)
        if key and key in known:
            by_parent.setdefault(key, []).append(position)
        else:
            orphans.append(position)

    return by_parent, orphans, column


def match_sheets(sheet_names, form_schema, main_sheet=""):
    """Associe les feuilles du classeur aux groupes repetes du formulaire.

    Retourne (correspondances, feuilles_ignorees) ou `correspondances` est une
    liste de (nom_de_feuille, RepeatGroup).
    """
    matched = []
    ignored = []
    used = set()

    for sheet in sheet_names:
        if sheet == main_sheet:
            continue
        group = form_schema.repeat(sheet) if form_schema else None
        if group is not None and group.path not in used and not group.nested:
            matched.append((sheet, group))
            used.add(group.path)
        else:
            ignored.append(sheet)

    return matched, ignored


def duplicate_keys(parent_key_list):
    """Cles principales apparaissant plusieurs fois.

    Deux lignes principales portant le meme `_index` rendent le rattachement
    indecidable : impossible de savoir a laquelle appartiennent les enfants.
    Les deux recevraient les memes repetitions, ce qui dupliquerait des donnees
    sans que rien ne le signale.
    """
    compte = {}
    for key in parent_key_list:
        compte[key] = compte.get(key, 0) + 1
    return {key for key, total in compte.items() if key and total > 1}


def _issues_by_row(report):
    """{position dans la feuille: [motifs]} a partir d'un rapport de controle."""
    grouped = {}
    if report is None:
        return grouped
    for issue in report.row_issues:
        grouped.setdefault(issue.row_number - 2, []).append(
            f"{issue.column} : {issue.message}"
        )
    return grouped


def prepare(main_frame, child_frames, form_schema):
    """Assemble, rattache et **verifie** les feuilles de repetition.

    child_frames : {nom_de_feuille: dataframe}
    Retourne (liste de RepeatData, avertissements a montrer a l'utilisateur).

    Le controle des valeurs enfants n'est pas un supplement : sans lui, un age
    aberrant ou un choix inexistant dans une ligne enfant ferait refuser toute
    la soumission par le serveur, et le message ne designerait que la ligne
    principale. L'utilisateur chercherait la faute au mauvais endroit.

    Une ligne enfant fautive rend sa ligne principale non envoyable, plutot que
    d'etre silencieusement omise : perdre un membre du menage sans le dire
    serait pire qu'un refus explicite.
    """
    from . import validation

    prepared = []
    warnings = []
    if not child_frames or form_schema is None:
        return prepared, warnings

    keys, _column, positional = parent_keys(main_frame)
    doublons = duplicate_keys(keys)

    for sheet, frame in child_frames.items():
        group = form_schema.repeat(sheet)
        if group is None:
            continue

        # Le groupe repete expose la meme interface qu'un formulaire : la
        # feuille enfant passe donc par exactement le meme controle que la
        # feuille principale, colonnes comme valeurs.
        statuses = validation.map_columns(frame.columns, group)
        report = validation.validate_dataframe(frame, group)
        by_parent, orphans, child_column = link_children(frame, keys)

        data = RepeatData(
            group=group, sheet=sheet, frame=frame, column_statuses=statuses,
            by_parent=by_parent, orphans=orphans, child_column=child_column,
            positional=positional, report=report,
        )

        # Report des fautes enfants sur les lignes principales concernees.
        par_ligne = _issues_by_row(report)
        for parent_key, positions in by_parent.items():
            motifs = []
            for position in positions:
                for motif in par_ligne.get(position, []):
                    motifs.append(f"feuille « {sheet} » ligne {position + 2} — {motif}")
            if motifs:
                data.invalid_parents[parent_key] = motifs

        prepared.append(data)

        if not data.mapped_columns:
            warnings.append(
                f"Feuille « {sheet} » : aucune colonne ne correspond aux questions "
                f"de la repetition « {group.display_label()} ». Elle sera ignoree."
            )
        for question in report.missing_required:
            warnings.append(
                f"Feuille « {sheet} » : la question obligatoire "
                f"« {group.relative(question.path)} » n'a pas de colonne."
            )
        if not child_column:
            warnings.append(
                f"Feuille « {sheet} » : colonne de liaison « {CHILD_KEY} » absente. "
                "Sans elle, impossible de savoir a quelle ligne principale rattacher "
                "chaque repetition : la feuille entiere est ignoree."
            )
        elif orphans:
            warnings.append(
                f"Feuille « {sheet} » : {len(orphans)} ligne(s) designent une ligne "
                f"principale inexistante (colonne « {child_column} ») et ne seront "
                "pas envoyees."
            )
        if report.issue_count:
            warnings.append(
                f"Feuille « {sheet} » : {report.issue_count} valeur(s) a corriger. "
                "Les lignes principales concernees ne seront pas envoyees."
            )

    if doublons and prepared:
        apercu = ", ".join(sorted(doublons)[:5])
        warnings.append(
            f"La feuille principale contient des numeros en double ({apercu}). "
            "Impossible de savoir a quelle ligne rattacher les repetitions : "
            "ces lignes ne seront pas envoyees. Corrigez la numerotation."
        )

    if positional and prepared:
        warnings.append(
            f"La feuille principale n'a pas de colonne « {PARENT_KEY} » : le "
            "rattachement se fait sur le numero de ligne (1, 2, 3...). Verifiez "
            f"que les valeurs de « {CHILD_KEY} » suivent bien cette numerotation."
        )

    for group in form_schema.repeats:
        if group.nested:
            warnings.append(
                f"La repetition « {group.display_label()} » est imbriquee dans une "
                "autre repetition : un classeur a deux niveaux ne peut pas "
                "l'exprimer, elle ne sera pas importee."
            )

    return prepared, warnings


def summarize(prepared):
    """Ligne de resume par feuille, pour l'interface et la ligne de commande."""
    lignes = []
    for data in prepared:
        rattachees = sum(len(positions) for positions in data.by_parent.values())
        lignes.append(
            f"{data.sheet} : {rattachees} repetition(s) rattachee(s)"
            + (f", {len(data.orphans)} orpheline(s)" if data.orphans else "")
            + f" — {len(data.mapped_columns)} colonne(s) reconnue(s)"
        )
    return lignes


def build_instances(data, parent_key, types_by_path):
    """Paires (chemin relatif, texte) de chaque repetition d'une ligne.

    Les chemins sont relatifs au groupe repete : la construction du XML place
    ensuite chaque instance dans son propre element.
    """
    from . import xmlbuild

    instances = []
    for position in data.instances_for(parent_key):
        values = {}
        for status in data.mapped_columns:
            values[status.path] = data.frame.iat[position, status.index]

        pairs = []
        for path, value in values.items():
            text = xmlbuild.format_value(value, types_by_path.get(path))
            if text is None:
                continue
            pairs.append((data.group.relative(path), text))
        if pairs:
            instances.append(pairs)
    return instances

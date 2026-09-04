"""Construction du XML de soumission ODK/Kobo.

Points couverts :
   1 - validation et assainissement des noms de colonnes (balises XML legales) ;
   2 - les entiers relus en float par pandas (1.0) redeviennent des entiers ;
   3 - les cellules vides sont omises au lieu de produire une balise vide ;
   6 - les en-tetes "groupe/question" produisent des elements imbriques ;
   7 - formats date / time / datetime / select_multiple / geopoint conformes.
"""

import re
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta

import pandas as pd

# Kobo genere des noms de question ASCII : lettres, chiffres, tiret bas, point,
# tiret ; jamais un chiffre en premiere position.
VALID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")

# Caracteres de controle interdits en XML 1.0 (hors tabulation / saut de ligne).
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Separateurs acceptes dans une cellule select_multiple.
MULTI_SPLIT_RE = re.compile(r"[,;|]+|\s+")

PATH_SEPARATOR = "/"

# Champs internes qui ne doivent jamais etre envoyes comme reponse : colonnes
# techniques d'un export Kobo et colonnes ajoutees par notre rapport d'erreurs,
# afin qu'un rapport corrige soit reimportable tel quel (point 11).
RESERVED_COLUMNS = {
    "submitted", "_id", "_uuid", "_submission_time", "_validation_status",
    "_status", "_index", "meta", "formhub",
    "_ligne_excel", "_motif",
}


# --------------------------------------------------------------------------
# Point 1 : noms de balises
# --------------------------------------------------------------------------

def is_valid_xml_name(name):
    return bool(VALID_NAME_RE.match(str(name or "")))


def is_valid_path(path):
    segments = split_path(path)
    return bool(segments) and all(is_valid_xml_name(segment) for segment in segments)


def split_path(path):
    """'groupe/question' -> ['groupe', 'question'] (point 6)."""
    raw = str(path or "").strip().strip(PATH_SEPARATOR)
    if not raw:
        return []
    return [segment.strip() for segment in raw.split(PATH_SEPARATOR) if segment.strip()]


def sanitize_xml_name(name):
    """Propose un nom de balise legal a partir d'un en-tete Excel quelconque.

    'Nom du village' -> 'Nom_du_village' ; 'Age (ans)' -> 'Age_ans' ;
    '2024_total' -> '_2024_total'. Sert a suggerer une correction, jamais a
    corriger en silence : le nom doit correspondre a une question du formulaire.
    """
    raw = str(name or "").strip()
    if not raw:
        return ""
    # Retire les accents sans perdre la lettre de base.
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = re.sub(r"[^A-Za-z0-9_.\-]+", "_", ascii_only).strip("_")
    if not cleaned:
        return ""
    if not re.match(r"^[A-Za-z_]", cleaned):
        cleaned = "_" + cleaned
    return re.sub(r"_{2,}", "_", cleaned)


def sanitize_path(path):
    segments = split_path(path)
    cleaned = [sanitize_xml_name(segment) for segment in segments]
    return PATH_SEPARATOR.join(segment for segment in cleaned if segment)


def inspect_columns(columns):
    """Analyse les en-tetes d'un tableau avant tout envoi.

    Retourne (colonnes_valides, problemes) ou chaque probleme est un dict
    {colonne, motif, suggestion} directement affichable a l'utilisateur.
    """
    valid = []
    problems = []
    seen = {}

    for column in columns:
        label = str(column)
        stripped = label.strip()

        if not stripped:
            problems.append({
                "column": label,
                "reason": "Colonne sans en-tete.",
                "suggestion": "",
            })
            continue

        if stripped.lower().startswith("unnamed:"):
            problems.append({
                "column": label,
                "reason": "Colonne sans en-tete (colonne d'index Excel).",
                "suggestion": "",
            })
            continue

        if not is_valid_path(stripped):
            problems.append({
                "column": label,
                "reason": "Nom incompatible avec une balise XML "
                          "(espace, accent, caractere special ou chiffre initial).",
                "suggestion": sanitize_path(stripped),
            })
            continue

        key = stripped.lower()
        if key in seen:
            problems.append({
                "column": label,
                "reason": f"Colonne en double avec '{seen[key]}'.",
                "suggestion": "",
            })
            continue

        seen[key] = stripped
        valid.append(stripped)

    return valid, problems


# --------------------------------------------------------------------------
# Points 2, 3, 7 : mise en forme des valeurs
# --------------------------------------------------------------------------

def _is_missing(value):
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(result, bool):
        return result
    return False


def _strip_control_chars(text):
    return CONTROL_CHARS_RE.sub("", text)


def _utc_offset_suffix(moment=None):
    """Decalage local au format +HH:MM, exige par ODK pour time et dateTime."""
    reference = moment if isinstance(moment, datetime) else datetime.now()
    offset = reference.astimezone().utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _number_to_text(value):
    """Point 2 : 1.0 -> '1'.

    pandas relit toute colonne entiere contenant une cellule vide en float64.
    Envoyer '1.0' sur un champ integer de Kobo declenche un refus 400, et un
    numero de telephone devient '90941410.0'.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        if float(value).is_integer():
            return str(int(value))
        return repr(float(value))
    return None


def _to_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if parsed is None or parsed is pd.NaT or _is_missing(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        return parsed.to_pydatetime()
    return None


def format_date(value):
    moment = _to_datetime(value)
    return moment.strftime("%Y-%m-%d") if moment else None


def format_datetime(value):
    moment = _to_datetime(value)
    if not moment:
        return None
    base = moment.strftime("%Y-%m-%dT%H:%M:%S")
    milliseconds = f".{moment.microsecond // 1000:03d}"
    if moment.tzinfo is not None:
        offset = moment.strftime("%z")
        suffix = f"{offset[:3]}:{offset[3:]}" if offset else _utc_offset_suffix(moment)
    else:
        suffix = _utc_offset_suffix(moment)
    return base + milliseconds + suffix


def format_time(value):
    if isinstance(value, time):
        moment_time = value
    else:
        moment = _to_datetime(value)
        if not moment:
            text = str(value).strip()
            return text or None
        moment_time = moment.time()
    base = moment_time.strftime("%H:%M:%S")
    milliseconds = f".{moment_time.microsecond // 1000:03d}"
    return base + milliseconds + _utc_offset_suffix()


def format_select_multiple(value):
    """Point 7 : Kobo attend des choix separes par des espaces, pas des virgules."""
    text = str(value).strip()
    if not text:
        return None
    parts = [part for part in MULTI_SPLIT_RE.split(text) if part]
    return " ".join(parts) if parts else None


def format_geopoint(value):
    """'12.6,2.1' -> '12.6 2.1' ; complete latitude/longitude/altitude/precision."""
    text = str(value).strip()
    if not text:
        return None
    parts = [part for part in re.split(r"[,;\s]+", text) if part]
    if len(parts) < 2:
        return text
    while len(parts) < 4:
        parts.append("0")
    return " ".join(parts[:4])


_TYPE_FORMATTERS = {
    "date": format_date,
    "datetime": format_datetime,
    "start": format_datetime,
    "end": format_datetime,
    "today": format_date,
    "time": format_time,
    "select_multiple": format_select_multiple,
    "select_multiple_from_file": format_select_multiple,
    "geopoint": format_geopoint,
    "geoshape": format_geopoint,
    "geotrace": format_geopoint,
}


def format_value(value, question_type=None):
    """Retourne le texte a inscrire dans le XML, ou None si la valeur est vide.

    Point 3 : renvoyer None (et non '') fait omettre l'element. Une balise vide
    sur un champ integer ou date suffit a faire refuser toute la soumission.
    """
    if _is_missing(value):
        return None

    formatter = _TYPE_FORMATTERS.get((question_type or "").strip().lower())
    if formatter is not None:
        formatted = formatter(value)
        return _strip_control_chars(formatted).strip() if formatted else None

    numeric = _number_to_text(value)
    if numeric is not None:
        return numeric or None

    if isinstance(value, (datetime, pd.Timestamp)):
        # Colonne datetime sans type connu : une date nue si l'heure est a zero.
        moment = _to_datetime(value)
        if moment and (moment.hour or moment.minute or moment.second):
            return format_datetime(moment)
        return format_date(moment)
    if isinstance(value, date):
        return format_date(value)
    if isinstance(value, time):
        return format_time(value)

    text = _strip_control_chars(str(value)).strip()
    return text or None


# --------------------------------------------------------------------------
# Point 6 : assemblage avec groupes imbriques
# --------------------------------------------------------------------------

def new_instance_id():
    return f"uuid:{uuid.uuid4()}"


def build_submission_xml(pairs, root_name, form_version, instance_id=None, formhub_uuid=None):
    """Assemble la soumission.

    pairs        : sequence de (chemin, texte) deja mis en forme, sans valeur vide
    root_name    : UID du formulaire (element racine et attribut id)
    instance_id  : identifiant stable de la ligne, cle de la deduplication Kobo
    Retourne (octets_xml, instance_id).
    """
    root_name = str(root_name or "").strip()
    if not is_valid_xml_name(root_name):
        raise ValueError(f"UID de formulaire invalide comme balise XML : '{root_name}'")

    instance_id = instance_id or new_instance_id()

    root = ET.Element(root_name)
    root.set("id", root_name)

    formhub = ET.SubElement(root, "formhub")
    ET.SubElement(formhub, "uuid").text = formhub_uuid or uuid.uuid4().hex

    groups = {(): root}
    for path, text in pairs:
        if text is None or text == "":
            continue
        segments = split_path(path)
        if not segments:
            continue

        parent = root
        for depth in range(len(segments) - 1):
            key = tuple(segments[: depth + 1])
            node = groups.get(key)
            if node is None:
                node = ET.SubElement(parent, segments[depth])
                groups[key] = node
            parent = node

        ET.SubElement(parent, segments[-1]).text = text

    if form_version:
        ET.SubElement(root, "__version__").text = str(form_version).strip()

    meta = ET.SubElement(root, "meta")
    ET.SubElement(meta, "instanceID").text = instance_id

    return ET.tostring(root, encoding="utf-8", xml_declaration=True), instance_id


def row_to_pairs(row_mapping, type_by_path=None):
    """Transforme une ligne {colonne: valeur} en paires (chemin, texte) pretes.

    type_by_path fournit le type Kobo de chaque question ; absent, la mise en
    forme se fait d'apres le type Python de la cellule.
    """
    types = type_by_path or {}
    pairs = []
    for column, value in row_mapping.items():
        path = str(column).strip()
        if not path or path.lower() in RESERVED_COLUMNS:
            continue
        text = format_value(value, types.get(path))
        if text is None:
            continue
        pairs.append((path, text))
    return pairs

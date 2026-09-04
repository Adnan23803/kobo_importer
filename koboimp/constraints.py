"""Point 10 : interpretation des contraintes XLSForm simples.

Une question Kobo peut porter une colonne « constraint » : `. >= 0 and . <= 120`,
`string-length(.) = 8`, `regex(., '^[0-9]{8}$')`. Jusqu'ici l'application les
ignorait : l'erreur n'apparaissait qu'apres l'aller-retour serveur, une ligne a
la fois.

Principe directeur : **ce qui n'est pas compris avec certitude est ignore.**
Une contrainte mal interpretee refuserait des donnees valides, ce qui est bien
pire que de ne pas la verifier du tout. Toute expression sortant de la grammaire
ci-dessous est marquee non supportee et laissee au serveur, qui reste l'autorite.

Grammaire reconnue :
    expression := terme_ou (' or ' terme_ou)*
    terme_ou   := atome (' and ' atome)*
    atome      := [ '(' ] comparaison | longueur | motif [ ')' ]
    comparaison := '.' operateur nombre
    longueur    := 'string-length(.)' operateur nombre
    motif       := "regex(., 'expression')"
    operateur   := '>' | '>=' | '<' | '<=' | '=' | '!=' | '<>'
"""

import re
from dataclasses import dataclass, field

import pandas as pd

# Constructions dont la valeur depend d'autre chose que la cellule courante :
# une autre question, la date du jour, la position dans un groupe repete.
# Elles sont hors de portee d'un controle cellule par cellule.
UNSUPPORTED_MARKERS = (
    "${", "selected(", "today()", "now()", "count(", "count-selected(",
    "if(", "coalesce(", "position(", "../", "indexed-repeat(", "jr:",
    "decimal-date-time(", "date(", "int(", "number(", "sum(", "concat(",
    "starts-with(", "ends-with(", "contains(", "substr(", "not(",
)

_OPERATORS = {
    ">=": lambda left, right: left >= right,
    "<=": lambda left, right: left <= right,
    "!=": lambda left, right: left != right,
    "<>": lambda left, right: left != right,
    ">": lambda left, right: left > right,
    "<": lambda left, right: left < right,
    "=": lambda left, right: left == right,
}
# Ordre de test : les operateurs a deux caracteres d'abord, sinon '>=' serait
# reconnu comme '>' suivi d'un '=' parasite.
_OPERATOR_ORDER = (">=", "<=", "!=", "<>", ">", "<", "=")

_NUMBER = r"[-+]?\d+(?:\.\d+)?"

_DOT_COMPARISON = re.compile(
    r"^\.\s*(" + "|".join(re.escape(op) for op in _OPERATOR_ORDER) + r")\s*(" + _NUMBER + r")$"
)
_LENGTH_COMPARISON = re.compile(
    r"^string-length\s*\(\s*\.\s*\)\s*(" + "|".join(re.escape(op) for op in _OPERATOR_ORDER)
    + r")\s*(" + _NUMBER + r")$"
)
_REGEX_CALL = re.compile(
    r"^regex\s*\(\s*\.\s*,\s*(['\"])(?P<pattern>.*)\1\s*\)$", re.DOTALL
)

_SPLIT_OR = re.compile(r"\s+or\s+", re.IGNORECASE)
_SPLIT_AND = re.compile(r"\s+and\s+", re.IGNORECASE)


@dataclass
class Constraint:
    """Contrainte compilee, attachee a une question."""

    expression: str = ""
    message: str = ""
    supported: bool = False
    description: str = ""
    _test: object = field(default=None, repr=False)

    def check(self, value):
        """True si la valeur satisfait la contrainte.

        Une cellule vide ou d'un type incoherent renvoie True : l'absence de
        reponse releve de « required », et le type releve du controle de type.
        Melanger les trois produirait deux messages pour une seule erreur.
        """
        if not self.supported or self._test is None:
            return True
        if value is None:
            return True
        try:
            if pd.isna(value):
                return True
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        if not text:
            return True
        try:
            return bool(self._test(value, text))
        except Exception:  # noqa: BLE001 - une contrainte douteuse ne refuse rien
            return True

    def explain(self):
        """Message affiche a l'utilisateur, celui du formulaire en priorite."""
        if self.message:
            return self.message
        if self.description:
            return f"Valeur hors des limites du formulaire ({self.description})."
        return "Valeur refusee par une regle du formulaire."


def _as_number(text):
    try:
        return float(str(text).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _compile_atom(atom):
    """Retourne (test, description) ou (None, '') si l'atome est hors grammaire."""
    cleaned = atom.strip()
    while cleaned.startswith("(") and cleaned.endswith(")") and _balanced(cleaned[1:-1]):
        cleaned = cleaned[1:-1].strip()

    match = _DOT_COMPARISON.match(cleaned)
    if match:
        operator, bound = match.group(1), float(match.group(2))
        compare = _OPERATORS[operator]

        def test(_value, text, compare=compare, bound=bound):
            number = _as_number(text)
            if number is None:
                return True      # pas un nombre : le controle de type s'en charge
            return compare(number, bound)

        return test, f"valeur {operator} {match.group(2)}"

    match = _LENGTH_COMPARISON.match(cleaned)
    if match:
        operator, bound = match.group(1), float(match.group(2))
        compare = _OPERATORS[operator]

        def test(_value, text, compare=compare, bound=bound):
            return compare(len(text), bound)

        return test, f"longueur {operator} {match.group(2)} caracteres"

    match = _REGEX_CALL.match(cleaned)
    if match:
        pattern = match.group("pattern")
        try:
            compiled = re.compile(pattern)
        except re.error:
            return None, ""

        def test(_value, text, compiled=compiled):
            # JavaRosa evalue regex() comme une correspondance totale.
            return compiled.fullmatch(text) is not None

        return test, f"format attendu : {pattern}"

    return None, ""


def _balanced(text):
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def compile_constraint(expression, message=""):
    """Compile une expression XLSForm ; non supportee = inoffensive."""
    raw = str(expression or "").strip()
    if not raw:
        return Constraint()

    lowered = raw.lower()
    if any(marker in lowered for marker in UNSUPPORTED_MARKERS):
        return Constraint(expression=raw, message=message, supported=False)

    or_groups = []
    descriptions = []
    for or_part in _SPLIT_OR.split(raw):
        and_tests = []
        and_descriptions = []
        for and_part in _SPLIT_AND.split(or_part):
            test, description = _compile_atom(and_part)
            if test is None:
                # Un seul atome incompris invalide toute l'expression : en
                # ignorer une moitie changerait le sens de la regle.
                return Constraint(expression=raw, message=message, supported=False)
            and_tests.append(test)
            and_descriptions.append(description)
        or_groups.append(and_tests)
        descriptions.append(" et ".join(and_descriptions))

    def evaluate(value, text, groups=or_groups):
        return any(all(test(value, text) for test in group) for group in groups)

    return Constraint(
        expression=raw,
        message=str(message or "").strip(),
        supported=True,
        description=" ou ".join(descriptions),
        _test=evaluate,
    )

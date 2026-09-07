"""Groupes repetes (begin_repeat) : de la lecture du classeur au XML envoye.

Convention : une feuille par repetition, rattachee a la feuille principale par
`_parent_index` -> `_index`, comme le font les exports Excel de KoboToolbox.

Lancement :  python tests/test_repetitions.py
"""

import os
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET

_TEMP = tempfile.mkdtemp(prefix="koboimp_repetitions_")
os.environ["LOCALAPPDATA"] = _TEMP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from koboimp import engine as engine_mod  # noqa: E402
from koboimp import excel, kobo_api, registry, repeats, schema, validation, xmlbuild  # noqa: E402

PASSED, FAILED = [], []


def check(label, condition, detail=""):
    (PASSED if condition else FAILED).append(label if condition else f"{label} :: {detail}")


def section(title):
    print(f"\n--- {title} ---")


# ==========================================================================
# Formulaire de reference : un menage, ses membres, ses parcelles
# ==========================================================================

ASSET = {
    "uid": "aMENAGE2026",
    "name": "Enquete menage",
    "has_deployment": True,
    "deployment__active": True,
    "deployed_version_id": "vR1",
    "content": {
        "survey": [
            {"type": "text", "name": "nom_chef", "label": ["Nom du chef"], "required": True},
            {"type": "begin_group", "name": "localisation", "label": ["Localisation"]},
            {"type": "text", "name": "village", "label": ["Village"]},
            {"type": "end_group"},
            {"type": "begin_repeat", "name": "membres", "label": ["Membres du menage"]},
            {"type": "text", "name": "prenom", "label": ["Prenom"], "required": True},
            {"type": "integer", "name": "age", "label": ["Age"],
             "constraint": ". >= 0 and . <= 120",
             "constraint_message": ["Age entre 0 et 120."]},
            {"type": "select_one", "select_from_list_name": "sexe",
             "name": "sexe", "label": ["Sexe"]},
            {"type": "end_repeat"},
            {"type": "begin_repeat", "name": "parcelles", "label": ["Parcelles"]},
            {"type": "decimal", "name": "surface", "label": ["Surface (ha)"]},
            {"type": "end_repeat"},
        ],
        "choices": [
            {"list_name": "sexe", "name": "m", "label": ["Masculin"]},
            {"list_name": "sexe", "name": "f", "label": ["Feminin"]},
        ],
    },
}
FORME = schema.parse_asset(ASSET)


# ==========================================================================
section("Lecture du formulaire")
# ==========================================================================

check("deux repetitions detectees", len(FORME.repeats) == 2,
      str([g.path for g in FORME.repeats]))
check("questions rattachees a leur repetition",
      [q.name for q in FORME.repeat("membres").questions] == ["prenom", "age", "sexe"])
check("tronc commun sans les repetitions",
      [q.path for q in FORME.importable] == ["nom_chef", "localisation/village"],
      str([q.path for q in FORME.importable]))
check("chemin relatif", FORME.repeat("membres").relative("membres/prenom") == "prenom")
check("nom de feuille", FORME.repeat("membres").sheet_name() == "membres")
check("obligatoire dans la repetition",
      FORME.repeat("membres").required_paths == ["membres/prenom"])
check("repetitions importables", len(FORME.importable_repeats) == 2)


# ==========================================================================
section("Construction du XML")
# ==========================================================================

payload, _ = xmlbuild.build_submission_xml(
    pairs=[("nom_chef", "Issa"), ("localisation/village", "Say")],
    root_name="aMENAGE2026", form_version="vR1",
    repeats=[
        ("membres", [
            [("prenom", "Ali"), ("age", "12")],
            [("prenom", "Fati"), ("age", "9")],
        ]),
        ("parcelles", [[("surface", "1.5")]]),
    ],
)
racine = ET.fromstring(payload)
check("un element par repetition", len(racine.findall("membres")) == 2)
check("ordre preserve",
      [m.findtext("prenom") for m in racine.findall("membres")] == ["Ali", "Fati"])
check("groupe ordinaire non duplique", len(racine.findall("localisation")) == 1)
check("seconde repetition", len(racine.findall("parcelles")) == 1)
check("champs du tronc commun intacts", racine.findtext("nom_chef") == "Issa")

# Repetition placee dans un groupe : les elements repetes doivent y rester.
imbrique, _ = xmlbuild.build_submission_xml(
    pairs=[("menage/nom", "Issa")], root_name="aF", form_version="v1",
    repeats=[("menage/membres", [[("prenom", "Ali")], [("prenom", "Fati")]])],
)
noeud = ET.fromstring(imbrique)
check("repetition dans un groupe", len(noeud.findall("menage/membres")) == 2)
check("groupe porteur unique", len(noeud.findall("menage")) == 1)

vide, _ = xmlbuild.build_submission_xml(
    pairs=[("a", "1")], root_name="aF", form_version="v1",
    repeats=[("membres", [[], []])],
)
check("instance vide non emise", len(ET.fromstring(vide).findall("membres")) == 0)

check("colonne de liaison jamais envoyee",
      xmlbuild.row_to_pairs({"_parent_index": 1, "_index": 2, "prenom": "Ali"})
      == [("prenom", "Ali")])


# ==========================================================================
section("Rattachement parent - enfant")
# ==========================================================================

PRINCIPAL = pd.DataFrame({
    "_index": [1, 2, 3],
    "nom_chef": ["Issa", "Fati", "Ali"],
    "localisation/village": ["Say", "Torodi", "Kollo"],
})
MEMBRES = pd.DataFrame({
    "_parent_index": [1, 1, 1, 3, 99],
    "prenom": ["Ali", "Fati", "Zara", "Moussa", "Orphelin"],
    "age": [12, 9, 30, 41, 5],
    "sexe": ["m", "f", "f", "m", "m"],
})

cles, colonne, positionnel = repeats.parent_keys(PRINCIPAL)
check("cles issues de _index", cles == ["1", "2", "3"] and colonne == "_index")
check("pas de numerotation implicite", not positionnel)

par_parent, orphelines, colonne_enfant = repeats.link_children(MEMBRES, cles)
check("trois membres pour la ligne 1", len(par_parent.get("1", [])) == 3)
check("aucun membre pour la ligne 2", par_parent.get("2") is None)
check("un membre pour la ligne 3", par_parent.get("3") == [3])
check("orpheline reperee", orphelines == [4])
check("colonne de liaison trouvee", colonne_enfant == "_parent_index")

check("entier et texte se rejoignent",
      repeats.link_children(pd.DataFrame({"_parent_index": ["1", 1.0, 1]}), cles)[0]["1"]
      == [0, 1, 2])

sans_index = pd.DataFrame({"nom": ["a", "b", "c"]})
cles2, _c, pos2 = repeats.parent_keys(sans_index)
check("numerotation implicite 1..n", cles2 == ["1", "2", "3"] and pos2)

sans_liaison = pd.DataFrame({"prenom": ["A", "B"]})
par2, orph2, col2 = repeats.link_children(sans_liaison, cles)
check("sans colonne de liaison : rien n'est devine",
      par2 == {} and orph2 == [0, 1] and col2 == "")


# ==========================================================================
section("Assemblage et controle")
# ==========================================================================

prepares, avertissements = repeats.prepare(
    PRINCIPAL, {"membres": MEMBRES}, FORME
)
check("une feuille preparee", len(prepares) == 1)
donnees = prepares[0]
check("colonnes de la repetition reconnues",
      {s.path for s in donnees.mapped_columns}
      == {"membres/prenom", "membres/age", "membres/sexe"},
      str([s.path for s in donnees.mapped_columns]))
check("_parent_index exclu des reponses",
      "_parent_index" not in {s.path for s in donnees.mapped_columns})
check("orphelines signalees a l'utilisateur",
      any("inexistante" in a for a in avertissements), str(avertissements))
check("resume lisible", "membres" in repeats.summarize(prepares)[0],
      str(repeats.summarize(prepares)))

vides, av2 = repeats.prepare(PRINCIPAL, {"membres": sans_liaison}, FORME)
check("feuille sans liaison signalee",
      any("liaison" in a for a in av2), str(av2))

# Une repetition imbriquee doit etre annoncee, jamais importee en silence.
IMBRIQUE = schema.parse_asset({
    "uid": "aI", "name": "I", "content": {"survey": [
        {"type": "begin_repeat", "name": "menages", "label": ["Menages"]},
        {"type": "text", "name": "nom"},
        {"type": "begin_repeat", "name": "personnes", "label": ["Personnes"]},
        {"type": "text", "name": "prenom"},
        {"type": "end_repeat"},
        {"type": "end_repeat"},
    ], "choices": []}})
check("repetition imbriquee reperee", IMBRIQUE.repeat("personnes").nested)
check("imbriquee exclue de l'import",
      [g.path for g in IMBRIQUE.importable_repeats] == ["menages"])
_p, av3 = repeats.prepare(pd.DataFrame({"a": [1]}), {"menages": pd.DataFrame({"nom": ["x"]})},
                          IMBRIQUE)
check("imbriquee annoncee", any("imbriquee" in a for a in av3), str(av3))


# ==========================================================================
section("Classeur Excel de bout en bout")
# ==========================================================================

CLASSEUR = os.path.join(_TEMP, "menages.xlsx")
with pd.ExcelWriter(CLASSEUR, engine="openpyxl") as writer:
    PRINCIPAL.to_excel(writer, sheet_name="Donnees", index=False)
    MEMBRES.to_excel(writer, sheet_name="membres", index=False)
    pd.DataFrame({"_parent_index": [1, 2], "surface": [1.5, 2.25]}).to_excel(
        writer, sheet_name="parcelles", index=False)
    pd.DataFrame({"info": ["feuille sans rapport"]}).to_excel(
        writer, sheet_name="Notice", index=False)

principal, feuille, enfants = excel.read_workbook(CLASSEUR, "Donnees", FORME)
check("feuille principale lue", len(principal) == 3 and feuille == "Donnees")
check("deux feuilles de repetition retenues", set(enfants) == {"membres", "parcelles"},
      str(sorted(enfants)))
check("feuille etrangere ignoree", "Notice" not in enfants)

prepares, _av = repeats.prepare(principal, enfants, FORME)
check("deux repetitions preparees", len(prepares) == 2)


class ClientCapture:
    def __init__(self):
        self.envois = []

    def submit(self, payload, filename="s.xml", stop_event=None):
        self.envois.append(payload)
        return kobo_api.SubmitResult(kobo_api.SUCCESS, 201, "ok", 1)


CONFIG = {
    "dry_run": False, "resume_mode": "new", "max_workers": 2,
    "output_dir": os.path.join(_TEMP, "echecs"),
    "log_file": os.path.join(_TEMP, "journal.csv"),
    "report_dir": os.path.join(_TEMP, "rapports"),
}
statuts = validation.map_columns(principal.columns, FORME)
base = registry.Registry(os.path.join(_TEMP, "reg.db"))
client = ClientCapture()

resultat = engine_mod.ImportEngine(
    CONFIG, principal, FORME, statuts, "SRC", "menages.xlsx", client, base,
    stop_event=threading.Event(), repeat_data=prepares,
).run()

check("trois soumissions envoyees", resultat.sent == 3, str(resultat.sent))

envois = {ET.fromstring(p).findtext("nom_chef"): ET.fromstring(p) for p in client.envois}
issa, fati, ali = envois["Issa"], envois["Fati"], envois["Ali"]

check("Issa porte ses 3 membres", len(issa.findall("membres")) == 3,
      str(len(issa.findall("membres"))))
check("prenoms corrects",
      [m.findtext("prenom") for m in issa.findall("membres")] == ["Ali", "Fati", "Zara"])
check("age converti en entier",
      [m.findtext("age") for m in issa.findall("membres")] == ["12", "9", "30"])
check("choix transmis", issa.findall("membres")[0].findtext("sexe") == "m")
check("Fati n'a aucun membre", len(fati.findall("membres")) == 0)
check("Ali a un membre", len(ali.findall("membres")) == 1)
check("orpheline jamais envoyee",
      all(b"Orphelin" not in p for p in client.envois))
check("parcelles rattachees",
      len(issa.findall("parcelles")) == 1 and len(fati.findall("parcelles")) == 1)
check("surface decimale preservee",
      issa.findall("parcelles")[0].findtext("surface") == "1.5")
check("tronc commun toujours present",
      issa.findtext("localisation/village") == "Say")


# ==========================================================================
section("Reprise : une correction d'enfant doit repartir")
# ==========================================================================

client2 = ClientCapture()
resultat2 = engine_mod.ImportEngine(
    CONFIG, principal, FORME, statuts, "SRC", "menages.xlsx", client2, base,
    stop_event=threading.Event(), repeat_data=prepares,
).run()
check("relance a l'identique : rien ne repart", len(client2.envois) == 0,
      str(len(client2.envois)))

# Le classeur est rejoue en entier, une seule cellule enfant ayant change :
# ne rejouer qu'une feuille sur deux ferait disparaitre les parcelles et
# modifierait legitimement toutes les lignes.
enfants_corriges = dict(enfants)
membres_corrige = enfants["membres"].copy()
membres_corrige.loc[0, "age"] = 13          # Ali a un an de plus
enfants_corriges["membres"] = membres_corrige
prepares_corriges, _ = repeats.prepare(principal, enfants_corriges, FORME)

client3 = ClientCapture()
engine_mod.ImportEngine(
    CONFIG, principal, FORME, statuts, "SRC", "menages.xlsx", client3, base,
    stop_event=threading.Event(), repeat_data=prepares_corriges,
).run()
check("la ligne dont un enfant a change repart", len(client3.envois) == 1,
      str(len(client3.envois)))
check("c'est bien la bonne ligne",
      ET.fromstring(client3.envois[0]).findtext("nom_chef") == "Issa")
check("la correction est dans l'envoi",
      ET.fromstring(client3.envois[0]).findall("membres")[0].findtext("age") == "13")
base.close()


# ==========================================================================
section("Sans repetition, rien ne change")
# ==========================================================================

PLAT = pd.DataFrame({"nom_chef": ["Issa"], "localisation/village": ["Say"]})
base2 = registry.Registry(os.path.join(_TEMP, "reg2.db"))
client4 = ClientCapture()
r4 = engine_mod.ImportEngine(
    CONFIG, PLAT, FORME, validation.map_columns(PLAT.columns, FORME),
    "SRC2", "plat.xlsx", client4, base2, stop_event=threading.Event(),
).run()
check("import sans repetition inchange", r4.sent == 1)
check("aucun element repete emis",
      len(ET.fromstring(client4.envois[0]).findall("membres")) == 0)
base2.close()


# ==========================================================================
section("Resultat")
print(f"\n{len(PASSED)} verification(s) reussie(s), {len(FAILED)} echec(s).")
for echec in FAILED:
    print(f"  ECHEC  {echec}")

import shutil  # noqa: E402
shutil.rmtree(_TEMP, ignore_errors=True)
sys.exit(1 if FAILED else 0)

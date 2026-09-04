"""Verification des ameliorations de la version 3.1.

Couvre les points retenus : 2 (diagnostic), 3 (correspondance manuelle),
7 (CSV), 8 (historique), 9 (profils), 10 (contraintes XLSForm),
11 (formulaire redeploye), 12 (ligne de commande), 13 (mises a jour).

Lancement :  python tests/test_ameliorations.py
"""

import io
import json
import os
import sys
import tempfile
import threading

_TEMP = tempfile.mkdtemp(prefix="koboimp_ameliorations_")
os.environ["LOCALAPPDATA"] = _TEMP      # doit preceder l'import de koboimp.paths

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from koboimp import cli, config as config_mod, constraints, diagnostics  # noqa: E402
from koboimp import engine as engine_mod  # noqa: E402
from koboimp import excel, kobo_api, profiles, registry, schema, updates, validation  # noqa: E402

PASSED, FAILED = [], []


def check(label, condition, detail=""):
    (PASSED if condition else FAILED).append(label if condition else f"{label} :: {detail}")


def section(title):
    print(f"\n--- {title} ---")


ASSET = {
    "uid": "aFORM2026",
    "name": "Enquete menage",
    "has_deployment": True,
    "deployment__active": True,
    "deployed_version_id": "vDEPLOY1",
    "content": {
        "survey": [
            {"type": "text", "name": "nom_village", "label": ["Nom du village"], "required": True},
            {"type": "integer", "name": "age", "label": ["Age du chef"],
             "constraint": ". >= 0 and . <= 120",
             "constraint_message": ["L'age doit etre compris entre 0 et 120 ans."],
             "hint": ["En annees revolues"]},
            {"type": "text", "name": "telephone", "label": ["Telephone"],
             "constraint": "regex(., '^[0-9]{8}$')"},
            {"type": "integer", "name": "menages", "label": ["Menages"],
             "constraint": ". > ${age}"},
            {"type": "select_one", "select_from_list_name": "regions",
             "name": "region", "label": ["Region"]},
        ],
        "choices": [
            {"list_name": "regions", "name": "niamey", "label": ["Niamey"]},
            {"list_name": "regions", "name": "tahoua", "label": ["Tahoua"]},
        ],
    },
}
FORME = schema.parse_asset(ASSET)


# ==========================================================================
section("Point 7 - lecture CSV")
# ==========================================================================

CSV_CAS = [
    ("point-virgule + cp1252", "cp1252", "nom;age\nIssa;34\nFati;22\n", ["nom", "age"], 2),
    ("virgule + BOM Excel", "utf-8-sig", "nom,age\nZara,29\n", ["nom", "age"], 1),
    ("tabulation", "utf-8", "nom\tage\nAli\t41\n", ["nom", "age"], 1),
    ("barre verticale", "utf-8", "nom|age\nOusmane|55\n", ["nom", "age"], 1),
]
for libelle, encodage, contenu, colonnes, lignes in CSV_CAS:
    chemin = os.path.join(_TEMP, f"t_{abs(hash(libelle))}.csv")
    io.open(chemin, "w", encoding=encodage, newline="").write(contenu)
    frame, feuille = excel.read_table(chemin)
    check(f"CSV {libelle}", list(frame.columns) == colonnes and len(frame) == lignes,
          f"{list(frame.columns)} / {len(frame)} lignes")
    check(f"CSV {libelle} : aucune feuille", feuille == "" and excel.list_sheets(chemin) == [])

accents = os.path.join(_TEMP, "accents.csv")
io.open(accents, "w", encoding="cp1252", newline="").write("nom;region\nIssa;Tahoua\n")
frame, _ = excel.read_table(accents)
check("CSV cp1252 : accents relus", frame.iloc[0]["region"] == "Tahoua", str(frame.iloc[0].tolist()))

vides = os.path.join(_TEMP, "vides.csv")
io.open(vides, "w", encoding="utf-8", newline="").write("a;b\n1;2\n\n\n\n")
frame, _ = excel.read_table(vides)
check("CSV : lignes vides de fin retirees", len(frame) == 1, str(len(frame)))

na = os.path.join(_TEMP, "na.csv")
io.open(na, "w", encoding="utf-8", newline="").write("choix\nNA\n")
frame, _ = excel.read_table(na)
check("CSV : le choix « NA » n'est pas pris pour un vide",
      str(frame.iloc[0]["choix"]) == "NA", repr(frame.iloc[0]["choix"]))

check("detection d'extension", excel.is_csv("x.CSV") and not excel.is_csv("x.xlsx"))


# ==========================================================================
section("Point 10 - contraintes XLSForm")
# ==========================================================================

check("contrainte numerique lue", FORME.get("age").has_constraint)
check("message du formulaire repris",
      FORME.get("age").constraint.explain().startswith("L'age doit"),
      FORME.get("age").constraint.explain())
check("indication (hint) lue", FORME.get("age").hint == "En annees revolues")
check("contrainte regex lue", FORME.get("telephone").has_constraint)
check("contrainte croisee ignoree", not FORME.get("menages").has_constraint)
check("recensement des questions contraintes",
      {q.name for q in FORME.constrained_questions} == {"age", "telephone"},
      str([q.name for q in FORME.constrained_questions]))

GRAMMAIRE = [
    (". >= 0 and . <= 120", [(50, True), (-1, False), (121, False), ("", True), (None, True)]),
    (".>0", [(1, True), (0, False)]),
    (". != 0", [(3, True), (0, False)]),
    ("string-length(.) = 8", [("12345678", True), ("1234", False)]),
    ("regex(., '^[0-9]{8}$')", [("90941410", True), ("9094", False)]),
    (". < 10 or . > 90", [(5, True), (95, True), (50, False)]),
    ("(. >= 1) and (. <= 5)", [(3, True), (9, False)]),
]
for expression, essais in GRAMMAIRE:
    regle = constraints.compile_constraint(expression)
    check(f"grammaire : {expression}", regle.supported)
    for valeur, attendu in essais:
        check(f"  {expression} avec {valeur!r}", regle.check(valeur) == attendu,
              f"obtenu {regle.check(valeur)}")

# Regle d'or : ce qui n'est pas compris ne doit jamais refuser une donnee.
HORS_GRAMMAIRE = [
    ". > ${date_debut}", "selected(., 'oui')", ". < today()", "if(. > 0, 1, 0)",
    "count-selected(.) > 2", "not(. = 1)", "fonction_inconnue(.)", ". >= 0 and bidule(.)",
]
for expression in HORS_GRAMMAIRE:
    regle = constraints.compile_constraint(expression)
    check(f"ignoree sans interpretation : {expression}",
          not regle.supported and regle.check(0) and regle.check("nimporte quoi"))

DONNEES = pd.DataFrame({
    "nom_village": ["Say", "Say", "Say", "Say"],
    "age": [34, 150, "abc", 40],
    "telephone": ["90941410", "90941410", "90941410", "123"],
    "region": ["niamey", "niamey", "niamey", "niamey"],
})
rapport = validation.validate_dataframe(DONNEES, FORME)
motifs = {(i.row_number, i.column): i.message for i in rapport.row_issues}
check("contrainte violee detectee", (3, "age") in motifs, str(sorted(motifs)))
check("message du formulaire affiche", "0 et 120" in motifs.get((3, "age"), ""),
      motifs.get((3, "age")))
check("regex violee detectee", (5, "telephone") in motifs)
check("valeur conforme non signalee", (2, "age") not in motifs)
check("type errone : une seule erreur, pas deux",
      sum(1 for (ligne, colonne) in motifs if ligne == 4 and colonne == "age") == 1)
check("avertissement sur les regles non verifiables",
      any("dependent d'autres reponses" in w for w in rapport.warnings), str(rapport.warnings))


# ==========================================================================
section("Point 3 - correspondance manuelle")
# ==========================================================================

COLONNES = ["Nom du village", "Age (ans)", "Telephone", "commentaires libres"]

auto = validation.map_columns(COLONNES, FORME)
check("sans correspondance : colonnes non reconnues",
      all(status.status != validation.COL_OK for status in auto[:2]),
      str([s.status for s in auto]))

check("suggestion sur le libelle",
      validation.suggest_target("Nom du village", FORME) == "nom_village")
check("suggestion approchee", validation.suggest_target("Age (ans)", FORME) == "age",
      validation.suggest_target("Age (ans)", FORME))
check("suggestion exacte insensible a la casse",
      validation.suggest_target("TELEPHONE", FORME) == "telephone")
check("pas de faux rapprochement par sous-chaine",
      validation.suggest_target("Village principal", FORME) != "age",
      validation.suggest_target("Village principal", FORME))
check("mot entier reconnu", validation.suggest_target("age du chef", FORME) == "age")
check("aucune suggestion hasardeuse",
      validation.suggest_target("commentaires libres", FORME) == "",
      validation.suggest_target("commentaires libres", FORME))

OVERRIDES = {
    "Nom du village": "nom_village",
    "Age (ans)": "age",
    "Telephone": "telephone",
    "commentaires libres": "",
}
manuel = validation.map_columns(COLONNES, FORME, OVERRIDES)
etats = {status.column: status.status for status in manuel}
check("colonne associee a la main", etats["Nom du village"] == validation.COL_OK)
check("nom avec espaces accepte", etats["Age (ans)"] == validation.COL_OK)
check("colonne ecartee volontairement",
      etats["commentaires libres"] == validation.COL_FORCED_IGNORE)
check("marquee comme manuelle", all(status.manual for status in manuel))
check("obligatoire desormais satisfait",
      validation.missing_required_questions(manuel, FORME) == [])

check("cible inexistante signalee",
      len(validation.validate_mapping({"a": "question_absente"}, FORME)) == 1)
check("deux colonnes vers la meme question",
      len(validation.validate_mapping({"a": "age", "b": "age"}, FORME)) == 1)
check("correspondance saine acceptee",
      validation.validate_mapping(OVERRIDES, FORME) == [])

TABLE = pd.DataFrame({
    "Nom du village": ["Say"], "Age (ans)": [34],
    "Telephone": ["90941410"], "commentaires libres": ["rien"],
})
rapport_manuel = validation.validate_dataframe(TABLE, FORME, overrides=OVERRIDES)
check("controle tient compte de la correspondance",
      len(rapport_manuel.mapped_columns) == 3 and not rapport_manuel.has_blocking_errors,
      f"{len(rapport_manuel.mapped_columns)} colonnes, bloquant={rapport_manuel.has_blocking_errors}")


# ==========================================================================
section("Points 8 et 3 - persistance (registre)")
# ==========================================================================

base = registry.Registry(os.path.join(_TEMP, "reg.db"))
base.save_mapping("aFORM2026", OVERRIDES)
check("correspondance memorisee", base.load_mapping("aFORM2026") == OVERRIDES)
check("colonne ecartee conservee", base.load_mapping("aFORM2026")["commentaires libres"] == "")
check("cloisonnee par formulaire", base.load_mapping("aAUTRE") == {})
base.save_mapping("aFORM2026", {"X": "age"})
check("remplacement complet, pas fusion", base.load_mapping("aFORM2026") == {"X": "age"})
base.clear_mapping("aFORM2026")
check("effacement", base.load_mapping("aFORM2026") == {})

identifiant = base.start_run("SRC", "menages.xlsx", "aFORM2026", "Enquete menage", 10)
base.finish_run(identifiant, sent=8, failed=2, skipped=0, stopped=False)
executions = base.recent_runs()
check("execution historisee", len(executions) == 1 and executions[0]["sent"] == 8)
check("champs necessaires a l'affichage",
      all(cle in executions[0] for cle in
          ("started_at", "source_name", "form_title", "sent", "failed", "skipped",
           "stopped", "dry_run")),
      str(sorted(executions[0])))
base.close()


# ==========================================================================
section("Point 9 - profils")
# ==========================================================================

check("un profil au depart", profiles.list_names() == [profiles.DEFAULT_NAME],
      str(profiles.list_names()))

premier = config_mod.load_config()
premier["form_title"] = "Projet A"
config_mod.set_token(premier, "JETON-A")
config_mod.save_config(premier)

profiles.create("Projet B", config_mod.as_payload(premier))
second = config_mod.load_config()
second["form_title"] = "Projet B"
config_mod.set_token(second, "JETON-B")
config_mod.save_config(second)

check("deux profils", len(profiles.list_names()) == 2, str(profiles.list_names()))
profiles.set_active(profiles.DEFAULT_NAME)
check("bascule vers A", config_mod.load_config()["form_title"] == "Projet A")
check("jeton propre a A", config_mod.get_token(config_mod.load_config()) == "JETON-A")
profiles.set_active("Projet B")
check("bascule vers B", config_mod.load_config()["form_title"] == "Projet B")
check("jeton propre a B", config_mod.get_token(config_mod.load_config()) == "JETON-B")
check("lecture directe d'un profil non actif",
      config_mod.load_config(profiles.DEFAULT_NAME)["form_title"] == "Projet A")

profiles.rename("Projet B", "Projet Beta")
check("renommage", "Projet Beta" in profiles.list_names() and profiles.active_name() == "Projet Beta")
profiles.duplicate("Projet Beta", "Copie")
check("duplication conserve le contenu",
      config_mod.load_config("Copie")["form_title"] == "Projet B")
profiles.delete("Copie")
check("suppression", "Copie" not in profiles.list_names())

try:
    profiles.create("Projet Beta")
    check("nom en double refuse", False, "aucune exception")
except profiles.ProfileError:
    check("nom en double refuse", True)

profiles.delete("Projet Beta")
try:
    profiles.delete(profiles.list_names()[0])
    check("dernier profil protege", False, "aucune exception")
except profiles.ProfileError:
    check("dernier profil protege", True)

# Un magasin corrompu ne doit pas empecher l'application de demarrer.
sauvegarde = io.open(profiles.STORE_FILE, encoding="utf-8").read()
io.open(profiles.STORE_FILE, "w", encoding="utf-8").write("{ceci n'est pas du JSON")
recuperee = config_mod.load_config()
check("magasin corrompu : demarrage possible", isinstance(recuperee, dict))
check("magasin corrompu : copie de secours",
      os.path.exists(profiles.STORE_FILE + ".corrompu"))
io.open(profiles.STORE_FILE, "w", encoding="utf-8").write(sauvegarde)


# ==========================================================================
section("Point 13 - mises a jour")
# ==========================================================================

for candidat, reference, attendu in [
    ("3.1.0", "3.0.0", True), ("3.0.0", "3.0.0", False), ("2.9.9", "3.0.0", False),
    ("v3.10.0", "3.9.0", True), ("3.1", "3.1.0", False), ("", "3.0.0", False),
    ("4.0", "3.9.9", True),
]:
    check(f"version {candidat or 'vide'} > {reference}",
          updates.is_newer(candidat, reference) == attendu)

check("manifeste simple",
      updates._extract({"version": "3.2.0", "url": "u", "notes": "n"}) == ("3.2.0", "u", "n"))
check("manifeste GitHub",
      updates._extract({"tag_name": "v3.2.0", "html_url": "g", "body": "b"}) == ("v3.2.0", "g", "b"))

# GitHub renvoie AUSSI une cle « url » qui pointe vers l'API : la retenir
# donnerait a l'utilisateur un lien inexploitable au lieu de la page de
# telechargement.
check("lien GitHub : html_url prime sur url",
      updates._extract({
          "tag_name": "v3.2.0",
          "url": "https://api.github.com/repos/x/y/releases/1",
          "html_url": "https://github.com/x/y/releases/tag/v3.2.0",
      })[1] == "https://github.com/x/y/releases/tag/v3.2.0")
check("manifeste simple : url conservee",
      updates._extract({"version": "3.2.0", "url": "https://x/f.exe"})[1] == "https://x/f.exe")

# L'adresse doit voyager avec le programme : un poste fraichement installe n'a
# pas de profil, donc pas de reglage.
_defaut = updates.DEFAULT_UPDATE_URL
try:
    updates.DEFAULT_UPDATE_URL = "https://defaut.test/v.json"
    check("adresse par defaut du programme utilisee",
          updates.resolve_url({}) == "https://defaut.test/v.json")
    check("reglage du profil prioritaire",
          updates.resolve_url({"update_url": "https://profil.test/v.json"})
          == "https://profil.test/v.json")
    check("profil vide retombe sur le defaut",
          updates.resolve_url({"update_url": "   "}) == "https://defaut.test/v.json")
finally:
    updates.DEFAULT_UPDATE_URL = _defaut
# Independant de l'adresse reellement livree dans cette construction.
_defaut = updates.DEFAULT_UPDATE_URL
try:
    updates.DEFAULT_UPDATE_URL = ""
    check("sans defaut ni reglage : aucune adresse", updates.resolve_url({}) == "")
    check("sans adresse : aucun appel reseau",
          not updates.check_for_update(updates.resolve_url({})).available)
finally:
    updates.DEFAULT_UPDATE_URL = _defaut
check("adresse livree avec cette construction",
      updates.resolve_url({}).startswith("http"), updates.resolve_url({}))
check("liste GitHub : brouillon ignore",
      updates._extract([{"draft": True, "tag_name": "v9"},
                        {"tag_name": "v3.3.0", "html_url": "h"}])[0] == "v3.3.0")
check("preversion ignoree",
      updates._extract({"tag_name": "v4.0.0", "prerelease": True})[0] == "")

silencieux = updates.check_for_update("")
check("desactive par defaut", not silencieux.available and not silencieux.error)
check("message explicite", "non configuree" in silencieux.headline())
injoignable = updates.check_for_update("https://adresse.invalide.test/v.json", timeout=3)
check("panne reseau sans exception", bool(injoignable.error) and not injoignable.available)
check("cle presente dans la configuration", "update_url" in config_mod.DEFAULT_CONFIG)


# ==========================================================================
section("Point 11 - formulaire redeploye")
# ==========================================================================


class ClientVersionne:
    def __init__(self, version):
        self.version = version
        self.envois = []

    def get_form_status(self, uid):
        return {"version": self.version, "deployed": True, "title": "Enquete menage",
                "uid": uid, "submissions": 0}

    def submit(self, payload, filename="s.xml", stop_event=None):
        self.envois.append(payload)
        return kobo_api.SubmitResult(kobo_api.SUCCESS, 201, "ok", 1)


TABLE_ENVOI = pd.DataFrame({"nom_village": ["Say", "Torodi"], "age": [34, 40]})
STATUTS = validation.map_columns(TABLE_ENVOI.columns, FORME)
CONFIG_ENVOI = {
    "dry_run": False, "resume_mode": "force", "max_workers": 2,
    "output_dir": os.path.join(_TEMP, "echecs"),
    "log_file": os.path.join(_TEMP, "journal.csv"),
    "report_dir": os.path.join(_TEMP, "rapports"),
}
base = registry.Registry(os.path.join(_TEMP, "reg2.db"))


def lancer(client, source="SRC-A"):
    return engine_mod.ImportEngine(
        CONFIG_ENVOI, TABLE_ENVOI, FORME, STATUTS, source, "menages.xlsx",
        client, base, stop_event=threading.Event(),
    ).run()


conforme = ClientVersionne("vDEPLOY1")
resultat = lancer(conforme)
check("version identique : import normal", resultat.sent == 2 and len(conforme.envois) == 2)

redeploye = ClientVersionne("vDEPLOY2")
try:
    lancer(redeploye, "SRC-B")
    check("redeploiement detecte", False, "aucune exception")
except engine_mod.FormVersionChanged as exc:
    check("redeploiement detecte", True)
    check("aucun envoi apres detection", len(redeploye.envois) == 0)
    check("versions rapportees", exc.expected == "vDEPLOY1" and exc.current == "vDEPLOY2")
    check("message actionnable",
          "recharger" in str(exc).lower() and "Formulaire" in str(exc), str(exc))


class ClientAncien:
    """Client sans get_form_status : le controle doit etre ignore, pas planter."""

    def __init__(self):
        self.envois = []

    def submit(self, payload, filename="s.xml", stop_event=None):
        self.envois.append(payload)
        return kobo_api.SubmitResult(kobo_api.SUCCESS, 201, "ok", 1)


ancien = ClientAncien()
check("client sans controle de version : import possible", lancer(ancien, "SRC-C").sent == 2)


class ClientCapricieux(ClientVersionne):
    def get_form_status(self, uid):
        raise kobo_api.KoboError("serveur injoignable")


traces = []
capricieux = ClientCapricieux("vDEPLOY1")
resultat = engine_mod.ImportEngine(
    CONFIG_ENVOI, TABLE_ENVOI, FORME, STATUTS, "SRC-D", "menages.xlsx",
    capricieux, base, stop_event=threading.Event(),
    log_callback=lambda message, level="info": traces.append(message),
).run()
check("controle de version en panne : import poursuivi", resultat.sent == 2)
check("panne du controle signalee",
      any("non verifiee" in message for message in traces), str(traces[:3]))
base.close()


# ==========================================================================
section("Point 2 - diagnostic")
# ==========================================================================

sans_jeton = config_mod.DEFAULT_CONFIG.copy()
sans_jeton["api_token_enc"] = ""
rapport = diagnostics.run_diagnostic(sans_jeton)
noms = [resultat.name for resultat in rapport.results]
check("controles locaux effectues",
      "Dossier de donnees" in noms and "Historique des imports" in noms, str(noms))
check("absence de jeton bloquante", rapport.failures and rapport.verdict()[0] == diagnostics.FAIL)
check("pas d'avalanche d'erreurs reseau", "API KoboToolbox (formulaires)" not in noms, str(noms))
check("rapport texte copiable",
      "Diagnostic Kobo Importer" in rapport.as_text() and "[FAIL]" in rapport.as_text())

check("dossier de donnees inscriptible", diagnostics.check_storage().status == diagnostics.OK)
check("registre lisible", diagnostics.check_registry().status == diagnostics.OK)

adresse_absente = config_mod.DEFAULT_CONFIG.copy()
adresse_absente["server_base_url"] = ""
check("adresse manquante signalee",
      diagnostics.check_configuration(adresse_absente).status == diagnostics.FAIL)


class ClientSonde:
    """Simule les reponses du serveur sur l'adresse d'envoi."""

    class _Session:
        def __init__(self, code):
            self.code = code

        def head(self, url, timeout=None):
            class Reponse:
                status_code = self.code
            return Reponse()

    def __init__(self, code):
        self.session = self._Session(code)

    def submission_url(self):
        return "https://exemple.test/submission"


ATTENDUS = [
    (204, diagnostics.OK, "endpoint vivant"),
    (200, diagnostics.OK, "endpoint vivant (200)"),
    (405, diagnostics.OK, "sondage refuse mais endpoint present"),
    (401, diagnostics.WARN, "authentification a verifier"),
    (410, diagnostics.FAIL, "endpoint supprime"),
    (404, diagnostics.FAIL, "endpoint inconnu"),
]
for code, attendu, libelle in ATTENDUS:
    resultat = diagnostics.check_submission_endpoint(ClientSonde(code))
    check(f"sondage HTTP {code} -> {libelle}", resultat.status == attendu,
          f"{resultat.status} : {resultat.detail}")

gone = diagnostics.check_submission_endpoint(ClientSonde(410))
check("410 : consigne de mise a jour", "Mettez a jour" in gone.hint, gone.hint)


# ==========================================================================
section("Point 12 - ligne de commande")
# ==========================================================================

analyseur = cli.build_parser()
args = analyseur.parse_args(["--import", "f.xlsx", "--form", "aX", "--mode", "retry",
                             "--workers", "3", "--dry-run"])
check("analyse des arguments d'import",
      args.import_file == "f.xlsx" and args.form == "aX"
      and args.mode == "retry" and args.workers == 3 and args.dry_run)

args = analyseur.parse_args(["--diagnostic", "--profile", "Projet"])
check("analyse du diagnostic", args.diagnostic and args.profile == "Projet")

import contextlib  # noqa: E402

for combinaison in (["--diagnostic", "--list-forms"], []):
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            analyseur.parse_args(combinaison)
        check(f"combinaison refusee {combinaison}", False, "acceptee a tort")
    except SystemExit:
        check(f"combinaison refusee {combinaison}", True)

check("codes de sortie distincts",
      len({cli.EXIT_OK, cli.EXIT_FAILURES, cli.EXIT_USAGE}) == 3)

journal = os.path.join(_TEMP, "cli.log")
rapporteur = cli.Reporter(journal, quiet=True)
rapporteur.write("ligne de test")
rapporteur.close()
check("journal CLI ecrit", "ligne de test" in io.open(journal, encoding="utf-8").read())

sortie = cli.main(["--profiles", "--quiet", "--log", os.path.join(_TEMP, "p.log")])
check("commande --profiles", sortie == cli.EXIT_OK, str(sortie))
sortie = cli.main(["--check", "absent.xlsx", "--profile", "Inexistant", "--quiet",
                   "--log", os.path.join(_TEMP, "q.log")])
check("profil inconnu : code d'utilisation", sortie == cli.EXIT_USAGE, str(sortie))


# ==========================================================================
section("Resultat")
print(f"\n{len(PASSED)} verification(s) reussie(s), {len(FAILED)} echec(s).")
for echec in FAILED:
    print(f"  ECHEC  {echec}")

import shutil  # noqa: E402
shutil.rmtree(_TEMP, ignore_errors=True)
sys.exit(1 if FAILED else 0)

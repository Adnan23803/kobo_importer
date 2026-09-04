"""Verification de l'interface : construction, navigation, import complet.

La fenetre s'ouvre reellement, mais brievement, et travaille dans un dossier de
donnees temporaire. Les boites de dialogue sont neutralisees pour ne pas bloquer.

Lancement :  python tests/smoke_ui.py
"""

import os
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

_TEMP = tempfile.mkdtemp(prefix="koboimp_smoke_")
os.environ["LOCALAPPDATA"] = _TEMP     # doit preceder l'import de koboimp.paths

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from koboimp import config as config_mod  # noqa: E402
from koboimp import kobo_api, paths, schema, validation  # noqa: E402
from koboimp.ui import app as app_mod  # noqa: E402
from koboimp.ui import steps as steps_mod  # noqa: E402

PASSED, FAILED = [], []


def check(label, condition, detail=""):
    (PASSED if condition else FAILED).append(label if condition else f"{label} :: {detail}")


class DialogueMuet:
    """Remplace tkinter.messagebox : rien ne s'affiche, rien ne bloque."""

    def __init__(self):
        self.appels = []

    def showinfo(self, *args, **kwargs):
        self.appels.append(("info", args))

    def showwarning(self, *args, **kwargs):
        self.appels.append(("warning", args))

    def showerror(self, *args, **kwargs):
        self.appels.append(("error", args))
        return None

    def askyesno(self, *args, **kwargs):
        self.appels.append(("question", args))
        return True


dialogues = DialogueMuet()
steps_mod.messagebox = dialogues
app_mod.messagebox = dialogues


ASSET = {
    "uid": "aSMOKEFORM01",
    "name": "Enquete de test",
    "has_deployment": True,
    "deployment__active": True,
    "deployment__submission_count": 5,
    "deployed_version_id": "vSMOKE1",
    "content": {
        "survey": [
            {"type": "text", "name": "nom", "label": ["Nom"], "required": True},
            {"type": "integer", "name": "age", "label": ["Age"]},
            {"type": "begin_group", "name": "lieu", "label": ["Lieu"]},
            {"type": "select_one", "select_from_list_name": "regions",
             "name": "region", "label": ["Region"]},
            {"type": "end_group"},
        ],
        "choices": [
            {"list_name": "regions", "name": "niamey", "label": ["Niamey"]},
            {"list_name": "regions", "name": "tahoua", "label": ["Tahoua"]},
        ],
    },
}

TABLE = pd.DataFrame({
    "nom": ["Issa", "Fati", "Ali", "Zara", "Moussa"],
    "age": [34.0, 22.0, 41.0, 29.0, 55.0],
    "lieu/region": ["niamey", "tahoua", "niamey", "zinder", "tahoua"],
    "Nom du village": ["Say"] * 5,
})


class ClientSimule:
    def __init__(self):
        self.envois = []

    def submit(self, payload, filename="s.xml", stop_event=None):
        self.envois.append(payload)
        nom = ET.fromstring(payload).findtext("nom") or ""
        if nom == "Fati":
            return kobo_api.SubmitResult(kobo_api.REJECTED, 400, "valeur refusee", 1)
        return kobo_api.SubmitResult(kobo_api.SUCCESS, 201, "ok", 1)

    def close(self):
        pass


def pump(app, seconds=0.4):
    fin = time.monotonic() + seconds
    while time.monotonic() < fin:
        app.update()
        time.sleep(0.01)


def pump_until(app, condition, timeout=25):
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        app.update()
        if condition():
            return True
        time.sleep(0.02)
    return False


print(f"Dossier de donnees temporaire : {paths.data_dir()}")

app = app_mod.App()
pump(app, 0.5)

check("fenetre creee", app.winfo_exists())
# Point 13 : la taille demandee est exprimee en pixels reels apres mise a
# l'echelle. Elle doit tenir sur l'ecran courant, barre des taches comprise, et
# rester compatible avec un portable 1366 x 768 a 100 %.
taille_min = app.wm_minsize()
ecran = (app.winfo_screenwidth(), app.winfo_screenheight())
echelle = app_mod.ctk.ScalingTracker.get_window_scaling(app) or 1.0
check("point 13 : largeur minimale tient sur l'ecran", taille_min[0] <= ecran[0],
      f"{taille_min} sur {ecran}")
check("point 13 : hauteur minimale laisse la barre des taches",
      taille_min[1] <= ecran[1] - 60, f"{taille_min} sur {ecran}")
check("point 13 : minimum logique compatible 1366x768",
      taille_min[0] / echelle <= 1000 and taille_min[1] / echelle <= 640,
      f"{taille_min} a l'echelle {echelle:.2f}")
check("quatre etapes construites", len(app.steps) == 4)
check("etape 1 affichee", app.current_index == 0)
check("point 22 : donnees hors dossier programme",
      paths.data_dir().startswith(_TEMP), paths.data_dir())
check("registre cree", os.path.exists(paths.REGISTRY_FILE))

# --- etape 1 : connexion -------------------------------------------------
connexion = app.steps[0]
ok, message = connexion.can_advance()
check("etape 1 bloque sans jeton", not ok, message)

connexion.server_var.set("kc.kobotoolbox.org")
connexion.token_var.set("jetondetest0123456789")
ok, message = connexion.can_advance()
check("etape 1 debloquee", ok, message)
check("adresse normalisee",
      app.session.config["server_base_url"] == "https://kc.kobotoolbox.org",
      app.session.config["server_base_url"])
check("point 23 : jeton chiffre en configuration",
      app.session.config["api_token_enc"] != "jetondetest0123456789")
check("point 23 : jeton relisible",
      config_mod.get_token(app.session.config) == "jetondetest0123456789")

connexion._toggle_token()
check("bouton afficher/masquer", connexion.toggle_button.cget("text") == "Masquer")
connexion._toggle_token()

# --- etape 2 : formulaire ------------------------------------------------
app.session.forms = [{"uid": "aSMOKEFORM01", "title": "Enquete de test",
                      "deployed": True, "submissions": 5}]
app.session.set_schema(schema.parse_asset(ASSET))

app.show_step(1)
pump(app)
formulaire = app.steps[1]
formulaire._apply_filter()
formulaire._render_detail()
pump(app)

check("point 8 : uid renseigne par l'application",
      app.session.config["asset_uid"] == "aSMOKEFORM01")
check("point 8 : version recuperee automatiquement",
      app.session.config["form_version"] == "vSMOKE1")
check("bouton modele actif", formulaire.template_button.cget("state") == "normal")
check("detail affiche la version", "vSMOKE1" in formulaire.detail.cget("text"))
ok, _ = formulaire.can_advance()
check("etape 2 franchissable", ok)

# --- etape 3 : fichier et verification -----------------------------------
app.show_step(2)
pump(app)
fichier = app.steps[2]

statuts = validation.map_columns(TABLE.columns, app.session.schema)
rapport = validation.validate_dataframe(TABLE, app.session.schema)
app.session.set_data(TABLE, os.path.join(_TEMP, "menages.xlsx"), "Feuil1",
                     "signature-test", statuts, rapport)
fichier._render()
pump(app)

check("point 10 : compteur de lignes", "5" in fichier.tiles._labels["rows"].cget("text"))
check("point 10 : une ligne a corriger",
      fichier.tiles._labels["issues"].cget("text") == "1",
      fichier.tiles._labels["issues"].cget("text"))
check("point 10 : trois colonnes reconnues",
      fichier.tiles._labels["mapped"].cget("text") == "3",
      fichier.tiles._labels["mapped"].cget("text"))
check("point 10 : correspondance affichee",
      len(fichier.mapping_list.winfo_children()) >= 4,
      str(len(fichier.mapping_list.winfo_children())))
check("point 10 : problemes listes",
      len(fichier.issues_list.winfo_children()) >= 1)
check("point 11 : export active", fichier.export_button.cget("state") == "normal")
ok, _ = fichier.can_advance()
check("etape 3 franchissable malgre une ligne fautive", ok)

# --- etape 4 : import ----------------------------------------------------
app.show_step(3)
pump(app)
importation = app.steps[3]

check("point 14 : bouton Arreter inactif hors import",
      importation.stop_button.cget("state") == "disabled")
check("point 14 : libelle du bouton", importation.stop_button.cget("text") == "Arreter")
check("plan annonce a l'avance", "seront" in importation.plan_label.cget("text"),
      importation.plan_label.cget("text"))

client = ClientSimule()
app.session.build_client = lambda: client

importation.start()
check("point 14 : Lancer desactive pendant l'import",
      importation.start_button.cget("state") == "disabled")
check("point 14 : Arreter actif pendant l'import",
      importation.stop_button.cget("state") == "normal")
check("navigation verrouillee pendant l'import", app._navigation_locked)

termine = pump_until(app, lambda: importation.start_button.cget("state") == "normal")
check("import termine", termine, "delai depasse")
pump(app, 0.5)

check("cinq envois emis", len(client.envois) == 5, str(len(client.envois)))
check("colonne invalide exclue de l'envoi",
      ET.fromstring(client.envois[0]).find("Nom_du_village") is None)
check("groupe respecte", ET.fromstring(client.envois[0]).find("lieu/region") is not None)
check("compteur envoyees", importation.tiles._labels["sent"].cget("text") == "4",
      importation.tiles._labels["sent"].cget("text"))
check("compteur echecs", importation.tiles._labels["failed"].cget("text") == "1",
      importation.tiles._labels["failed"].cget("text"))
check("barre de progression a 100 %", importation.progressbar.get() == 1.0)
check("point 18 : journal alimente", importation.journal._lines > 0,
      str(importation.journal._lines))
check("point 18 : erreur comptee dans le journal", importation.journal._errors >= 1)
check("point 11 : rapport propose", importation.report_button.cget("state") == "normal")
check("point 11 : rapport ecrit sur disque",
      importation._report_path and os.path.exists(importation._report_path),
      importation._report_path)
check("navigation deverrouillee", not app._navigation_locked)

rapport_lu = pd.read_excel(importation._report_path, sheet_name="A corriger")
check("rapport contient la ligne fautive", list(rapport_lu["nom"]) == ["Fati"],
      str(list(rapport_lu["nom"])))

# --- relance : rien ne doit repartir (points 5 et 21) ---------------------
client2 = ClientSimule()
app.session.build_client = lambda: client2
importation.start()
pump_until(app, lambda: importation.start_button.cget("state") == "normal")
pump(app, 0.4)
check("point 5 : relance ne renvoie que l'echec", len(client2.envois) == 1,
      str(len(client2.envois)))
check("point 21 : lignes deja passees ignorees",
      importation.tiles._labels["skipped"].cget("text") == "4",
      importation.tiles._labels["skipped"].cget("text"))

# --- parametres avances --------------------------------------------------
dialogue = app_mod.AdvancedDialog(app)
pump(app, 0.4)
check("point 12 : fenetre avancee ouverte", dialogue.winfo_exists())
dialogue.workers_var.set("9")
dialogue.timeout_var.set("45")
dialogue.collect()
check("reglage workers pris en compte", app.session.config["max_workers"] == 9)
check("reglage delai pris en compte", app.session.config["request_timeout"] == 45)
dialogue.workers_var.set("999")
dialogue.collect()
check("valeur hors bornes ramenee au maximum", app.session.config["max_workers"] == 16)
check("historique resume", "envoyee" in dialogue._history_text(), dialogue._history_text())

export = os.path.join(_TEMP, "partage.json")
config_mod.export_config(app.session.config, export, include_token=False)
import json
with open(export, encoding="utf-8") as handle:
    partage = json.load(handle)
check("point 23 : export sans jeton", partage["api_token_enc"] == "")
dialogue.destroy()
pump(app, 0.2)

# --- navigation ----------------------------------------------------------
app.go_to(0)
pump(app, 0.2)
check("retour a l'etape 1", app.current_index == 0)
app.go_to(3)
pump(app, 0.2)
check("saut a l'etape 4", app.current_index == 3)

# --- point 13 : lisibilite a la taille minimale --------------------------
# Le vrai critere n'est pas un nombre de pixels, mais le fait que les commandes
# restent atteignables : sur l'ancienne version, les boutons du bas passaient
# sous la barre des taches d'un portable 1366 x 768.
app.geometry(f"{taille_min[0]}x{taille_min[1]}")
pump(app, 0.6)
hauteur_fenetre = app.winfo_height()
for numero in range(4):
    app.go_to(numero)
    pump(app, 0.35)
    bas_bouton = app.next_button.winfo_rooty() + app.next_button.winfo_height()
    bas_fenetre = app.winfo_rooty() + hauteur_fenetre
    check(f"point 13 : commandes visibles a l'etape {numero + 1}",
          bas_bouton <= bas_fenetre, f"bouton a {bas_bouton}, fenetre finit a {bas_fenetre}")
    check(f"point 13 : barre d'etat visible a l'etape {numero + 1}",
          app.status_label.winfo_rooty() + app.status_label.winfo_height() <= bas_fenetre)

# --- nouvelles fenetres (points 2, 3, 8, 9) ------------------------------
from koboimp import profiles  # noqa: E402
from koboimp.ui import dialogs as dialogs_mod  # noqa: E402

dialogs_mod.messagebox = steps_mod.messagebox   # neutralise les boites modales

for nom, fabrique in (
    ("point 2 : fenetre Diagnostic", lambda: dialogs_mod.DiagnosticDialog(app)),
    ("point 8 : fenetre Historique", lambda: dialogs_mod.HistoryDialog(app)),
    ("point 9 : fenetre Profils", lambda: dialogs_mod.ProfileDialog(app)),
):
    try:
        fenetre = fabrique()
        app.update()
        check(nom, bool(fenetre.winfo_exists()))
        fenetre.destroy()
        app.update()
    except Exception as exc:  # noqa: BLE001
        check(nom, False, f"{exc.__class__.__name__} : {exc}")

# La correspondance manuelle exige un fichier charge : il l'est a ce stade.
try:
    recu = {}

    def _appliquer(overrides):
        """Reproduit le rappel reel de FileStep.open_mapping."""
        recu.update(overrides)
        app.session.set_overrides(overrides)

    mapping = dialogs_mod.MappingDialog(app, _appliquer)
    app.update()
    check("point 3 : fenetre Correspondance", bool(mapping.winfo_exists()))
    check("point 3 : une ligne par colonne du fichier",
          len(mapping._rows) == len(app.session.column_statuses),
          f"{len(mapping._rows)} lignes pour {len(app.session.column_statuses)} colonnes")
    proposees = sum(1 for var in mapping._rows.values()
                    if var.get() != mapping.IGNORE_LABEL)
    check("point 3 : correspondances pre-remplies", proposees >= 1, str(proposees))
    # La pre-selection ne doit jamais viser deux fois la meme question : le
    # bouton Appliquer serait bloque des l'ouverture.
    cibles = [mapping._path_by_label.get(var.get(), "") for var in mapping._rows.values()]
    retenues = [cible for cible in cibles if cible]
    check("point 3 : aucune question visee deux fois",
          len(retenues) == len(set(retenues)), str(retenues))
    check("point 3 : bouton Appliquer actif d'emblee",
          str(mapping.apply_button.cget("state")) == "normal",
          str(mapping.apply_button.cget("state")))
    mapping.apply()
    app.update()
    check("point 3 : correspondance transmise", bool(recu), str(recu))
    check("point 3 : correspondance enregistree",
          bool(app.session.registry.load_mapping(app.session.schema.uid)))
except Exception as exc:  # noqa: BLE001
    check("point 3 : fenetre Correspondance", False, f"{exc.__class__.__name__} : {exc}")

# --- bascule de profil (point 9) -----------------------------------------
try:
    profiles.create("Profil de test", config_mod.as_payload(app.session.config))
    app.switch_profile("Profil de test")
    app.update()
    check("point 9 : profil actif change", profiles.active_name() == "Profil de test")
    check("point 9 : retour a la premiere etape", app.current_index == 0)
    check("point 9 : formulaire oublie", app.session.schema is None)
    check("point 9 : fichier oublie", app.session.dataframe is None)
    check("point 9 : bouton d'en-tete a jour",
          "Profil de test" in app.profile_button.cget("text"),
          app.profile_button.cget("text"))
except Exception as exc:  # noqa: BLE001
    check("point 9 : bascule de profil", False, f"{exc.__class__.__name__} : {exc}")

# --- fermeture -----------------------------------------------------------
app.on_close()
try:
    detruite = not app.winfo_exists()
except Exception:
    detruite = True   # l'interpreteur Tcl est deja parti : la fenetre est bien fermee
check("fenetre fermee", detruite)
check("configuration ecrite", os.path.exists(profiles.STORE_FILE))

print(f"\n{len(PASSED)} verification(s) reussie(s), {len(FAILED)} echec(s).")
for echec in FAILED:
    print(f"  ECHEC  {echec}")

import shutil
shutil.rmtree(_TEMP, ignore_errors=True)
sys.exit(1 if FAILED else 0)

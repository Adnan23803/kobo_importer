# Kobo Importer 3.1

Application Windows qui importe un fichier **Excel ou CSV** dans un formulaire KoboToolbox.

L'utilisateur choisit son formulaire dans une liste, telecharge un modele Excel
correspondant, verifie son fichier hors ligne, puis lance l'envoi. Les lignes
deja envoyees ne repartent jamais deux fois, et celles qui echouent reviennent
dans un classeur corrigeable et reimportable tel quel.

---

## Sommaire

- [Pour l'utilisateur](#pour-lutilisateur)
- [Installation](#installation)
- [Ou sont rangees les donnees](#ou-sont-rangees-les-donnees)
- [Format du fichier](#format-du-fichier-excel)
- [Reprise et doublons](#reprise-et-doublons)
- [Pour le developpeur](#pour-le-developpeur)
- [Construire l'executable](#construire-lexecutable)
- [Diagnostic](#diagnostic)
- [Correspondance des colonnes](#correspondance-des-colonnes)
- [Profils de configuration](#profils-de-configuration)
- [Ligne de commande](#ligne-de-commande)
- [Publier une mise a jour](#publier-une-mise-a-jour)
- [Ce qui a change en version 3.1](#ce-qui-a-change-en-version-31)
- [Ce qui a change en version 3.0](#ce-qui-a-change-en-version-30)
- [Limites connues](#limites-connues)

---

## Pour l'utilisateur

L'application se parcourt en quatre etapes, dans l'ordre.

**1. Connexion.** Collez l'adresse de votre serveur KoboToolbox (celle de votre
navigateur, par exemple `https://kf.kobotoolbox.org` ou `https://eu.kobotoolbox.org`)
et votre jeton d'acces. Le bouton « Ou trouver mon jeton ? » indique le chemin
exact dans KoboToolbox. « Tester la connexion » confirme que tout est bon avant
d'aller plus loin.

Ces informations ne sont demandees qu'une fois. Le jeton est chiffre avec votre
session Windows : un autre utilisateur du meme poste ne peut pas le lire.

**2. Formulaire.** La liste de vos formulaires est chargee depuis votre compte.
Choisissez celui de destination : l'identifiant et le numero de version sont
renseignes automatiquement.

Le bouton **« Telecharger le modele Excel »** produit un classeur pret a remplir,
avec les bonnes colonnes, le type attendu de chaque question et des listes
deroulantes pour les questions a choix. Partir de ce modele evite la quasi-totalite
des erreurs de format.

**3. Fichier.** Choisissez votre classeur (et la feuille, s'il en contient
plusieurs). L'application le verifie immediatement, **sans rien envoyer** :

- combien de lignes sont pretes, combien sont a corriger ;
- quelles colonnes correspondent a une question, lesquelles seront ignorees,
  lesquelles portent un nom incompatible (avec le nom attendu en suggestion) ;
- quelle valeur precise pose probleme, sur quelle ligne, et pourquoi.

« Exporter la liste des problemes » produit un classeur contenant vos donnees
d'origine, la ligne concernee et le motif en francais. Corrigez-le et reimportez-le
directement.

**4. Import.** Trois modes :

| Mode | Effet |
|---|---|
| Nouvelles lignes seulement | ne renvoie jamais ce qui est deja arrive (defaut) |
| Reprendre les echecs | ne retente que les lignes tombees en erreur |
| Tout renvoyer | ignore l'historique — cree des doublons si les lignes sont deja sur le serveur |

La case **Simulation** genere les fichiers XML sans rien envoyer : utile pour
verifier le resultat avant de toucher au serveur.

Pendant l'envoi, la progression, le debit et les compteurs sont affiches en
direct. Le bouton **Arreter** interrompt proprement : les lignes deja envoyees
sont conservees, les autres seront reprises au prochain lancement.

A la fin, si des lignes ne sont pas passees, un classeur **« a corriger »** est
genere automatiquement et propose a l'ouverture.

---

## Installation

**Avec l'installeur** (recommande) : lancez `KoboImporter_3.0.0_installation.exe`.
L'installation se fait pour l'utilisateur courant et ne demande pas de droits
administrateur. Un raccourci est cree sur le Bureau si vous le souhaitez.

**Sans installeur** : copiez le dossier `KoboImporter` complet ou vous voulez et
lancez `KoboImporter.exe`. Le dossier doit rester entier — l'executable a besoin
des fichiers qui l'accompagnent.

---

## Ou sont rangees les donnees

Tout ce que l'application ecrit se trouve dans :

```
%LOCALAPPDATA%\KoboImporter\
├── config.json           adresse du serveur, jeton chiffre, preferences
├── registry.db           historique des lignes envoyees
├── journal_import.csv    journal detaille (point-virgule, lisible dans Excel)
├── rapports\             classeurs « a corriger »
└── xml_echecs\           XML des lignes refusees, pour diagnostic
```

Le bouton « Ouvrir le dossier de donnees », dans **Parametres avances**, y mene
directement. Ces emplacements sont modifiables dans la meme fenetre.

Une configuration issue d'une version anterieure, placee a cote de l'executable,
est reprise automatiquement au premier lancement.

---

## Format du fichier Excel

Formats acceptes : `.xlsx`, `.xlsm`, `.xls`, et `.csv` (encodage et separateur
detectes automatiquement). Pour un classeur a plusieurs feuilles, la feuille se
choisit dans l'interface.

**La premiere ligne porte les noms techniques des questions**, pas leurs libelles.
C'est exactement ce que produit le modele telechargeable, et aussi ce que produit
un export de donnees Kobo — un export corrige peut donc etre reimporte tel quel.

- Une question dans un groupe s'ecrit `groupe/question`, avec une barre oblique
  (par exemple `identification/nom_village`).
- Les colonnes inconnues du formulaire sont ignorees sans bloquer l'import :
  vous pouvez conserver vos colonnes de travail.
- Une cellule vide est omise de l'envoi ; elle n'ecrase rien et ne provoque pas
  de refus, sauf si la question est obligatoire.

Formats attendus par type de question :

| Type | Format accepte |
|---|---|
| `integer` | nombre entier |
| `decimal` | nombre decimal |
| `date` | `2026-03-04`, ou une vraie date Excel |
| `datetime` | date et heure ; le fuseau est ajoute automatiquement |
| `time` | `14:30:00` |
| `select_one` | le **nom** du choix, pas son libelle |
| `select_multiple` | plusieurs choix separes par une virgule, un point-virgule ou un espace |
| `geopoint` | `13.51, 2.11` ou `13.51 2.11` |

Les listes de choix acceptees figurent dans la feuille « Notice » du modele, et
sont rappelees dans le message d'erreur en cas de valeur inconnue.

---

## Reprise et doublons

L'application tient un registre local de chaque ligne envoyee, identifiee par
une empreinte de son contenu. Consequences pratiques :

- **relancer le meme fichier ne cree pas de doublon** : les lignes deja arrivees
  sont ignorees ;
- **modifier une ligne** la rend a nouveau envoyable, les autres restent
  inchangees ;
- **trier ou reordonner le fichier** ne perd pas l'historique ;
- **renommer ou deplacer le fichier** non plus : l'identification porte sur le
  contenu ;
- une coupure reseau pendant un envoi ne fait pas de doublon : la ligne conserve
  son identifiant de soumission, et le serveur reconnait le renvoi (reponse 202).

Le meme fichier envoye vers **deux formulaires differents** compte bien comme deux
imports distincts.

Pour repartir de zero sur un fichier : **Parametres avances → Oublier l'historique
du fichier courant**. A n'utiliser qu'en connaissance de cause — les lignes deja
presentes sur le serveur seront recreees.

---

## Pour le developpeur

### Lancer depuis les sources

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python kobo_importer_app.py
```

Python 3.11 ou superieur. Les versions des dependances sont figees dans
`requirements.txt` : une montee de version silencieuse de pandas ou de
customtkinter suffit a casser une reconstruction.

### Tests

```bash
python tests\test_koboimp.py     # noyau, sans interface ni reseau
python tests\smoke_ui.py         # interface : navigation et import de bout en bout
```

Aucune dependance de test externe, aucun appel reseau : le serveur Kobo est
simule. `smoke_ui.py` ouvre brievement une fenetre et travaille dans un dossier
temporaire.

### Organisation du code

```
kobo_importer_app.py        point d'entree
koboimp/
├── paths.py                emplacements de fichiers
├── config.py               configuration, adresses de serveur, migration
├── security.py             chiffrement du jeton (DPAPI Windows, via ctypes)
├── kobo_api.py             client HTTP : liste des formulaires, envoi, reessais
├── schema.py               lecture du formulaire (questions, types, choix)
├── xmlbuild.py             construction du XML de soumission
├── validation.py           controle du fichier avant envoi
├── excel.py                lecture, modele, rapports Excel
├── registry.py             historique des lignes envoyees + correspondances (SQLite)
├── constraints.py          interpretation des contraintes XLSForm
├── engine.py               moteur d'import
├── profiles.py             configurations nommees
├── diagnostics.py          controle installation, reseau, compte, adresses
├── updates.py              verification de version
├── cli.py                  pilotage en ligne de commande
└── ui/
    ├── theme.py            palette
    ├── widgets.py          composants reutilisables
    ├── steps.py            les quatre etapes de l'assistant
    ├── dialogs.py          diagnostic, correspondance, historique, profils
    └── app.py              fenetre principale et parametres avances
```

Le moteur (`engine.py`) ne connait pas l'interface : il recoit deux fonctions de
rappel, l'une pour la progression, l'autre pour le journal. Il est testable sans
fenetre, ce que fait `tests/test_koboimp.py`.

### Adresses de serveur

KoboToolbox expose historiquement deux hotes : KPI (`kf.*`, l'interface web et
l'API `/api/v2/assets`) et KoboCAT (`kc.*`). L'application deduit l'un de l'autre
a partir de l'adresse saisie, et bascule automatiquement sur `kc.*` si l'envoi
echoue en 404 ou 410. Les deux adresses restent modifiables dans
**Parametres avances** pour un serveur auto-heberge inhabituel.

#### Endpoint de soumission

Les soumissions partent sur l'endpoint **OpenRosa `/submission`**.

KoboToolbox a supprime l'integralite de son API v1 dans la version de juin 2026.
`/api/v1/submissions`, utilise jusqu'a la version 3.0.0 de cette application,
renvoie desormais **410 Gone** sur `kf.*` comme sur `kc.*`. Le fichier
[`REMOVALS.md`](https://github.com/kobotoolbox/kpi/blob/main/REMOVALS.md) du
depot KPI designe son remplacant : « *Use the OpenRosa submission endpoints:
`/submission`, `/{username}/submission`, or `/collector/{token}/submission`* ».

Le protocole lui-meme est inchange : envoi multipart du champ
`xml_submission_file`, le formulaire cible etant identifie par l'attribut `id`
de la racine du XML. Aucun fichier deja prepare n'est a refaire.

Un code 410 n'est jamais reessaye : contrairement a une panne passagere, une
ressource supprimee le restera. L'application l'annonce explicitement et invite
a mettre a jour plutot que d'insister quatre fois par ligne.

---

## Construire l'executable

```bash
build_exe.bat
```

Le script installe les dependances, execute les tests, puis construit
`dist\KoboImporter\KoboImporter.exe`.

La construction se fait **en mode dossier** et non en fichier unique : un
mono-fichier se decompresse dans `%TEMP%` a chaque lancement, pendant lesquelles
l'utilisateur croit que rien ne se passe et double-clique a nouveau. Mesure sur
le poste de developpement : premier lancement 7 s (cache disque froid), puis
**1,6 a 1,8 s** — un mono-fichier paie le cout complet a chaque fois.

Le dossier produit occupe environ 88 Mo non compresses ; l'installeur, qui
comprime en LZMA2, revient a une taille comparable a l'ancien fichier unique.

La compression UPX est desactivee, car elle declenche regulierement des faux
positifs antivirus, bloquants sur un poste d'organisation.

Pour produire l'installeur : installez [Inno Setup 6.3 ou plus recent](https://jrsoftware.org/isdl.php),
ouvrez `installer.iss` et compilez. Le resultat arrive dans `installeur\`.
L'installation se fait pour l'utilisateur courant, sans droits administrateur.

---

## Diagnostic

Bouton **Diagnostic** dans l'en-tete, ou `KoboImporter.exe --diagnostic`.

Il verifie dans l'ordre : dossier de donnees inscriptible, registre lisible,
configuration complete, resolution du nom du serveur, horloge du poste, acces a
l'API, **adresse d'envoi des donnees**, et etat du formulaire selectionne.

Chaque controle repond correct / a verifier / probleme, avec la marche a suivre
quand quelque chose cloche. Le rapport se copie ou s'enregistre en un clic, pour
etre joint a une demande d'assistance.

C'est le premier reflexe quand un import echoue sans explication claire. En
aout 2026, la suppression de l'API v1 par KoboToolbox a rendu tous les envois
impossibles avec un message illisible ; il avait fallu sonder les adresses a la
main pour comprendre. Ce diagnostic repond a la meme question en dix secondes :

```
[ OK ] Adresse d'envoi des donnees
        https://kf.kobotoolbox.org/submission repond 204 (OpenRosa).
```

contre, avec l'ancienne adresse :

```
[FAIL] Adresse d'envoi des donnees
        https://kf.kobotoolbox.org/api/v1/submissions repond 410 : cette adresse n'existe plus.
     -> KoboToolbox a probablement modifie son API. Mettez a jour Kobo Importer,
        ou corrigez l'adresse dans Parametres avances.
```

---

## Correspondance des colonnes

Par defaut, une colonne est reconnue si son en-tete porte le nom technique de la
question (`nom_village`, ou `groupe/question`). C'est ce que produit le modele
Excel telechargeable.

Quand le fichier vient d'ailleurs — un partenaire, un ancien systeme, un export
tiers — les en-tetes ne tombent jamais juste. Le bouton **Associer les colonnes
a la main** ouvre alors un ecran ou chaque colonne recoit sa question de
destination dans une liste deroulante :

- un apercu des trois premieres valeurs aide a reconnaitre la colonne ;
- **Proposer automatiquement** pre-remplit par rapprochement de nom et de
  libelle, sans jamais viser deux fois la meme question ;
- une colonne peut etre explicitement **ecartee** ;
- un conflit (deux colonnes vers la meme question) bloque la validation et
  s'affiche en clair.

La correspondance est enregistree **par formulaire**, pas par fichier : elle ne
se fait qu'une fois, meme si le fichier change a chaque collecte. Elle est
egalement appliquee par le mode ligne de commande.

Il n'est donc plus necessaire de renommer les en-tetes dans Excel avant
d'importer.

---

## Profils de configuration

Un profil rassemble un serveur, un jeton, un formulaire et des dossiers de
travail. Le profil actif est affiche en permanence dans l'en-tete ; cliquer
dessus ouvre la gestion des profils.

Basculer d'un profil a l'autre recharge toute la configuration et ramene a la
premiere etape. Le formulaire, le fichier charge et la correspondance en cours
sont volontairement oublies : travailler avec le formulaire d'un projet et le
serveur d'un autre serait une erreur couteuse a rattraper.

Le dernier profil ne peut pas etre supprime. Les profils sont stockes dans
`%LOCALAPPDATA%\KoboImporter\profiles.json`, chaque jeton restant chiffre par la
DPAPI. Un `config.json` issu d'une version anterieure est repris automatiquement
comme profil « Par defaut ».

---

## Ligne de commande

L'executable ouvre l'interface au double-clic, et bascule en mode console des
qu'un argument lui est passe.

```bash
KoboImporter.exe --diagnostic
KoboImporter.exe --list-forms
KoboImporter.exe --profiles
KoboImporter.exe --check donnees.xlsx --form aBcDeF123
KoboImporter.exe --import donnees.csv --form aBcDeF123 --mode new
KoboImporter.exe --import donnees.xlsx --form aBcDeF123 --dry-run --profile "Projet B"
KoboImporter.exe --help
```

| Option | Effet |
|---|---|
| `--form UID` | formulaire de destination |
| `--sheet NOM` | feuille du classeur (Excel uniquement) |
| `--profile NOM` | profil de configuration a utiliser |
| `--mode` | `new` (defaut), `retry` (echecs seulement), `force` (tout renvoyer) |
| `--workers N` | envois simultanes, 1 a 16 |
| `--dry-run` | genere les fichiers sans rien envoyer |
| `--report-dir` | ou deposer le classeur des lignes a corriger |
| `--log FICHIER` | copie la sortie dans un fichier |
| `--quiet` | n'ecrit que dans `--log` |
| `--verbose` | detaille chaque etape |

Codes de sortie : **0** succes, **1** echecs rencontres, **2** erreur
d'utilisation. De quoi enchainer dans une tache planifiee Windows :

```bat
KoboImporter.exe --import "C:\collecte\jour.xlsx" --form aBcDeF123 ^
                 --quiet --log "C:\collecte\import.log"
if errorlevel 1 echo Des lignes ont echoue, voir import.log
```

Le mode `new` etant le defaut, relancer la meme commande chaque nuit sur un
fichier qui s'enrichit n'envoie que les nouvelles lignes.

---

## Publier une mise a jour

### Ce que fait, et ne fait pas, la verification de version

Kobo Importer **signale** qu'une version plus recente existe. Il ne la telecharge
pas et ne l'installe pas. Au demarrage, il interroge un fichier JSON a une
adresse fixe ; si le numero annonce est superieur au sien, il affiche un message
avec le lien de telechargement. L'utilisateur installe ensuite lui-meme.

C'est un choix assume : une mise a jour silencieuse demanderait de remplacer un
executable en cours d'execution, de gerer les droits d'ecriture dans
`C:\Program Files` et de signer les binaires pour ne pas etre pris pour un
logiciel malveillant. Beaucoup de complexite et de risque pour un outil qu'on
lance quelques fois par semaine.

### Preparer le depot, une seule fois

1. Ouvrez `koboimp/updates.py` et renseignez l'adresse du manifeste :

   ```python
   DEFAULT_UPDATE_URL = "https://api.github.com/repos/Adnan23803/kobo_importer/releases/latest"
   ```

   Cette adresse est **inscrite dans l'executable**. Sans elle, rien ne serait
   verifie : le reglage des parametres avances vit dans `%LOCALAPPDATA%`, qu'une
   installation neuve ne possede pas.

2. Deux sources possibles :

   | Source | Adresse | Remarque |
   |---|---|---|
   | Releases GitHub | `https://api.github.com/repos/Adnan23803/kobo_importer/releases/latest` | rien a maintenir, mais **60 requetes par heure et par adresse IP** en acces anonyme : a eviter si de nombreux postes partagent la meme sortie Internet |
   | Fichier JSON du depot | `https://raw.githubusercontent.com/Adnan23803/kobo_importer/main/derniere_version.json` | pas de quota genant, et vous maitrisez le texte affiche |

   Les deux formats sont reconnus. Un modele est fourni :
   [`derniere_version.json`](derniere_version.json).

3. Reconstruisez : `build_exe.bat`.

### A chaque nouvelle version

1. **Numero de version**, aux quatre endroits :

   | Fichier | Ligne |
   |---|---|
   | `koboimp/__init__.py` | `__version__ = "3.2.0"` |
   | `version_info.txt` | `filevers`, `prodvers`, `FileVersion`, `ProductVersion` |
   | `installer.iss` | `#define MaVersion "3.2.0"` |
   | `derniere_version.json` | `"version": "3.2.0"` (si vous utilisez cette source) |

2. `build_exe.bat` — les trois suites de tests doivent passer, sans quoi la
   construction s'arrete.

3. Compilez `installer.iss` dans Inno Setup.

4. Publiez sur GitHub :

   ```bash
   git tag v3.2.0 && git push --tags
   gh release create v3.2.0 "installeur/KoboImporter_3.2.0_installation.exe" \
      --title "Kobo Importer 3.2.0" \
      --notes "Ce qui change dans cette version."
   ```

   Le fichier joint doit etre **l'installeur**, pas le dossier `dist`.

5. Si vous utilisez le manifeste plutot que l'API GitHub, mettez a jour
   `derniere_version.json` et poussez-le.

Au prochain lancement, les postes deja equipes afficheront l'annonce.

### L'installation par-dessus l'ancienne version

`installer.iss` porte un `AppId` fixe, qui ne doit **jamais** changer. C'est lui
qui fait qu'un nouvel installeur met a jour l'installation existante au lieu
d'en creer une seconde a cote.

Sont conserves, car ranges dans `%LOCALAPPDATA%\KoboImporter` et non dans le
dossier du programme (point 22) :

- les profils et leurs jetons (`profiles.json`) ;
- l'historique des lignes envoyees et les correspondances de colonnes
  (`registry.db`) ;
- le journal et les rapports.

L'utilisateur retrouve donc son environnement, et surtout : **les lignes deja
envoyees restent connues**, ce qui evite tout doublon apres une mise a jour.

### Verifier avant de diffuser

```bash
KoboImporter.exe --check-update    # interroge le manifeste, affiche le verdict
KoboImporter.exe --diagnostic      # controle complet de l'installation
```

Le bouton **Verifier maintenant** des parametres avances fait la meme chose sans
attendre un redemarrage.

### Prevenir les postes deja installes d'un changement urgent

La panne du 410, en aout 2026, a rendu les envois impossibles du jour au
lendemain. Dans ce cas de figure :

1. publiez la version corrigee et le manifeste ;
2. les postes l'annoncent a leur prochain lancement ;
3. pour les utilisateurs qui n'ont pas encore installe, `--diagnostic` nomme
   precisement la panne, ce qui evite l'incomprehension et les appels.

---

## Ce qui a change en version 3.1

### Verifier avant d'accuser ses donnees

- **Diagnostic integre** : installation, reseau, horloge, compte, adresse
  d'envoi et formulaire, avec un verdict par controle et un rapport copiable.
  Une panne comme celle du 410 ne peut plus rester incomprise.
- **Detection d'un formulaire redeploye** : la version deployee est reverifiee
  juste avant l'envoi. Si le formulaire a change depuis la verification du
  fichier, l'import est refuse **avant le premier envoi** et l'application
  ramene a l'etape Formulaire. Auparavant, les soumissions seraient parties
  vers des questions renommees ou disparues.

### Moins de travail manuel

- **Correspondance manuelle des colonnes**, memorisee par formulaire : un
  fichier qu'on n'a pas produit soi-meme n'a plus besoin d'etre retouche dans
  Excel.
- **CSV accepte en entree**, avec detection automatique de l'encodage
  (`utf-8-sig`, `utf-8`, `cp1252`, `latin-1`) et du separateur (`;`, `,`,
  tabulation, `|`). Passer par Excel pour convertir un CSV reintroduisait
  precisement les degradations que cette application corrige.
- **Profils de configuration nommes** : plus de ressaisie quand on alterne
  entre plusieurs projets.

### Des erreurs trouvees plus tot

- **Contraintes XLSForm interpretees** : `. >= 0 and . <= 120`,
  `string-length(.) = 8`, `regex(., '...')` et leurs combinaisons `and` / `or`
  sont verifiees hors ligne, avec le message d'erreur du formulaire lui-meme.

  Regle de conception : **ce qui n'est pas compris avec certitude est ignore**.
  Une contrainte dependant d'une autre reponse (`. > ${age}`), de la date du
  jour ou d'une fonction non reconnue est laissee au serveur, qui reste
  l'autorite. Refuser a tort des donnees valides serait bien pire que de ne pas
  verifier.

### Exploitation

- **Historique des imports** consultable : la table etait alimentee depuis la
  version 3.0 sans jamais etre affichee.
- **Mode ligne de commande** pour les imports planifies et le diagnostic a
  distance.
- **Verification de mise a jour**, desactivee par defaut et activable par une
  adresse de manifeste JSON dans les parametres avances.

---

## Ce qui a change en version 3.0

Vingt-sept corrections et ameliorations, regroupees par nature.

### Fiabilite des donnees envoyees

1. **Noms de colonnes verifies.** Un en-tete comportant un espace, un accent, un
   caractere special ou un chiffre initial produisait un XML illegal, refuse en
   bloc. Ils sont maintenant detectes avant l'envoi, avec le nom attendu en
   suggestion.
2. **Entiers preserves.** pandas relit toute colonne entiere contenant une cellule
   vide en decimal : `1` devenait `1.0`, refuse sur un champ `integer`, et un
   numero de telephone devenait `90941410.0`.
3. **Cellules vides omises.** Une balise vide sur un champ `integer` ou `date`
   suffisait a faire refuser toute la soumission ; l'element est desormais absent.
4. **Code 202 reconnu.** Kobo repond 202 pour une soumission deja recue. Ce cas
   etait compte comme un echec, reessaye trois fois, puis declare perdu — alors
   que la donnee etait bien arrivee.
5. **Protection anti-doublon reelle.** L'ancien test portait sur une colonne
   `submitted` que rien n'ecrivait jamais : relancer un fichier renvoyait tout en
   double. Voir [Reprise et doublons](#reprise-et-doublons).
6. **Groupes pris en charge.** Un en-tete `groupe/question` produit des elements
   imbriques, comme l'attend un formulaire structure.
7. **Formats corrects.** `date`, `datetime` (avec fuseau), `time`, `geopoint`, et
   `select_multiple` converti en valeurs separees par des espaces comme l'exige Kobo.

### Simplicite d'utilisation

8. **Plus d'identifiant a saisir.** Les formulaires sont listes depuis le compte ;
   l'UID et la version deployee sont renseignes automatiquement.
9. **Modele Excel telechargeable**, genere depuis le formulaire.
10. **Verification hors ligne** avant tout envoi : correspondance des colonnes,
    types, listes de choix, questions obligatoires.
11. **Rapport d'erreurs reimportable** : les donnees d'origine, la ligne, le motif
    en francais.
12. **Assistant en quatre etapes** a la place de quatre onglets et six boutons ;
    les cinq chemins de dossiers et le nombre de connexions partent dans
    « Parametres avances ».
13. **Fenetre adaptee a l'ecran.** L'ancienne imposait 1450 x 920 avec un minimum
    de 1280 x 800 : sur un portable 1366 x 768, les boutons du bas passaient sous
    la barre des taches. La taille est desormais calculee a partir de l'ecran et
    de son facteur d'echelle.
14. **Details d'usage** : bouton « Arreter » nomme et actif seulement pendant un
    import, choix de la feuille du classeur, message clair quand le fichier est
    ouvert dans Excel, et reponses d'erreur du serveur traduites au lieu du XML brut.

### Vitesse

15. **Connexions HTTP reutilisees.** Chaque envoi ouvrait une connexion et une
    poignee de main TLS complete. Une session par thread avec connexions
    persistantes divise nettement le temps total sur une liaison a forte latence.
16. **XML construit en memoire.** Auparavant : une ecriture disque, une relecture
    et un deplacement de fichier par ligne. Seuls les echecs sont ecrits, pour
    diagnostic.
17. **Journal CSV efficace.** Un objet pandas cree et un fichier rouvert a chaque
    ligne ont laissé place a un unique descripteur et `csv.writer`.
18. **Interface fluide.** Les notifications sont regroupees et limitees en debit,
    et le journal a une memoire bornee : un import de 50 000 lignes ne fige plus
    la fenetre.
19. **Arret immediat.** Les taches en attente sont annulees au lieu d'etre
    attendues une par une.
20. **Reessais intelligents.** Repli exponentiel, respect de `Retry-After` sur un
    code 429, et aucun reessai sur une erreur de donnees (400/401/403/404) qui se
    reproduirait a l'identique.

### Robustesse et deploiement

21. **Registre local** des lignes envoyees : reprise apres coupure ou plantage,
    identifiant de soumission stable, aucun doublon.
22. **Ecriture dans `%LOCALAPPDATA%`.** L'application ecrivait a cote de son
    executable : impossible depuis `C:\Program Files` sans droits administrateur.
23. **Jeton chiffre** par la DPAPI Windows, et export de configuration sans secret.
24. **Validation unitaire effective.** L'ancienne fonction retournait toujours
    `True` ; elle sert maintenant de dernier rempart si la verification a ete sautee.
25. **Construction en mode dossier** et installeur Inno Setup.
26. **UPX desactive**, cause classique de faux positif antivirus.
27. **Icone, numero de version** visible dans l'application et dans les proprietes
    du fichier, et dependances figees.

---

## Limites connues

- **Groupes repetes** : un tableau plat ne peut pas les exprimer. Les questions
  concernees sont signalees et ignorees.
- **Pieces jointes** (photo, audio, fichier) : non transmises par l'import Excel.
  Les colonnes correspondantes sont signalees.
- **Formulaire non deploye** : les envois sont refuses par le serveur.
  L'application le signale des l'etape 2 et redemande confirmation au lancement.
- **DPAPI** : une configuration copiee sur un autre poste ou un autre compte
  Windows perd son jeton, qu'il faut ressaisir. C'est le comportement voulu.
- **Contraintes XLSForm** : seules les regles portant sur la cellule elle-meme
  sont verifiees hors ligne. Celles qui dependent d'une autre reponse, de la
  date du jour ou d'une fonction non reconnue restent controlees par le serveur.
- **Libelles de choix** : une question a choix attend le *nom* technique de
  l'option (`niamey`), pas son libelle (`Niamey`). Le modele Excel genere
  fournit les listes deroulantes correspondantes.
- **Verification de mise a jour** : inactive tant qu'aucune adresse de manifeste
  n'est renseignee dans les parametres avances. Aucune requete n'est alors emise.

---

Concu par **Adnan Adamou** — via Data Solution — WhatsApp +227 90941410

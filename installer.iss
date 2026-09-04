; Point 25 : installeur Windows pour Kobo Importer.
;
; Un dossier compresse envoye par messagerie inquiete les services informatiques
; et se retrouve souvent bloque. Un installeur cree un raccourci, s'inscrit dans
; « Applications et fonctionnalites » et se desinstalle proprement.
;
; Utilisation :
;   1. construire l'application :   build_exe.bat
;   2. installer Inno Setup 6.3 ou plus recent
;      https://jrsoftware.org/isdl.php
;      (la directive ArchitecturesAllowed=x64compatible exige cette version ;
;       sur une version anterieure, remplacez-la par ArchitecturesAllowed=x64)
;   3. ouvrir ce fichier dans Inno Setup et cliquer sur « Compile »
;
; Le programme d'installation obtenu se trouve dans le dossier « installeur ».

#define MonNom          "Kobo Importer"
#define MaVersion       "3.1.0"
#define MonEditeur      "Data Solution - Adnan Adamou"
#define MonExecutable   "KoboImporter.exe"

[Setup]
; Identifiant unique de l'application : ne jamais le modifier entre deux
; versions, sinon Windows installerait une seconde application au lieu de
; mettre a jour la premiere.
AppId={{7B3F1C42-9E5A-4D18-8C2B-4F6A19D3E507}
AppName={#MonNom}
AppVersion={#MaVersion}
AppVerName={#MonNom} {#MaVersion}
AppPublisher={#MonEditeur}
DefaultDirName={autopf}\KoboImporter
DefaultGroupName={#MonNom}
UninstallDisplayIcon={app}\{#MonExecutable}
OutputDir=installeur
OutputBaseFilename=KoboImporter_{#MaVersion}_installation
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; L'installation par utilisateur evite d'exiger les droits administrateur,
; rarement accordes sur un poste d'organisation.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "francais"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Creer un raccourci sur le Bureau"; \
    GroupDescription: "Raccourcis :"

[Files]
Source: "dist\KoboImporter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MonNom}"; Filename: "{app}\{#MonExecutable}"
Name: "{group}\Desinstaller {#MonNom}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MonNom}"; Filename: "{app}\{#MonExecutable}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MonExecutable}"; Description: "Lancer {#MonNom}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; La configuration et l'historique restent dans %LOCALAPPDATA%\KoboImporter :
; une reinstallation retrouve les lignes deja envoyees. Supprimez ce dossier
; manuellement pour repartir de zero.
Type: filesandordirs; Name: "{app}\_internal"

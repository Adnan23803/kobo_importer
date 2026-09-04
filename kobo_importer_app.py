"""Kobo Importer - point d'entree.

Toute la logique vit dans le paquet koboimp :
    koboimp/paths.py      emplacements de fichiers
    koboimp/config.py     configuration et adresses de serveur
    koboimp/security.py   chiffrement du jeton (DPAPI Windows)
    koboimp/kobo_api.py   client HTTP KoboToolbox
    koboimp/schema.py     lecture du formulaire
    koboimp/xmlbuild.py   construction du XML de soumission
    koboimp/validation.py controle du fichier avant envoi
    koboimp/excel.py      lecture, modele et rapports Excel
    koboimp/registry.py   historique des lignes envoyees
    koboimp/engine.py     moteur d'import
    koboimp/diagnostics.py controle de l'installation et du serveur
    koboimp/profiles.py   configurations nommees
    koboimp/updates.py    verification de version
    koboimp/cli.py        pilotage en ligne de commande
    koboimp/ui/           interface graphique

Lancement :
    python kobo_importer_app.py              interface graphique
    python kobo_importer_app.py --diagnostic controle de l'installation
    python kobo_importer_app.py --help       toutes les commandes
"""

import os
import sys
import traceback


def _show_startup_error(exc):
    """Une erreur au demarrage ne doit pas se solder par une fenetre qui
    disparait sans message (l'executable est construit sans console)."""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        from tkinter import messagebox
        messagebox.showerror(
            "Kobo Importer - erreur au demarrage",
            f"L'application n'a pas pu demarrer :\n\n{exc}\n\n{detail[-1500:]}",
        )
    except Exception:  # noqa: BLE001 - dernier recours
        print(detail, file=sys.stderr)


def _ensure_importable():
    """Execution depuis un autre repertoire de travail."""
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)


def main():
    _ensure_importable()

    # Point 12 : la presence d'arguments bascule en mode ligne de commande ;
    # un double-clic, qui n'en passe aucun, ouvre l'interface comme avant.
    if len(sys.argv) > 1:
        from koboimp.cli import main as run_cli
        sys.exit(run_cli())

    from koboimp.ui.app import main as run_app
    run_app()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - fenetre d'erreur plutot qu'un plantage muet
        _show_startup_error(error)
        sys.exit(1)

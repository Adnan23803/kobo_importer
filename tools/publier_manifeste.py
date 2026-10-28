"""Met a jour derniere_version.json apres publication d'un installeur.

Appele par la construction automatique (.github/workflows/publication.yml),
avec VERSION, URL et SHA256 dans l'environnement. Les autres cles, notes et
commentaire compris, sont conservees telles quelles.
"""

import json
import os
import re
import sys

MANIFESTE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "derniere_version.json")


def main():
    version = os.environ.get("VERSION", "").strip()
    url = os.environ.get("URL", "").strip()
    sha256 = os.environ.get("SHA256", "").strip().lower()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"Version invalide : {version!r}")
    if not url.startswith("https://"):
        sys.exit(f"Adresse invalide : {url!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        sys.exit(f"Empreinte invalide : {sha256!r}")

    with open(MANIFESTE, encoding="utf-8") as handle:
        data = json.load(handle)
    data.update(version=version, url=url, sha256=sha256)
    with open(MANIFESTE, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Manifeste : {version} -> {url}")


if __name__ == "__main__":
    main()

"""Points 5 et 21 : registre local des lignes deja envoyees.

Le mecanisme precedent reposait sur une colonne « submitted » que rien n'ecrivait
jamais : relancer le meme fichier renvoyait tout en double. Ici, chaque ligne
recoit une cle stable (empreinte de son contenu) et un instanceID definitif.

Consequences directes :
  - relancer un fichier n'envoie que ce qui n'est pas deja passe ;
  - un renvoi apres coupure reseau porte le meme instanceID, donc Kobo le
    reconnait comme doublon (202) au lieu de creer une seconde soumission ;
  - une reprise apres plantage ne depend plus du contenu d'un dossier.
"""

import hashlib
import os
import sqlite3
import threading
import uuid
from datetime import datetime

# Statuts stockes.
PENDING = "PENDING"
SUCCESS = "SUCCESS"
DUPLICATE = "DUPLICATE"
INVALID = "INVALID"
DRY_RUN = "DRY_RUN"
STOPPED = "STOPPED"

SENT_STATUSES = {SUCCESS, DUPLICATE}
FAILURE_STATUSES = {"REJECTED", "AUTH", "NOT_FOUND", "GONE", "SERVER",
                    "NETWORK", "RATE", INVALID, "ERROR"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS rows (
    row_key     TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    source_name TEXT,
    form_uid    TEXT,
    row_index   INTEGER,
    instance_id TEXT NOT NULL,
    status      TEXT NOT NULL,
    http_status INTEGER,
    message     TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rows_source ON rows(source_id);
CREATE INDEX IF NOT EXISTS idx_rows_status ON rows(source_id, status);

CREATE TABLE IF NOT EXISTS mappings (
    form_uid   TEXT NOT NULL,
    column_name TEXT NOT NULL,
    target_path TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (form_uid, column_name)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    source_name TEXT,
    source_id   TEXT,
    form_uid    TEXT,
    form_title  TEXT,
    total       INTEGER DEFAULT 0,
    sent        INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    skipped     INTEGER DEFAULT 0,
    stopped     INTEGER DEFAULT 0,
    dry_run     INTEGER DEFAULT 0
);
"""


def _now():
    return datetime.now().isoformat(timespec="seconds")


def compute_row_keys(dataframe, source_id):
    """Cle stable par ligne : empreinte du contenu, pas de la position.

    Deux lignes strictement identiques dans le meme fichier sont distinguees par
    un compteur d'occurrence : elles restent deux soumissions differentes, tout
    en gardant une cle insensible a un tri ou a une insertion de ligne.
    """
    occurrences = {}
    keys = []
    for values in dataframe.itertuples(index=False, name=None):
        parts = []
        for value in values:
            if value is None or value != value:  # None / NaN / NaT
                parts.append("")
            else:
                parts.append(str(value))
        digest = hashlib.sha1(  # noqa: S324 - identification, pas de securite
            f"{source_id}\x1e{chr(31).join(parts)}".encode("utf-8", "replace")
        ).hexdigest()
        rank = occurrences.get(digest, 0)
        occurrences[digest] = rank + 1
        keys.append(digest if rank == 0 else f"{digest}#{rank}")
    return keys


class Registry:
    """Acces au registre. Sur, mais concu pour etre appele depuis un seul thread
    coordinateur ; un verrou protege malgre tout les acces concurrents."""

    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.commit()

    def close(self):
        with self._lock:
            try:
                self._connection.commit()
            finally:
                self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    # -- enregistrement des lignes ----------------------------------------

    def register_rows(self, source_id, source_name, form_uid, row_keys):
        """Cree les lignes absentes et retourne {row_key: (instance_id, statut)}.

        Une seule transaction, meme pour 50 000 lignes.
        """
        stamp = _now()
        payload = [
            (key, source_id, source_name, form_uid, index, f"uuid:{uuid.uuid4()}",
             PENDING, None, None, stamp, stamp)
            for index, key in enumerate(row_keys)
        ]
        with self._lock:
            self._connection.executemany(
                "INSERT OR IGNORE INTO rows (row_key, source_id, source_name, form_uid,"
                " row_index, instance_id, status, http_status, message, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                payload,
            )
            self._connection.commit()
        return self.states(row_keys)

    def states(self, row_keys):
        """{row_key: {instance_id, status, message, http_status}} pour ces cles."""
        result = {}
        with self._lock:
            for start in range(0, len(row_keys), 500):
                chunk = row_keys[start:start + 500]
                placeholders = ",".join("?" * len(chunk))
                cursor = self._connection.execute(
                    f"SELECT row_key, instance_id, status, http_status, message"
                    f" FROM rows WHERE row_key IN ({placeholders})",
                    chunk,
                )
                for record in cursor:
                    result[record["row_key"]] = {
                        "instance_id": record["instance_id"],
                        "status": record["status"],
                        "http_status": record["http_status"],
                        "message": record["message"],
                    }
        return result

    def mark_many(self, updates):
        """updates : liste de (row_key, statut, code_http, message)."""
        if not updates:
            return
        stamp = _now()
        payload = [
            (status, http_status, (message or "")[:1000], stamp, row_key)
            for row_key, status, http_status, message in updates
        ]
        with self._lock:
            self._connection.executemany(
                "UPDATE rows SET status=?, http_status=?, message=?, updated_at=?"
                " WHERE row_key=?",
                payload,
            )
            self._connection.commit()

    # -- consultation ------------------------------------------------------

    def summary(self, source_id):
        """Compte par statut pour un fichier donne."""
        with self._lock:
            cursor = self._connection.execute(
                "SELECT status, COUNT(*) AS total FROM rows WHERE source_id=? GROUP BY status",
                (source_id,),
            )
            counts = {record["status"]: record["total"] for record in cursor}
        counts["_sent"] = sum(counts.get(status, 0) for status in SENT_STATUSES)
        counts["_failed"] = sum(counts.get(status, 0) for status in FAILURE_STATUSES)
        counts["_total"] = sum(value for key, value in counts.items() if not key.startswith("_"))
        return counts

    def last_seen(self, source_id):
        with self._lock:
            cursor = self._connection.execute(
                "SELECT MAX(updated_at) AS moment FROM rows WHERE source_id=?", (source_id,)
            )
            record = cursor.fetchone()
        return record["moment"] if record else None

    def forget_source(self, source_id):
        """Efface l'historique d'un fichier (relance complete assumee)."""
        with self._lock:
            cursor = self._connection.execute("DELETE FROM rows WHERE source_id=?", (source_id,))
            self._connection.commit()
        return cursor.rowcount

    def recent_runs(self, limit=20):
        with self._lock:
            cursor = self._connection.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(record) for record in cursor]

    # -- correspondances manuelles (point 3) -------------------------------

    def save_mapping(self, form_uid, overrides):
        """Memorise la correspondance choisie pour ce formulaire.

        Rattachee au formulaire et non au fichier : le meme jeu de colonnes
        revient a chaque collecte, la correspondance n a a etre etablie qu une
        fois. La chaine vide, qui signifie « colonne ecartee », est conservee :
        c est une decision de l utilisateur, pas une absence de decision.
        """
        if not form_uid:
            return 0
        stamp = _now()
        rows = [
            (form_uid, str(column), str(target or ""), stamp)
            for column, target in (overrides or {}).items()
        ]
        with self._lock:
            self._connection.execute("DELETE FROM mappings WHERE form_uid=?", (form_uid,))
            if rows:
                self._connection.executemany(
                    "INSERT INTO mappings (form_uid, column_name, target_path, updated_at)"
                    " VALUES (?,?,?,?)",
                    rows,
                )
            self._connection.commit()
        return len(rows)

    def load_mapping(self, form_uid):
        if not form_uid:
            return {}
        with self._lock:
            cursor = self._connection.execute(
                "SELECT column_name, target_path FROM mappings WHERE form_uid=?",
                (form_uid,),
            )
            return {record["column_name"]: record["target_path"] for record in cursor}

    def clear_mapping(self, form_uid):
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM mappings WHERE form_uid=?", (form_uid,)
            )
            self._connection.commit()
        return cursor.rowcount

    # -- suivi des executions ---------------------------------------------

    def start_run(self, source_id, source_name, form_uid, form_title, total, dry_run=False):
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO runs (started_at, source_name, source_id, form_uid, form_title,"
                " total, dry_run) VALUES (?,?,?,?,?,?,?)",
                (_now(), source_name, source_id, form_uid, form_title, total, int(bool(dry_run))),
            )
            self._connection.commit()
            return cursor.lastrowid

    def finish_run(self, run_id, sent, failed, skipped, stopped):
        if not run_id:
            return
        with self._lock:
            self._connection.execute(
                "UPDATE runs SET finished_at=?, sent=?, failed=?, skipped=?, stopped=?"
                " WHERE id=?",
                (_now(), sent, failed, skipped, int(bool(stopped)), run_id),
            )
            self._connection.commit()


def select_rows_to_send(states, row_keys, resume_mode):
    """Determine les lignes a traiter selon le mode de reprise.

    new   : tout ce qui n'est pas deja arrive sur le serveur
    retry : uniquement ce qui a echoue lors d'une tentative precedente
    force : tout, sans consulter l'historique
    """
    if resume_mode == "force":
        return list(range(len(row_keys))), 0

    selected = []
    skipped = 0
    for index, key in enumerate(row_keys):
        status = (states.get(key) or {}).get("status", PENDING)
        if status in SENT_STATUSES:
            skipped += 1
            continue
        if resume_mode == "retry" and status not in FAILURE_STATUSES:
            skipped += 1
            continue
        selected.append(index)
    return selected, skipped

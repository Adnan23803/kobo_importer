"""Point 23 : chiffrement du token API avec la DPAPI Windows.

Le token n'est plus stocke en clair dans config.json. La DPAPI lie le secret au
compte Windows courant : un autre utilisateur du meme poste ne peut pas le lire,
et l'operation reste transparente (aucune saisie de mot de passe).

Implemente en ctypes pour ne pas ajouter pywin32 aux dependances.
Si la DPAPI est indisponible (autre OS, appel en erreur), on retombe sur un
stockage en clair signale par un prefixe explicite.
"""

import base64
import ctypes
import sys
from ctypes import wintypes

PREFIX_DPAPI = "dpapi:"
PREFIX_PLAIN = "plain:"

_DESCRIPTION = "KoboImporter API token"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _available():
    return sys.platform == "win32"


def _blob_from_bytes(raw):
    buffer = ctypes.create_string_buffer(raw, len(raw))
    return _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _blob_to_bytes(blob):
    size = int(blob.cbData)
    if size == 0:
        return b""
    return ctypes.string_at(blob.pbData, size)


def _free(blob):
    if blob.pbData:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _protect(raw):
    crypt32 = ctypes.windll.crypt32
    blob_in, _keep = _blob_from_bytes(raw)
    blob_out = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        ctypes.c_wchar_p(_DESCRIPTION),
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(ctypes.GetLastError(), "CryptProtectData a echoue")
    try:
        return _blob_to_bytes(blob_out)
    finally:
        _free(blob_out)


def _unprotect(raw):
    crypt32 = ctypes.windll.crypt32
    blob_in, _keep = _blob_from_bytes(raw)
    blob_out = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(ctypes.GetLastError(), "CryptUnprotectData a echoue")
    try:
        return _blob_to_bytes(blob_out)
    finally:
        _free(blob_out)


def encrypt_token(token):
    """Retourne la forme stockable d'un token. Chaine vide -> chaine vide."""
    token = (token or "").strip()
    if not token:
        return ""
    if _available():
        try:
            encrypted = _protect(token.encode("utf-8"))
            return PREFIX_DPAPI + base64.b64encode(encrypted).decode("ascii")
        except Exception:
            pass
    return PREFIX_PLAIN + base64.b64encode(token.encode("utf-8")).decode("ascii")


def decrypt_token(stored):
    """Relit un token stocke. Accepte aussi l'ancien format en clair."""
    value = (stored or "").strip()
    if not value:
        return ""
    if value.startswith(PREFIX_DPAPI):
        payload = base64.b64decode(value[len(PREFIX_DPAPI):])
        try:
            return _unprotect(payload).decode("utf-8")
        except Exception:
            # Config copiee depuis un autre poste ou un autre compte Windows.
            return ""
    if value.startswith(PREFIX_PLAIN):
        try:
            return base64.b64decode(value[len(PREFIX_PLAIN):]).decode("utf-8")
        except Exception:
            return ""
    # Ancien config.json : token ecrit tel quel.
    return value


def is_protected(stored):
    return bool(stored) and str(stored).startswith(PREFIX_DPAPI)


def mask(token, keep=4):
    """Affichage abrege d'un token dans les journaux."""
    token = token or ""
    if len(token) <= keep:
        return "*" * len(token)
    return "*" * (len(token) - keep) + token[-keep:]

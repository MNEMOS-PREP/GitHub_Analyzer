"""The one choke point every string passes through before it can reach a model.

Rules, all enforced by tests:
  1. Secret-looking files (.env, private keys, credentials) are withheld whole.
  2. Everything else is redacted and only then truncated, so a secret can never
     be cut into an unmatched fragment. Redaction is `secrets.redact` plus two
     passes of our own: whole PEM private-key blocks, and realistic assignment
     formats (env/YAML/JSON/URL userinfo/bearer/cloud tokens) that redact misses.
     The extra passes replace only the secret VALUE so a review stays readable.
  3. `safe_text` is total and never longer than `max_chars`; if redaction itself
     fails it fails closed and returns a withheld marker instead of raw text.

Design notes
  * The extra passes do NOT skip "placeholder" words (test, example, your, ...)
    the way `secrets.redact` does: for the one barrier before an LLM, redacting
    a harmless placeholder costs nothing, leaking a real key costs a lot.
  * The name-agnostic assignment rule needs a value of 20+ chars that has a letter,
    a digit and entropy >= 3.0, which keeps `max_token_count = 1024`,
    `key = os.environ["K"]` and long identifiers readable.
  * Every pattern is bounded or linear (no nested quantifiers), so a 2 MB
    adversarial string cannot stall it.
"""

from __future__ import annotations

import re

from gh_analyzer.secrets import _entropy, redact

WITHHELD = "[file withheld: may contain secrets]"
TRUNCATED = "\n...[truncated]"
_FAILED = "[text withheld: could not be made safe]"
_R = "[REDACTED]"

_SECRET_NAMES = {
    ".env", ".npmrc", ".netrc", ".pypirc",
    "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa", "credentials.json",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")


def is_secret_file(path: str) -> bool:
    """True when the file's basename marks it as holding secrets (case-insensitive)."""
    name = re.split(r"[\\/]", path or "")[-1].lower()
    return name in _SECRET_NAMES or name.startswith(".env.") or name.endswith(_SECRET_SUFFIXES)


# --- pass 1: whole private-key blocks -------------------------------------------------

_PK_BEGIN = re.compile(r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY(?: BLOCK)?-----")
_PK_END = re.compile(r"-----END [A-Z ]{0,20}PRIVATE KEY(?: BLOCK)?-----")


def _strip_private_keys(text: str) -> str:
    """Replace BEGIN..END blocks; an unterminated BEGIN swallows the rest of the text."""
    out: list[str] = []
    pos = 0
    while True:
        m = _PK_BEGIN.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        out.append(_R)
        e = _PK_END.search(text, m.end())
        if not e:
            break
        pos = e.end()
    return "".join(out)


# --- pass 2: realistic secret formats redact() misses ---------------------------------

_VALUE = r"[A-Za-z0-9+/=_.~\-]"
# keyword inside a name (API_KEY, client_secret, MY_TOKEN, "password"), optional quote,
# ':' or '=' or blanks, optional quote, then the value (group 2).
_ASSIGN = re.compile(
    r"""(?i)((?:key|secret|token|passw(?:or)?d|credential)[A-Za-z0-9_.\-]{0,40}["']?"""
    r"""(?:[ \t]*[:=][ \t]*|[ \t]+)["']?)(""" + _VALUE + r"{20,})")
# `Bearer <tok>` and the concatenated form `"Bearer " + "<tok>"`.
_BEARER = re.compile(
    r"""(?i)(\bBearer(?:[ \t]+|[ \t]*["'][ \t]*\+[ \t]*["']))([A-Za-z0-9._~+/\-]{20,}=*)""")
# `Basic <base64>`: always redacted on an Authorization line, elsewhere only when it looks encoded.
_BASIC = re.compile(r"(?i)(\bBasic[ \t]+)([A-Za-z0-9+/]{8,}={0,2})(?![A-Za-z0-9+/=])")
# The user part may be empty (`redis://:pw@host`).
_URL_PASSWORD = re.compile(r"""(\b[A-Za-z][A-Za-z0-9+.\-]{0,20}://[^\s:/@'"]{0,64}:)([^\s@/'"]{1,200})(@)""")
# The label line and the line after it (a YAML/INI value may sit on the next line).
_AWS_LINE = re.compile(r"(?im)^.*(?:aws_secret|secret_access_key).*(?:\r?\n.*)?")
_AWS_VALUE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{36,64}(?![A-Za-z0-9+/=])")
# A value assigned (with ':' or '=') to a name ending in a password-like word: 16+ chars is enough, and an
# all-letter or all-digit value counts too. Not followed by '(' or '[' so calls and lookups stay readable.
_NAMED = re.compile(
    r"""(?i)((?<![A-Za-z0-9])(?:passw(?:or)?d|pwd|secret|api[_\-]?key|token)["']?[ \t]*[:=][ \t]*["']?)"""
    r"""([A-Za-z0-9+/=_.~\-]{16,})(?![A-Za-z0-9+/=_.~\-(\[])""")
_TOKENS = re.compile(
    r"(?<![A-Za-z0-9])(?:github_pat_[A-Za-z0-9_]{22,}|(?:sk|rk|pk)_live_[A-Za-z0-9]{16,})")
# Prefixed tokens glued to a word character (`sec_ghp_...`, `x_sk-...`, `ID_AKIA...`) slip past the leading \b
# in `secrets.redact`. GitHub and AWS prefixes are distinctive enough to match anywhere; `sk-` must not follow a
# letter, so ordinary words like `task-...` or `disk-...` stay readable.
_GLUED = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{36,}|AKIA[0-9A-Z]{16}|(?<![A-Za-z])sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")


def _looks_secret(value: str) -> bool:
    return (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)
            and _entropy(value) >= 3.0)


def _assign(m: re.Match) -> str:
    return m.group(1) + _R if _looks_secret(m.group(2)) else m.group(0)


def _bearer(m: re.Match) -> str:
    return m.group(1) + _R if _entropy(m.group(2)) >= 3.0 else m.group(0)


def _basic(m: re.Match) -> str:
    value = m.group(2)
    before = m.string[max(0, m.start() - 200):m.start()]
    line = before[before.rfind("\n") + 1:].lower()        # bounded look-back keeps this linear
    if "authoriz" in line or "=" in value or _looks_secret(value):
        return m.group(1) + _R
    return m.group(0)


def _named(m: re.Match) -> str:
    value = m.group(2)
    plain = (value.isalpha() or value.isdigit()) and _entropy(value) >= 2.5
    return m.group(1) + _R if plain or _looks_secret(value) else m.group(0)


def _extra_redact(text: str) -> str:
    text = _TOKENS.sub(_R, text)
    text = _GLUED.sub(_R, text)
    text = _URL_PASSWORD.sub(lambda m: m.group(1) + _R + m.group(3), text)
    text = _BEARER.sub(_bearer, text)
    text = _BASIC.sub(_basic, text)
    text = _AWS_LINE.sub(lambda m: _AWS_VALUE.sub(_R, m.group(0)), text)
    text = _ASSIGN.sub(_assign, text)
    return _NAMED.sub(_named, text)


def safe_text(text: str, path: str | None = None, max_chars: int | None = None) -> str:
    """Return `text` with secrets redacted, never longer than `max_chars` when given."""
    try:
        if path is not None and is_secret_file(path):
            return WITHHELD
        out = _extra_redact(redact(_strip_private_keys(text)))
        if max_chars is not None:
            cap = max(max_chars, 0)
            if len(out) > cap:
                if cap > len(TRUNCATED):
                    out = out[:cap - len(TRUNCATED)] + TRUNCATED
                else:
                    out = out[:cap]
        return out
    except Exception:  # fail closed: never hand back unredacted text
        return _FAILED

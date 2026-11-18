"""The safe-text choke point: nothing secret survives, and it never raises."""

import time

import pytest

from gh_analyzer import secrets
from gh_analyzer.agents import safe
from gh_analyzer.agents.safe import is_secret_file, safe_text

WITHHELD = "[file withheld: may contain secrets]"
MARKER = "\n...[truncated]"
# Built from pieces so this file holds no literal key.
OPENAI = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
AWS = "AKIA" + "7Q2XM9LPW4ZB3KD8"
GHP = "ghp_" + "R7tY2uI9oP4aS6dF8gH1jK3lZ5xC0vB7nM2q"
PASSWORD = "Zq8!vK3#mP9$xL2@wR7"


def test_redacts_known_secret_kinds():
    text = (
        f"openai {OPENAI} end\n"
        f"aws {AWS} end\n"
        f"gh {GHP} end\n"
        f'password = "{PASSWORD}"\n'
        "plain line stays\n"
    )
    out = safe_text(text)
    for secret in (OPENAI, AWS, GHP, PASSWORD):
        assert secret not in out
    assert out.count("[REDACTED]") == 4
    assert "plain line stays" in out
    assert out.startswith("openai [REDACTED] end\n")


def test_python_default_argument_secret():
    out = safe_text(f'def connect(key="{OPENAI}"):\n    return key\n')
    assert OPENAI not in out
    assert "[REDACTED]" in out
    assert "def connect(" in out


@pytest.mark.parametrize("path", [
    ".env", ".env.local", ".ENV", "config/.env", "a\\b\\.env.production",
    "id_rsa", "ssh/id_ed25519", "id_dsa", "id_ecdsa", "credentials.json", "Credentials.JSON",
    "certs/server.pem", "tls.KEY", "store.p12", "store.pfx",
    ".npmrc", "home/.NETRC", ".pypirc", "app.keystore", "release.JKS",
])
def test_secret_files_are_withheld(path):
    assert is_secret_file(path)
    out = safe_text("TOP_SECRET_CONTENT", path=path)
    assert out == WITHHELD
    assert "TOP_SECRET_CONTENT" not in out


@pytest.mark.parametrize("path", [
    "main.py", "src/environment.py", "env.py", "README.md", "keys.py",
    "credentials.json.md", "monkey", "docs/id_rsa.md", "npmrc", "keystore.py", "",
])
def test_ordinary_files_are_not_withheld(path):
    assert not is_secret_file(path)
    assert safe_text("hello", path=path) == "hello"


def test_max_chars_truncates_after_redaction():
    text = "x" * 19 + " " + OPENAI + " " + "y" * 20      # redacted: 51 chars
    out = safe_text(text, max_chars=39)                   # body 24 chars: cut inside "[REDACTED]"
    assert out == "x" * 19 + " [RED" + MARKER
    assert "sk-" not in out and "A1b2C3" not in out
    out2 = safe_text(text, max_chars=50)                  # body 35 chars: marker kept whole
    assert out2 == "x" * 19 + " [REDACTED] " + "y" * 4 + MARKER
    assert OPENAI not in out2


def test_max_chars_is_a_true_cap():
    for n in (0, 1, 5, 15, 16, 17, 30, 100):
        assert len(safe_text("abcdefghij" * 20, max_chars=n)) <= n


def test_truncation_marker_rules():
    assert safe_text("short", max_chars=5) == "short"
    assert safe_text("short", max_chars=100) == "short"
    assert safe_text("abcdef", max_chars=3) == "abc"                   # too small for a marker
    assert safe_text("a" * 40, max_chars=15) == "a" * 15              # == marker length: no marker
    assert safe_text("a" * 40, max_chars=16) == "a" + MARKER


def test_non_secret_text_unchanged():
    text = "version = '1.2.3'\npath = \"src/app/main.py\"\nurl = https://example.org/x\n\u00e9\u4e2d\n"
    assert safe_text(text) == text


def test_placeholders_are_left_alone():
    for text in ('api_key = "${API_KEY_FROM_VAULT}"', 'api_key = "<paste-key-here>"',
                 'token = os.environ["API_TOKEN"]'):
        assert safe_text(text) == text


def test_safe_text_is_total():
    nasty = ["", " ", "\x00", "\ud800", "\u4e2d\u6587" * 50, "a" * 2_000_000,
             "sk-" * 100_000, "password = '" + "a" * 500_000, "\n" * 100_000]
    for s in nasty:
        for kwargs in ({}, {"max_chars": 0}, {"max_chars": -5}, {"max_chars": 10},
                       {"path": ".env"}, {"path": ""}):
            out = safe_text(s, **kwargs)
            assert isinstance(out, str)
            if "max_chars" in kwargs:
                assert len(out) <= max(kwargs["max_chars"], 0)


# ---- formats that plain secrets.redact lets through ----------------------------------

TOK = "Zq8Kx3Lm9Pw2Rt7Vb4Nc6Yh1"                       # 25 chars, mixed, high entropy
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG" + "/bPxRfiCYzR3Tq8LmN2v"   # 40 chars
BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj"
PW = "Xp9Lq2Vn7Rk4Tm8Zc3"
BEARER = "eyJhbGciOiJIUzI1NiJ9" + ".Vq8Kx3Lm9Pw2Rt7Vb4Nc6Yh1"
GITHUB_PAT = "github_pat_" + "11AEZQ7RA0Hn4kP2xYv9Lc_Wd3FgBt5Ju8Ms"
STRIPE = ["sk_live_" + "51Hq8Kx3Lm9Pw2Rt7Vb4Nc6Y", "rk_live_" + "51Hq8Kx3Lm9Pw2Rt7Vb4Nc6Y",
          "pk_live_" + "51Hq8Kx3Lm9Pw2Rt7Vb4Nc6Y"]

FORMATS = [
    (f"export API_KEY={TOK}\n", TOK),
    (f"MY_TOKEN={TOK}\n", TOK),
    (f"ENV API_KEY {TOK}\n", TOK),
    (f"password: {TOK}\n", TOK),
    (f"key = {TOK}\n", TOK),
    (f'{{"api_key": "{TOK}"}}', TOK),
    (f"{{'token': '{TOK}'}}", TOK),
    (f'SECRET_KEY = "{TOK}"', TOK),
    (f'client_secret = "{TOK}"', TOK),
    (f"Authorization: Bearer {BEARER}\n", BEARER),
    (f"postgres://admin:{PW}@db.internal:5432/app", PW),
    (f"aws_secret_access_key = {AWS_SECRET}\n", AWS_SECRET),
    (f'AWS_SECRET: "{AWS_SECRET}"', AWS_SECRET),
    (f"[default]\nsecret_access_key={AWS_SECRET}\n", AWS_SECRET),
    (f"token {GITHUB_PAT} here", GITHUB_PAT),
] + [(f"billing {v} here", v) for v in STRIPE]


@pytest.mark.parametrize("text,secret", FORMATS)
def test_extra_formats_redacted_value_only(text, secret):
    assert secret in secrets.redact(text)            # plain redact misses it
    out = safe_text(text)
    assert secret not in out
    assert out == text.replace(secret, "[REDACTED]")  # name and context kept


@pytest.mark.parametrize("kind", ["", "RSA ", "EC ", "OPENSSH ", "ENCRYPTED ", "PGP "])
def test_private_key_block_is_redacted_whole(kind):
    suffix = " BLOCK" if kind == "PGP " else ""
    begin = f"-----BEGIN {kind}PRIVATE KEY{suffix}-----"
    end = f"-----END {kind}PRIVATE KEY{suffix}-----"
    text = f"before\n{begin}\n{BODY}\n{BODY[::-1]}\n{end}\nafter\n"
    assert BODY in secrets.redact(text)               # plain redact leaks the body
    assert safe_text(text) == "before\n[REDACTED]\nafter\n"


def test_two_private_key_blocks_and_literal_backslash_n():
    one = f"-----BEGIN PRIVATE KEY-----\n{BODY}\n-----END PRIVATE KEY-----"
    assert safe_text(f"a {one} b {one} c") == "a [REDACTED] b [REDACTED] c"
    esc = f"-----BEGIN RSA PRIVATE KEY-----\\n{BODY}\\n-----END RSA PRIVATE KEY-----"
    assert safe_text("k=" + esc) == "k=[REDACTED]"


def test_unterminated_private_key_block_swallows_the_rest():
    text = f"@@ diff\n+x = 1\n+-----BEGIN RSA PRIVATE KEY-----\n+{BODY}\n+{BODY[::-1]}\n"
    assert BODY in secrets.redact(text)
    out = safe_text(text)
    assert out == "@@ diff\n+x = 1\n+[REDACTED]"
    assert BODY not in out and BODY[::-1] not in out


@pytest.mark.parametrize("text", [
    "max_token_count = 1024",
    "password_field = form.password",
    'api_key = os.environ["API_KEY"]',
    "token = tokenizer.encode(text, add_special_tokens=True)",
    "SECRET_KEY = settings.SECRET_KEY",
    'commit = "' + "a1b2c3d4e5" * 4 + '"',
    "key = someVeryLongCamelCaseIdentifierNameForTheCache",
    "tokenizerConfig = SomeVeryLongCamelCaseIdentifierNameForConfigs",
    "A token identifies the caller; keep each token secret and rotate the key regularly.",
    "Use the API key from your dashboard, then set the password in settings.",
    "monkey patch applied to the donkey_cart",
    "https://github.com/org/repo/blob/main/src/module.py",
    "postgres://admin@db.internal:5432/app",
    "Authorization: Bearer <token>",
])
def test_ordinary_code_is_not_redacted(text):
    assert safe_text(text) == text


GHO = "gho_" + "R7tY2uI9oP4aS6dF8gH1jK3lZ5xC0vB7nM2q"
GHS = "ghs_" + "R7tY2uI9oP4aS6dF8gH1jK3lZ5xC0vB7nM2q"


@pytest.mark.parametrize("text,secret", [
    (f"sec_{GHP}.txt", GHP),
    (f"x_{OPENAI}", OPENAI),
    (f"tok{GHO} here", GHO),
    (f"v1_{GHS}", GHS),
    (f"ID_{AWS} end", AWS),
    (f"key9{OPENAI}", OPENAI),
])
def test_tokens_glued_to_word_characters_are_redacted(text, secret):
    assert secret in secrets.redact(text)            # the \b in plain redact lets it through
    out = safe_text(text)
    assert secret not in out and "[REDACTED]" in out


@pytest.mark.parametrize("text", [
    "task-management-framework-v2 is the module",
    "disk-usage-report-generator-2024.py",
    "ask-before-merging-into-main-branch-123",
])
def test_words_ending_in_sk_dash_are_not_redacted(text):
    assert safe_text(text) == text


def test_fail_closed_when_redact_raises(monkeypatch):
    def boom(_):
        raise RuntimeError("boom")
    monkeypatch.setattr(safe, "redact", boom)
    out = safe_text(f"password = '{PASSWORD}' and {OPENAI}")
    assert out == safe._FAILED
    assert PASSWORD not in out and OPENAI not in out


# ---- final fix wave: URL with empty user, Basic auth, AWS next line, Bearer concat, named passwords ----

PW16 = "Zq8vLm2pXw7rTn4K"                                   # 16 chars, mixed
ALPHA_PW = "hQwPzKmRtLsVbN" + "cXyJdFgHaE"                  # 24 letters only
DIGIT_KEY = "839201746583" + "920174658392"                 # 24 digits only
BASIC = "dXNlcjpaUTh2TG0ycFh3N3JUbjRL" + "cGFzcw=="
BASIC_LETTERS = "dXNlcjpwYXNz"                               # "user:pass", no digits
BEARER_CAT = "eyJhbGciOiJIUzI1NiJ9" + "abcDEF1234567890xyz"


@pytest.mark.parametrize("text", [
    f"REDIS_URL=redis://:{PW16}@cache.internal:6379/0",
    f"conn = connect('redis://:{PW16}@cache.internal:6379')",
    f'conn = connect("redis://:{PW16}@h")',
])
def test_url_password_with_empty_user_is_redacted(text):
    out = safe_text(text)
    assert PW16 not in out
    assert out == text.replace(PW16, "[REDACTED]")


@pytest.mark.parametrize("text", [
    "redis://cache.internal:6379/0",
    "http://localhost:8080/path?q=1",
    "https://user@example.org:8443/x",
    "see http://[::1]:8080/ and mailto:a@b.c",
    "ssh://git@github.com:22/org/repo.git",
    "urls: https://a.b/c, https://d.e:9/f@g",
])
def test_ordinary_urls_are_not_redacted(text):
    assert safe_text(text) == text


@pytest.mark.parametrize("text,secret", [
    (f"Authorization: Basic {BASIC}\n", BASIC),
    (f"Authorization: Basic {BASIC_LETTERS}\n", BASIC_LETTERS),
    (f'headers = {{"Authorization": "Basic {BASIC}"}}', BASIC),
    (f"aws_secret_access_key:\n  {AWS_SECRET}\n", AWS_SECRET),
    (f"[default]\naws_secret_access_key =\n{AWS_SECRET}\n", AWS_SECRET),
    (f'h = "Bearer " + "{BEARER_CAT}"', BEARER_CAT),
    (f"h = 'Bearer '+'{BEARER_CAT}'", BEARER_CAT),
    (f"password={ALPHA_PW}", ALPHA_PW),
    (f"API_KEY={DIGIT_KEY}", DIGIT_KEY),
    (f'db_password: "{ALPHA_PW}"', ALPHA_PW),
    (f"client_secret = '{DIGIT_KEY}'", DIGIT_KEY),
    (f"PWD={ALPHA_PW}", ALPHA_PW),
    (f"passwd: {ALPHA_PW}", ALPHA_PW),
    (f"MY_TOKEN={ALPHA_PW}", ALPHA_PW),
    (f"password={PW16}", PW16),
    (f'"password": "{PW16}"', PW16),
])
def test_more_secret_formats_redacted_value_only(text, secret):
    out = safe_text(text)
    assert secret not in out
    assert out == text.replace(secret, "[REDACTED]")


@pytest.mark.parametrize("text", [
    "Basic usage: call run() first.",
    "Basic authentication is supported.",
    "token = getAuthenticationToken()",
    "password = form.cleaned_data",
    "secret = settings.SECRET",
    "a token identifies the caller and the password protects it",
    "timeout = 1700000000000000",
    "aws_secret_access_key:\n  see the README for details\n",
    "Bearer authentication",
    'prefix = "Bearer " + token',
])
def test_new_rules_leave_ordinary_text_alone(text):
    assert safe_text(text) == text


def test_extra_patterns_are_linear_on_adversarial_input():
    nasty = ["key=" * 200_000, "password: " + "A1" * 500_000, "://" * 300_000,
             "aws_secret " + "A" * 2_000_000, "-----BEGIN PRIVATE KEY-----" * 50_000,
             "-----BEGIN PRIVATE KEY-----\n-----END PRIVATE KEY-----" * 50_000,
             "Bearer " * 200_000, "github_pat_" * 100_000, "key" + " " * 1_000_000,
             "token " + "a1" * 1_000_000, "a://b:" * 100_000, "sk_live_" * 100_000]
    start = time.monotonic()
    for s in nasty:
        assert isinstance(safe_text(s), str)
    assert time.monotonic() - start < 30

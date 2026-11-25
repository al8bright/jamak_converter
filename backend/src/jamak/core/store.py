"""Settings database: API keys (DPAPI-encrypted), translation profiles, app settings.

One SQLite file at <home>/jamak.db. Job caches stay as files under jobs/.
Schema changes are applied in order using PRAGMA user_version.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from jamak.config import paths
from jamak.core import providers, secrets

# cloud ids come from core/providers.py (one entry per service); test_providers checks they match
Provider = Literal["anthropic", "gemini", "openai", "deepseek", "xai", "mistral", "openrouter", "deepl", "papago", "openai_compat"]
Kind = Literal["local_managed", "local_external", "anthropic", "gemini", "openai", "deepseek", "xai", "mistral", "openrouter", "deepl", "papago"]
CLOUD_KINDS = providers.CLOUD_KINDS
KIND_PROVIDER: dict[str, str] = {**{k: k for k in CLOUD_KINDS}, "local_external": "openai_compat"}
ENV_KEYS = providers.ENV_KEYS

DEFAULT_KEY = "default_translation_profile_id"

MIGRATIONS = [
    """
    CREATE TABLE api_keys (
      id          INTEGER PRIMARY KEY,
      provider    TEXT NOT NULL,
      label       TEXT NOT NULL,
      secret      BLOB NOT NULL,
      last4       TEXT NOT NULL,
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL
    );
    CREATE TABLE translation_profiles (
      id          INTEGER PRIMARY KEY,
      name        TEXT NOT NULL UNIQUE,
      kind        TEXT NOT NULL,
      model       TEXT,
      base_url    TEXT,
      api_key_id  INTEGER REFERENCES api_keys(id) ON DELETE SET NULL,
      options     TEXT NOT NULL DEFAULT '{}',
      last_test   TEXT,
      created_at  TEXT NOT NULL,
      updated_at  TEXT NOT NULL
    );
    CREATE TABLE app_settings (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    """,
]


class StoreError(ValueError):
    pass


class ProfileOptions(BaseModel):
    """The per-profile part of TranslateOptions."""

    batch_size: int = Field(25, ge=1, le=200)
    context_lines: int = Field(5, ge=0, le=50)
    parallel: int | None = Field(None, ge=1, le=16)  # None: 1 for local, 4 for cloud
    gpu_layers: int = Field(99, ge=0, le=999)
    effort: Literal["low", "medium", "high"] | None = None  # Claude only; None = model default
    refusal: Literal["local", "mark"] = "local"  # cloud refusal: retranslate locally, or only flag
    fallback_profile_id: int | None = None  # cloud: when the service fails (quota, outage), use this profile
    temperature: float | None = Field(None, ge=0, le=2)


class Profile(BaseModel):
    id: int
    name: str
    kind: Kind
    model: str | None = None
    base_url: str | None = None
    api_key_id: int | None = None
    options: ProfileOptions = Field(default_factory=ProfileOptions)
    last_test: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def cloud(self) -> bool:
        return self.kind in CLOUD_KINDS


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: Kind
    model: str | None = None
    base_url: str | None = None
    api_key_id: int | None = None
    options: ProfileOptions = Field(default_factory=ProfileOptions)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | None = None):
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or paths.home / "jamak.db"

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            self._migrate(con)
            with con:
                yield con
        finally:
            con.close()

    def _migrate(self, con: sqlite3.Connection) -> None:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version >= len(MIGRATIONS):
            return
        for i in range(version, len(MIGRATIONS)):
            with con:
                con.executescript(MIGRATIONS[i])
                con.execute(f"PRAGMA user_version={i + 1}")
        if version == 0:
            self._seed(con)

    def _seed(self, con: sqlite3.Connection) -> None:
        now = _now()
        with con:
            cur = con.execute(
                "INSERT INTO translation_profiles (name, kind, model, options, created_at, updated_at)"
                " VALUES (?, 'local_managed', NULL, '{}', ?, ?)", ("로컬 · Gemma 4 12B", now, now))
            con.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                        (DEFAULT_KEY, str(cur.lastrowid)))

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as con:
            row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as con:
            con.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))

    # ------------------------------------------------------------------ api keys
    def list_keys(self) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("SELECT id, provider, label, last4, created_at FROM api_keys ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def add_key(self, provider: str, label: str, secret: str) -> dict[str, Any]:
        secret = secret.strip()
        info = providers.get(provider)
        if info is None and provider != "openai_compat":
            raise StoreError("알 수 없는 제공자입니다")
        parts = secret.split("\n")
        if info is not None and len(parts) != len(info.key_parts):
            raise StoreError(f"{info.name}에는 {', '.join(info.key_parts)}이(가) 필요합니다")
        if any(len(x.strip()) < 8 for x in parts):
            raise StoreError("키가 너무 짧습니다")
        secret = "\n".join(x.strip() for x in parts)
        blob = secrets.protect(secret)
        now = _now()
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO api_keys (provider, label, secret, last4, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (provider, label.strip() or provider, blob, secret[-4:], now, now))
            key_id = cur.lastrowid
        return next(k for k in self.list_keys() if k["id"] == key_id)

    def remove_key(self, key_id: int) -> bool:
        with self._conn() as con:
            row = con.execute("SELECT secret FROM api_keys WHERE id=?", (key_id,)).fetchone()
            gone = con.execute("DELETE FROM api_keys WHERE id=?", (key_id,)).rowcount > 0
        if row is not None:
            secrets.forget(bytes(row["secret"]))
        return gone

    def key_secret(self, key_id: int) -> str:
        with self._conn() as con:
            row = con.execute("SELECT secret FROM api_keys WHERE id=?", (key_id,)).fetchone()
        if row is None:
            raise StoreError("API 키가 없습니다")
        return secrets.unprotect(bytes(row["secret"]))

    def key_info(self, key_id: int | None) -> dict[str, Any] | None:
        if key_id is None:
            return None
        return next((k for k in self.list_keys() if k["id"] == key_id), None)

    # ------------------------------------------------------------------ profiles
    @staticmethod
    def _row(r: sqlite3.Row) -> Profile:
        d = dict(r)
        d["options"] = ProfileOptions.model_validate(json.loads(d.get("options") or "{}"))
        d["last_test"] = json.loads(d["last_test"]) if d.get("last_test") else None
        return Profile.model_validate(d)

    def list_profiles(self) -> list[Profile]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM translation_profiles ORDER BY id").fetchall()
        return [self._row(r) for r in rows]

    def get_profile(self, profile_id: int) -> Profile | None:
        with self._conn() as con:
            r = con.execute("SELECT * FROM translation_profiles WHERE id=?", (profile_id,)).fetchone()
        return self._row(r) if r else None

    def find_profile(self, name_or_id: str) -> Profile | None:
        if name_or_id.isdigit():
            p = self.get_profile(int(name_or_id))
            if p:
                return p
        with self._conn() as con:
            r = con.execute("SELECT * FROM translation_profiles WHERE name=?", (name_or_id,)).fetchone()
        return self._row(r) if r else None

    def _check(self, con: sqlite3.Connection, p: ProfileInput) -> None:
        if p.kind == "local_external" and not (p.base_url or "").strip():
            raise StoreError("외부 서버 프로필에는 서버 주소가 필요합니다")
        fb = p.options.fallback_profile_id
        if fb is not None:
            r = con.execute("SELECT kind FROM translation_profiles WHERE id=?", (fb,)).fetchone()
            if r is None or r["kind"] not in CLOUD_KINDS:
                raise StoreError("대신 쓸 프로필은 클라우드 프로필이어야 합니다")
        info = providers.get(p.kind)
        if info is not None and info.llm and not (p.model or "").strip():
            raise StoreError("클라우드 프로필에는 모델 ID가 필요합니다")
        if p.api_key_id is not None:
            r = con.execute("SELECT provider FROM api_keys WHERE id=?", (p.api_key_id,)).fetchone()
            if r is None:
                raise StoreError("API 키가 없습니다")
            if r["provider"] != KIND_PROVIDER.get(p.kind):
                raise StoreError("다른 제공자의 키입니다")

    def add_profile(self, p: ProfileInput) -> Profile:
        now = _now()
        with self._conn() as con:
            self._check(con, p)
            try:
                cur = con.execute(
                    "INSERT INTO translation_profiles (name, kind, model, base_url, api_key_id, options,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (p.name.strip(), p.kind, p.model or None, p.base_url or None, p.api_key_id,
                     p.options.model_dump_json(exclude_none=True), now, now))
            except sqlite3.IntegrityError as e:
                raise StoreError("같은 이름의 프로필이 이미 있습니다") from e
            pid = cur.lastrowid
        return self.get_profile(pid)  # type: ignore[return-value]

    def update_profile(self, profile_id: int, p: ProfileInput) -> Profile:
        with self._conn() as con:
            self._check(con, p)
            try:
                n = con.execute(
                    "UPDATE translation_profiles SET name=?, kind=?, model=?, base_url=?, api_key_id=?, options=?,"
                    " last_test=NULL, updated_at=? WHERE id=?",
                    (p.name.strip(), p.kind, p.model or None, p.base_url or None, p.api_key_id,
                     p.options.model_dump_json(exclude_none=True), _now(), profile_id)).rowcount
            except sqlite3.IntegrityError as e:
                raise StoreError("같은 이름의 프로필이 이미 있습니다") from e
        if not n:
            raise StoreError("프로필이 없습니다")
        return self.get_profile(profile_id)  # type: ignore[return-value]

    def remove_profile(self, profile_id: int) -> bool:
        with self._conn() as con:
            n = con.execute("DELETE FROM translation_profiles WHERE id=?", (profile_id,)).rowcount
        if n and self.get_setting(DEFAULT_KEY) == str(profile_id):
            rest = self.list_profiles()
            local = [p for p in rest if p.kind == "local_managed"] or rest
            with self._conn() as con:
                if local:
                    con.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                                (DEFAULT_KEY, str(local[0].id)))
                else:
                    con.execute("DELETE FROM app_settings WHERE key=?", (DEFAULT_KEY,))
        return n > 0

    def set_test_result(self, profile_id: int, ok: bool, message: str) -> None:
        with self._conn() as con:
            con.execute("UPDATE translation_profiles SET last_test=? WHERE id=?",
                        (json.dumps({"ok": ok, "message": message, "at": _now()}, ensure_ascii=False),
                         profile_id))

    def default_profile_id(self) -> int | None:
        v = self.get_setting(DEFAULT_KEY)
        return int(v) if v and v.isdigit() else None

    def set_default(self, profile_id: int) -> None:
        if self.get_profile(profile_id) is None:
            raise StoreError("프로필이 없습니다")
        self.set_setting(DEFAULT_KEY, str(profile_id))

    def default_profile(self) -> Profile | None:
        pid = self.default_profile_id()
        return self.get_profile(pid) if pid else None

    # ------------------------------------------------------------------ secrets for a profile
    def profile_secret(self, p: Profile) -> str | None:
        """The key to call this profile's service with: linked key first, then environment."""
        import os

        if p.api_key_id is not None:
            return self.key_secret(p.api_key_id)
        for name in ENV_KEYS.get(p.kind, ()):
            if os.environ.get(name):
                return os.environ[name]
        return None

    def has_secret(self, p: Profile) -> bool:
        import os

        if p.api_key_id is not None:
            return self.key_info(p.api_key_id) is not None
        return any(os.environ.get(n) for n in ENV_KEYS.get(p.kind, ()))


store = Store()

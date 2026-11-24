"""Settings database, DPAPI key protection, profile resolution, settings API."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from jamak.config import PipelineOptions
from jamak.core import secrets
from jamak.core.store import ProfileInput, ProfileOptions, Store, StoreError

SECRET = "sk-ant-test-0123456789abcdefWXYZ"


@pytest.fixture
def db(jamak_home):
    return Store()


def test_dpapi_roundtrip_and_no_plaintext():
    blob = secrets.protect(SECRET)
    assert SECRET.encode() not in blob
    assert secrets.unprotect(blob) == SECRET


def test_first_run_seeds_local_default(db):
    profiles = db.list_profiles()
    assert len(profiles) == 1 and profiles[0].kind == "local_managed"
    assert db.default_profile_id() == profiles[0].id
    with sqlite3.connect(db.path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1


def test_keys_are_encrypted_and_listed_without_secret(db):
    k = db.add_key("anthropic", "개인 키", SECRET)
    assert k["last4"] == "WXYZ" and "secret" not in k
    assert db.key_secret(k["id"]) == SECRET
    raw = db.path.read_bytes() + (db.path.with_name("jamak.db-wal").read_bytes()
                                  if db.path.with_name("jamak.db-wal").exists() else b"")
    assert SECRET.encode() not in raw
    assert all("secret" not in row for row in db.list_keys())


def test_profile_crud_and_key_unlink_on_delete(db):
    k = db.add_key("anthropic", "a", SECRET)
    p = db.add_profile(ProfileInput(name="Claude", kind="anthropic", model="claude-opus-5-5", api_key_id=k["id"],
                                    options=ProfileOptions(parallel=4, effort="low")))
    assert p.options.effort == "low" and p.api_key_id == k["id"]
    with pytest.raises(StoreError):
        db.add_profile(ProfileInput(name="Claude", kind="anthropic", model="x"))
    with pytest.raises(StoreError):  # gemini profile cannot use an anthropic key
        db.add_profile(ProfileInput(name="G", kind="gemini", model="m", api_key_id=k["id"]))
    db.remove_key(k["id"])
    assert db.get_profile(p.id).api_key_id is None
    db.set_default(p.id)
    db.remove_profile(p.id)
    assert db.default_profile().kind == "local_managed"


def test_resolve_options_applies_profile_and_never_stores_key(db):
    from jamak.core.translate.factory import resolve_options

    k = db.add_key("anthropic", "a", SECRET)
    p = db.add_profile(ProfileInput(name="C", kind="anthropic", model="claude-sonnet-5-5", api_key_id=k["id"]))
    opts = resolve_options(PipelineOptions(), p.id, db=db)
    t = opts.translate
    assert (t.mode, t.api_model, t.parallel, t.api_key_id) == ("anthropic", "claude-sonnet-5-5", 4, k["id"])
    dumped = opts.model_dump_json()
    assert SECRET not in dumped
    # default profile (local) when nothing is chosen
    assert resolve_options(PipelineOptions(), db=db).translate.mode == "managed"


def test_cache_key_ignores_api_key(db, tmp_path):
    from jamak.core.pipeline import translate_identity
    from jamak.core.translate.factory import resolve_options

    k1 = db.add_key("anthropic", "a", SECRET)
    k2 = db.add_key("anthropic", "b", SECRET[::-1])
    p = db.add_profile(ProfileInput(name="C", kind="anthropic", model="claude-opus-5-5", api_key_id=k1["id"]))
    a = translate_identity(resolve_options(PipelineOptions(), p.id, db=db))
    db.update_profile(p.id, ProfileInput(name="C", kind="anthropic", model="claude-opus-5-5", api_key_id=k2["id"]))
    b = translate_identity(resolve_options(PipelineOptions(), p.id, db=db))
    assert a == b
    assert SECRET not in json.dumps(a)


def test_settings_api_never_returns_secret(jamak_home):
    from jamak.api.server import create_app

    c = TestClient(create_app())
    r = c.post("/settings/api-keys", json={"provider": "anthropic", "label": "개인 키", "secret": SECRET})
    assert r.status_code == 201 and SECRET not in r.text
    key_id = r.json()["id"]
    r = c.post("/settings/translation-profiles", json={
        "name": "Claude · Opus", "kind": "anthropic", "model": "claude-opus-5-5", "api_key_id": key_id,
        "options": {"parallel": 4}})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["key"]["last4"] == "WXYZ" and r.json()["status"]["text"] == "연결 테스트 전"
    assert c.put("/settings/default-profile", json={"profile_id": pid}).status_code == 200
    listing = c.get("/settings/translation-profiles").json()
    assert [p["is_default"] for p in listing] == [False, True]
    for path in ("/settings/api-keys", "/settings/translation-profiles", "/system/checks"):
        assert SECRET not in c.get(path).text
    assert c.post("/settings/translation-profiles", json={"name": "x", "kind": "gemini"}).status_code == 400
    assert c.put("/settings/ui", json={"palette": "acid"}).json()["palette"] == "acid"
    assert c.get("/settings/ui").json()["palette"] == "acid"
    u = c.get("/system/usage").json()
    assert 0 <= u["cpu"] <= 100 and u["ram_total_gb"] > 0


def test_job_uses_chosen_profile(jamak_home, tmp_path):
    from jamak.api.jobs import JobManager
    from jamak.core.store import store

    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    p = store.add_profile(ProfileInput(name="ext", kind="local_external", base_url="http://127.0.0.1:9/v1"))
    mgr = JobManager()
    mgr.q.put = lambda item: None  # don't run
    st = mgr.submit_run(video, PipelineOptions(), profile_id=p.id)
    assert st.engine["kind"] == "external" and st.engine["profile_name"] == "ext"
    from jamak.api.jobs import load_options
    from jamak.core.cache import Job

    saved = load_options(Job(video)).translate
    assert saved.mode == "external" and saved.base_url == "http://127.0.0.1:9/v1"

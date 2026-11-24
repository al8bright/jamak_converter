"""Translation APIs that are not LLMs: DeepL and NAVER Papago.

They translate one text per subtitle line: no prompt, no tone instructions. To keep glossary
names, each Japanese term found in a line is replaced by its Korean form before sending
(DeepL keeps it untouched inside an ignored <keep> tag; Papago passes Hangul through as is).
Usage is counted in characters sent, which is what both services bill.
"""

from __future__ import annotations

import html
import re

import httpx

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core import providers
from jamak.core.translate.base import AuthError, BatchError, BatchTranslator, EngineError
from jamak.core.types import Line


def apply_glossary(text: str, glossary: list[GlossaryEntry], wrap=lambda ko: ko) -> str:
    """Replace glossary terms (longest first, so 風間くん wins over 風間) with their Korean form."""
    terms = sorted((e for e in glossary if e.ja and e.ko and e.ja in text), key=lambda e: -len(e.ja))
    if not terms:
        return text
    pattern = re.compile("|".join(re.escape(e.ja) for e in terms))
    table = {e.ja: e.ko for e in terms}
    return pattern.sub(lambda m: wrap(table[m.group(0)]), text)


class _MtTranslator(BatchTranslator):
    transient = (httpx.HTTPError,)

    def __init__(self, opts: TranslateOptions):
        super().__init__(opts)
        self.stats["characters"] = 0

    def _count(self, texts: list[str]) -> None:
        with self._lock:
            self.stats["characters"] += sum(len(t) for t in texts)


class DeepLTranslator(_MtTranslator):
    kind = "deepl"

    def __init__(self, opts: TranslateOptions, key: str, transport: httpx.BaseTransport | None = None):
        super().__init__(opts)
        host = "api-free.deepl.com" if key.strip().endswith(":fx") else "api.deepl.com"
        self.url = f"https://{host}/v2"
        self.client = httpx.Client(timeout=opts.request_timeout, transport=transport,
                                   headers={"Authorization": f"DeepL-Auth-Key {key.strip()}"})

    def close(self) -> None:
        self.client.close()

    def _request(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry]) -> dict[int, str]:
        texts = [apply_glossary(html.escape(ln.ja, quote=False), glossary,
                                lambda ko: f"<keep>{html.escape(ko, quote=False)}</keep>") for ln in batch]
        body = {"text": texts, "source_lang": "JA", "target_lang": "KO", "tag_handling": "xml",
                "ignore_tags": ["keep"], "split_sentences": "nonewlines"}
        ctx = " ".join(c.get("ja", "") for c in context)[-1000:]
        if ctx:
            body["context"] = ctx
        r = self.client.post(f"{self.url}/translate", json=body)
        if r.status_code == 403:
            raise AuthError("DeepL이 키를 거부했습니다 (HTTP 403)")
        if r.status_code == 456:
            raise EngineError("DeepL 이번 달 사용 한도를 넘었습니다 (HTTP 456)")
        if r.status_code != 200:
            raise BatchError(f"DeepL HTTP {r.status_code}: {r.text[:200]}")
        out = [t.get("text", "") for t in r.json().get("translations", [])]
        if len(out) != len(batch):
            raise BatchError("DeepL returned a different number of lines")
        self._count(texts)
        self._record()
        return {ln.id: _strip_keep(t) for ln, t in zip(batch, out, strict=True)}


def _strip_keep(text: str) -> str:
    return html.unescape(re.sub(r"</?keep>", "", text)).strip()


def deepl_usage(key: str, transport: httpx.BaseTransport | None = None) -> dict:
    host = "api-free.deepl.com" if key.strip().endswith(":fx") else "api.deepl.com"
    with httpx.Client(timeout=20, transport=transport, headers={"Authorization": f"DeepL-Auth-Key {key.strip()}"}) as c:
        r = c.get(f"https://{host}/v2/usage")
    if r.status_code == 403:
        raise AuthError("DeepL이 키를 거부했습니다 (HTTP 403)")
    r.raise_for_status()
    d = r.json()
    return {"characters": d.get("character_count"), "limit": d.get("character_limit")}


PAPAGO_URL = "https://papago.apigw.ntruss.com/nmt/v1/translation"


class PapagoTranslator(_MtTranslator):
    kind = "papago"

    def __init__(self, opts: TranslateOptions, key: str, transport: httpx.BaseTransport | None = None):
        super().__init__(opts)
        try:
            client_id, secret = providers.split_secret(key, 2)
        except ValueError as e:
            raise EngineError("Papago 키는 Client ID와 Client Secret이 모두 필요합니다") from e
        self.client = httpx.Client(timeout=opts.request_timeout, transport=transport, headers={
            "X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": secret})

    def close(self) -> None:
        self.client.close()

    def _one(self, text: str) -> str:
        r = self.client.post(PAPAGO_URL, data={"source": "ja", "target": "ko", "text": text})
        if r.status_code in (401, 403):
            raise AuthError(f"Papago가 키를 거부했습니다 (HTTP {r.status_code})")
        if r.status_code != 200:
            code = ""
            try:
                code = str((r.json().get("error") or {}).get("errorCode", ""))
            except ValueError:
                pass
            if r.status_code == 429 or code in ("010", "N2MT10"):  # quota
                raise EngineError(f"Papago 사용 한도를 넘었습니다 (HTTP {r.status_code})")
            raise BatchError(f"Papago HTTP {r.status_code}: {r.text[:200]}")
        self._count([text])
        self._record()
        return r.json()["message"]["result"]["translatedText"]

    def _request(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry]) -> dict[int, str]:
        texts = [apply_glossary(re.sub(r"\s*\n\s*", " ", ln.ja), glossary) for ln in batch]
        if len(batch) > 1:  # one request for the batch; lines stay apart as long as the line count matches
            out = self._one("\n".join(texts)).split("\n")
            if len(out) == len(batch):
                return {ln.id: t.strip() for ln, t in zip(batch, out, strict=True)}
        return {ln.id: self._one(t).strip() for ln, t in zip(batch, texts, strict=True)}

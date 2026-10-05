#!/usr/bin/env python3
"""참고문헌 BibTeX 만들기 — 기억이 아니라 **공식 서지 정보**(Crossref·arXiv API)로만 채운다.

    make_bib.py --md docs/model-literature-2026-10.md --md notes/fedora/labeling-standard-recipe.md \
                --titles docs/references-titles.txt --misc docs/references-misc.json --out docs/references.bib

- 마크다운에서 DOI(10.xxxx/...)와 arXiv 번호를 뽑아 조회한다.
- `--titles` 파일의 각 줄(DOI 없는 논문 제목)은 Crossref 제목 검색 결과 제목이 0.9 이상 같을 때만 넣는다(아니면 경고만).
- `--misc` JSON은 법령·규정·웹 문서처럼 API에 없는 항목(사람이 확인한 것만).
- 각 항목 note에 확인 출처와 날짜를 남긴다. 확인 못 한 것은 넣지 않고 마지막에 목록으로 알린다.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

UA = {"User-Agent": "soupdata-bib/1.0 (research reference builder)"}
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s)\]>,;]+")
ARXIV_RE = re.compile(r"(?:arXiv:|arxiv\.org/abs/)(\d{4}\.\d{4,5})", re.I)
STOP = {"a", "an", "the", "on", "of", "for", "and", "to", "in", "with", "what", "why", "how", "towards", "toward", "via", "using"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _ascii(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())


def _key(family: str, year, title: str, used: set[str]) -> str:
    word = next((w for w in re.findall(r"[A-Za-z]+", title) if w.lower() not in STOP), "ref")
    base = f"{_ascii(family) or 'anon'}{year or ''}{word.lower()}"
    key, i = base, 2
    while key in used:
        key, i = f"{base}{i}", i + 1
    used.add(key)
    return key


def _esc(s) -> str:
    return str(s).replace("&", r"\&").replace("%", r"\%").replace("#", r"\#").replace("_", r"\_")


def crossref_doi(doi: str) -> dict | None:
    try:
        return json.loads(_get("https://api.crossref.org/works/" + urllib.parse.quote(doi)))["message"]
    except Exception:
        return None


def crossref_title(title: str) -> dict | None:
    q = urllib.parse.urlencode({"query.bibliographic": title, "rows": 5})
    try:
        items = json.loads(_get("https://api.crossref.org/works?" + q))["message"]["items"]
    except Exception:
        return None
    norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()  # noqa: E731
    best = max(items, key=lambda it: difflib.SequenceMatcher(None, norm(title), norm((it.get("title") or [""])[0])).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, norm(title), norm((best.get("title") or [""])[0])).ratio() >= 0.9:
        return best
    return None


def arxiv_batch(ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    for i in range(0, len(ids), 20):
        chunk = ids[i:i + 20]
        try:
            root = ET.fromstring(_get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
                {"id_list": ",".join(chunk), "max_results": len(chunk)})))
        except Exception:
            continue
        for e in root.findall("a:entry", ns):
            aid = re.sub(r"v\d+$", "", (e.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1])
            title = " ".join((e.findtext("a:title", "", ns) or "").split())
            if not title or aid not in chunk:
                continue
            out[aid] = {"title": title, "authors": [" ".join((a.findtext("a:name", "", ns) or "").split()) for a in e.findall("a:author", ns)],
                        "year": (e.findtext("a:published", "", ns) or "")[:4], "doi": e.findtext("x:doi", None, ns),
                        "journal_ref": e.findtext("x:journal_ref", None, ns)}
        time.sleep(3)  # arXiv API 예절(요청 간격)
    return out


def entry_from_crossref(m: dict, used: set[str], how: str) -> str:
    authors = [f"{a.get('family', '')}, {a.get('given', '')}".strip(", ") for a in m.get("author", []) if a.get("family") or a.get("given")]
    year = ((m.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    title = (m.get("title") or [""])[0]
    typ = {"journal-article": "article", "proceedings-article": "inproceedings", "book-chapter": "incollection"}.get(m.get("type"), "misc")
    venue_field = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle"}.get(typ, "howpublished")
    fields = {"author": " and ".join(authors) or None, "title": "{" + title + "}", venue_field: (m.get("container-title") or [None])[0],
              "year": year, "volume": m.get("volume"), "number": m.get("issue"), "pages": m.get("page"), "doi": m.get("DOI"),
              "note": f"Metadata verified via Crossref ({how}) {date.today().isoformat()}"}
    key = _key((m.get("author") or [{}])[0].get("family", ""), year, title, used)
    body = ",\n".join(f"  {k} = {{{_esc(v) if k not in ('title',) else v}}}" for k, v in fields.items() if v)
    return f"@{typ}{{{key},\n{body}\n}}"


def entry_from_arxiv(aid: str, a: dict, used: set[str]) -> str:
    family = a["authors"][0].split()[-1] if a["authors"] else ""
    fields = {"author": " and ".join(a["authors"]), "title": "{" + a["title"] + "}", "year": a["year"], "eprint": aid,
              "archivePrefix": "arXiv", "doi": a.get("doi"), "journal": a.get("journal_ref"), "url": f"https://arxiv.org/abs/{aid}",
              "note": f"Metadata verified via arXiv API {date.today().isoformat()}"}
    key = _key(family, a["year"], a["title"], used)
    typ = "article" if a.get("journal_ref") else "misc"
    body = ",\n".join(f"  {k} = {{{_esc(v) if k != 'title' else v}}}" for k, v in fields.items() if v)
    return f"@{typ}{{{key},\n{body}\n}}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--md", action="append", default=[])
    p.add_argument("--titles")
    p.add_argument("--misc")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    text = "\n".join(Path(f).read_text(encoding="utf-8") for f in a.md)
    dois = sorted({d.rstrip(".") for d in DOI_RE.findall(text)})
    arx = sorted(set(ARXIV_RE.findall(text)))
    used: set[str] = set()
    entries, missing = [], []
    for d in dois:
        m = crossref_doi(d)
        (entries.append(entry_from_crossref(m, used, f"DOI {d}")) if m else missing.append(f"DOI {d}"))
    for aid, meta in sorted(arxiv_batch(arx).items()):
        entries.append(entry_from_arxiv(aid, meta, used))
    missing += [f"arXiv {x}" for x in arx if not any(f"eprint = {{{x}}}" in e for e in entries)]
    if a.titles:
        for t in [ln.strip() for ln in Path(a.titles).read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]:
            m = crossref_title(t)
            if m and not any((m.get("DOI") or "").lower() in e.lower() for e in entries):
                entries.append(entry_from_crossref(m, used, "title search"))
            elif not m:
                missing.append(f"title: {t}")
    if a.misc:
        for it in json.loads(Path(a.misc).read_text(encoding="utf-8")):
            key = it.pop("key")
            body = ",\n".join(f"  {k} = {{{v}}}" for k, v in it.items())
            entries.append(f"@misc{{{key},\n{body}\n}}")
    header = f"% 자동 생성: research/tools/make_bib.py ({date.today().isoformat()}) — Crossref·arXiv API로 확인한 항목만. 손으로 고치지 말고 다시 생성한다.\n"
    Path(a.out).write_text(header + "\n\n".join(entries) + "\n", encoding="utf-8")
    print(f"{len(entries)}개 항목 → {a.out}")
    for m in missing:
        print(f"  확인 못 함(넣지 않음): {m}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

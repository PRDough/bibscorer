import io
import re

import bibtexparser

from importers import merge_entries
from tests.test_importers import EBSCO_XML

# 和 EBSCO_XML 里的第一篇是同一篇（DOI 相同、大小写不同），字段更完整
DUPLICATE_BIB = rb"""
@article{han2026,
  title = {The Double-Edged Sword of Negative Environmental Information},
  author = {Han, Tingliang and Gu, Jintu},
  year = {2026},
  journal = {Sustainability},
  doi = {https://doi.org/10.3390/SU18031559},
  keywords = {worry; children},
  volume = {18},
  number = {3},
  pages = {1559},
  abstract = {Children and environmental worry.}
}
@article{zhao2026city,
  title = {A different paper that happens to share a citation key},
  author = {Zhao, Q.},
  year = {2026}
}
"""


def test_merge_by_doi_keeps_most_complete():
    a = {"ID": "x1", "ENTRYTYPE": "article", "title": "Short", "doi": "10.1/ABC"}
    b = {"ID": "x2", "ENTRYTYPE": "article", "title": "Short", "doi": "https://doi.org/10.1/abc",
         "journal": "J", "year": "2020"}
    entries, sources, removed = merge_entries([("a.bib", [a]), ("b.bib", [b])])
    assert removed == 1
    assert entries == [b]
    assert sources == ["a.bib; b.bib"]


def test_merge_by_title_when_no_doi():
    a = {"ID": "a", "ENTRYTYPE": "article", "title": "Climate Policy and {Green} Transformation in Chinese Cities"}
    b = {"ID": "b", "ENTRYTYPE": "article", "title": "Climate policy and green transformation in Chinese cities."}
    entries, sources, removed = merge_entries([("a.bib", [a]), ("b.xml", [b])])
    assert removed == 1 and len(entries) == 1


def test_short_titles_need_same_first_author():
    a = {"ID": "a", "ENTRYTYPE": "article", "title": "Introduction", "author": "Smith, A."}
    b = {"ID": "b", "ENTRYTYPE": "article", "title": "Introduction", "author": "Lee, K."}
    entries, _, removed = merge_entries([("a.bib", [a, b])])
    assert removed == 0 and len(entries) == 2


def test_duplicates_within_one_file_are_removed():
    a = {"ID": "a", "ENTRYTYPE": "article", "title": "T", "doi": "10.1/x"}
    entries, sources, removed = merge_entries([("a.bib", [a, dict(a, ID="b")])])
    assert removed == 1 and sources == ["a.bib"]


def test_conflicting_citation_keys_get_suffixes():
    a = {"ID": "smith2021", "ENTRYTYPE": "article", "title": "First paper about coal mines"}
    b = {"ID": "smith2021", "ENTRYTYPE": "article", "title": "Second paper about steel plants"}
    c = {"ID": "smith2021a", "ENTRYTYPE": "article", "title": "Third paper with an existing suffix"}
    entries, _, _ = merge_entries([("a.bib", [a, c]), ("b.bib", [b])])
    ids = [e["ID"] for e in entries]
    assert len(set(ids)) == 3 and ids[0] == "smith2021" and ids[1] == "smith2021a"
    # 原来的条目不会被改动
    assert b["ID"] == "smith2021"


def upload_files(client, files, **data):
    data.setdefault("weights", "worry:2\nclimate:1")
    data["bibfile"] = [(io.BytesIO(content), name) for name, content in files]
    return client.post("/", data=data, content_type="multipart/form-data").get_data(as_text=True)


def test_upload_multiple_files(client):
    html = upload_files(client, [("refs.bib", DUPLICATE_BIB), ("Exposure.xml", EBSCO_XML.encode())])
    assert "Uploaded: <strong>refs.bib, Exposure.xml</strong>." in html
    assert "refs.bib: 2 · Exposure.xml: 3 · 1 duplicate removed." in html
    # 2 + 3 - 1 = 4 篇
    assert re.search(r"<strong>\d+</strong> of 4 entries matched", html)
    assert 'name="source" value="mixed"' in html
    assert "Entries from .bib files are exported exactly as uploaded" in html
    labels = [l.strip() for l in re.findall(r'id="field_\d+"[^>]*>([^<]+)', html)]
    assert "Source file" in labels

    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    csv = client.post("/download", data={"token": token, "export_format": "csv", "fields": ["ID", "source_file"]})
    lines = csv.get_data().decode("utf-8-sig").splitlines()
    assert lines[0] == "Citation key,Source file"
    assert "han2026,refs.bib; Exposure.xml" in lines

    bib = client.post("/download", data={"token": token, "export_format": "bib"}).get_data(as_text=True)
    entries = bibtexparser.loads(bib).entries
    ids = [e["ID"] for e in entries]
    assert len(ids) == 4 == len(set(ids))
    # 保留了更完整的 .bib 版本；冲突的引用键改了名；source_file 不写进 .bib
    kept = next(e for e in entries if e["ID"] == "han2026")
    assert kept["journal"] == "Sustainability"
    assert "zhao2026citya" in ids
    assert all("source_file" not in e for e in entries)


def test_rerank_keeps_upload_note(client):
    html = upload_files(client, [("refs.bib", DUPLICATE_BIB), ("Exposure.xml", EBSCO_XML.encode())])
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    note = re.search(r'name="upload_note" value="([^"]*)"', html).group(1)
    html = client.post("/", data={"weights": "climate:1", "token": token, "source": "mixed", "upload_note": note,
                                  "uploaded_filename": "refs.bib, Exposure.xml"},
                       content_type="multipart/form-data").get_data(as_text=True)
    assert "1 duplicate removed" in html


def test_bad_file_among_several_names_it(client):
    html = upload_files(client, [("refs.bib", DUPLICATE_BIB), ("broken.xml", b"<records><record>")])
    assert "broken.xml: Could not read the XML file." in html


def test_too_many_files(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "MAX_FILES", 1)
    html = upload_files(client, [("a.bib", DUPLICATE_BIB), ("b.bib", DUPLICATE_BIB)])
    assert "Upload at most 1 files" in html

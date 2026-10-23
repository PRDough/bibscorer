import io
import os

import bibtexparser
import openpyxl

from tests.test_importers import EBSCO_XML
from tests.test_merge import DUPLICATE_BIB

FETCH = {"X-Requested-With": "fetch"}


def dedupe(client, files, headers=FETCH, **data):
    data["bibfile"] = [(io.BytesIO(content), name) for name, content in files]
    return client.post("/dedupe", data=data, headers=headers, content_type="multipart/form-data")


def both_files():
    return [("refs.bib", DUPLICATE_BIB), ("Exposure.xml", EBSCO_XML.encode())]


def test_dedupe_downloads_merged_bib(client):
    r = dedupe(client, both_files())
    assert r.status_code == 200
    assert r.headers["Content-Disposition"] == "attachment; filename=bibscore_merged.bib"
    assert r.headers["X-Merge-Summary"] == "2 files, 5 entries: 1 duplicate removed, 4 kept"
    entries = bibtexparser.loads(r.get_data(as_text=True)).entries
    ids = [e["ID"] for e in entries]
    assert len(ids) == 4 == len(set(ids))
    # 保留更完整的版本；原文件的顺序不变（refs.bib 在前）
    assert ids[0] == "han2026" and entries[0]["journal"] == "Sustainability"
    assert all("source_file" not in e for e in entries)


def test_dedupe_csv(client):
    r = dedupe(client, both_files(), dedupe_format="csv")
    assert r.headers["Content-Disposition"] == "attachment; filename=bibscore_merged.csv"
    lines = r.get_data().decode("utf-8-sig").splitlines()
    header = lines[0].split(",")
    assert header[:3] == ["Title", "Authors", "Year"]
    assert "Source file" in header and "Citation key" in header
    # 没有打分，所以没有分数列
    assert "Keyword score" not in header and "Matched keywords" not in header
    assert len(lines) == 5


def test_dedupe_excel(client):
    r = dedupe(client, both_files(), dedupe_format="xlsx")
    assert r.headers["Content-Disposition"] == "attachment; filename=bibscore_merged.xlsx"
    rows = list(openpyxl.load_workbook(io.BytesIO(r.data))["References"].values)
    assert rows[0][0] == "Title" and len(rows) == 5


def test_unknown_format_falls_back_to_bib(client):
    r = dedupe(client, both_files(), dedupe_format="exe")
    assert r.headers["Content-Disposition"] == "attachment; filename=bibscore_merged.bib"


def test_dedupe_saves_nothing(client):
    import app as app_module
    dedupe(client, both_files())
    assert not os.path.exists(app_module.OUTPUT_FOLDER) or not os.listdir(app_module.OUTPUT_FOLDER)


def test_dedupe_errors_as_text_for_fetch(client):
    r = client.post("/dedupe", data={}, headers=FETCH, content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_data(as_text=True) == "Choose the files to combine first."
    r = dedupe(client, [("refs.bib", DUPLICATE_BIB), ("broken.xml", b"<records><record>")])
    assert r.status_code == 400 and r.get_data(as_text=True) == "broken.xml: Could not read the XML file."


def test_dedupe_errors_without_javascript_show_page(client):
    r = dedupe(client, [("broken.xml", b"<records><record>")], headers={})
    html = r.get_data(as_text=True)
    assert "Could not read the XML file." in html and 'class="error"' in html


def test_dedupe_button_markup(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="dedupe-box" hidden' in html
    assert 'formaction="/dedupe"' in html and "formnovalidate" in html
    assert '<option value="xlsx">Excel (.xlsx)</option>' in html


def test_dedupe_blocked_for_ai_agents(client):
    r = dedupe(client, both_files(), headers={"User-Agent": "GPTBot/1.1"})
    assert r.status_code == 403

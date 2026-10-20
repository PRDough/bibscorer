import io
import re

import bibtexparser
import openpyxl

WEIGHTS = "just transition:3\nsteel*:2\ncoal:1\nart:1"

# 带 LaTeX、花括号和已有 note 的条目，用来检查 .bib 导出是否保留原文
RAW_BIB = rb"""
@article{doe2023,
  title = {Just transition in the {steel} industry: {CO$_2$} and jobs},
  author = {Doe, Jane and M{\"u}ller, Rick},
  year = {2023},
  journal = {Energy Policy},
  abstract = {We study how steelmaking regions manage a just transition away from coal.},
  keywords = {steel; just transition},
  note = {Preprint}
}
@inproceedings{smith2021,
  title = {Coal phase-out and labour markets},
  author = {Smith, A.},
  year = {2021},
  booktitle = {Proceedings of Energy},
  abstract = {A review of coal regions and the just transition debate.}
}
@book{lee2019,
  title = {Art history of industrial landscapes},
  author = {Lee, K.},
  year = {2019}
}
"""


def upload(client, **data):
    data.setdefault("weights", WEIGHTS)
    data["bibfile"] = (io.BytesIO(RAW_BIB), "refs.bib")
    html = client.post("/", data=data, content_type="multipart/form-data").get_data(as_text=True)
    return re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)


def export(client, token, **data):
    data["token"] = token
    return client.post("/download", data=data)


def test_bib_export_round_trips_original_entries(client):
    token = upload(client)
    r = export(client, token, export_format="bib")
    assert r.headers["Content-Disposition"] == "attachment; filename=bibscore_ranking.bib"
    exported = bibtexparser.loads(r.get_data(as_text=True)).entries
    original = {e["ID"]: e for e in bibtexparser.loads(RAW_BIB.decode()).entries}
    # 按排名顺序输出，内容和上传的原文完全一致（LaTeX、花括号都在）
    assert [e["ID"] for e in exported] == ["doe2023", "smith2021", "lee2019"]
    for entry in exported:
        assert entry == original[entry["ID"]]
    assert "{CO$_2$}" in exported[0]["title"]


def test_bib_export_ignores_field_selection(client):
    token = upload(client)
    r = export(client, token, export_format="bib", fields=["title"])
    entry = bibtexparser.loads(r.get_data(as_text=True)).entries[0]
    assert entry["journal"] == "Energy Policy"


def test_bib_export_top_n_and_note(client):
    token = upload(client)
    r = export(client, token, export_format="bib", export_scope="top", top_n="2", bib_note="1")
    entries = bibtexparser.loads(r.get_data(as_text=True)).entries
    assert [e["ID"] for e in entries] == ["doe2023", "smith2021"]
    assert entries[0]["note"].startswith("Preprint; BibScorer rank 1, keyword score ")
    assert entries[1]["note"].startswith("BibScorer rank 2, keyword score ")


def test_min_score_filter(client):
    token = upload(client)
    csv = export(client, token, export_format="csv", export_scope="min", min_value="4",
                 fields=["ID", "score"]).get_data().decode("utf-8-sig").splitlines()
    assert csv[0] == "Citation key,Keyword score"
    scores = [float(line.split(",")[1]) for line in csv[1:]]
    assert scores and all(s >= 4 for s in scores)
    assert len(csv) - 1 < 3


def test_min_similarity_filter_uses_ranking_metric(client):
    token = upload(client, question="coal regions and jobs", rank_by="similarity")
    csv = export(client, token, export_format="csv", export_scope="min", min_value="0.2", rank_by="similarity",
                 fields=["ID", "similarity"]).get_data().decode("utf-8-sig").splitlines()
    sims = [float(line.split(",")[1]) for line in csv[1:]]
    assert sims and all(s >= 0.2 for s in sims)


def test_excel_export(client):
    token = upload(client, question="coal")
    r = export(client, token, export_format="xlsx", fields=["title", "author", "score", "similarity"])
    assert r.headers["Content-Disposition"] == "attachment; filename=bibscore_ranking.xlsx"
    book = openpyxl.load_workbook(io.BytesIO(r.data))
    sheet = book["Ranking"]
    rows = list(sheet.values)
    assert rows[0] == ("Title", "Authors", "Keyword score", "Similarity")
    assert len(rows) == 4
    # 分数是数字，不是文本
    assert isinstance(rows[1][2], (int, float)) and isinstance(rows[1][3], float)
    assert sheet.freeze_panes == "A2"


def test_export_errors_keep_choices(client):
    token = upload(client)
    html = export(client, token, export_format="csv", export_scope="top", top_n="abc",
                  fields=["title"]).get_data(as_text=True)
    assert "Enter how many top entries" in html
    assert 'value="top" checked' in html and 'value="title" id="field_1" checked' in html

    html = export(client, token, export_format="bib", export_scope="min", min_value="999").get_data(as_text=True)
    assert "No entries match" in html
    assert 'value="bib" checked' in html

    html = export(client, token, export_format="xlsx").get_data(as_text=True)
    assert "at least one field" in html


def test_bib_export_needs_original_entries(client):
    import app as app_module
    import os
    token = upload(client)
    os.remove(app_module.result_path(token, ".raw.json"))
    html = export(client, token, export_format="bib").get_data(as_text=True)
    assert "original entries have expired" in html

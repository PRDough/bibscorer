import io
import os
import re

import app as app_module

WEIGHTS = "just transition:3\nsteel*:2\ncoal:1\nart:1"


def upload(client, bib, **data):
    data.setdefault("weights", WEIGHTS)
    data["bibfile"] = (io.BytesIO(bib), "sample.bib")
    return client.post("/", data=data, content_type="multipart/form-data")


def rerank(client, token, **data):
    data.update(token=token, uploaded_filename="sample.bib")
    data.setdefault("weights", WEIGHTS)
    return client.post("/", data=data, content_type="multipart/form-data")


def get_token(html):
    return re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)


def download(client, token, fields):
    return client.post("/download", data={"token": token, "fields": fields})


def download_csv(client, token, fields):
    return download(client, token, fields).get_data().decode("utf-8-sig")


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="question"' in html
    assert "<details class=\"advanced\" >" in html  # 默认系数时收起


def test_upload_shows_ranking(client, sample_bib):
    html = upload(client, sample_bib).get_data(as_text=True)
    assert "Uploaded: <strong>sample.bib</strong>" in html
    assert "<strong>3</strong> of 3 entries matched" in html
    assert "<mark>steel</mark>" in html
    assert "Doe, Jane et al., 2023" in html
    # 标题 (3+2)×3 + 摘要 (steelmaking 2 + jt 3 + coal 1)×1 + keywords (2+3)×2 = 31
    assert "<strong>31</strong>" in html
    # 摘要里的命中词也要高亮
    assert "<mark>steelmaking</mark>" in html and "<mark>coal</mark>" in html


def test_bib_content_is_escaped(client, sample_bib):
    html = upload(client, sample_bib).get_data(as_text=True)
    assert "&lt;script&gt;" in html
    assert "<script>}" not in html


def test_download_csv(client, sample_bib):
    token = get_token(upload(client, sample_bib).get_data(as_text=True))
    r = download(client, token, ["title", "score", "matched_keywords"])
    assert r.headers["Content-Disposition"] == "attachment; filename=bibscore_ranking.csv"
    assert r.get_data().startswith(b"\xef\xbb\xbf")  # BOM，Excel 才能正确识别 UTF-8
    lines = r.get_data().decode("utf-8-sig").splitlines()
    assert lines[0] == "Title,Keyword score,Matched keywords"
    assert lines[1].startswith("Just transition in the steel industry,31,")


def test_custom_multipliers_keep_advanced_open(client, sample_bib):
    html = upload(client, sample_bib, fw_title="1").get_data(as_text=True)
    assert "<details class=\"advanced\" open>" in html
    assert 'value="1" min="0"' in html


def test_stylesheet_is_served(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert b"--rule" in r.data


def test_rerank_without_reupload(client, sample_bib):
    token = get_token(upload(client, sample_bib).get_data(as_text=True))
    html = rerank(client, token, weights="art:5", fw_title="1", fw_abstract="1", fw_keywords="1").get_data(as_text=True)
    assert "Uploaded: <strong>sample.bib</strong>" in html
    csv = download_csv(client, token, ["title", "score"])
    assert csv.splitlines()[1] == "Art history of industrial landscapes,5"


def test_semantic_ranking(client, sample_bib):
    question = "workers in mining towns industrial heritage"
    token = get_token(upload(client, sample_bib, question=question, rank_by="similarity").get_data(as_text=True))
    csv = download_csv(client, token, ["ID", "score", "similarity"]).splitlines()
    assert csv[0] == "Citation key,Keyword score,Similarity"
    # 按相似度排序时，没命中多少关键词但内容相关的 c 排第一
    assert csv[1].startswith("c,")
    sims = [float(line.split(",")[2]) for line in csv[1:]]
    assert sims == sorted(sims, reverse=True)
    assert os.path.exists(app_module.result_path(token, ".npy"))


def test_semantic_vectors_are_cached(client, sample_bib, fake_model, monkeypatch):
    token = get_token(upload(client, sample_bib, question="coal").get_data(as_text=True))
    calls = []
    original = fake_model.passage_embed
    monkeypatch.setattr(fake_model, "passage_embed", lambda texts, **kw: calls.append(1) or original(texts, **kw))
    rerank(client, token, question="steel")
    assert calls == []


def test_question_only_without_keywords(client, sample_bib):
    html = upload(client, sample_bib, weights="", question="coal regions").get_data(as_text=True)
    assert 'class="sim"' in html
    assert 'class="error"' not in html


def test_semantic_disabled(client, sample_bib, monkeypatch):
    monkeypatch.setenv("SEMANTIC_ENABLED", "0")
    html = client.get("/").get_data(as_text=True)
    assert 'id="question"' not in html
    html = upload(client, sample_bib, question="coal").get_data(as_text=True)
    assert 'class="sim"' not in html


def test_semantic_entry_limit(client, sample_bib, monkeypatch):
    monkeypatch.setattr(app_module.semantic, "MAX_ENTRIES", 2)
    html = upload(client, sample_bib, question="coal").get_data(as_text=True)
    assert "up to 2 entries" in html


def test_semantic_failure_is_reported(client, sample_bib, monkeypatch):
    def broken():
        raise RuntimeError("no model")
    monkeypatch.setattr(app_module.semantic, "get_model", broken)
    html = upload(client, sample_bib, question="coal").get_data(as_text=True)
    assert "temporarily unavailable" in html


def test_download_requires_fields(client, sample_bib):
    token = get_token(upload(client, sample_bib).get_data(as_text=True))
    html = client.post("/download", data={"token": token, "weights": WEIGHTS}).get_data(as_text=True)
    assert "at least one field" in html
    assert 'id="ranking-title"' in html


def test_invalid_token_is_rejected(client):
    r = client.post("/download", data={"token": "../../etc/passwd", "fields": ["title"]})
    assert "expired" in r.get_data(as_text=True)
    r = client.post("/", data={"weights": WEIGHTS, "token": "0" * 32}, content_type="multipart/form-data")
    assert "expired" in r.get_data(as_text=True)


def test_upload_errors(client):
    def post(data):
        return client.post("/", data=data, content_type="multipart/form-data").get_data(as_text=True)

    assert "choose a .bib" in post({"weights": WEIGHTS})
    assert "UTF-8" in post({"weights": WEIGHTS, "bibfile": (io.BytesIO(b"\xff\xfe\x00"), "x.bib")})
    assert "No references were found" in post({"weights": WEIGHTS, "bibfile": (io.BytesIO(b"hello"), "x.bib")})
    assert "at least one valid" in post({"weights": "nonsense", "bibfile": (io.BytesIO(b"@article{a, title={x}}"), "x.bib")})


def test_expired_results_are_removed(client, sample_bib, monkeypatch):
    token = get_token(upload(client, sample_bib).get_data(as_text=True))
    monkeypatch.setattr(app_module, "RESULT_TTL", -1)
    upload(client, sample_bib)
    assert not os.path.exists(app_module.result_path(token))


def test_export_fields_have_readable_names_and_order():
    import pandas as pd
    columns = ["keywords", "abstract", "year", "author", "title", "ENTRYTYPE", "ID",
               "web_of_science_categories", "score", "matched_keywords", "similarity", "empty_field"]
    df = pd.DataFrame([{c: ("" if c == "empty_field" else "x") for c in columns}])
    fields = app_module.export_fields(df)
    assert fields == [
        ("title", "Title"), ("author", "Authors"), ("year", "Year"), ("score", "Keyword score"),
        ("similarity", "Similarity"), ("matched_keywords", "Matched keywords"), ("abstract", "Abstract"),
        ("keywords", "Keywords"), ("ID", "Citation key"), ("ENTRYTYPE", "Entry type"),
        ("web_of_science_categories", "Web of science categories"),
    ]


def test_results_band_and_field_labels_on_page(client, sample_bib):
    html = upload(client, sample_bib).get_data(as_text=True)
    assert 'class="results-band"' in html
    assert 'value="ID" id="field_' in html and "Citation key" in html
    assert ">ENTRYTYPE<" not in html

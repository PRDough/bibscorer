import io
import json
import re

SITE_ID = "00000000-0000-4000-8000-000000000000"
SRC = "https://stats.example.com/script.js"


def event_on_page(html):
    return json.loads(re.search(r"var event = (.*?);\n", html).group(1))


def post(client, **data):
    return client.post("/", data=data, content_type="multipart/form-data").get_data(as_text=True)


def test_no_script_unless_configured(client, monkeypatch):
    monkeypatch.delenv("ANALYTICS_ID", raising=False)
    monkeypatch.delenv("ANALYTICS_SRC", raising=False)
    assert "data-website-id" not in client.get("/").get_data(as_text=True)
    # 只设置其中一个也不加载
    monkeypatch.setenv("ANALYTICS_ID", SITE_ID)
    assert "data-website-id" not in client.get("/").get_data(as_text=True)
    monkeypatch.delenv("ANALYTICS_ID")
    monkeypatch.setenv("ANALYTICS_SRC", SRC)
    assert "data-website-id" not in client.get("/").get_data(as_text=True)


def test_invalid_settings_are_ignored(client, monkeypatch):
    monkeypatch.setenv("ANALYTICS_SRC", SRC)
    monkeypatch.setenv("ANALYTICS_ID", 'abc" onload="alert(1)')
    assert "data-website-id" not in client.get("/").get_data(as_text=True)
    monkeypatch.setenv("ANALYTICS_ID", SITE_ID)
    monkeypatch.setenv("ANALYTICS_SRC", "http://insecure.example.com/script.js")
    assert "data-website-id" not in client.get("/").get_data(as_text=True)


def test_script_limited_to_production_domain(client, monkeypatch):
    monkeypatch.setenv("ANALYTICS_SRC", f" {SRC} ")
    monkeypatch.setenv("ANALYTICS_ID", f"  {SITE_ID}\n")
    html = client.get("/").get_data(as_text=True)
    tag = re.search(r"<script defer src=\"https://stats.example.com/script.js\"[^>]*>", html).group(0)
    assert f'data-website-id="{SITE_ID}"' in tag
    assert 'data-domains="bibscorer.prdough.com"' in tag
    # 只有一个统计脚本，页面浏览由脚本自动统计
    assert html.count(SRC) == 1


def test_no_score_event_on_page_load(client):
    assert event_on_page(client.get("/").get_data(as_text=True)) is None


def test_score_completed_event(client, sample_bib):
    html = post(client, weights="steel:2", bibfile=(io.BytesIO(sample_bib), "a.bib"))
    event = event_on_page(html)
    assert event["name"] == "score_completed"
    assert event["data"] == {"entries": 3, "semantic": "no", "ranked_by": "score"}
    assert re.fullmatch(r"[0-9a-f]{32}", event["id"])


def test_semantic_score_completed_event(client, sample_bib):
    html = post(client, weights="", question="coal", rank_by="similarity", bibfile=(io.BytesIO(sample_bib), "a.bib"))
    assert event_on_page(html)["data"] == {"entries": 3, "semantic": "yes", "ranked_by": "similarity"}


def test_each_result_gets_its_own_event_id(client, sample_bib):
    first = event_on_page(post(client, weights="steel:2", bibfile=(io.BytesIO(sample_bib), "a.bib")))
    second = event_on_page(post(client, weights="steel:2", bibfile=(io.BytesIO(sample_bib), "a.bib")))
    assert first["id"] != second["id"]


def test_score_failed_reasons(client, sample_bib, monkeypatch):
    def reason(**data):
        event = event_on_page(post(client, **data))
        assert event["name"] == "score_failed"
        return event["data"]["reason"]

    assert reason(weights="steel:2") == "no_file"
    assert reason(weights="steel:2", token="0" * 32) == "expired"
    assert reason(weights="steel:2", bibfile=(io.BytesIO(b"hello"), "x.bib")) == "invalid_file"
    assert reason(weights="nonsense", bibfile=(io.BytesIO(sample_bib), "a.bib")) == "no_keywords"

    import app as app_module
    monkeypatch.setattr(app_module.semantic, "MAX_ENTRIES", 1)
    assert reason(weights="", question="coal", bibfile=(io.BytesIO(sample_bib), "a.bib")) == "too_many_entries"


def test_semantic_failure_reason(client, sample_bib, monkeypatch):
    import app as app_module

    def broken():
        raise RuntimeError("no model")
    monkeypatch.setattr(app_module.semantic, "get_model", broken)
    html = post(client, weights="", question="coal", bibfile=(io.BytesIO(sample_bib), "a.bib"))
    assert event_on_page(html)["data"]["reason"] == "semantic_unavailable"


def test_too_large_reason(client, monkeypatch):
    import app as app_module
    monkeypatch.setitem(app_module.app.config, "MAX_CONTENT_LENGTH", 100)
    r = client.post("/", data={"weights": "a:1", "bibfile": (io.BytesIO(b"x" * 500), "big.bib")},
                    content_type="multipart/form-data")
    assert r.status_code == 413
    assert event_on_page(r.get_data(as_text=True))["data"]["reason"] == "too_large"


def test_export_pages_never_send_score_events(client, sample_bib):
    html = post(client, weights="steel:2", bibfile=(io.BytesIO(sample_bib), "a.bib"))
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    # 导出出错时会重新显示结果页，但这不是一次新的评分
    html = client.post("/download", data={"token": token, "export_format": "csv"}).get_data(as_text=True)
    assert "at least one field" in html
    assert event_on_page(html) is None

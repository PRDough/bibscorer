import io
import re

import bibtexparser
import pytest

import app as app_module
from importers import UnsupportedFile, citation_key, looks_like_xml, parse_ebsco_xml

# 和 EBSCO 导出格式相同的小样例（内容是编的）
EBSCO_XML = """<records>
  <record>
    <title>The Double-Edged Sword of Negative Environmental Information: Worry and Intentions Among Children.</title>
    <publicationDate>20260201</publicationDate>
    <authors>Han, Tingliang ; Gu, Jintu ; Müller, Anna</authors>
    <publisher>MDPI</publisher>
    <plink>https://research.ebsco.com/plink/aaaa</plink>
    <longDbName>Sustainability Reference Center</longDbName>
    <accessionNumber>191586368</accessionNumber>
    <abstract>Children are exposed to negative environmental information; p &lt; 0.05 &amp; more.</abstract>
    <doi>10.3390/su18031559</doi>
  </record>
  <record>
    <title>Can City Climate Policy Promote Corporate Green Transformation in China?</title>
    <publicationDate>20260801</publicationDate>
    <authors>Zhao, Zixin ; Gong, Yue</authors>
    <publisher>MDPI</publisher>
    <plink>https://research.ebsco.com/plink/bbbb</plink>
    <longDbName>Complementary Index</longDbName>
    <accessionNumber>196265852</accessionNumber>
    <abstract>Climate governance and green transformation of firms.</abstract>
    <doi></doi>
  </record>
  <record>
    <title>The double effect of worry.</title>
    <publicationDate>2026</publicationDate>
    <authors>Han, Li</authors>
    <publisher>Frontiers Media S.A.</publisher>
    <plink>https://research.ebsco.com/plink/cccc</plink>
    <longDbName>Complementary Index</longDbName>
    <accessionNumber>1</accessionNumber>
    <abstract>Worry about climate.</abstract>
    <doi>10.1/x</doi>
  </record>
</records>
"""


def test_looks_like_xml():
    assert looks_like_xml("\n  <records>")
    assert not looks_like_xml("@article{a, title={x}}")


def test_parse_ebsco_fields():
    first, second, third = parse_ebsco_xml(EBSCO_XML)
    assert first == {
        "ENTRYTYPE": "article",
        "ID": "han2026double",
        "title": "The Double-Edged Sword of Negative Environmental Information: Worry and Intentions Among Children",
        "author": "Han, Tingliang and Gu, Jintu and Müller, Anna",
        "year": "2026",
        "month": "feb",
        "publisher": "MDPI",
        "url": "https://research.ebsco.com/plink/aaaa",
        "database": "Sustainability Reference Center",
        "accessionnumber": "191586368",
        "abstract": "Children are exposed to negative environmental information; p < 0.05 & more.",
        "doi": "10.3390/su18031559",
    }
    # 空的 doi 不保留；问号结尾的标题不去标点
    assert "doi" not in second
    assert second["title"].endswith("China?")
    assert second["ID"] == "zhao2026city"
    # 只有年份时不加 month；重复的引用键加后缀
    assert "month" not in third and third["year"] == "2026"
    assert third["ID"] == "han2026doublea"


def test_citation_key_fallbacks():
    assert citation_key({"title": "A study", "year": "2020"}) == "anon2020study"
    assert citation_key({"author": "World Health Organization", "title": "Report"}) == "worldreport"


@pytest.mark.parametrize("text, message", [
    ("<records></records>", "Unsupported XML format"),
    ("<html><body>hi</body></html>", "Unsupported XML format"),
    ("<records><record>", "Could not read"),
])
def test_bad_xml(text, message):
    with pytest.raises(UnsupportedFile, match=message):
        parse_ebsco_xml(text)


def test_xml_bomb_is_rejected():
    bomb = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<records><record><title>&lol2;</title></record></records>"""
    with pytest.raises(UnsupportedFile):
        parse_ebsco_xml(bomb)


def upload_xml(client, **data):
    data.setdefault("weights", "worry:2\nclimate:1")
    data["bibfile"] = (io.BytesIO(EBSCO_XML.encode()), "Exposure.xml")
    return client.post("/", data=data, content_type="multipart/form-data").get_data(as_text=True)


def test_upload_ebsco_xml(client):
    html = upload_xml(client)
    assert "Uploaded: <strong>Exposure.xml</strong>" in html
    assert "<strong>3</strong> of 3 entries matched" in html
    assert "<mark>Worry</mark>" in html
    assert 'name="source" value="ebsco"' in html
    assert "Converted from EBSCO XML" in html
    # 没有关键词，导出选项里不出现 Keywords；EBSCO 字段有易读的名字，排在最后
    labels = [l.strip() for l in re.findall(r'id="field_\d+"[^>]*>([^<]+)', html)]
    assert "Keywords" not in labels
    assert labels[-2:] == ["Source database", "EBSCO accession number"]


def test_ebsco_rerank_keeps_source(client):
    html = upload_xml(client)
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    html = client.post("/", data={"weights": "climate:1", "token": token, "source": "ebsco",
                                  "uploaded_filename": "Exposure.xml"},
                       content_type="multipart/form-data").get_data(as_text=True)
    assert "Converted from EBSCO XML" in html


def test_ebsco_bib_export_imports_cleanly(client):
    html = upload_xml(client)
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    r = client.post("/download", data={"token": token, "export_format": "bib"})
    entries = bibtexparser.loads(r.get_data(as_text=True)).entries
    assert len(entries) == 3
    assert {e["ID"] for e in entries} == {"han2026double", "zhao2026city", "han2026doublea"}
    first = next(e for e in entries if e["ID"] == "han2026double")
    assert first["author"] == "Han, Tingliang and Gu, Jintu and Müller, Anna"
    assert first["doi"] == "10.3390/su18031559" and first["month"] == "feb"


def test_bibtex_upload_still_says_original(client, sample_bib):
    html = client.post("/", data={"weights": "steel:1", "bibfile": (io.BytesIO(sample_bib), "a.bib")},
                       content_type="multipart/form-data").get_data(as_text=True)
    assert 'name="source" value="bibtex"' in html
    assert "exactly as uploaded" in html

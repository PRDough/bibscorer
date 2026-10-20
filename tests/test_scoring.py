import pandas as pd
import pytest

from scoring import (SCORED_FIELDS, clean_text, keyword_pattern, parse_field_weights,
                     parse_weights, score_dataframe)


@pytest.mark.parametrize("raw, expected", [
    ("CO$_2$ emissions", "CO2 emissions"),
    ("{Steel} \\& {Iron}", "Steel & Iron"),
    ("10\\% of   firms\n", "10% of firms"),
    ("H$_2$ storage", "H2 storage"),
    (float("nan"), ""),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_parse_weights():
    raw = "CO2: capture:3\nsteel:2\nbad:x\n:4\n*:1\nno colon\nneg:-1"
    assert parse_weights(raw) == {"CO2: capture": 3, "steel": 2, "neg": -1}


@pytest.mark.parametrize("keyword, text, count", [
    ("art", "partial art Art start", 2),
    ("steel", "steel steels steelmaking", 1),
    ("steel*", "steel steels steelmaking stainless", 3),
    ("just transition", "Just\n  transition, just-transition", 1),
    ("AI", "AI said chain", 1),
])
def test_keyword_pattern(keyword, text, count):
    assert len(keyword_pattern(keyword).findall(text)) == count


def test_parse_field_weights_defaults_and_validation():
    assert parse_field_weights({}) == SCORED_FIELDS
    weights = parse_field_weights({"fw_title": "0", "fw_abstract": "-2", "fw_keywords": "abc"})
    assert weights == {"title": 0, "abstract": SCORED_FIELDS["abstract"], "keywords": SCORED_FIELDS["keywords"]}


def test_score_dataframe_applies_field_multipliers():
    df = pd.DataFrame([
        {"title": "Steel and CO2", "abstract": "", "keywords": "steel; just transition; steel"},
        {"title": "Other", "abstract": "A Just Transition study", "keywords": "coal"},
    ])
    scored = score_dataframe(df, {"steel": 2, "just transition": 3}, {"title": 3, "abstract": 1, "keywords": 2})
    # 标题 steel 1×2×3 + keywords (steel 2×2 + just transition 3)×2 = 20
    assert scored["score"].tolist() == [20, 3]
    assert scored["matched_keywords"].tolist() == ["steel ×3; just transition ×1", "just transition ×1"]


def test_score_dataframe_rescoring_replaces_old_columns():
    df = pd.DataFrame([{"title": "steel", "abstract": "", "keywords": ""}])
    once = score_dataframe(df, {"steel": 1}, SCORED_FIELDS)
    twice = score_dataframe(once, {"steel": 2}, {"title": 0.5, "abstract": 1, "keywords": 1})
    assert list(twice.columns).count("score") == 1
    assert twice["score"].tolist() == [1]


def test_fractional_scores_are_rounded():
    df = pd.DataFrame([{"title": "steel", "abstract": "", "keywords": ""}])
    scored = score_dataframe(df, {"steel": 1}, {"title": 1 / 3, "abstract": 1, "keywords": 1})
    assert scored["score"].tolist() == [0.33]

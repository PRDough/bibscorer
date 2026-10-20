import os

import numpy as np
import pytest

import semantic


def test_similarities_zero_for_empty_text(fake_model):
    texts = ["coal mining", ""]
    vectors = semantic.embed_passages(texts)
    sims = semantic.similarities(semantic.embed_query("coal"), vectors, texts)
    assert sims[0] > 0 and sims[1] == 0


def test_vectors_are_normalized(fake_model):
    vectors = semantic.embed_passages(["a b c", "steel steel"])
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1)


def test_passage_text():
    assert semantic.passage_text({"title": "T", "abstract": "A"}) == "T. A"
    assert semantic.passage_text({"title": "", "abstract": "A"}) == "A"


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="set RUN_SLOW=1 to run the real model")
def test_real_model_ranks_related_text_higher():
    texts = [
        "Workers lose jobs when coal mines close in mining regions.",
        "A recipe for chocolate cake with strawberries.",
    ]
    sims = semantic.similarities(semantic.embed_query("employment effects of coal phase-out"),
                                 semantic.embed_passages(texts), texts)
    assert sims[0] > sims[1] + 0.1

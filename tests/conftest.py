import re
import zlib

import numpy as np
import pytest

import app as app_module
import semantic


class FakeEmbedding:
    """不下载模型的假向量模型：按词哈希成词袋向量，词重合越多越相似。"""

    dim = 64

    def _vector(self, text):
        vec = np.zeros(self.dim, dtype=np.float32)
        for word in re.findall(r"\w+", text.lower()):
            vec[zlib.crc32(word.encode()) % self.dim] += 1
        return vec

    def passage_embed(self, texts, batch_size=None):
        return (self._vector(t) for t in texts)

    def query_embed(self, text):
        return iter([self._vector(text)])


@pytest.fixture
def fake_model(monkeypatch):
    model = FakeEmbedding()
    monkeypatch.setattr(semantic, "get_model", lambda: model)
    return model


@pytest.fixture
def client(tmp_path, monkeypatch, fake_model):
    monkeypatch.setattr(app_module, "OUTPUT_FOLDER", str(tmp_path / "output"))
    monkeypatch.delenv("SEMANTIC_ENABLED", raising=False)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


SAMPLE_BIB = b"""
@article{a, title={Just transition in the steel industry}, author={Doe, Jane and Roe, Rick}, year={2023},
  abstract={We study how steelmaking regions manage a just transition away from coal, with a focus on CO$_2$ reduction.},
  keywords={steel; just transition; decarbonization}}
@article{b, title={Coal phase-out and labour markets <script>}, author={Smith, A.}, year={2021},
  abstract={A partial review of coal regions and the just transition debate.}, keywords={coal; employment}}
@article{c, title={Art history of industrial landscapes}, author={Lee, K.}, year={2019},
  abstract={Industrial heritage and workers in former mining towns.}}
"""


@pytest.fixture
def sample_bib():
    return SAMPLE_BIB

"""语义打分：用本地向量模型（fastembed + ONNX）计算每篇文献和研究问题的相似度。

模型在第一次用到时才加载，之后在进程内复用。
设置环境变量 SEMANTIC_ENABLED=0 可以整体关闭这个功能。
"""
import os
import numpy as np

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
CACHE_DIR = os.environ.get("EMBED_CACHE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".models"))
# 单个文件最多对多少篇做语义打分，避免免费服务器上跑太久
MAX_ENTRIES = int(os.environ.get("SEMANTIC_MAX_ENTRIES", "1000"))
# 小批量、少线程可以把内存峰值压在 300 MB 左右（Render 免费版只有 512 MB）
BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "8"))
THREADS = int(os.environ.get("EMBED_THREADS", "1"))

_model = None


def is_enabled():
    if os.environ.get("SEMANTIC_ENABLED", "1") == "0":
        return False
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return False
    return True


def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL_NAME, cache_dir=CACHE_DIR, threads=THREADS)
    return _model


def _normalize(vectors):
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


# 用标题和摘要代表一篇文献
def passage_text(entry):
    return ". ".join(t for t in (entry.get('title', ""), entry.get('abstract', "")) if t)


def embed_passages(texts):
    return _normalize(list(get_model().passage_embed(texts, batch_size=BATCH_SIZE)))


def embed_query(question):
    return _normalize(next(iter(get_model().query_embed(question))))


# 余弦相似度；没有标题和摘要的文献记为 0
def similarities(query_vector, passage_vectors, texts):
    sims = passage_vectors @ query_vector
    return np.where([bool(t) for t in texts], sims, 0.0)


if __name__ == "__main__":
    # 部署时预先下载模型：python semantic.py
    get_model()
    print(f"Model {MODEL_NAME} is ready in {CACHE_DIR}")

from flask import Flask, render_template, request, send_file
from markupsafe import Markup, escape
import os
import io
import re
import time
import uuid
import numpy as np
import pandas as pd
import bibtexparser
from werkzeug.utils import secure_filename

import semantic
from scoring import (SCORED_FIELDS, clean_text, keyword_pattern, parse_field_weights,
                     parse_weights, score_dataframe)

app = Flask(__name__)
# 上传上限 10 MB
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

OUTPUT_FOLDER = 'output'
# 结果文件保留时间（秒），过期自动删除
RESULT_TTL = 60 * 60
# 网页上最多显示多少条排名
PREVIEW_LIMIT = 100
# 研究问题最长字数
QUESTION_MAX_LENGTH = 1000


# 把命中的关键词用 <mark> 包起来，其余文本照常转义
def highlight(text, patterns):
    spans = sorted(m.span() for p in patterns for m in p.finditer(text))
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, pos = [], 0
    for start, end in merged:
        out.append(escape(text[pos:start]))
        out.append(Markup("<mark>") + escape(text[start:end]) + Markup("</mark>"))
        pos = end
    out.append(escape(text[pos:]))
    return Markup("").join(out)


def short_authors(authors):
    names = [a.strip() for a in re.sub(r"[{}]", "", authors).split(" and ") if a.strip()]
    if not names:
        return ""
    return names[0] + (" et al." if len(names) > 1 else "")


def preview_rows(df, weights):
    patterns = [keyword_pattern(k) for k in weights]
    rows = []
    for rank, entry in enumerate(df.head(PREVIEW_LIMIT).to_dict('records'), start=1):
        rows.append({
            'rank': rank,
            'score': entry['score'],
            'similarity': entry.get('similarity', ""),
            'title': highlight(entry.get('title', ""), patterns),
            'abstract': highlight(entry.get('abstract', ""), patterns),
            'authors': short_authors(entry.get('author', "")),
            'year': entry.get('year', ""),
            'matches': entry['matched_keywords'],
        })
    return rows


# 每次上传生成独立的一组文件，避免不同用户互相覆盖：
#   <token>.base.csv  清洗后的原始数据（原顺序），用于重新打分
#   <token>.npy       每篇文献的向量（和 base 同顺序），做过语义打分才有
#   <token>.csv       最近一次打分排序后的结果，用于下载
def result_path(token, suffix=".csv"):
    try:
        token = uuid.UUID(token).hex
    except (ValueError, TypeError, AttributeError):
        return None
    return os.path.join(OUTPUT_FOLDER, token + suffix)


def load_csv(token, suffix=".csv"):
    path = result_path(token, suffix)
    if not path or not os.path.exists(path):
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def save_csv(df, token, suffix=".csv"):
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    df.to_csv(result_path(token, suffix), index=False)


def remove_expired_results():
    if not os.path.isdir(OUTPUT_FOLDER):
        return
    now = time.time()
    for name in os.listdir(OUTPUT_FOLDER):
        path = os.path.join(OUTPUT_FOLDER, name)
        if now - os.path.getmtime(path) > RESULT_TTL:
            try:
                os.remove(path)
            except OSError:
                pass


# 计算（或读取缓存的）文献向量，再算和研究问题的相似度
def semantic_similarities(base, token, question):
    texts = [semantic.passage_text(e) for e in base.to_dict('records')]
    path = result_path(token, ".npy")
    if os.path.exists(path):
        vectors = np.load(path)
        os.utime(path)
    else:
        vectors = semantic.embed_passages(texts)
        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        np.save(path, vectors)
    return semantic.similarities(semantic.embed_query(question), vectors, texts)


def render_index(**kwargs):
    context = dict(headers=None, weights_text="", uploaded_filename=None, token=None, error=None,
                   rows=None, total=0, matched=0, field_weights=dict(SCORED_FIELDS),
                   question="", rank_by="score", has_similarity=False,
                   semantic_enabled=semantic.is_enabled(), semantic_max=semantic.MAX_ENTRIES)
    context.update(kwargs)
    return render_template("index.html", **context)


def render_results(df, token, weights, **kwargs):
    return render_index(
        headers=list(df.columns),
        token=token,
        rows=preview_rows(df, weights),
        total=len(df),
        matched=int((df['matched_keywords'] != "").sum()),
        has_similarity='similarity' in df.columns,
        **kwargs,
    )


# 读取上传的 .bib 文件；出错时返回 (None, 错误信息)
def read_bib(bib_file):
    # 直接在内存中解析，不把上传文件留在服务器上
    try:
        bib_database = bibtexparser.loads(bib_file.read().decode("utf-8-sig"))
    except UnicodeDecodeError:
        return None, "The file is not valid UTF-8 text."
    except Exception:
        return None, "Could not parse the file as BibTeX."

    entries = bib_database.entries
    if not entries:
        return None, "No BibTeX entries were found in the file."
    df = pd.DataFrame(entries).fillna("")

    # 清洗字段
    for col in SCORED_FIELDS:
        if col in df.columns:
            df[col] = df[col].apply(clean_text)
        else:
            df[col] = ""
    return df, None


def form_state(form):
    return dict(
        weights_text=form.get("weights", ""),
        field_weights=parse_field_weights(form),
        question=form.get("question", "").strip()[:QUESTION_MAX_LENGTH],
        rank_by=form.get("rank_by", "score"),
    )


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_index()

    state = form_state(request.form)
    weights = parse_weights(state['weights_text'])
    question = state['question'] if semantic.is_enabled() else ""

    bib_file = request.files.get("bibfile")
    if bib_file and bib_file.filename:
        filename = secure_filename(bib_file.filename)
        base, error = read_bib(bib_file)
        token = uuid.uuid4().hex
    else:
        # 没有选新文件时，对上次上传的数据重新打分
        filename = request.form.get("uploaded_filename")
        token = request.form.get("token")
        base = load_csv(token, ".base.csv")
        error = None if base is not None else "Please choose a .bib file to upload."
        if base is None and token:
            error = "Your previous upload has expired. Please upload the file again."

    if error:
        return render_index(error=error, **state)
    if not weights and not question:
        return render_index(error="Enter at least one valid keyword:weight pair (weights must be integers)"
                                  + (" or a research question." if semantic.is_enabled() else "."), **state)
    if question and len(base) > semantic.MAX_ENTRIES:
        return render_index(error=f"Semantic ranking supports up to {semantic.MAX_ENTRIES} entries per file; "
                                  f"this file has {len(base)}. Clear the research question to rank by keywords only.",
                            **state)

    if bib_file and bib_file.filename:
        save_csv(base, token, ".base.csv")
    else:
        os.utime(result_path(token, ".base.csv"))
    remove_expired_results()

    df = score_dataframe(base, weights, state['field_weights'])
    if question:
        try:
            sims = semantic_similarities(base, token, question)
        except Exception:
            app.logger.exception("Semantic ranking failed")
            return render_index(error="Semantic ranking is temporarily unavailable. Clear the research question "
                                      "to rank by keywords only.", **state)
        df['similarity'] = np.round(sims.astype(float), 3)

    if 'similarity' in df.columns and state['rank_by'] == "similarity":
        order = ['similarity', 'score']
    else:
        state['rank_by'] = "score"
        order = ['score', 'similarity'] if 'similarity' in df.columns else ['score']
    df_sorted = df.sort_values(order, ascending=False, kind='stable')
    save_csv(df_sorted, token)

    return render_results(df_sorted, token, weights, uploaded_filename=filename, **state)


@app.route("/download", methods=["POST"])
def download():
    token = request.form.get("token")
    state = form_state(request.form)
    df = load_csv(token)
    if df is None:
        return render_index(error="Your results have expired. Please upload the file again.", **state)

    headers = list(df.columns)
    selected_fields = [f for f in request.form.getlist("fields") if f in headers]
    if not selected_fields:
        return render_results(
            df, token, parse_weights(state['weights_text']),
            uploaded_filename=request.form.get("uploaded_filename"),
            error="Please select at least one field to export.",
            **state,
        )

    buffer = io.BytesIO()
    df[selected_fields].to_csv(buffer, index=False)
    buffer.seek(0)
    return send_file(buffer, mimetype="text/csv", as_attachment=True, download_name="filtered_output.csv")


@app.errorhandler(413)
def file_too_large(e):
    return render_index(error="The file is too large (limit: 10 MB)."), 413


if __name__ == "__main__":
    # 调试模式用环境变量开启：FLASK_DEBUG=1 python app.py
    app.run()

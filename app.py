from flask import Flask, render_template, request, send_file
from markupsafe import Markup, escape
import os
import io
import json
import re
import time
import uuid
import numpy as np
import pandas as pd
import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter
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
# 导出格式：(扩展名, MIME 类型)
EXPORT_FORMATS = {
    'csv': ("csv", "text/csv"),
    'xlsx': ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    'bib': ("bib", "application/x-bibtex"),
}
EXPORT_SCOPES = ('all', 'top', 'min')


# 导出字段在页面上显示的名字。列名本身来自 .bib 文件的字段名，
# ENTRYTYPE 和 ID 是 bibtexparser 给条目类型（@article 等）和引用键起的名字；
# score、matched_keywords、similarity 是打分时加的列。导出的 CSV 表头也用这些名字。
FIELD_LABELS = {
    'title': "Title", 'author': "Authors", 'year': "Year", 'journal': "Journal",
    'booktitle': "Book or proceedings title", 'abstract': "Abstract", 'keywords': "Keywords",
    'doi': "DOI", 'url': "URL", 'isbn': "ISBN", 'issn': "ISSN", 'volume': "Volume",
    'number': "Issue", 'pages': "Pages", 'publisher': "Publisher", 'editor': "Editors",
    'month': "Month", 'note': "Note", 'language': "Language",
    'ENTRYTYPE': "Entry type", 'ID': "Citation key",
    'score': "Keyword score", 'matched_keywords': "Matched keywords", 'similarity': "Similarity",
}
# 常用字段排在前面，其余按原顺序跟在后面
FIELD_ORDER = ['title', 'author', 'year', 'journal', 'booktitle', 'score', 'similarity',
               'matched_keywords', 'abstract', 'keywords', 'doi', 'url', 'ID', 'ENTRYTYPE']


def field_label(name):
    if name in FIELD_LABELS:
        return FIELD_LABELS[name]
    # 其他字段（如 web_of_science_categories）：下划线换成空格，首字母大写
    words = re.sub(r"[_-]+", " ", name).strip()
    return words[:1].upper() + words[1:] if words else name


def export_fields(headers):
    rank = {name: i for i, name in enumerate(FIELD_ORDER)}
    ordered = sorted(headers, key=lambda h: (rank.get(h, len(FIELD_ORDER)), headers.index(h)))
    return [(name, field_label(name)) for name in ordered]


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
#   <token>.raw.json  上传时解析出的原始条目（未清洗，原顺序），用于导出 .bib
#   <token>.base.csv  清洗后的原始数据（原顺序），用于重新打分
#   <token>.npy       每篇文献的向量（和 base 同顺序），做过语义打分才有
#   <token>.csv       最近一次打分排序后的结果，用于下载；_row 列记录它在原文件里的位置
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
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if '_row' in df.columns:
        df = df.set_index('_row')
    return df


def save_csv(df, token, suffix=".csv", keep_row=False):
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    df.to_csv(result_path(token, suffix), index=keep_row, index_label='_row' if keep_row else None)


def save_raw_entries(entries, token):
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    with open(result_path(token, ".raw.json"), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)


def load_raw_entries(token):
    path = result_path(token, ".raw.json")
    if not path or not os.path.exists(path):
        return None
    os.utime(path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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
                   default_field_weights=SCORED_FIELDS, export_fields=[],
                   question="", rank_by="score", has_similarity=False,
                   export_format="csv", export_scope="all", top_n="", min_value="", bib_note=False,
                   checked_fields=[],
                   semantic_enabled=semantic.is_enabled(), semantic_max=semantic.MAX_ENTRIES)
    context.update(kwargs)
    return render_template("index.html", **context)


def render_results(df, token, weights, **kwargs):
    return render_index(
        headers=list(df.columns),
        export_fields=export_fields(list(df.columns)),
        token=token,
        rows=preview_rows(df, weights),
        total=len(df),
        matched=int((df['matched_keywords'] != "").sum()),
        has_similarity='similarity' in df.columns,
        **kwargs,
    )


# 读取上传的 .bib 文件；返回 (清洗后的表, 原始条目, 错误信息)
def read_bib(bib_file):
    # 直接在内存中解析，不把上传文件留在服务器上
    try:
        bib_database = bibtexparser.loads(bib_file.read().decode("utf-8-sig"))
    except UnicodeDecodeError:
        return None, None, "The file is not valid UTF-8 text."
    except Exception:
        return None, None, "Could not parse the file as BibTeX."

    entries = bib_database.entries
    if not entries:
        return None, None, "No BibTeX entries were found in the file."
    df = pd.DataFrame(entries).fillna("")

    # 清洗字段
    for col in SCORED_FIELDS:
        if col in df.columns:
            df[col] = df[col].apply(clean_text)
        else:
            df[col] = ""
    return df, entries, None


def form_state(form):
    return dict(
        weights_text=form.get("weights", ""),
        field_weights=parse_field_weights(form),
        question=form.get("question", "").strip()[:QUESTION_MAX_LENGTH],
        rank_by=form.get("rank_by", "score"),
        # 导出选项也放在这里，重新排序后保持不变
        export_format=form.get("export_format") if form.get("export_format") in EXPORT_FORMATS else "csv",
        export_scope=form.get("export_scope") if form.get("export_scope") in EXPORT_SCOPES else "all",
        top_n=form.get("top_n", "").strip(),
        min_value=form.get("min_value", "").strip(),
        bib_note=form.get("bib_note") == "1",
        checked_fields=form.getlist("fields"),
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
        base, raw_entries, error = read_bib(bib_file)
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
        save_raw_entries(raw_entries, token)
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
    save_csv(df_sorted, token, keep_row=True)

    return render_results(df_sorted, token, weights, uploaded_filename=filename, **state)


# 导出时按哪一列筛选“不低于某个值”：跟当前的排序方式一致
def ranking_metric(df, rank_by):
    return 'similarity' if rank_by == "similarity" and 'similarity' in df.columns else 'score'


# 按“全部 / 前 N 篇 / 不低于某个值”筛选；出错时返回 (None, 错误信息)
def select_entries(df, state):
    if state['export_scope'] == "top":
        try:
            n = int(state['top_n'])
        except ValueError:
            n = 0
        if n < 1:
            return None, "Enter how many top entries to export (a whole number of 1 or more)."
        return df.head(n), None
    if state['export_scope'] == "min":
        metric = ranking_metric(df, state['rank_by'])
        try:
            threshold = float(state['min_value'])
        except ValueError:
            return None, f"Enter the minimum {field_label(metric).lower()} to export."
        return df[pd.to_numeric(df[metric], errors='coerce') >= threshold], None
    return df, None


def csv_bytes(export):
    buffer = io.BytesIO()
    # utf-8-sig 让 Excel 正确显示 × 和带重音的作者名
    export.to_csv(buffer, index=False, encoding="utf-8-sig")
    return buffer.getvalue()


def excel_bytes(export):
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.utils import get_column_letter

    # Excel 不接受部分控制字符；分数类的列转成数字，方便在 Excel 里排序和筛选
    export = export.apply(lambda col: col.str.replace(ILLEGAL_CHARACTERS_RE, "", regex=True))
    for name in ("Keyword score", "Similarity"):
        if name in export.columns:
            export[name] = pd.to_numeric(export[name], errors='coerce')

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export.to_excel(writer, index=False, sheet_name="Ranking")
        sheet = writer.sheets["Ranking"]
        sheet.freeze_panes = "A2"
        for i, name in enumerate(export.columns, start=1):
            longest = max([len(str(name))] + [len(str(v)) for v in export[name].head(200)])
            sheet.column_dimensions[get_column_letter(i)].width = min(longest + 2, 60)
    return buffer.getvalue()


# 用上传时的原始条目生成 .bib，保留原文（LaTeX、花括号等），按排名顺序输出
def bib_bytes(df, raw_entries, add_note):
    entries = []
    for rank, (row, record) in enumerate(zip(df.index, df.to_dict('records')), start=1):
        entry = dict(raw_entries[int(row)])
        if add_note:
            text = f"BibScorer rank {rank}, keyword score {record['score']}"
            if record.get('similarity', "") != "":
                text += f", similarity {record['similarity']}"
            entry['note'] = f"{entry['note']}; {text}" if entry.get('note') else text
        entries.append(entry)
    database = BibDatabase()
    database.entries = entries
    writer = BibTexWriter()
    writer.order_entries_by = None
    # 常用字段排在前面，其余按字母顺序
    writer.display_order = ['title', 'author', 'year', 'journal', 'booktitle']
    writer.indent = "  "
    return writer.write(database).encode("utf-8")


@app.route("/download", methods=["POST"])
def download():
    token = request.form.get("token")
    state = form_state(request.form)
    df = load_csv(token)
    if df is None:
        return render_index(error="Your results have expired. Please upload the file again.", **state)

    def show_error(message):
        return render_results(
            df, token, parse_weights(state['weights_text']),
            uploaded_filename=request.form.get("uploaded_filename"),
            error=message,
            **state,
        )

    fmt = state['export_format']
    selected_fields = [f for f in request.form.getlist("fields") if f in df.columns]
    if fmt != "bib" and not selected_fields:
        return show_error("Please select at least one field to export.")

    selected, error = select_entries(df, state)
    if error:
        return show_error(error)
    if selected.empty:
        return show_error("No entries match that range. Try a lower minimum.")

    if fmt == "bib":
        raw_entries = load_raw_entries(token)
        if raw_entries is None:
            return show_error("The original entries have expired. Please upload the file again.")
        data = bib_bytes(selected, raw_entries, state['bib_note'])
    else:
        export = selected[selected_fields].rename(columns=field_label)
        data = csv_bytes(export) if fmt == "csv" else excel_bytes(export)

    extension, mimetype = EXPORT_FORMATS[fmt]
    return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=True,
                     download_name=f"bibscore_ranking.{extension}")


@app.errorhandler(413)
def file_too_large(e):
    return render_index(error="The file is too large (limit: 10 MB)."), 413


if __name__ == "__main__":
    # 调试模式用环境变量开启：FLASK_DEBUG=1 python app.py
    app.run()

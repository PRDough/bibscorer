from flask import Flask, Response, g, render_template, request, send_file
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
from importers import UnsupportedFile, looks_like_xml, merge_entries, parse_ebsco_xml
from scoring import (SCORED_FIELDS, clean_text, keyword_pattern, parse_field_weights,
                     parse_weights, score_dataframe)

app = Flask(__name__)
# 上传上限 10 MB
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

# 已知 AI 爬虫和 AI 助手的 User-Agent 标识（不区分大小写，包含即拦截）。
# 只能拦住如实声明身份的程序；伪装成普通浏览器的请求识别不了。
BLOCKED_AI_AGENTS = (
    # Anthropic
    "ClaudeBot", "Claude-SearchBot", "Claude-User", "Claude-Web", "anthropic-ai",
    # OpenAI
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "OAI-AdsBot",
    # Perplexity
    "PerplexityBot", "Perplexity-User",
    # 其他 AI / 数据采集
    "Bytespider", "CCBot", "Meta-ExternalAgent", "Meta-ExternalFetcher", "cohere-ai",
    # Google-Extended 和 Applebot-Extended 不是真实的 User-Agent，只在 robots.txt 里起作用
    "Amazonbot", "Applebot-Extended", "Google-Extended", "Diffbot", "ImagesiftBot",
    "Omgilibot", "Timpibot", "YouBot", "MistralAI-User",
)
_BLOCKED_AGENTS_LOWER = tuple(a.lower() for a in BLOCKED_AI_AGENTS)

OUTPUT_FOLDER = 'output'
# 结果文件保留时间（秒），过期自动删除
RESULT_TTL = 60 * 60
# 网页上最多显示多少条排名
PREVIEW_LIMIT = 100
# 访问统计：ANALYTICS_SRC（脚本地址）和 ANALYTICS_ID 都设置了才加载，且只在正式域名上发送数据
ANALYTICS_DOMAIN = "bibscorer.prdough.com"
# 一次最多上传几个文件
MAX_FILES = 20
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
    'accessionnumber': "EBSCO accession number", 'database': "Source database",
    'source_file': "Source file",
    'score': "Keyword score", 'matched_keywords': "Matched keywords", 'similarity': "Similarity",
}
# 常用字段排在前面，其余按原顺序跟在后面
FIELD_ORDER = ['title', 'author', 'year', 'month', 'journal', 'booktitle', 'score', 'similarity',
               'matched_keywords', 'abstract', 'keywords', 'doi', 'url', 'publisher', 'ID', 'ENTRYTYPE']
# 排在最后的字段
FIELD_ORDER_LAST = ['source_file', 'database', 'accessionnumber']


def field_label(name):
    if name in FIELD_LABELS:
        return FIELD_LABELS[name]
    # 其他字段（如 web_of_science_categories）：下划线换成空格，首字母大写
    words = re.sub(r"[_-]+", " ", name).strip()
    return words[:1].upper() + words[1:] if words else name


# 导出选项：去掉所有条目都为空的列（比如 EBSCO XML 没有关键词），再按常用程度排序
def export_fields(df):
    headers = [h for h in df.columns if (df[h].astype(str) != "").any()]
    rank = {name: i for i, name in enumerate(FIELD_ORDER)}
    rank.update({name: len(FIELD_ORDER) + 1 + i for i, name in enumerate(FIELD_ORDER_LAST)})
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


@app.before_request
def block_ai_agents():
    # robots.txt 对所有程序开放，好让遵守规则的爬虫读到“禁止抓取”
    if request.path == "/robots.txt":
        return None
    user_agent = request.headers.get("User-Agent", "").lower()
    if any(agent in user_agent for agent in _BLOCKED_AGENTS_LOWER):
        return Response("Forbidden\n", status=403, mimetype="text/plain")
    return None


@app.route("/robots.txt")
def robots_txt():
    lines = []
    for agent in BLOCKED_AI_AGENTS:
        lines += [f"User-agent: {agent}", "Disallow: /", ""]
    lines += ["User-agent: *", "Allow: /", ""]
    return Response("\n".join(lines), mimetype="text/plain")


@app.context_processor
def analytics():
    src = os.environ.get("ANALYTICS_SRC", "").strip()
    site_id = os.environ.get("ANALYTICS_ID", "").strip()
    try:
        site_id = str(uuid.UUID(site_id))
    except ValueError:
        site_id = ""
    if not site_id or not src.startswith("https://"):
        return dict(analytics=None)
    return dict(analytics={"src": src, "id": site_id, "domain": ANALYTICS_DOMAIN})


# 本次请求要在页面上记录的评分结果事件；只有提交评分（POST /）的响应才有。
# score_started 在浏览器提交表单时发送，见 index.html。
def score_event(context):
    if request.endpoint != "index" or request.method != "POST":
        return None
    if context['error']:
        name, data = "score_failed", {"reason": g.get("score_failure", "other")}
    elif context['rows']:
        name, data = "score_completed", {
            "entries": context['total'],
            "semantic": "yes" if context['has_similarity'] else "no",
            "ranked_by": context['rank_by'],
        }
    else:
        return None
    # 每次响应一个唯一 id，浏览器据此避免同一次结果被重复记录（比如从缓存恢复页面）
    return {"id": uuid.uuid4().hex, "name": name, "data": data}


def score_failed(reason, message, state):
    g.score_failure = reason
    return render_index(error=message, **state)


def render_index(**kwargs):
    context = dict(headers=None, weights_text="", uploaded_filename=None, token=None, error=None,
                   rows=None, total=0, matched=0, field_weights=dict(SCORED_FIELDS),
                   default_field_weights=SCORED_FIELDS, export_fields=[],
                   question="", rank_by="score", has_similarity=False,
                   export_format="csv", export_scope="all", top_n="", min_value="", bib_note=False,
                   checked_fields=[], source=None, upload_note="",
                   semantic_enabled=semantic.is_enabled(), semantic_max=semantic.MAX_ENTRIES)
    context.update(kwargs)
    context['score_event'] = score_event(context)
    return render_template("index.html", **context)


def render_results(df, token, weights, **kwargs):
    return render_index(
        headers=list(df.columns),
        export_fields=export_fields(df),
        token=token,
        rows=preview_rows(df, weights),
        total=len(df),
        matched=int((df['matched_keywords'] != "").sum()),
        has_similarity='similarity' in df.columns,
        **kwargs,
    )


# 读取一个上传的文件（BibTeX 或 EBSCO XML，按内容自动识别）；
# 返回 (原始条目, 来源格式, 错误信息)
def parse_upload(upload):
    # 直接在内存中解析，不把上传文件留在服务器上
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, None, "The file is not valid UTF-8 text."

    if looks_like_xml(text):
        try:
            entries = parse_ebsco_xml(text)
        except UnsupportedFile as e:
            return None, None, str(e)
        source = "ebsco"
    else:
        try:
            entries = bibtexparser.loads(text).entries
        except Exception:
            return None, None, "Could not parse the file as BibTeX."
        source = "bibtex"

    if not entries:
        return None, None, "No references were found in the file."
    return entries, source, None


# 读取所有上传的文件，合并去重；返回 (清洗后的表, 原始条目, 来源格式, 上传说明, 错误信息)
def read_uploads(uploads):
    files, sources, counts = [], set(), []
    for upload in uploads:
        name = secure_filename(upload.filename) or "file"
        entries, source, error = parse_upload(upload)
        if error:
            # 只传了一个文件时不用在错误前面加文件名
            return None, None, None, None, error if len(uploads) == 1 else f"{name}: {error}"
        files.append((name, entries))
        sources.add(source)
        counts.append(f"{name}: {len(entries)}")

    entries, source_files, removed = merge_entries(files)
    df = pd.DataFrame(entries).fillna("")
    df['source_file'] = source_files

    # 清洗字段
    for col in SCORED_FIELDS:
        if col in df.columns:
            df[col] = df[col].apply(clean_text)
        else:
            df[col] = ""

    note = ""
    if len(uploads) > 1 or removed:
        parts = counts if len(uploads) > 1 else []
        if removed:
            parts.append(f"{removed} duplicate{'s' if removed != 1 else ''} removed")
        note = " · ".join(parts)
    source = sources.pop() if len(sources) == 1 else "mixed"
    return df, entries, source, note, None


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

    uploads = [f for f in request.files.getlist("bibfile") if f and f.filename]
    if len(uploads) > MAX_FILES:
        return score_failed("too_many_files", f"Upload at most {MAX_FILES} files at a time.", state)
    if uploads:
        filename = ", ".join(secure_filename(f.filename) or "file" for f in uploads)
        base, raw_entries, source, upload_note, error = read_uploads(uploads)
        token = uuid.uuid4().hex
    else:
        # 没有选新文件时，对上次上传的数据重新打分
        filename = request.form.get("uploaded_filename")
        source = request.form.get("source")
        upload_note = request.form.get("upload_note", "")
        token = request.form.get("token")
        base = load_csv(token, ".base.csv")
        error = None if base is not None else "Please choose a .bib file to upload."
        if base is None and token:
            error = "Your previous upload has expired. Please upload the file again."

    if error:
        reason = "invalid_file" if uploads else ("expired" if token else "no_file")
        return score_failed(reason, error, state)
    if not weights and not question:
        return score_failed("no_keywords", "Enter at least one valid keyword:weight pair (weights must be integers)"
                            + (" or a research question." if semantic.is_enabled() else "."), state)
    if question and len(base) > semantic.MAX_ENTRIES:
        return score_failed("too_many_entries",
                            f"Semantic ranking supports up to {semantic.MAX_ENTRIES} entries per file; "
                            f"this file has {len(base)}. Clear the research question to rank by keywords only.",
                            state)

    if uploads:
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
            return score_failed("semantic_unavailable", "Semantic ranking is temporarily unavailable. "
                                "Clear the research question to rank by keywords only.", state)
        df['similarity'] = np.round(sims.astype(float), 3)

    if 'similarity' in df.columns and state['rank_by'] == "similarity":
        order = ['similarity', 'score']
    else:
        state['rank_by'] = "score"
        order = ['score', 'similarity'] if 'similarity' in df.columns else ['score']
    df_sorted = df.sort_values(order, ascending=False, kind='stable')
    save_csv(df_sorted, token, keep_row=True)

    return render_results(df_sorted, token, weights, uploaded_filename=filename, source=source,
                          upload_note=upload_note, **state)


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
            source=request.form.get("source"),
            upload_note=request.form.get("upload_note", ""),
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
    g.score_failure = "too_large"
    return render_index(error="The file is too large (limit: 10 MB)."), 413


if __name__ == "__main__":
    # 调试模式用环境变量开启：FLASK_DEBUG=1 python app.py
    app.run()

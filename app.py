from flask import Flask, render_template, request, send_file
from markupsafe import Markup, escape
import os
import io
import re
import time
import uuid
import pandas as pd
import bibtexparser
from werkzeug.utils import secure_filename

app = Flask(__name__)
# 上传上限 10 MB
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

OUTPUT_FOLDER = 'output'
# 打分结果保留时间（秒），过期自动删除
RESULT_TTL = 60 * 60
# 参与打分的字段及默认系数：标题最重要，摘要最轻
SCORED_FIELDS = {'title': 3, 'abstract': 1, 'keywords': 2}
# 网页上最多显示多少条排名
PREVIEW_LIMIT = 100
# 打分时生成的列，重新打分前要先去掉
SCORE_COLUMNS = ['score', 'matched_keywords']
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# 清洗函数
def clean_text(text):
    if pd.isna(text):
        return ""
    # --- 处理被破坏的 LaTeX 表达式 ---
    text = re.sub(r"CO\$_[^0-9]*?(\d)\$", r"CO\1", text, flags=re.IGNORECASE)
    text = re.sub(r"H\$_[^0-9]*?(\d)\$", r"H\1", text, flags=re.IGNORECASE)
    text = re.sub(r"([A-Z])\$_[^0-9]*?(\d)\$", r"\1\2", text, flags=re.IGNORECASE)

    # 删除所有残留的 $...$ latex 公式
    text = re.sub(r"\$[^$]*\$", "", text)

    # --- 其他通用符号清洗 ---
    text = re.sub(r"\$_ ?(?:extrm|textrm)?(\d)\$", r"\1", text)
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\\%", "%", text)
    text = re.sub(r"\\n", " ", text)
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\\t", " ", text)
    text = re.sub(r"\\&", "&", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"CO\s*2", "CO2", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# 把关键词编译成整词匹配的正则；结尾带 * 表示前缀匹配（steel* 匹配 steelmaking）
def keyword_pattern(keyword):
    prefix = keyword.endswith('*')
    words = keyword.rstrip('*').split()
    body = r"\s+".join(re.escape(w) for w in words)
    tail = r"\w*" if prefix else r"(?!\w)"
    return re.compile(r"(?<!\w)" + body + tail, re.IGNORECASE)


# 解析 "关键词:权重"，按最后一个冒号切分，关键词本身可以带冒号
def parse_weights(raw_input):
    weights = {}
    for line in raw_input.splitlines():
        if ':' not in line:
            continue
        k, v = line.rsplit(':', 1)
        k = k.strip()
        if not k.rstrip('*').strip():
            continue
        try:
            weights[k] = int(v.strip())
        except ValueError:
            continue
    return weights


# 读取页面上的字段系数，非法或负数时用默认值
def parse_field_weights(form):
    field_weights = {}
    for field, default in SCORED_FIELDS.items():
        try:
            value = float(form.get(f"fw_{field}", default))
        except ValueError:
            value = default
        field_weights[field] = value if value >= 0 else default
    return field_weights


# 打分函数：返回总分和每个关键词的命中次数
def score_entry(entry, patterns, field_weights):
    score = 0
    hits = {}
    for field, factor in field_weights.items():
        text = entry.get(field, "")
        for keyword, (pattern, weight) in patterns.items():
            count = len(pattern.findall(text))
            if count:
                score += count * weight * factor
                hits[keyword] = hits.get(keyword, 0) + count
    return score, hits


def score_dataframe(df, weights, field_weights):
    df = df.drop(columns=[c for c in SCORE_COLUMNS if c in df.columns])
    patterns = {k: (keyword_pattern(k), w) for k, w in weights.items()}
    results = [score_entry(row, patterns, field_weights) for row in df.to_dict('records')]
    scores = pd.Series([round(s, 2) for s, _ in results], index=df.index)
    if (scores % 1 == 0).all():
        scores = scores.astype(int)
    df['score'] = scores
    df['matched_keywords'] = [
        "; ".join(f"{k} ×{n}" for k, n in sorted(hits.items(), key=lambda kv: -kv[1]))
        for _, hits in results
    ]
    return df.sort_values('score', ascending=False, kind='stable')


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
            'title': highlight(entry.get('title', ""), patterns),
            'abstract': highlight(entry.get('abstract', ""), patterns),
            'authors': short_authors(entry.get('author', "")),
            'year': entry.get('year', ""),
            'matches': entry['matched_keywords'],
        })
    return rows


# 每次上传生成独立的结果文件，避免不同用户互相覆盖
def result_path(token):
    try:
        token = uuid.UUID(token).hex
    except (ValueError, TypeError, AttributeError):
        return None
    return os.path.join(OUTPUT_FOLDER, f"{token}.csv")


def load_result(token):
    path = result_path(token)
    if not path or not os.path.exists(path):
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def remove_expired_results():
    now = time.time()
    for name in os.listdir(OUTPUT_FOLDER):
        path = os.path.join(OUTPUT_FOLDER, name)
        if name.endswith(".csv") and now - os.path.getmtime(path) > RESULT_TTL:
            try:
                os.remove(path)
            except OSError:
                pass


def render_index(**kwargs):
    context = dict(headers=None, weights_text="", uploaded_filename=None, token=None, error=None,
                   rows=None, total=0, matched=0, field_weights=dict(SCORED_FIELDS))
    context.update(kwargs)
    return render_template("index.html", **context)


def render_results(df, token, weights, **kwargs):
    return render_index(
        headers=list(df.columns),
        token=token,
        rows=preview_rows(df, weights),
        total=len(df),
        matched=int((df['matched_keywords'] != "").sum()),
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


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_index()

    bib_file = request.files.get("bibfile")
    raw_input = request.form.get("weights", "")
    weights = parse_weights(raw_input)
    field_weights = parse_field_weights(request.form)
    form_state = dict(weights_text=raw_input, field_weights=field_weights)

    if bib_file and bib_file.filename:
        filename = secure_filename(bib_file.filename)
        df, error = read_bib(bib_file)
        token = uuid.uuid4().hex
    else:
        # 没有选新文件时，对上次上传的数据重新打分
        filename = request.form.get("uploaded_filename")
        token = request.form.get("token")
        df = load_result(token)
        error = None if df is not None else "Please choose a .bib file to upload."
        if df is None and token:
            error = "Your previous upload has expired. Please upload the file again."

    if error:
        return render_index(error=error, **form_state)
    if not weights:
        return render_index(error="No valid keyword:weight pairs found (weights must be integers).", **form_state)

    df_sorted = score_dataframe(df, weights, field_weights)
    remove_expired_results()
    df_sorted.to_csv(result_path(token), index=False)

    return render_results(df_sorted, token, weights, uploaded_filename=filename, **form_state)


@app.route("/download", methods=["POST"])
def download():
    token = request.form.get("token")
    raw_input = request.form.get("weights", "")
    form_state = dict(weights_text=raw_input, field_weights=parse_field_weights(request.form))
    df = load_result(token)
    if df is None:
        return render_index(error="Your results have expired. Please upload the file again.", **form_state)

    headers = list(df.columns)
    selected_fields = [f for f in request.form.getlist("fields") if f in headers]
    if not selected_fields:
        return render_results(
            df, token, parse_weights(raw_input),
            uploaded_filename=request.form.get("uploaded_filename"),
            error="Please select at least one field to export.",
            **form_state,
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

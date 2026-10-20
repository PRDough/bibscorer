from flask import Flask, render_template, request, send_file
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


# 打分函数
def score_entry(keywords, weights):
    if pd.isna(keywords):
        return 0
    keywords = keywords.lower()
    score = 0
    for keyword, weight in weights.items():
        score += keywords.count(keyword.lower()) * weight
    return score


# 解析 "关键词:权重"，按最后一个冒号切分，关键词本身可以带冒号
def parse_weights(raw_input):
    weights = {}
    for line in raw_input.splitlines():
        if ':' not in line:
            continue
        k, v = line.rsplit(':', 1)
        try:
            weights[k.strip()] = int(v.strip())
        except ValueError:
            continue
    weights.pop("", None)
    return weights


# 每次上传生成独立的结果文件，避免不同用户互相覆盖
def result_path(token):
    try:
        token = uuid.UUID(token).hex
    except (ValueError, TypeError, AttributeError):
        return None
    return os.path.join(OUTPUT_FOLDER, f"{token}.csv")


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
    context = dict(headers=None, weights_text="", uploaded_filename=None, token=None, error=None)
    context.update(kwargs)
    return render_template("index.html", **context)


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_index()

    bib_file = request.files.get("bibfile")
    raw_input = request.form.get("weights", "")
    weights = parse_weights(raw_input)

    if not bib_file or not bib_file.filename:
        return render_index(weights_text=raw_input, error="Please choose a .bib file to upload.")
    if not weights:
        return render_index(weights_text=raw_input, error="No valid keyword:weight pairs found (weights must be integers).")

    filename = secure_filename(bib_file.filename)

    # 直接在内存中解析，不把上传文件留在服务器上
    try:
        bib_database = bibtexparser.loads(bib_file.read().decode("utf-8-sig"))
    except UnicodeDecodeError:
        return render_index(weights_text=raw_input, error="The file is not valid UTF-8 text.")
    except Exception:
        return render_index(weights_text=raw_input, error="Could not parse the file as BibTeX.")

    entries = bib_database.entries
    if not entries:
        return render_index(weights_text=raw_input, error="No BibTeX entries were found in the file.")
    df = pd.DataFrame(entries)

    # 清洗字段
    for col in ['title', 'abstract', 'keywords']:
        if col in df.columns:
            df[col] = df[col].apply(clean_text)
        else:
            df[col] = ""

    # 打分：title、abstract、keywords 三个字段合并计数
    scored_text = df['title'] + " " + df['abstract'] + " " + df['keywords']
    df['score'] = scored_text.apply(lambda x: score_entry(x, weights))

    # 排序保存
    df_sorted = df.sort_values('score', ascending=False)
    remove_expired_results()
    token = uuid.uuid4().hex
    df_sorted.to_csv(result_path(token), index=False)

    return render_index(
        headers=list(df_sorted.columns),
        weights_text=raw_input,
        uploaded_filename=filename,
        token=token,
    )


@app.route("/download", methods=["POST"])
def download():
    path = result_path(request.form.get("token"))
    if not path or not os.path.exists(path):
        return render_index(
            weights_text=request.form.get("weights", ""),
            error="Your results have expired. Please upload the file again.",
        )

    df = pd.read_csv(path)
    headers = list(df.columns)
    selected_fields = [f for f in request.form.getlist("fields") if f in headers]
    if not selected_fields:
        return render_index(
            headers=headers,
            weights_text=request.form.get("weights", ""),
            uploaded_filename=request.form.get("uploaded_filename"),
            token=request.form.get("token"),
            error="Please select at least one field to export.",
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

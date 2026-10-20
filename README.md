# BibScorer

[English](#english) | [中文](#中文)

---

## English

BibScorer is a small Flask web app that ranks your references by keywords you choose and by meaning. Upload a BibTeX file or an EBSCO XML export, give each keyword a weight, and download the ranked list as CSV, Excel or BibTeX.

**Try it online: <https://bibscorer.onrender.com>**

> The site runs on Render's free plan, which puts it to sleep after about 15 minutes with no visitors. If nobody has used it recently, the first page load can take up to a minute while the server wakes up. Please wait and don't keep refreshing. After that it responds normally. If the page still doesn't load after a few minutes, refresh once, or [run it locally](#run-locally).

### How it works

1. **Upload** one or more reference files (UTF-8, up to 10 MB in total, at most 20 files). The format of each file is detected automatically:
   - **BibTeX (.bib)**, from Zotero, Mendeley, Google Scholar and similar tools.
   - **EBSCO XML**, from EBSCO's *Download → XML*. Titles, authors, dates, abstracts, DOIs, links and publishers are converted to BibTeX fields, and a citation key such as `han2026double` is generated for each record. EBSCO's XML has no keywords or journal names, so only the title and abstract are scored.

   When you upload several files, their entries are combined and **duplicates are removed automatically**. Two entries count as duplicates if they have the same DOI, or, when there's no DOI, the same title (ignoring case, punctuation and braces). For very short titles, the first author must match too. The most complete version of each duplicate is kept. The page shows how many entries came from each file and how many duplicates were removed, and a `Source file` field records where each entry came from. If two different entries share a citation key, such as `smith2021` in two files, the second one becomes `smith2021a`.
2. **Define weights**, one `keyword:weight` pair per line. Weights are integers:
   ```
   just transition:3
   steel:2
   CO2: capture:1
   ```
   The text after the last colon is the weight, so keywords may contain colons. Negative weights push matching entries down.
   - Keywords match **whole words**, case-insensitive: `art` does not match "partial".
   - Add `*` to match a **prefix**: `steel*` matches "steel", "steels" and "steelmaking".
3. **Set field multipliers** (default: title ×3, keywords ×2, abstract ×1). A match in the title then counts three times as much as one in the abstract. Set a field to 0 to ignore it.
4. **Scoring.** Each match adds *keyword weight × field multiplier*. Entries are sorted by total score, highest first.
5. **Review the ranking** on the page. The top 100 entries are shown with their score, matched keywords and highlighted titles. Click a title to see its abstract. To try different weights, edit them and click **Analyze and Rank** again; there's no need to re-upload.
6. **Export** in three formats:
   - **CSV** or **Excel (.xlsx)**: pick the fields you want. Column headers use readable names, such as `Citation key` for the BibTeX key. The CSV is UTF-8 with a BOM, so Excel shows accents and symbols correctly. In the Excel file, scores are stored as numbers and the header row is frozen.
   - **BibTeX (.bib)**: re-import the ranked references into Zotero, EndNote or Mendeley. The file contains the complete original entries exactly as uploaded, including LaTeX and braces. Optionally, the rank and score can be added to each entry's `note` field.

   Export all entries, only the **top N**, or only those with a **score at least X**. When ranking by similarity, the minimum applies to similarity instead.

Before scoring, BibScorer strips common BibTeX/LaTeX markup from those three fields, such as braces, `$...$` math and escaped characters. For example, `CO$_2$` becomes `CO2`.

### Semantic ranking (optional)

Keywords miss papers that describe the same idea in different words. You can also write a **research question** in one sentence, e.g. *"How do steel-producing regions protect workers during decarbonization?"*. BibScorer then embeds each entry's title and abstract with a small local model ([`BAAI/bge-small-en-v1.5`](https://huggingface.co/BAAI/bge-small-en-v1.5) via [fastembed](https://github.com/qdrant/fastembed)) and adds a `similarity` column, the cosine similarity from 0 to 1. Choose **Rank by → Similarity to research question** to sort by it. Keyword weights become optional when you use a question.

- The model runs on the server itself. No API key is needed and your references aren't sent to any third party.
- It works best in English.
- The first semantic run on a file takes the longest, because every abstract has to be embedded. Later re-ranks of the same file reuse those vectors.
- The public site allows up to 300 entries per file for semantic ranking. Run it locally to handle larger files (default limit: 1000).

### Privacy

- The uploaded `.bib` file itself is parsed in memory and not saved.
- Each upload's data (the parsed entries and the cleaned text) and results are saved under a random ID in `output/`, so users can't see each other's data. They are deleted after one hour of inactivity.

### Run locally

Requires Python 3.10+.

```bash
git clone https://github.com/PRDough/bibscorer.git
cd bibscorer
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>. To turn on debug mode, run `FLASK_DEBUG=1 python app.py`. The embedding model (about 70 MB) is downloaded into `.models/` the first time you use semantic ranking.

### Configuration

All settings are optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `SEMANTIC_ENABLED` | `1` | Set to `0` to turn off semantic ranking and hide the research-question box |
| `SEMANTIC_MAX_ENTRIES` | `1000` | Maximum entries per file for semantic ranking |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Any text model [supported by fastembed](https://qdrant.github.io/fastembed/examples/Supported_Models/) |
| `EMBED_CACHE_DIR` | `.models/` | Where the model is stored |
| `EMBED_BATCH_SIZE` / `EMBED_THREADS` | `8` / `1` | Larger values are faster but use more memory |

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests use a fake embedding model, so they run in seconds without downloading anything. To also test the real model, run `RUN_SLOW=1 pytest`. GitHub Actions runs the tests on every push and pull request.

### Deploy to Render

The repository includes a `render.yaml` blueprint. In Render, choose **New → Blueprint** and connect this repository. The build installs the dependencies and pre-downloads the embedding model. The app then runs with `gunicorn` on Python 3.12.

- With small batches, the app peaks at about 300 MB of memory, which fits the free plan's 512 MB.
- The free plan's CPU is slow, so semantic ranking a few hundred entries can take a minute or more. That's why the request timeout is raised to 10 minutes and the entry limit is 300.
- The disk is wiped on restart, so any stored results are lost and users have to upload again.

### Project structure

```
app.py                 Flask app: upload, ranking page and CSV download
scoring.py             Keyword scoring: text cleaning, weight parsing, whole-word matching
importers.py           EBSCO XML import (parsed safely with defusedxml)
semantic.py            Semantic ranking with a local embedding model
templates/layout.html  Base layout
templates/index.html   Upload, weights, ranking and field-selection page
static/style.css       Black-and-white stylesheet (Optima + Baskerville)
tests/                 pytest suite
requirements.txt       Python dependencies (requirements-dev.txt adds pytest)
render.yaml            Render deployment config
```

### License

[MIT](LICENSE)

---

## 中文

BibScorer 是一个基于 Flask 的小型网页工具，按你自定义的关键词和权重、以及与研究问题的语义相似度，给文献打分排序。支持上传 BibTeX 文件或 EBSCO 导出的 XML，结果可以导出为 CSV、Excel 或 BibTeX。

**在线使用：<https://bibscorer.onrender.com>**

> 网站部署在 Render 免费版上，大约 15 分钟没人访问就会自动休眠。如果最近没人用过，第一次打开可能要等 1 分钟左右，服务器正在唤醒。请耐心等待，不要反复刷新。唤醒后就能正常使用。如果等了几分钟还打不开，刷新一次，或者按下面的方法[在本地运行](#本地运行)。

### 使用方法

1. **上传**一个或多个文献文件（UTF-8 编码，合计最大 10 MB，最多 20 个文件），每个文件的格式都会自动识别：
   - **BibTeX (.bib)**：Zotero、Mendeley、Google Scholar 等工具导出的文件。
   - **EBSCO XML**：在 EBSCO 里用 *Download → XML* 导出的文件。标题、作者、日期、摘要、DOI、链接和出版商会转换成 BibTeX 字段，并为每条记录生成一个引用键，比如 `han2026double`。EBSCO 的 XML 里没有关键词和期刊名，所以只按标题和摘要打分。

   同时上传多个文件时，所有文献会合在一起，并**自动去重**。判断重复的规则是：DOI 相同；没有 DOI 时，标题相同（忽略大小写、标点和花括号）。标题很短时，还要求第一作者也相同。重复的文献只保留字段最完整的那个版本。页面上会显示每个文件读到了多少篇、去掉了多少篇重复，导出时可以选 `Source file` 字段，看每篇来自哪个文件。如果两篇不同的文献用了同一个引用键（比如两个文件里都有 `smith2021`），第二篇会改成 `smith2021a`。
2. **设置权重**，每行一条 `关键词:权重`，权重为整数：
   ```
   just transition:3
   steel:2
   CO2: capture:1
   ```
   以最后一个冒号后面的内容作为权重，所以关键词里可以带冒号。权重可以是负数，用来把命中的文献往后排。
   - 关键词按**整词**匹配，不区分大小写：`art` 不会匹配到 "partial"。
   - 结尾加 `*` 表示**前缀匹配**：`steel*` 能匹配 "steel"、"steels" 和 "steelmaking"。
3. **设置字段系数**（默认：标题 ×3、keywords ×2、摘要 ×1）。这样同一个关键词出现在标题里，得分是出现在摘要里的 3 倍。某个字段设为 0 就不参与打分。
4. **打分规则**：每命中一次，加 *关键词权重 × 字段系数*。结果按总分从高到低排列。
5. **在网页上查看排名**：显示前 100 篇的分数、命中的关键词，标题里命中的词会高亮。点击标题可以展开摘要。想换权重试试，改完直接再点 **Analyze and Rank**，不用重新上传。
6. **导出**，支持三种格式：
   - **CSV** 或 **Excel (.xlsx)**：勾选需要的字段。表头用易读的名称，比如 BibTeX 引用键对应 `Citation key`。CSV 是带 BOM 的 UTF-8，用 Excel 打开时重音字母和符号也能正常显示。Excel 文件里的分数存成数字，首行冻结。
   - **BibTeX (.bib)**：把排好序的文献导回 Zotero、EndNote 或 Mendeley。文件里是上传时的完整原始条目，LaTeX 和花括号都原样保留。可以选择把排名和分数写进每条文献的 `note` 字段。

   可以导出全部、只导出**前 N 篇**，或者只导出**分数不低于某个值**的。按相似度排序时，这个下限就改成针对相似度。

打分前，程序会先清理这三个字段里常见的 BibTeX/LaTeX 符号，比如花括号、`$...$` 公式和转义字符。例如 `CO$_2$` 会变成 `CO2`。

### 语义打分（可选）

关键词会漏掉那些换了一种说法、但内容相关的文献。你还可以用一句话写下**研究问题**，比如 *"How do steel-producing regions protect workers during decarbonization?"*。程序会用一个本地小模型（通过 [fastembed](https://github.com/qdrant/fastembed) 运行 [`BAAI/bge-small-en-v1.5`](https://huggingface.co/BAAI/bge-small-en-v1.5)）把每篇文献的标题和摘要转成向量，然后加一列 `similarity`，即余弦相似度，范围 0 到 1。在 **Rank by** 里选 **Similarity to research question** 就按它排序。填了研究问题后，关键词权重可以不填。

- 模型就在服务器上运行，不需要 API key，文献也不会发给任何第三方。
- 英文效果最好。
- 一个文件第一次做语义打分最慢，因为每篇摘要都要转成向量。之后对同一个文件重新排序，会直接用算好的向量。
- 在线网站上，每个文件最多对 300 篇做语义打分。文件更大的话请在本地运行（默认上限 1000 篇）。

### 隐私

- 上传的 `.bib` 文件本身只在内存中解析，不会保存。
- 每次上传的数据（解析出的条目和清洗后的文本）和结果以随机编号单独存放在 `output/` 下，用户之间互相看不到。一小时没有使用就会自动删除。

### 本地运行

需要 Python 3.10 及以上版本。

```bash
git clone https://github.com/PRDough/bibscorer.git
cd bibscorer
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

然后打开 <http://127.0.0.1:5000>。需要调试模式时运行 `FLASK_DEBUG=1 python app.py`。第一次使用语义打分时，会把向量模型（约 70 MB）下载到 `.models/`。

### 配置

以下都是可选的环境变量：

| 变量 | 默认值 | 含义 |
|---|---|---|
| `SEMANTIC_ENABLED` | `1` | 设为 `0` 关闭语义打分，页面上也不显示研究问题输入框 |
| `SEMANTIC_MAX_ENTRIES` | `1000` | 每个文件最多对多少篇做语义打分 |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | 可以换成任何 [fastembed 支持的](https://qdrant.github.io/fastembed/examples/Supported_Models/)文本模型 |
| `EMBED_CACHE_DIR` | `.models/` | 模型存放位置 |
| `EMBED_BATCH_SIZE` / `EMBED_THREADS` | `8` / `1` | 调大会更快，但更占内存 |

### 测试

```bash
pip install -r requirements-dev.txt
pytest
```

测试用的是假的向量模型，不下载任何东西，几秒钟就能跑完。要连真实模型一起测，运行 `RUN_SLOW=1 pytest`。每次 push 和提 pull request 时，GitHub Actions 都会自动跑测试。

### 部署到 Render

仓库里已经有 `render.yaml`。在 Render 中选择 **New → Blueprint**，连接本仓库即可。构建时会安装依赖并预先下载向量模型，然后用 Python 3.12 通过 `gunicorn` 运行。

- 分小批计算时，内存峰值约 300 MB，免费版的 512 MB 够用。
- 免费版的 CPU 很慢，几百篇做语义打分可能要一分钟以上。所以请求超时放宽到了 10 分钟，每个文件的上限设成 300 篇。
- 重启后磁盘会清空，已保存的结果会丢失，用户需要重新上传。

### 目录结构

```
app.py                 Flask 主程序：上传、排名页面和 CSV 下载
scoring.py             关键词打分：文本清理、权重解析、整词匹配
importers.py           EBSCO XML 导入（用 defusedxml 安全解析）
semantic.py            用本地向量模型做语义打分
templates/layout.html  基础布局
templates/index.html   上传、权重、排名和字段选择页面
static/style.css       黑白样式表（Optima + Baskerville）
tests/                 pytest 测试
requirements.txt       Python 依赖（requirements-dev.txt 额外包含 pytest）
render.yaml            Render 部署配置
```

### 许可证

[MIT](LICENSE)

# BibScorer

[English](#english) | [中文](#中文)

---

## English

BibScorer is a small Flask web app that ranks the references in a BibTeX file by keywords you choose. Upload a `.bib` file, give each keyword a weight, and download the ranked list as CSV.

**Try it online: <https://bibscorer.onrender.com>**

> The site runs on Render's free plan, which puts it to sleep after about 15 minutes with no visitors. If nobody has used it recently, the first page load can take up to a minute while the server wakes up. Please wait and don't keep refreshing. After that it responds normally. If the page still doesn't load after a few minutes, refresh once, or [run it locally](#run-locally).

### How it works

1. **Upload** a `.bib` file (UTF-8, up to 10 MB).
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
6. **Export.** Tick the fields you want (e.g. `title`, `author`, `year`, `score`, `matched_keywords`) and download them as `filtered_output.csv`.

Before scoring, BibScorer strips common BibTeX/LaTeX markup from those three fields, such as braces, `$...$` math and escaped characters. For example, `CO$_2$` becomes `CO2`.

### Privacy

- Uploaded `.bib` files are parsed in memory and never written to disk.
- Each upload's scored result is saved under a random ID in `output/`, so users can't see each other's data. Results are deleted after one hour.

### Run locally

Requires Python 3.10+.

```bash
git clone https://github.com/PRDough/bibscore_web.git
cd bibscore_web
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>. To turn on debug mode, run `FLASK_DEBUG=1 python app.py`.

### Deploy to Render

The repository includes a `render.yaml` blueprint. In Render, choose **New → Blueprint** and connect this repository. It runs the app with `gunicorn app:app` on Python 3.12.

On Render's free plan the disk is wiped on restart, so any stored results are lost and users have to upload again.

### Project structure

```
app.py                 Flask app: cleaning, scoring, upload and download routes
templates/layout.html  Base layout (Bootstrap 5)
templates/index.html   Upload, weights and field-selection page
requirements.txt       Python dependencies
render.yaml            Render deployment config
```

### License

[MIT](LICENSE)

---

## 中文

BibScorer 是一个基于 Flask 的小型网页工具，按你自定义的关键词和权重给 BibTeX 文献打分、排序，并导出为 CSV。

**在线使用：<https://bibscorer.onrender.com>**

> 网站部署在 Render 免费版上，大约 15 分钟没人访问就会自动休眠。如果最近没人用过，第一次打开可能要等 1 分钟左右，服务器正在唤醒。请耐心等待，不要反复刷新。唤醒后就能正常使用。如果等了几分钟还打不开，刷新一次，或者按下面的方法[在本地运行](#本地运行)。

### 使用方法

1. **上传** `.bib` 文件（UTF-8 编码，最大 10 MB）。
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
6. **导出**：勾选需要的字段（如 `title`、`author`、`year`、`score`、`matched_keywords`），下载为 `filtered_output.csv`。

打分前，程序会先清理这三个字段里常见的 BibTeX/LaTeX 符号，比如花括号、`$...$` 公式和转义字符。例如 `CO$_2$` 会变成 `CO2`。

### 隐私

- 上传的 `.bib` 文件只在内存中解析，不会存到磁盘。
- 每次上传的打分结果以随机编号单独存放在 `output/` 下，用户之间互相看不到。结果一小时后自动删除。

### 本地运行

需要 Python 3.10 及以上版本。

```bash
git clone https://github.com/PRDough/bibscore_web.git
cd bibscore_web
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

然后打开 <http://127.0.0.1:5000>。需要调试模式时运行 `FLASK_DEBUG=1 python app.py`。

### 部署到 Render

仓库里已经有 `render.yaml`。在 Render 中选择 **New → Blueprint**，连接本仓库即可。它会用 Python 3.12 运行 `gunicorn app:app`。

Render 免费版重启后磁盘会清空，已保存的结果会丢失，用户需要重新上传。

### 目录结构

```
app.py                 Flask 主程序：文本清理、打分、上传与下载
templates/layout.html  基础布局（Bootstrap 5）
templates/index.html   上传、权重设置和字段选择页面
requirements.txt       Python 依赖
render.yaml            Render 部署配置
```

### 许可证

[MIT](LICENSE)

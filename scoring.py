"""关键词打分逻辑：文本清洗、权重解析、整词匹配和按字段加权计分。

这里不依赖 Flask，可以单独测试或在脚本里使用。
"""
import re
import pandas as pd

# 参与打分的字段及默认系数：标题最重要，摘要最轻
SCORED_FIELDS = {'title': 3, 'abstract': 1, 'keywords': 2}
# 打分时生成的列，重新打分前要先去掉
SCORE_COLUMNS = ['score', 'matched_keywords', 'similarity']


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


# 读取字段系数（来自表单等字典），非法或负数时用默认值
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


# 给整张表打分，加上 score 和 matched_keywords 两列（不排序）
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
    return df

"""把非 BibTeX 的文献导出文件转换成和 bibtexparser 一样的条目（dict）。

目前支持 EBSCO 导出的 XML：
    <records>
      <record>
        <title>…</title> <authors>A, B ; C, D</authors> <publicationDate>20260201</publicationDate>
        <abstract>…</abstract> <doi>…</doi> <publisher>…</publisher> <plink>…</plink>
        <longDbName>…</longDbName> <accessionNumber>…</accessionNumber>
      </record>
    </records>
"""
import re
import unicodedata

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
# EBSCO 标签 -> BibTeX 字段；没列出的标签按小写原名保留
EBSCO_FIELDS = {
    'title': 'title', 'abstract': 'abstract', 'doi': 'doi', 'publisher': 'publisher',
    'plink': 'url', 'longdbname': 'database', 'accessionnumber': 'accessionnumber',
    'source': 'journal', 'journal': 'journal', 'publicationname': 'journal', 'sourcetitle': 'journal',
    'subjects': 'keywords', 'subject': 'keywords', 'keywords': 'keywords', 'keyword': 'keywords',
    'language': 'language', 'issn': 'issn', 'isbn': 'isbn', 'volume': 'volume', 'issue': 'number',
    'pages': 'pages',
}
# 生成引用键时跳过的词
KEY_STOPWORDS = {'a', 'an', 'the', 'of', 'on', 'in', 'and', 'for', 'to', 'from', 'with', 'by', 'at',
                 'can', 'how', 'what', 'why', 'when', 'does', 'do', 'is', 'are', 'who', 'which'}


class UnsupportedFile(ValueError):
    """文件看起来是 XML，但不是能识别的格式。"""


def looks_like_xml(text):
    return text.lstrip().startswith('<')


def _ascii_word(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', text.lower())


# 引用键：第一作者姓 + 年份 + 标题第一个实词，例如 han2026double
def citation_key(entry):
    first_author = entry.get('author', '').split(' and ')[0]
    surname = first_author.split(',')[0] if ',' in first_author else (first_author.split() or [''])[0]
    words = [_ascii_word(w) for w in re.split(r'[\s\-–—:]+', entry.get('title', ''))]
    word = next((w for w in words if w and w not in KEY_STOPWORDS), '')
    return (_ascii_word(surname) or 'anon') + entry.get('year', '') + word


def _unique_keys(entries):
    seen = {}
    for entry in entries:
        key = citation_key(entry)
        count = seen.get(key, 0)
        seen[key] = count + 1
        # 重复的键依次加 a、b、c……
        if count == 0:
            entry['ID'] = key
        elif count <= 26:
            entry['ID'] = key + chr(ord('a') + count - 1)
        else:
            entry['ID'] = f"{key}{count}"
    return entries


def _ebsco_record(record):
    entry = {'ENTRYTYPE': 'article'}
    values = {}
    for child in record:
        tag = child.tag.split('}')[-1]
        text = " ".join("".join(child.itertext()).split())
        if text:
            values.setdefault(tag.lower(), []).append(text)

    for tag, texts in values.items():
        if tag in ('authors', 'author'):
            names = [n.strip() for t in texts for n in t.split(';') if n.strip()]
            entry['author'] = " and ".join(names)
        elif tag == 'publicationdate':
            digits = re.sub(r'\D', '', texts[0])
            if len(digits) >= 4:
                entry['year'] = digits[:4]
            if len(digits) >= 6 and 1 <= int(digits[4:6]) <= 12:
                entry['month'] = MONTHS[int(digits[4:6]) - 1]
        else:
            field = EBSCO_FIELDS.get(tag, tag)
            entry[field] = "; ".join(texts)

    # EBSCO 的标题结尾带句号；问号、感叹号保留
    if entry.get('title', '').endswith('.') and not entry['title'].endswith('..'):
        entry['title'] = entry['title'][:-1]
    return entry


def parse_ebsco_xml(text):
    try:
        root = fromstring(text)
    except (ParseError, DefusedXmlException):
        raise UnsupportedFile("Could not read the XML file.")
    records = [el for el in root.iter() if el.tag.split('}')[-1] == 'record']
    if root.tag.split('}')[-1] != 'records' or not records:
        raise UnsupportedFile("Unsupported XML format. BibScorer reads XML exported from EBSCO.")
    return _unique_keys([_ebsco_record(r) for r in records])

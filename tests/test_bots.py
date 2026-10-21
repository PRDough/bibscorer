import io

import pytest

BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


@pytest.mark.parametrize("agent", [
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
    "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
    "CCBot/2.0 (https://commoncrawl.org/faq/)",
    "claude-user",
])
def test_ai_agents_get_403_everywhere(client, agent):
    for path in ("/", "/static/style.css"):
        r = client.get(path, headers={"User-Agent": agent})
        assert r.status_code == 403, (path, agent)
        assert b"BibScorer" not in r.data
    r = client.post("/", data={"weights": "a:1", "bibfile": (io.BytesIO(b"@article{a, title={x}}"), "a.bib")},
                    headers={"User-Agent": agent}, content_type="multipart/form-data")
    assert r.status_code == 403


@pytest.mark.parametrize("agent", [
    BROWSER,
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "",
])
def test_browsers_and_search_engines_still_work(client, agent):
    r = client.get("/", headers={"User-Agent": agent})
    assert r.status_code == 200
    assert b"Rank references" in r.data


def test_robots_txt_is_readable_by_everyone(client):
    for agent in (BROWSER, "ClaudeBot/1.0"):
        r = client.get("/robots.txt", headers={"User-Agent": agent})
        assert r.status_code == 200
        assert r.mimetype == "text/plain"
    text = r.get_data(as_text=True)
    assert "User-agent: ClaudeBot\nDisallow: /" in text
    assert "User-agent: GPTBot\nDisallow: /" in text
    assert "User-agent: Google-Extended\nDisallow: /" in text
    assert text.rstrip().endswith("User-agent: *\nAllow: /")

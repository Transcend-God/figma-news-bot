"""
OpenAI Blog Daily News -> Notion Sync
Uses RSS feed from openai.com/news/rss.xml as primary data source.
Playwright is NOT used (Cloudflare Turnstile blocks headless browsers).
"""

import os
import re
import sys
import json
import traceback
import requests
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_PAGE_ID = os.environ.get("NOTION_OPENAI_PAGE_ID", "")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

CST = timezone(timedelta(hours=8))

RSS_URL = "https://openai.com/news/rss.xml"


def classify_openai_article(title: str) -> str:
    t = title.lower()
    keywords = [
        ("safety", "AI 安全与治理"), ("governance", "AI 安全与治理"),
        ("provenance", "AI 安全与治理"), ("synthid", "AI 安全与治理"),
        ("alignment", "AI 安全与治理"), ("responsible", "AI 安全与治理"),
        ("bug bounty", "AI 安全与治理"), ("child safety", "AI 安全与治理"),
        ("teen safety", "AI 安全与治理"), ("prompt injection", "AI 安全与治理"),
        ("cyber", "网络安全"), ("vulnerability", "网络安全"),
        ("phishing", "网络安全"), ("supply chain", "网络安全"),
        ("agent", "AI Agent 应用"), ("codex", "AI Agent 应用"),
        ("orchestration", "AI Agent 应用"),
        ("gartner", "行业认可"), ("leader", "行业认可"),
        ("disprove", "科学研究"), ("conjecture", "科学研究"),
        ("research", "科学研究"), ("paper", "科学研究"), ("theorem", "科学研究"),
        ("reasoning", "科学研究"), ("chain-of-thought", "科学研究"),
        ("partner", "企业合作"), ("collaboration", "企业合作"),
        ("dell", "企业合作"), ("microsoft", "企业合作"), ("aws", "企业合作"),
        ("acquire", "收购与投资"), ("acquires", "收购与投资"), ("funding", "收购与投资"),
        ("launch", "产品发布"), ("introducing", "产品发布"),
        ("chatgpt", "ChatGPT"), ("gpt-5", "模型发布"), ("gpt-4", "模型发布"),
        ("sora", "视频与多模态"), ("images", "视频与多模态"),
        ("voice", "语音与实时"), ("real-time", "语音与实时"),
        ("education", "教育与公益"), ("fellowship", "教育与公益"),
        ("foundation", "教育与公益"), ("disaster", "教育与公益"),
        ("election", "社会与政策"), ("policy", "社会与政策"),
        ("industrial", "社会与政策"),
    ]
    for kw, cat in keywords:
        if kw in t:
            return cat
    return "其他"


def _strip_cdata(text):
    """Remove CDATA wrapper if present."""
    if text and text.startswith("<![CDATA[") and text.endswith("]]>"):
        return text[9:-3]
    return text or ""


def fetch_openai_articles() -> list:
    """Fetch articles from OpenAI RSS 2.0 feed."""
    print(f"  Fetching RSS: {RSS_URL}")
    resp = requests.get(RSS_URL, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
    })
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    # RSS 2.0: <rss><channel><item>...</item></channel></rss>
    items = root.findall(".//item")
    articles = []

    for item in items:
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        date_el = item.find("pubDate")

        title = _strip_cdata(title_el.text) if title_el is not None else ""
        url = _strip_cdata(link_el.text) if link_el is not None else ""
        summary = _strip_cdata(desc_el.text) if desc_el is not None else ""
        date_raw = _strip_cdata(date_el.text) if date_el is not None else ""

        if not title or not url:
            continue

        # Skip non-news pages (academy, signals, business/guides, etc.)
        skip_paths = ["/academy/", "/signals/", "/business/guides", "/business/resources"]
        if any(p in url for p in skip_paths):
            continue

        # Extract slug
        slug_match = re.search(r"/index/([a-z0-9-]+)/?$", url)
        if not slug_match:
            slug_match = re.search(r"/([a-z0-9-]+)/?$", url)
        slug = slug_match.group(1) if slug_match else re.sub(r"[^a-z0-9-]", "", url.split("/")[-1].split("?")[0])

        # Parse date (RFC 2822 format: "Mon, 01 Jun 2026 17:00:00 GMT")
        date_str = ""
        if date_raw:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(date_raw)
                date_str = dt.astimezone(CST).strftime("%Y-%m-%d")
            except:
                date_str = date_raw[:11].strip()

        articles.append({
            "title": title,
            "url": url,
            "date": date_str,
            "summary": summary,
            "slug": slug,
            "category": classify_openai_article(title),
        })

    # Limit to recent articles (max ~3 months, ~60 entries from RSS)
    articles = articles[:60]

    print(f"  Parsed {len(articles)} articles from RSS (limited to latest 60)")
    return articles


def fetch_existing_slugs() -> set:
    """Get slugs of articles already on the Notion page."""
    slugs = set()
    url = f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children?page_size=100"
    while url:
        try:
            resp = requests.get(url, headers=NOTION_HEADERS, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"  Warning: Notion API error: {e}")
            break
        data = resp.json()
        blocks = data.get("results", [])

        for block in blocks:
            btype = block["type"]
            # Check bookmark blocks
            if btype == "bookmark":
                bm_url = block.get("bookmark", {}).get("url", "")
                slug_match = re.search(r"/index/([a-z0-9-]+)/?$", bm_url)
                if slug_match:
                    slugs.add(slug_match.group(1))
                # Also try generic slug extraction
                generic = re.search(r"/([a-z0-9-]+)/?$", bm_url)
                if generic and not slug_match:
                    slugs.add(generic.group(1))
            # Check rich text for links
            rich_text = block.get(btype, {}).get("rich_text", [])
            for rt in rich_text:
                link = rt.get("href") or ""
                if not link:
                    text_obj = rt.get("text") or {}
                    if text_obj:
                        link_obj = text_obj.get("link") or {}
                        link = link_obj.get("url") or ""
                if link:
                    slug_match = re.search(r"/index/([a-z0-9-]+)/?$", link)
                    if slug_match:
                        slugs.add(slug_match.group(1))

        url = None
        if data.get("has_more"):
            cursor = data.get("next_cursor")
            if cursor:
                url = (
                    f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children"
                    f"?page_size=100&start_cursor={cursor}"
                )

    return slugs


def build_notion_blocks(new_articles: list) -> list:
    """Convert new articles to Notion block format. Latest first."""
    cat_emoji = {
        "AI 安全与治理": "⚠️",
        "网络安全": "🔒",
        "AI Agent 应用": "⚡",
        "行业认可": "⭐",
        "科学研究": "💡",
        "企业合作": "✅",
        "产品发布": "🚀",
        "模型发布": "🧠",
        "ChatGPT": "💬",
        "视频与多模态": "🎬",
        "语音与实时": "🎙️",
        "教育与公益": "📚",
        "社会与政策": "🏛️",
        "收购与投资": "💰",
        "其他": "📌",
    }

    today = datetime.now(CST).strftime("%Y-%m-%d")
    blocks = [
        {"object": "block", "type": "divider", "divider": {}},
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": f"📰 更新于 {today}"}}]
            },
        },
    ]

    for a in new_articles:
        emoji = cat_emoji.get(a["category"], "📌")
        date_info = f" ({a['date']})" if a.get("date") else ""
        title_line = f"[{a['category']}] {a['title']}{date_info}"

        blocks.append({"object": "block", "type": "divider", "divider": {}})
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": title_line}}]
            },
        })
        blocks.append({
            "object": "block",
            "type": "bookmark",
            "bookmark": {
                "url": a["url"],
                "caption": [{"type": "text", "text": {"content": "点击阅读原文"}}]
            },
        })
        summary = a.get("summary", "") or "待补充核心内容总结"
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": emoji},
                "rich_text": [{"type": "text", "text": {"content": summary}}]
            },
        })

    return blocks


def append_to_notion(blocks: list):
    """Append blocks to Notion page."""
    for i in range(0, len(blocks), 100):
        batch = blocks[i : i + 100]
        resp = requests.patch(
            f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children",
            headers=NOTION_HEADERS,
            json={"children": batch},
        )
        resp.raise_for_status()
        print(f"  Batch {i // 100 + 1}: {resp.status_code}")


def main():
    print("=" * 50)
    print(f"OpenAI News Bot (RSS) | {datetime.now(CST).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    if not NOTION_API_KEY or not NOTION_PAGE_ID:
        print("ERROR: NOTION_API_KEY or NOTION_OPENAI_PAGE_ID not set")
        sys.exit(1)

    print("\n[1/4] Fetching OpenAI RSS feed...")
    articles = fetch_openai_articles()
    for a in articles[:5]:
        print(f"    - {a['title'][:80]}")
    if len(articles) > 5:
        print(f"    ... and {len(articles) - 5} more")

    if not articles:
        print("  No articles found, skipping.")
        return

    print("\n[2/4] Checking existing slugs in Notion...")
    existing = fetch_existing_slugs()
    print(f"  Existing slugs: {len(existing)}")

    print("\n[3/4] Filtering new articles...")
    new_articles = [a for a in articles if a["slug"] not in existing]
    # Sort by date descending (latest first)
    new_articles.sort(key=lambda x: x.get("date", ""), reverse=True)
    print(f"  New articles: {len(new_articles)}")
    for a in new_articles[:5]:
        print(f"    + {a['title'][:80]}")
    if len(new_articles) > 5:
        print(f"    + ... and {len(new_articles) - 5} more")

    if not new_articles:
        print("  No new articles. Nothing to update.")
        return

    print("\n[4/4] Appending to Notion...")
    blocks = build_notion_blocks(new_articles)
    append_to_notion(blocks)
    print(f"  Done! Added {len(new_articles)} articles.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)

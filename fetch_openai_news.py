"""
OpenAI Blog Daily News -> Notion Sync
Uses WebFetch approach via requests (no Playwright needed for OpenAI).
"""

import os
import re
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_PAGE_ID = os.environ["NOTION_OPENAI_PAGE_ID"]

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

CST = timezone(timedelta(hours=8))


def classify_openai_article(title: str) -> str:
    keywords = [
        ("safety", "AI 安全与治理"), ("governance", "AI 安全与治理"),
        ("provenance", "AI 安全与治理"), ("synthid", "AI 安全与治理"),
        ("alignment", "AI 安全与治理"), ("responsible", "AI 安全与治理"),
        ("agent", "AI Agent 应用"), ("codex", "AI Agent 应用"), ("Agent", "AI Agent 应用"),
        ("gartner", "行业认可"), ("leader", "行业认可"),
        ("disprove", "科学研究"), ("conjecture", "科学研究"),
        ("research", "科学研究"), ("paper", "科学研究"), ("theorem", "科学研究"),
        ("partner", "企业合作"), ("dell", "企业合作"), ("collaboration", "企业合作"),
        ("launch", "产品更新"), ("introducing", "产品更新"),
        ("chatgpt", "产品更新"), ("gpt-", "产品更新"), ("gpt5", "产品更新"),
        ("sora", "产品更新"), ("feature", "产品更新"), ("update", "产品更新"),
        ("finance", "产品更新"), ("personal", "产品更新"),
    ]
    for kw, cat in keywords:
        if kw.lower() in title.lower():
            return cat
    return "其他"


def fetch_openai_articles() -> list:
    """Fetch articles from OpenAI news page and sitemaps."""
    articles = []
    seen_slugs = set()

    # 1. Fetch main news page for latest articles
    print("  Fetching openai.com/news ...")
    try:
        resp = requests.get("https://openai.com/news/", timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        # Extract article links with titles and dates
        # OpenAI news page has links to /index/xxx
        pattern = re.compile(r'<a[^>]+href="(/index/[^"]+/?)"[^>]*>(.*?)</a>', re.DOTALL)
        title_pattern = re.compile(r'<(?:h[234]|strong|span)[^>]*>(.*?)</(?:h[234]|strong|span)>', re.DOTALL)
        date_pattern = re.compile(r'<time[^>]*>(.*?)</time>', re.DOTALL)

        matches = pattern.findall(resp.text)
        for href, link_html in matches:
            slug = href.strip("/").split("/")[-1]
            if slug in seen_slugs or len(slug) < 5:
                continue
            seen_slugs.add(slug)

            # Try to extract title from link HTML
            title_match = title_pattern.search(link_html)
            title = title_match.group(1).strip() if title_match else ""
            # Clean HTML tags
            title = re.sub(r'<[^>]+>', '', title).strip()
            if not title or len(title) < 10:
                title = re.sub(r'<[^>]+>', '', link_html).strip()[:200]

            date = ""
            date_match = date_pattern.search(link_html)
            if date_match:
                date = date_match.group(1).strip()

            if title and len(title) >= 10:
                articles.append({
                    "title": title,
                    "url": f"https://openai.com{href}",
                    "date": date,
                    "summary": "",
                    "slug": slug,
                })
    except Exception as e:
        print(f"  Warning: news page fetch failed: {e}")

    # 2. Fetch release sitemap for more articles (only keep latest ~30)
    print("  Fetching sitemap ...")
    try:
        resp = requests.get("https://openai.com/sitemap.xml/release/", timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        url_pattern = re.compile(r'<loc>(https://openai\.com/index/[^<]+)</loc>')
        sitemap_articles = []
        for match in url_pattern.finditer(resp.text):
            url = match.group(1).rstrip("/")
            slug = url.split("/")[-1]
            if slug in seen_slugs or len(slug) < 5:
                continue
            seen_slugs.add(slug)
            sitemap_articles.append({
                "title": slug.replace("-", " ").title(),
                "url": url,
                "date": "",
                "summary": "",
                "slug": slug,
            })
        # Only take the last 30 (most recent in sitemap order)
        articles.extend(sitemap_articles[-30:])
    except Exception as e:
        print(f"  Warning: sitemap fetch failed: {e}")

    # Classify
    for a in articles:
        a["category"] = classify_openai_article(a["title"])

    return articles


def fetch_existing_slugs() -> set:
    """Get slugs of articles already on the Notion page."""
    slugs = set()
    url = f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children?page_size=100"
    while url:
        resp = requests.get(url, headers=NOTION_HEADERS)
        resp.raise_for_status()
        data = resp.json()
        blocks = data.get("results", [])

        for block in blocks:
            # Check bookmark blocks
            btype = block["type"]
            if btype == "bookmark":
                bm_url = block.get("bookmark", {}).get("url", "")
                slug_match = re.search(r"/index/([a-z0-9-]+)/?$", bm_url)
                if slug_match:
                    slugs.add(slug_match.group(1))
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
    """Convert new articles to Notion block format."""
    cat_emoji = {
        "AI 安全与治理": "⚠️",
        "AI Agent 应用": "⚡",
        "行业认可": "⭐",
        "科学研究": "💡",
        "企业合作": "✅",
        "产品更新": "🚀",
        "其他": "📌",
    }

    today = datetime.now(CST).strftime("%Y-%m-%d")
    blocks = [
        {"object": "block", "type": "divider", "divider": {}},
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"📰 更新于 {today}"}}
                ]
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
        if a.get("summary"):
            blocks.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": emoji},
                    "rich_text": [{"type": "text", "text": {"content": a["summary"]}}]
                },
            })
        else:
            # Placeholder callout when no summary
            blocks.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": emoji},
                    "rich_text": [{"type": "text", "text": {"content": "待补充核心内容总结"}}]
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
    print(f"OpenAI News Bot | {datetime.now(CST).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    print("\n[1/4] Fetching OpenAI news...")
    articles = fetch_openai_articles()
    print(f"  Found {len(articles)} articles")
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
    print(f"  New articles: {len(new_articles)}")

    if not new_articles:
        print("  No new articles. Nothing to update.")
        return

    print("\n[4/4] Appending to Notion...")
    blocks = build_notion_blocks(new_articles)
    append_to_notion(blocks)
    print(f"  Done! Added {len(new_articles)} articles.")


if __name__ == "__main__":
    main()

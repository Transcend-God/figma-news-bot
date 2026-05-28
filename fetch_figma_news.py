"""
Figma Blog Daily News -> Notion Sync
Uses Playwright to render JS-heavy Figma blog page.
"""

import os
import re
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_PAGE_ID = os.environ["NOTION_PAGE_ID"]
BLOG_URL = "https://www.figma.com/blog/"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

CST = timezone(timedelta(hours=8))

# Category slugs to skip (not blog posts)
CATEGORY_SLUGS = {
    "3d-design", "accessibility", "ai", "behind-the-scenes", "brainstorming",
    "branding", "career-and-education", "case-study", "collaboration", "config",
    "culture", "design", "design-systems", "design-thinking", "dev-mode",
    "diagramming", "engineering", "events", "everything", "figjam", "figma-buzz",
    "figma-design", "figma-draw", "figma-make", "figma-mcp", "figma-sites",
    "figma-slides", "figma-weave", "hiring", "infrastructure", "inside-figma",
    "insights", "leadership", "maker-stories", "marketing", "meetings",
    "motion", "news", "operations", "plugins-and-tooling", "portfolio",
    "product-management", "product-updates", "productivity",
    "profiles-and-interviews", "prototyping", "quality-and-performance",
    "react", "report", "research", "security", "social-impact", "software-is-culture",
    "strategy", "thought-leadership", "tips-and-inspiration", "typography",
    "ui-ux", "wireframing", "working-well", "writing",
}


def classify_article(title: str) -> str:
    keywords = [
        ("agent", "AI \u4e0e\u4ee3\u7406"),
        ("Agent", "AI \u4e0e\u4ee3\u7406"),
        ("AI", "AI \u4e0e\u4ee3\u7406"),
        ("MCP", "MCP \u751f\u6001"),
        ("designer", "\u8bbe\u8ba1\u6d1e\u5bdf"),
        ("Design", "\u8bbe\u8ba1\u6d1e\u5bdf"),
        ("design", "\u8bbe\u8ba1\u6d1e\u5bdf"),
        ("engineer", "\u5de5\u7a0b\u6280\u672f"),
        ("Postgres", "\u5de5\u7a0b\u6280\u672f"),
        ("PGKeeper", "\u5de5\u7a0b\u6280\u672f"),
        ("Make", "\u4ea7\u54c1\u66f4\u65b0"),
        ("Config", "\u4ea7\u54c1\u66f4\u65b0"),
        ("code", "\u8bbe\u8ba1\u5230\u4ee3\u7801"),
        ("Friends", "\u793e\u533a\u6d3b\u52a8"),
        ("community", "\u793e\u533a\u6d3b\u52a8"),
        ("brand", "\u793e\u533a\u6d3b\u52a8"),
    ]
    for kw, cat in keywords:
        if kw.lower() in title.lower():
            return cat
    return "\u5176\u4ed6"


def fetch_blog_articles() -> list:
    """Use Playwright to render Figma blog and extract articles."""
    from playwright.sync_api import sync_playwright

    cat_slugs_json = json.dumps(list(CATEGORY_SLUGS))
    js_code = """() => {
        var all = document.querySelectorAll('a[href]');
        var seen = new Set();
        var categorySlugs = %s;
        var results = [];

        for (var i = 0; i < all.length; i++) {
            var a = all[i];
            var href = a.getAttribute('href') || '';
            if (!href || href.indexOf('/blog/') === -1 || href === '/blog/') continue;
            if (href.indexOf('context=localeChange') !== -1) continue;

            var slugMatch = href.match(/\\/blog\\/([a-z0-9-]+)\\/?$/);
            if (!slugMatch) continue;
            var slug = slugMatch[1];
            if (categorySlugs.indexOf(slug) !== -1) continue;
            if (seen.has(slug)) continue;
            seen.add(slug);

            var h2 = a.querySelector('h2');
            var title = h2 ? h2.textContent.trim() : '';
            if (!title || title.length < 10) {
                var rawText = a.textContent.trim();
                var lines = rawText.split(String.fromCharCode(10));
                title = lines[0]
                    .replace(/(January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d{1,2},?\\s*\\d{4}.*$/, '')
                    .trim();
            }
            if (!title || title.length < 10) continue;

            var dateStr = '';
            var timeEl = a.querySelector('time');
            if (timeEl) dateStr = timeEl.textContent.trim();

            var summary = '';
            var pEl = a.querySelector('p');
            if (pEl) summary = pEl.textContent.trim();

            results.push({
                title: title,
                url: href,
                date: dateStr,
                summary: summary,
                slug: slug
            });
        }
        return results;
    }""" % cat_slugs_json

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BLOG_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        items = page.evaluate(js_code)
        browser.close()

    for item in items:
        item["category"] = classify_article(item["title"])

    return items


def fetch_existing_slugs() -> set:
    """Get slugs of articles already on the Notion page by extracting URLs."""
    slugs = set()
    url = f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children?page_size=100"
    while url:
        resp = requests.get(url, headers=NOTION_HEADERS)
        resp.raise_for_status()
        data = resp.json()
        blocks = data.get("results", [])

        for block in blocks:
            btype = block["type"]
            rich_text = block.get(btype, {}).get("rich_text", [])
            for rt in rich_text:
                link = rt.get("href") or ""
                if not link:
                    text_obj = rt.get("text") or {}
                    if text_obj:
                        link_obj = text_obj.get("link") or {}
                        link = link_obj.get("url") or ""
                if link:
                    slug_match = re.search(r"/blog/([a-z0-9-]+)/?$", link)
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
    today = datetime.now(CST).strftime("%Y-%m-%d")
    blocks = [
        {"object": "block", "type": "divider", "divider": {}},
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"\U0001f4f0 \u66f4\u65b0\u4e8e {today}"}}
                ]
            },
        },
    ]

    categories = {}
    for a in new_articles:
        categories.setdefault(a["category"], []).append(a)

    for cat, arts in categories.items():
        blocks.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": cat}}]
                },
            }
        )
        for a in arts:
            date_info = f" | {a['date']}" if a.get("date") else ""
            summary = f"\n{a['summary']}" if a.get("summary") else ""
            display = f"{a['title']}\n{a['title']}{date_info}{summary}"
            display = display[:2000]

            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": display,
                                    "link": {"url": a["url"]},
                                },
                            }
                        ]
                    },
                }
            )

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
    print(f"Figma News Bot | {datetime.now(CST).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    print("\n[1/4] Fetching Figma blog (Playwright)...")
    articles = fetch_blog_articles()
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

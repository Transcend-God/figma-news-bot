# Figma Daily News Bot

每天自动抓取 Figma Blog 最新文章，同步到 Notion 页面。

## 工作流

1. 每天 UTC 1:00（北京时间 9:00）自动运行
2. 抓取 [Figma Blog](https://www.figma.com/blog/) 最新文章
3. 对比 Notion 已有内容，过滤出新文章
4. 追加到 Notion「Figma 最新动态」页面

## 配置

在 GitHub 仓库的 Settings → Secrets and variables → Actions 中添加：

| Secret | 值 |
|--------|-----|
| `NOTION_API_KEY` | Notion Integration Token |
| `NOTION_PAGE_ID` | Notion 页面 ID |

## 手动触发

在 Actions 页面点击 `Run workflow` 即可手动执行一次。

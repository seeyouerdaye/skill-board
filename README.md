# GitHub 热门 Skill 看板

每天北京时间 06:00 自动更新"前一天 GitHub 最火的 skill / 项目"看板：
skill 榜按装机量（skills.sh）、项目榜按昨日新增 star 数，每条带用途说明。

## 架构

```
GitHub Actions (cron 0 22 * * * UTC)
  └─ collector.py  ──抓──> skills.sh /trending /hot（HTML 解析）
                   ──调──> GitHub Search API（stars>500 活跃池 + 30 天内新晋池）
  └─ commit ──> data/latest.json + data/YYYY-MM-DD.json + prev_stars.json + desc-cache.json
看板 dashboard.html ──fetch──> ./data/latest.json（本地/发布后均可用）
```

## 数据文件

| 文件 | 作用 |
|---|---|
| `data/latest.json` | 看板页消费的最终数据（三榜） |
| `data/YYYY-MM-DD.json` | 每日快照（页面用它标记"🆕 新上榜"） |
| `data/prev_stars.json` | 项目 star 滚动池，明天算"昨日新增"用（首日为空 → 项目榜暂按总星标展示） |
| `data/desc-cache.json` | 仓库描述缓存，省 API 配额 |
| `data/zh-intro.json` | 中文简介（人工/agent 维护，collector 不会覆盖）；新上榜条目按同一风格补写 |
| `data/solves.json`（可选） | `"repo/name"` 或 `"repo"` → 一句"💪 对你能解决什么"，由 agent 增量补写 |

## 搭建（一次性，约 10 分钟）

1. 在 GitHub 新建一个空仓库（如 `yourname/skill-board`），把本目录推上去：
   ```bash
   cd skill_board
   git init && git add -A && git commit -m "init skill board"
   git remote add origin https://github.com/yourname/skill-board.git
   git push -u origin main
   ```
2. Actions 无需任何 secret——工作流用的是仓库内置 `GITHUB_TOKEN`（已在 `permissions: contents: write` 授权提交）。
3. 到仓库 Settings → Actions 确认 workflow 已启用；可点 "Run workflow" 手动跑一次验证。
4. 看板页：直接用 WorkBuddy「发布为应用」把本目录发布成在线链接（静态站点即可），或本地 `python -m http.server 8777` 打开 `dashboard.html`。

## 维护要点

- ⚠️ skills.sh **没有官方 API**，本方案解析其 SSR HTML。若某天 collector 报
  `仅解析到 N 条，页面结构可能已改版`，去更新 `parse_skills_page()` 的正则。
- 未认证 GitHub API 限流：搜索 10 次/分（已带 15s 退避重试）、核心 60 次/时（描述补充每次最多 25 条）。
  上到 Actions 后带内置 token，自动放开。
- 「💪 对你」说明：每天打开看板后，让 agent 对 🆕 新上榜条目各写一句，追加进 `data/solves.json` 即可，旧条目不重复生成。

## 本地运行

```bash
python collector.py     # 采集一次，写 data/
python -m http.server 8777   # 然后浏览器打开 http://localhost:8777/dashboard.html
```

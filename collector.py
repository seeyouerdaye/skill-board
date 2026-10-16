# -*- coding: utf-8 -*-
"""GitHub 热门 skill/项目 看板采集器
数据源：
  1. skills.sh  /trending(24h) /hot  —— skill 装机量榜（SSR HTML，无官方 API，注意改版风险）
  2. GitHub Search API            —— 项目榜（用 prev_stars.json 滚动池算昨日新增 star）
输出：
  data/latest.json          看板页消费的最终数据
  data/YYYY-MM-DD.json      每日快照
  data/prev_stars.json      项目 star 滚动池（明天算增量用）
  data/desc-cache.json      仓库描述缓存（省 API 配额）
"""
import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CST = timezone(timedelta(hours=8))
TODAY = datetime.now(CST).strftime("%Y-%m-%d")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) skill-board/0.1",
      "Accept": "application/vnd.github+json"}

ENTRY_RE = re.compile(r'<a class="group grid[^"]*" href="/(?P<href>[^"]+)">(?P<body>.*?)</a></div>', re.S)
NAME_RE = re.compile(r"<h3[^>]*>([^<]+)</h3>")
REPO_RE = re.compile(r"<p[^>]*>([^<]+)</p>")
INST_RE = re.compile(r'<span class="font-mono text-sm text-foreground">([^<]+)</span>')

TOP_N = 20          # 每榜条数
POOL_N = 220        # star 滚动池大小（算昨日增量用）
DESC_BUDGET = 25    # 每次运行最多补多少条新描述（未认证 core API 限 60 次/时）


def http_get(url, token=None, timeout=30):
    headers = dict(UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def parse_num(s):
    """'19.1K' -> 19100；'3.9M' -> 3900000；纯数字原样"""
    s = str(s).strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*([KMB]?)$", s, re.I)
    if not m:
        return None
    mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()]
    return int(float(m.group(1)) * mult)


def parse_skills_page(html):
    """解析 skills.sh 榜单页 -> [{name, repo, url, installs}]"""
    out, seen = [], set()
    for m in ENTRY_RE.finditer(html):
        body = m.group("body")
        name, repo = NAME_RE.search(body), REPO_RE.search(body)
        if not (name and repo):
            continue
        inst = INST_RE.findall(body)
        item = {
            "name": name.group(1).strip(),
            "repo": repo.group(1).strip(),
            "url": "https://www.skills.sh/" + m.group("href").strip("/"),
            "installs": inst[-1].strip() if inst else "",
            "installs_n": parse_num(inst[-1]) if inst else None,
        }
        key = item["repo"] + "/" + item["name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fetch_repo_desc(repos, cache, token=None):
    """补齐仓库描述（一次性缓存，尊重未认证配额）"""
    need = [r for r in dict.fromkeys(repos) if r not in cache and "/" in r][:DESC_BUDGET]
    for repo in need:
        try:
            d = json.loads(http_get(f"https://api.github.com/repos/{repo}", token=token))
            cache[repo] = (d.get("description") or "").strip()
        except Exception:
            cache[repo] = cache.get(repo, "")
        time.sleep(1.0)  # 未认证 60 次/时，留余量
    return cache


def search_with_retry(url, token=None, tries=3):
    """搜索 API 未认证 10 次/分，403 时退避重试"""
    for i in range(tries):
        try:
            return json.loads(http_get(url, token=token))
        except urllib.error.HTTPError as e:
            if i == tries - 1:
                print(f"[warn] search failed {e.code}: {url[:80]}")
                return {}
            time.sleep(15 * (i + 1))
        except Exception as e:
            print(f"[warn] search error: {e}")
            return {}
    return {}


def collect_projects(token=None):
    """项目榜：两个搜索合并成池，与 prev_stars.json 差值 = 昨日新增 star"""
    y = (datetime.now(CST) - timedelta(days=1)).strftime("%Y-%m-%d")
    m30 = (datetime.now(CST) - timedelta(days=30)).strftime("%Y-%m-%d")
    q1 = f"https://api.github.com/search/repositories?q=stars%3A%3E500+pushed%3A%3E{y}&sort=stars&order=desc&per_page=100"
    q2 = f"https://api.github.com/search/repositories?q=created%3A%3E%3D{m30}+stars%3A%3E100&sort=stars&order=desc&per_page=100"
    pool = {}
    for u in (q1, q2):
        d = search_with_retry(u, token=token)
        for it in d.get("items", []):
            pool[it["full_name"]] = {
                "name": it["full_name"],
                "stars": it["stargazers_count"],
                "desc": (it.get("description") or "").strip(),
                "url": it["html_url"],
            }
        time.sleep(2.0)

    prev = load_json(DATA / "prev_stars.json", {})
    ranked = []
    for name, it in pool.items():
        delta = it["stars"] - prev.get(name, it["stars"]) if name in prev else None
        ranked.append({**it, "delta": delta})
    has_delta = any(r["delta"] is not None for r in ranked)
    if has_delta:
        ranked.sort(key=lambda x: (x["delta"] if x["delta"] is not None else -1, x["stars"]), reverse=True)
        top = [r for r in ranked if r["delta"] is not None][:TOP_N]
    else:
        # 首日无昨日池：按总星标兜底，明天起自动切换为昨日增量
        ranked.sort(key=lambda x: x["stars"], reverse=True)
        top = ranked[:TOP_N]
        print("[info] 首日无昨日池，项目榜暂按总星标展示")

    # 更新滚动池：全量当前值，明天用来算差
    (DATA / "prev_stars.json").write_text(
        json.dumps({k: v["stars"] for k, v in pool.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return top, len(pool), has_delta


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    global DESC_BUDGET
    if token:
        DESC_BUDGET = 60  # 认证后 core API 5000 次/时，可放开补描述
    DATA.mkdir(exist_ok=True)
    meta_alerts = []

    # ---- skill 榜 ----
    boards = {}
    for board, path in (("trending", "https://www.skills.sh/trending"),
                        ("hot", "https://www.skills.sh/hot")):
        try:
            items = parse_skills_page(http_get(path))[:TOP_N]
            if len(items) < 5:
                meta_alerts.append(f"{board}: 仅解析到 {len(items)} 条，页面结构可能已改版")
            boards[board] = items
        except Exception as e:
            meta_alerts.append(f"{board}: 抓取失败 {e}")
            boards[board] = []
        time.sleep(1.0)

    # ---- 项目榜 ----
    projects, pool_n, has_delta = collect_projects(token)

    # ---- 描述缓存 ----
    desc = load_json(DATA / "desc-cache.json", {})
    skill_repos = [i["repo"] for b in boards.values() for i in b]
    desc = fetch_repo_desc(skill_repos, desc, token)
    (DATA / "desc-cache.json").write_text(json.dumps(desc, ensure_ascii=False, indent=1), encoding="utf-8")
    for b in boards.values():
        for i in b:
            i["desc"] = desc.get(i["repo"], "")

    out = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "skills": boards,
        "projects": projects,
        "meta": {"alerts": meta_alerts, "pool_size": pool_n, "projects_by_delta": has_delta, "sources": [
            "https://www.skills.sh/trending", "https://www.skills.sh/hot",
            "GitHub Search API (stars>500 pushed>1d / created>30d stars>100)"]},
    }
    (DATA / "latest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (DATA / f"{TODAY}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"✅ {TODAY} 采集完成：trending {len(boards['trending'])} | hot {len(boards['hot'])} | "
          f"projects {len(projects)}（池 {pool_n}）| 描述缓存 {len(desc)}")
    for a in meta_alerts:
        print(f"⚠️ {a}")


if __name__ == "__main__":
    main()

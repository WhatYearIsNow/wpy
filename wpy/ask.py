# -*- coding: utf-8 -*-
"""微北洋学习板块智能问答。

用法:
    python -m wpy ask "你的问题"

依赖:
    - wpy 数据库 (data/sitewide.db)
    - 天津大学 AI API 密钥 (.tju_key 文件)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

# 从 wpy 包内导入，不再需要 sys.path.insert
from .config import DB_PATH, BASE_DIR  # noqa: E402

DB = DB_PATH
KEY_FILE = os.path.join(BASE_DIR, ".tju_key")
API_BASE = "https://ai.tju.edu.cn/api/v3"
MODEL = "tju-llm"
BOARD_ID = 3  # 学习新知
TOP_N = 12

# 简称/别名 → 全称（可自行扩充）。用户问"设建"时自动补成"设计与建造"。
ALIASES = {
    "设建": "设计与建造",
}

STOPWORDS = set(
    (
        "的 了 吗 呢 啊 哦 吧 是 在 有 哪些 什么 怎么 怎样 如何 推荐 求 大家 "
        "我 你 他 她 这 那 和 与 及 或 一个 一些 可以 能不能 有没有 请问 一下 "
        "知道 告诉 说说 聊聊 哪 多少 几 吧 呀 哈 嘛 嗯 有哪些 是什么 怎么办 "
        "一下呢 想 问问 请教 各位 同学 现在 目前 请问一下 有没有人 "
        "课程 课 相关 内容 东西 问题 情况 方面 时候 地方 东西 这个 那个 我们 你们"
    ).split()
)


def extract_keywords(q: str) -> tuple[str, list[str]]:
    """返回 (核心短语, [其他双字词])。含别名扩展。"""
    raw = q
    s = re.sub(r"[\s,，。！？、；：“”‘’《》〈〉\(\)\[\]\{\}~!@#\$%\^&\*\-_\+=\|/\\]+", " ", q)
    for sw in sorted(STOPWORDS, key=len, reverse=True):
        if len(sw) >= 2:  # 只删多字停用词；单字(与/和/的)保留，避免割裂短语如"设计与建造"
            s = s.replace(sw, " ")
    runs = []
    for run in s.split():
        run = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", run)
        if len(run) >= 2:
            runs.append(run)
    # 别名扩展：原文里出现别名就补全称
    for alias, full in ALIASES.items():
        if alias in raw and full not in runs:
            runs.append(full)
    runs = [r for r in runs if r not in STOPWORDS]
    core = max(runs, key=len) if runs else ""
    bigrams = set()
    for r in runs:
        if r != core:
            for i in range(len(r) - 1):
                bigrams.add(r[i : i + 2])
    bigrams = {b for b in bigrams if len(b) >= 2 and b not in STOPWORDS and b != core}
    return core, list(bigrams)


def retrieve(core: str, bigrams: list[str]) -> list:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if not core and not bigrams:
        rows = cur.execute(
            """SELECT id, 标题, 正文, 标签, 评论数, 点赞数, 发布时间
               FROM posts WHERE 正文已补=1
               ORDER BY 评论数 DESC LIMIT ?""",
            [TOP_N],
        ).fetchall()
        conn.close()
        return rows
    # 加权打分：标题权重最高、标签次之、正文最低；核心短语再加权
    # 权重: (标题, 标签, 正文)
    kw_list = ([(core, 8, 5, 3)] if core else []) + [(b, 4, 3, 1) for b in bigrams]
    score_terms, where_terms, score_params, where_params = [], [], [], []
    for kw, wt, wtag, wbody in kw_list:
        score_terms.append(
            f"(CASE WHEN 标题 LIKE ? THEN {wt} ELSE 0 END) + "
            f"(CASE WHEN 标签 LIKE ? THEN {wtag} ELSE 0 END) + "
            f"(CASE WHEN 正文 LIKE ? THEN {wbody} ELSE 0 END)"
        )
        score_params += [f"%{kw}%", f"%{kw}%", f"%{kw}%"]
        where_terms.append("(标题 LIKE ? OR 正文 LIKE ? OR 标签 LIKE ?)")
        where_params += [f"%{kw}%"] * 3
    sql = f"""
      SELECT id, 标题, 正文, 标签, 评论数, 点赞数, 发布时间,
             ({" + ".join(score_terms)}) AS score
      FROM posts
      WHERE 正文已补=1
        AND ({" OR ".join(where_terms)})
      ORDER BY score DESC, 评论数 DESC, 发布时间 DESC
      LIMIT ?
    """
    rows = cur.execute(sql, score_params + where_params + [TOP_N]).fetchall()
    conn.close()
    return rows


def fetch_floors(pid: int, limit: int = 12) -> list:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(
        """SELECT 昵称, 内容, 点赞数 FROM floors
           WHERE 帖子ID=? AND length(内容)>0
           ORDER BY 父楼ID ASC, 点赞数 DESC, id ASC LIMIT ?""",
        (pid, limit),
    ).fetchall()
    conn.close()
    return rows


def ask_llm(question: str, posts: list) -> str:
    lines = []
    for i, p in enumerate(posts, 1):
        body = (p["正文"] or "").strip()
        if len(body) > 900:
            body = body[:900] + "……"
        lines.append(
            f"[原帖{i}] MP{p['id']}｜标题：{p['标题']}｜标签：{p['标签'] or ''}｜时间：{p['发布时间'][:10]}\n楼主：{body}\n"
        )
        floors = fetch_floors(p["id"])
        for f in floors:
            fc = (f["内容"] or "").strip().replace("\n", " ")
            if len(fc) > 300:
                fc = fc[:300] + "……"
            lines.append(f"  · 评论({f['昵称']}): {fc}")
        lines.append("")
    context = "\n".join(lines)
    prompt = f"""你是天津大学微北洋论坛的问答助手。请严格依据下面提供的论坛原帖及评论回答用户问题。

要求：
1. 只基于提供的原帖内容作答，不要编造未提及的信息。
2. 用简洁的中文分点回答。
3. 每提到一个原帖里的观点，就在该句后用 MP号标注出处（格式如 MP457060）。
4. 只允许引用下面材料里真实出现过的 MP号，绝对不要编造或猜测不存在的 MP号。
5. 回答末尾单独列出本次参考的所有 MP号清单（去重）。
6. 若原帖里没有相关信息，直接说明"论坛暂未检索到相关讨论"。

【用户问题】
{question}

【论坛原帖】
{context}
"""
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1500,
    }
    try:
        key = load_key()
    except FileNotFoundError:
        print("错误: 未找到 AI API 密钥文件。", file=sys.stderr)
        print(f"请在 {KEY_FILE} 中写入有效的 API Key。", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: 读取 API 密钥失败: {e}", file=sys.stderr)
        sys.exit(1)

    req = urllib.request.Request(
        API_BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        print(f"错误: AI API 请求失败 (HTTP {e.code})", file=sys.stderr)
        try:
            body = e.read().decode("utf-8", "replace")
            print(f"响应: {body[:200]}", file=sys.stderr)
        except Exception:
            pass
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"错误: 无法连接 AI API: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, IndexError) as e:
        print(f"错误: AI API 响应格式异常: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: AI 问答失败: {e}", file=sys.stderr)
        sys.exit(1)


def load_key() -> str:
    """读取 AI API 密钥文件。"""
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def main() -> None:
    """智能问答入口。"""
    q = " ".join(sys.argv[1:]).strip()
    if not q:
        print('用法: python -m wpy ask "你的问题"')
        sys.exit(1)
    core, bigrams = extract_keywords(q)
    posts = retrieve(core, bigrams)
    print(f"(核心词: {core or '无'}，分词: {bigrams or '无'}，检索到 {len(posts)} 帖)\n")
    if not posts:
        print("板块内暂未检索到相关讨论，换个问法试试。")
        sys.exit(0)
    ans = ask_llm(q, posts)
    print(ans)
    print("\n--- 参考原帖 ---")
    for i, p in enumerate(posts, 1):
        print(f"MP{p['id']}  {p['标题']}  (评论{p['评论数']})")

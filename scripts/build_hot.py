#!/usr/bin/env python3
"""生成热榜站 hot.html：新帖榜（日/周/月）+ 总榜。默认按浏览量。

热榜跳转沿用访客在看板的个人会话，不在 HTML 中嵌入共享登录口令。
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wpy.config import DB_PATH, HOT_PATH, HTML_PATH  # noqa: E402

DB = Path(DB_PATH)
OUT = Path(HOT_PATH)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

def q(sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]

def fmt_time(s):
    if not s: return ""
    return s[:16].replace("T", " ")

WINDOWS = {
    "day": ("近24小时", "-1 day"),
    "week": ("近7天", "-7 days"),
    "month": ("近30天", "-30 days"),
}
# 维度顺序：浏览量放第一个作为默认
DIMENSIONS = [
    ("view", "浏览量"),
    ("like", "点赞数"),
    ("comment", "评论数"),
    ("fav", "收藏数"),
]
COL_MAP = {"view": "浏览量", "like": "点赞数", "comment": "评论数", "fav": "收藏数"}

BOARD_MAP = {
    1: "校务专区", 2: "青年湖底", 3: "学习新知", 4: "交友恋爱",
    5: "二次元", 6: "摆摊", 8: "数码科技", 13: "美食专区",
}

# 收集所有需要正文的 post id
needed_ids = set()

def fetch_list(where_sql, order_col, limit=50):
    rows = q(f"""
        SELECT id, 标题, 作者, 分管部门, 板块ID, 点赞数, 评论数, 收藏数, 浏览量, 发布时间
        FROM posts WHERE {where_sql} ORDER BY {order_col} DESC LIMIT {limit}
    """)
    for r in rows:
        r["发布时间"] = fmt_time(r["发布时间"])
        r["板块名"] = BOARD_MAP.get(r["板块ID"], "")
        needed_ids.add(r["id"])
    return rows

new_posts = {}
for wk, (label, delta) in WINDOWS.items():
    new_posts[wk] = {"label": label}
    for dk, _ in DIMENSIONS:
        new_posts[wk][dk] = fetch_list(
            f"replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','{delta}')", COL_MAP[dk], 50)

overall = {}
for dk, _ in DIMENSIONS:
    overall[dk] = fetch_list("1=1", COL_MAP[dk], 100)

# 批量取正文
body_map = {}
if needed_ids:
    ph = ",".join("?" * len(needed_ids))
    rows = q(f"SELECT id, 正文 FROM posts WHERE id IN ({ph})", list(needed_ids))
    for r in rows:
        body_map[str(r["id"])] = (r["正文"] or "")[:3000]  # 截断 3000 字

archaeology = {"status": "collecting", "message": "快照表攒集中，几天后开放"}

data = {
    "newPosts": new_posts,
    "overall": overall,
    "archaeology": archaeology,
    "bodies": body_map,
    "boards": BOARD_MAP,
    "generatedAt": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
}

HTML = r"""<!DOCTYPE html>
<html lang="zh-cn">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="apple-touch-icon" href="apple-touch-icon.png">
  <link rel="manifest" href="manifest.json">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <meta name="apple-mobile-web-app-title" content="热榜">
  <meta name="theme-color" content="#2b7fff">
<title>青年湖 · 热榜</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --primary:#2C7EDF;--primary-pale:#E9EEF5;--bg:#F6F9FD;--card:#fff;
  --line:#EDF1F7;--txt:#1F1F1F;--sub:#979797;--info:#4E4E4E;
  --like:#FF4757;--star:#F5A623;--comment:#2C7EDF;--view:#8E8E93;
  --gold:#FFB800;--silver:#B0B8C4;--bronze:#CD7F32;
  --radius:12px;--shell:min(94vw,680px);
}
body{background:var(--bg);color:var(--txt);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
  padding:0 0 60px;-webkit-font-smoothing:antialiased}
header{background:#fff;position:sticky;top:0;z-index:40;border-bottom:1px solid var(--line)}
.top{width:var(--shell);margin:0 auto;padding:16px 20px 12px}
.brand{font-size:18px;font-weight:700;color:var(--primary);letter-spacing:-.3px}
.brand small{font-size:12px;color:var(--sub);font-weight:400;margin-left:8px}
.tabs{display:flex;gap:6px;margin-top:12px}
.tab{padding:7px 16px;border-radius:20px;font-size:13.5px;cursor:pointer;
  background:var(--bg);color:var(--info);border:0;transition:all .15s;font-weight:500}
.tab:hover{background:var(--primary-pale)}
.tab.on{background:var(--primary);color:#fff}
.subtabs{display:flex;gap:4px;margin-top:10px;align-items:center}
.subtab{padding:5px 14px;border-radius:16px;font-size:12.5px;cursor:pointer;
  background:transparent;color:var(--sub);border:1px solid var(--line);transition:all .15s}
.subtab:hover{border-color:var(--primary);color:var(--primary)}
.subtab.on{background:var(--primary-pale);color:var(--primary);border-color:var(--primary-pale);font-weight:600}
.board-btn{padding:5px 14px;border-radius:16px;font-size:12.5px;cursor:pointer;background:transparent;color:var(--primary);border:1px solid var(--primary-pale);margin:0;
  font-weight:500;transition:all .15s}
.board-dropdown{display:none;position:absolute;top:100%;left:0;margin-top:6px;
  background:#fff;border:1px solid var(--line);border-radius:10px;
  box-shadow:0 8px 24px rgba(0,0,0,.1);padding:6px;min-width:120px;z-index:50}
.board-dropdown.on{display:block}
.board-dropdown .opt{padding:7px 12px;border-radius:6px;font-size:13px;cursor:pointer;color:var(--txt)}
.board-dropdown .opt:hover{background:var(--primary-pale)}
.board-dropdown .opt.on{color:var(--primary);font-weight:600}
.board-wrap{position:relative;flex-shrink:0;display:flex;align-items:center}
.shell{width:var(--shell);margin:18px auto 0;padding:0 4px}
.meta{font-size:12.5px;color:var(--sub);margin:0 0 14px;padding-left:4px}
.card{background:var(--card);border-radius:var(--radius);padding:14px 18px;
  margin-bottom:10px;display:flex;align-items:flex-start;gap:14px;
  transition:all .18s;text-decoration:none;color:inherit;border:1px solid transparent}
.card:hover{box-shadow:0 4px 16px rgba(44,126,223,.08);border-color:var(--primary-pale);transform:translateY(-1px)}
.rank{width:32px;flex-shrink:0;text-align:center;padding-top:2px}
.rank-num{font-size:20px;font-weight:800;color:#C4CDD9;line-height:1;font-variant-numeric:tabular-nums}
.card.r1 .rank-num{color:var(--gold)}
.card.r2 .rank-num{color:var(--silver)}
.card.r3 .rank-num{color:var(--bronze)}
.body{flex:1;min-width:0}
.title{font-size:15px;color:var(--txt);line-height:1.45;font-weight:600;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.row{display:flex;gap:14px;margin-top:6px;font-size:12.5px;color:var(--sub);flex-wrap:wrap;align-items:center}
.stat{display:inline-flex;align-items:center;gap:3px;font-variant-numeric:tabular-nums}
.stat.like{color:var(--like)}
.stat.comment{color:var(--comment)}
.stat.fav{color:var(--star)}
.stat.view{color:var(--view)}
.badge{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11px;
  background:var(--primary-pale);color:var(--primary);font-weight:500}
.empty{text-align:center;padding:60px 20px;color:var(--sub);font-size:14px}
</style>
</head>
<body>
<header>
  <div class="top">
    <div class="brand">青年湖 · 热榜 <small id="gen"></small></div>
    <div class="tabs" id="mainTabs"></div>
    <div class="subtabs">
      <div class="board-wrap">
        <div class="board-btn" id="boardBtn"><span id="boardLabel">全部</span> ▾
          <div class="board-dropdown" id="boardDropdown"></div>
        </div>
      </div>
      <div class="subtabs" id="subTabs"></div>
    </div>
  </div>
</header>
<div class="shell">
  <div class="meta" id="meta"></div>
  <div id="list"></div>
</div>
<script>
const DATA = __DATA__;
const MAIN = [
  {k:"new_day", label:"日榜"},
  {k:"new_week", label:"周榜"},
  {k:"new_month", label:"月榜"},
  {k:"overall", label:"总榜"},
];
const SUBS = {
  new_day:    [{k:"view",label:"浏览"},{k:"like",label:"点赞"},{k:"comment",label:"评论"},{k:"fav",label:"收藏"}],
  new_week:   [{k:"view",label:"浏览"},{k:"like",label:"点赞"},{k:"comment",label:"评论"},{k:"fav",label:"收藏"}],
  new_month:  [{k:"view",label:"浏览"},{k:"like",label:"点赞"},{k:"comment",label:"评论"},{k:"fav",label:"收藏"}],
  overall:    [{k:"view",label:"浏览"},{k:"like",label:"点赞"},{k:"comment",label:"评论"},{k:"fav",label:"收藏"}],
  arch: [],
};
function parseQuery(){
  const qs = new URLSearchParams(location.search);
  return {
    main: MAIN.find(t=>t.k===qs.get("m")) ? qs.get("m") : "new_day",
    sub: qs.get("s") || "view",
    board: parseInt(qs.get("b")) || 0,
  };
}
let cur = parseQuery();

// 初始化 sub 为当前 main 下有效的维度
(function(){
  const subs = SUBS[cur.main];
  if(subs.length && !subs.find(s=>s.k===cur.sub)) cur.sub = subs[0].k;
})();

function updateURL(){
  const params = new URLSearchParams();
  if(cur.main !== "new_day") params.set("m", cur.main);
  if(cur.sub !== "view") params.set("s", cur.sub);
  if(cur.board) params.set("b", cur.board);
  const qs = params.toString();
  const newURL = qs ? location.pathname + "?" + qs : location.pathname;
  history.replaceState(null, "", newURL);
}

// 缓存 API 请求结果，避免同一 board+dim+window 重复请求
const apiCache = {};

function renderMainTabs(){
  document.getElementById("mainTabs").innerHTML = MAIN.map(t=>
    `<button class="tab ${cur.main===t.k?"on":""}" data-k="${t.k}">${t.label}</button>`
  ).join("");
}
function renderSubTabs(){
  document.getElementById("subTabs").innerHTML = SUBS[cur.main].map(t=>
    `<button class="subtab ${cur.sub===t.k?"on":""}" data-k="${t.k}">${t.label}</button>`
  ).join("");
}
function renderBoardDropdown(){
  const boards = [{id:0,name:"全部"}].concat(
    Object.entries(DATA.boards).map(([id,name])=>({id:Number(id),name}))
  );
  document.getElementById("boardDropdown").innerHTML = boards.map(b=>
    `<div class="opt ${cur.board===b.id?"on":""}" data-id="${b.id}">${b.name}</div>`
  ).join("");
  const curName = cur.board ? DATA.boards[cur.board] : "全部";
  document.getElementById("boardLabel").textContent = curName;
}
function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function rowHtml(r, i){
  const rank = i+1;
  const rcls = rank<=3 ? " r"+rank : "";
  const params = new URLSearchParams({postId: r.id});
  const href = "/wpy/?" + params.toString();
  return `<a class="card${rcls}" href="${href}">
    <div class="rank"><div class="rank-num">${rank}</div></div>
    <div class="body">
      <div class="title">${esc(r.标题||"(无标题)")}</div>
      <div class="row">
        <span class="stat view">👁 ${r.浏览量||0}</span>
        <span class="stat like">♥ ${r.点赞数||0}</span>
        <span class="stat comment">💬 ${r.评论数||0}</span>
        <span class="stat fav">★ ${r.收藏数||0}</span>
        ${r.分管部门?`<span class="badge">${esc(r.分管部门)}</span>`:""}
        <span>${esc(r.作者||"")} · ${r.发布时间||""}</span>
      </div>
    </div>
  </a>`;
}
function windowKey(){
  if(cur.main==="overall") return "all";
  return cur.main.replace("new_","");
}
async function render(){
  renderMainTabs(); renderSubTabs(); renderBoardDropdown();
  document.getElementById("gen").textContent = DATA.generatedAt;
  let list = "", meta = "";
  if(cur.main==="arch"){
    list = `<div class="empty">${DATA.archaeology.message}</div>`;
  } else if(cur.board){
    // 分区：走 API 实时查库
    const wk = windowKey();
    const cacheKey = cur.board + "_" + cur.sub + "_" + wk;
    if(!apiCache[cacheKey]){
      apiCache[cacheKey] = fetch("/api/board-ranking?board="+cur.board+"&dim="+cur.sub+"&window="+wk)
        .then(r=>r.json())
        .catch(e=>{ console.error("API 请求失败",e); return {data:[]}; });
    }
    const res = await apiCache[cacheKey];
    const rows = res.data || [];
    const dimLabel = (SUBS[cur.main].find(s=>s.k===cur.sub)||{}).label||"";
    const boardName = res.boardName || DATA.boards[cur.board] || "";
    meta = cur.main==="overall"
      ? `总榜 · ${boardName} · 按${dimLabel} · Top ${rows.length}`
      : `${DATA.newPosts[wk].label}新帖 · ${boardName} · 按${dimLabel} · Top ${rows.length}`;
    list = rows.map((r,i)=>rowHtml(r,i)).join("") || `<div class="empty">该板块暂无帖子</div>`;
  } else {
    // 全部：用静态数据
    const wk = windowKey();
    const block = cur.main==="overall" ? DATA.overall : DATA.newPosts[wk];
    let rows = block[cur.sub]||[];
    const dimLabel = (SUBS[cur.main].find(s=>s.k===cur.sub)||{}).label||"";
    meta = cur.main==="overall"
      ? `总榜 · 全部 · 按${dimLabel} · Top ${rows.length}`
      : `${block.label}新帖 · 全部 · 按${dimLabel} · Top ${rows.length}`;
    list = rows.map((r,i)=>rowHtml(r,i)).join("") || `<div class="empty">暂无帖子</div>`;
  }
  document.getElementById("meta").textContent = meta;
  document.getElementById("list").innerHTML = list;
}
document.getElementById("mainTabs").addEventListener("click", e=>{
  const b = e.target.closest(".tab"); if(!b) return;
  cur.main = b.dataset.k;
  const subs = SUBS[cur.main];
  if(subs.length && !subs.find(s=>s.k===cur.sub)) cur.sub = subs[0].k;
  updateURL(); render();
});
document.getElementById("subTabs").addEventListener("click", e=>{
  const b = e.target.closest(".subtab"); if(!b) return;
  cur.sub = b.dataset.k; updateURL(); render();
});
document.getElementById("boardBtn").addEventListener("click", e=>{
  e.stopPropagation();
  e.preventDefault();
  document.getElementById("boardDropdown").classList.toggle("on");
});
document.getElementById("boardDropdown").addEventListener("click", e=>{
  const o = e.target.closest(".opt"); if(!o) return;
  cur.board = Number(o.dataset.id);
  document.getElementById("boardDropdown").classList.remove("on");
  updateURL(); render();
});
document.addEventListener("click", e=>{
  if(!e.target.closest(".board-wrap"))
    document.getElementById("boardDropdown").classList.remove("on");
});
render();
</script>
</body>
</html>
"""

OUT.write_text(HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
print(f"生成完成: {OUT} ({OUT.stat().st_size//1024} KB)")

# Patch 主站 HTML：支持 URL ?postId=xxx 自动打开帖子
MAIN_HTML = Path(HTML_PATH)
patch_js = """
// 热站跳转：URL 带 postId 自动打开（不压历史栈，返回键直接回热榜）
(function(){
  var m = location.search.match(/[?&]postId=(\\d+)/);
  if(m && typeof NAV !== 'undefined'){
    var pid = Number(m[1]);
    setTimeout(function(){
      try {
        NAV.push({kind:'post', id:pid});
        if(typeof renderTop === 'function') renderTop();
      } catch(e){}
    }, 400);
  }
})();
"""
main_html = MAIN_HTML.read_text(encoding="utf-8")
if "热站跳转" not in main_html:
    main_html = main_html.replace("</script>\n</body>", patch_js + "</script>\n</body>")
    MAIN_HTML.write_text(main_html, encoding="utf-8")
    print("主站 HTML 已 patch：支持 ?postId=xxx 自动打开")
else:
    print("主站 HTML 已 patch 过，跳过")

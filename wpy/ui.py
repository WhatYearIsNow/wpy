# -*- coding: utf-8 -*-
"""看板前端资源与页面生成。

页面是服务版空壳：数据由 board_api / server 实时查询，
不再把 20 万帖内嵌进 HTML（会超过 100MB，浏览器打不开）。

CSS/JS 模板已拆分到 ui_templates/ 目录，便于独立编辑。
"""
import time

# CSS/JS 从 ui_templates 包导入（文件已拆分，此处为兼容层）
from .ui_templates import CSS, JS  # noqa: F401  供外部导入兼容


def js_json(obj):
    import json
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

HTML_SHELL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#ffffff">
<title>微北洋 青年湖底 · 全站帖子看板</title>
<style>__CSS__</style>
</head>
<body class="role-guest">
<div class="pbar" id="pbar"></div>
<header>
  <div class="top">
    <span class="brand" id="home" title="回到主界面（清空筛选）" onclick="goHome()">青年湖底</span>
    <div class="searchwrap">
      <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle>
        <path d="M20 20l-3.5-3.5"></path></svg>
      <input id="q" type="search" placeholder="搜索标题 / 正文 / 作者 / 标签 / MP号 / MPID"
             title="支持：MP号（#MP123456、MP123456 或 App 分享口令/链接）直达帖子；MPID（MPID12345、MPID:12345 或 uid:12345）看该用户全部帖子；纯数字同时按帖子 id / MPID / 正文匹配；引号包住的短语精确匹配">
    </div>
    <button class="btn-filter" id="btnfilter" onclick="togglePanel()">
      <span class="dot"></span>筛选</button>
    <button class="btn-filter user-only" id="btnnew" onclick="openComposer()">&#9998; 发帖</button>
    <span class="rolebadge" id="rolebadge"></span>
  </div>

  <div class="tabwrap">
  <div class="tabbar">
    <button class="chip on" data-tab="all">全部</button>
    <button class="chip" data-tab="ess">精华</button>
    <button class="chip" data-tab="2">湖底</button>
    <button class="chip" data-tab="3">学习</button>
    <button class="chip" data-tab="4">交友</button>
    <button class="chip" data-tab="13">美食</button>
    <button class="chip" data-tab="1">校务</button>
    <button class="chip" data-tab="other">其他</button>
    <span class="divider"></span>
    <span class="switch tiny user-only" id="sw-mine"><i></i>我的</span>
    <span class="switch tiny" id="sw-fav"><i></i>收藏</span>
    <span class="switch tiny" id="sw-follow"><i></i>关注</span>
  </div>
  </div>

  <div class="userbar" id="userbar"></div>

  <div class="panel" id="panel">
    <div class="panel-in">
      <div class="prow">
        <span class="plabel">排序</span>
        <select id="so">
          <option value="new">最新发布</option>
          <option value="vs">浏览最多</option>
          <option value="lk">点赞最多</option>
          <option value="cm">评论最多</option>
          <option value="fv">收藏最多</option>
          <option value="old">最早发布</option>
        </select>
        <span class="plabel">年份</span>
        <select id="yr"><option value="">全部</option></select>
        <button class="btn-clear" onclick="clearFilters()">清除筛选</button>
      </div>
      <div class="prow">
        <span class="plabel">日期</span>
        <input id="d1" type="date" title="起始日期">
        <span class="dash">—</span>
        <input id="d2" type="date" title="结束日期">
        <span class="hint">留空表示不限</span>
      </div>
    </div>
  </div>
</header>
<div class="wrap">
<div class="cols">
  <aside class="hotside" id="hotside">
    <div class="hotcard">
      <div class="hothead">
        <span class="hoticon">&#128293;</span><span class="hotttl">热榜</span>
        <button class="hotrefresh" id="hotrefresh" title="刷新热榜">&#8635;</button>
      </div>
      <div class="hottabs">
        <button class="chipt on" data-w="day">日榜</button>
        <button class="chipt" data-w="week">周榜</button>
        <button class="chipt" data-w="all">总榜</button>
      </div>
      <ol class="hotlist" id="hotlist"></ol>
      <div class="hotnote" id="hotnote" style="display:none"></div>
    </div>
  </aside>
  <div class="colmain">
    <div id="list"></div>
    <button class="loadmore" id="more">加载更多</button>
    <div class="empty" id="empty" style="display:none">没有匹配的帖子</div>
  </div>
</div>
</div>
<button class="backtop" id="backtop" title="回到顶部" aria-label="回到顶部"
  onclick="window.scrollTo({top:0,behavior:'smooth'})">
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
    stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>
</button>
<div class="modal" id="modal"><div id="sheet"></div></div>
<div class="zoom" id="zoom"><img id="zimg" alt=""></div>
<div class="user-pop" id="userpop"></div>
<div class="followpop" id="followpop"></div>
<div class="askbox" id="askbox"></div>
<div class="toast" id="toast"></div>
<script>__JS__</script>
</body>
</html>
"""

TYPE_COLORS = {
    1: "#5B8FF9", 2: "#5AD8A6", 3: "#F6BD16", 4: "#E8684A",
    5: "#6F5EF9", 6: "#6DC8EC", 10: "#FF9D4D", 13: "#269A99",
}
DEFAULT_COLOR = "#979797"


def build_html(out_path, gen_date=None):
    gen_date = gen_date or time.strftime("%Y-%m-%d %H:%M")
    js_s = (JS.replace("__COLORS__", js_json(TYPE_COLORS))
              .replace("__DEFAULT_COLOR__", DEFAULT_COLOR))
    doc = (HTML_SHELL.replace("__CSS__", CSS)
                     .replace("__JS__", js_s)
                     .replace("__NPOST__", "0")
                     .replace("__NCMT__", "0")
                     .replace("__GENDATE__", gen_date))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return len(doc.encode("utf-8"))


def build_from_db(db_path=None, out_path=None, *args, **kwargs):
    """兼容旧入口：只生成服务版空壳，不读库。"""
    from .config import HTML_PATH as _HTML
    path = out_path or _HTML
    size = build_html(path)
    print("已生成 %s：%.1f KB（服务版空壳）" % (path, size / 1024))
    print("请用 python -m wpy serve 或菜单 [5] 打开。")
    return size

# -*- coding: utf-8 -*-
"""热榜排行查询，供 board 与 hot 两个服务的 /api/board-ranking 使用。

board 传 None、"all" 或空串时查全站榜；传数字时按板块过滤（原有行为）。
"""

COL_MAP = {"view": "浏览量", "like": "点赞数", "comment": "评论数", "fav": "收藏数"}
BOARD_MAP = {
    1: "校务专区",
    2: "青年湖底",
    3: "学习新知",
    4: "交友恋爱",
    5: "二次元",
    6: "摆摊",
    8: "数码科技",
    13: "美食专区",
}
WINDOW_SQL = {
    "day": "replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-1 day')",
    "week": "replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-7 days')",
    "month": "replace(substr(发布时间,1,19),'T',' ') > datetime('now','localtime','-30 days')",
}


class RankingError(ValueError):
    """参数不合法，HTTP 层应转成 400。"""


def _fmt_time(s):
    if not s:
        return ""
    return s[:16].replace("T", " ")


def query_ranking(db_path, board=None, dim="view", window="all", limit=50):
    """查热榜，返回 (board_name, rows)。rows 已是可直接 JSON 化的字典列表。"""
    if dim not in COL_MAP:
        raise RankingError("dim 必须是 view/like/comment/fav")

    board_name = "全站"
    board_filter = ""
    if board not in (None, "", "all"):
        if not str(board).isdigit():
            raise RankingError("board 必须是数字或 all")
        board_name = BOARD_MAP.get(int(board), "")
        board_filter = f"板块ID = {int(board)} AND "

    conditions = [board_filter + WINDOW_SQL.get(window, "1=1")]
    where = " AND ".join(c for c in conditions if c) or "1=1"
    # limit 来自查询串，钳到合理区间防止超大查询
    limit = max(1, min(int(limit), 200))

    import sqlite3

    db = sqlite3.connect(str(db_path))
    try:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, 标题, 作者, 分管部门, 板块ID, 点赞数, 评论数, 收藏数, 浏览量, 发布时间 "
            f"FROM posts WHERE {where} ORDER BY {COL_MAP[dim]} DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        db.close()

    result = []
    for r in rows:
        result.append(
            {
                "id": r["id"],
                "标题": r["标题"],
                "作者": r["作者"],
                "分管部门": r["分管部门"],
                "板块ID": r["板块ID"],
                "点赞数": r["点赞数"] or 0,
                "评论数": r["评论数"] or 0,
                "收藏数": r["收藏数"] or 0,
                "浏览量": r["浏览量"] or 0,
                "发布时间": _fmt_time(r["发布时间"]),
            }
        )
    return board_name, result

# -*- coding: utf-8 -*-
"""微北洋「青年湖底」全站抓取包。

兼容入口：
    from wpy.crawler import main, api_get, open_db, run_pipeline, do_list
实现分布在子模块；本文件只做再导出，不保留第二套业务逻辑。
"""

from ..db import (
    FLOOR_COLUMNS,
    OFFICIAL_COLUMNS,
    POST_BASE_NAMES,
    POST_COLUMNS,
    POST_COL_NAMES,
    _int,
    board_display_names,
    collect_image_refs,
    ensure_type_row,
    floor_tuple,
    is_essence,
    iter_floors,
    mark_status,
    open_db,
    post_tuple,
    record_pagination_event,
    reset_incomplete_comments,
    save_posts,
)
from ..http import (
    BadRequest,
    FatalError,
    Fuse,
    FuseTripped,
    LIMITER,
    RateLimiter,
    Throttled,
    api_get,
    get_bytes,
    pace,
    request_json,
    with_retry,
)
from ..runlog import (
    Ticker,
    log,
    mark_finished,
    progress,
    write_run_state,
)
from .body import do_body
from .comment import (
    crawl_one_post,
    diagnose_comments,
    do_comment,
    fetch_floors,
    fetch_official,
)
from .cli_app import build_parser, main
from .essence import crawl_essence, crawl_my_posts
from .export_ops import do_export, do_reopen, do_verify
from .image import collect_images, do_image
from .list import do_list, do_probe, fetch_list
from .pipeline import do_partial, do_resume, do_update, fetch_posts_by_id, run_pipeline
from .shared import (
    build_post_filter,
    crawlable_types,
    map_type_names,
    run_workers,
    usable_types,
    warn_if_only_recommend,
    warn_unknown_types,
)
from .status_view import do_status, read_run_state
from ..auth import get_token  # noqa: F401  兼容再导出

__all__ = [
    "main",
    "run_pipeline",
    "do_list",
    "do_body",
    "do_comment",
    "do_image",
    "do_export",
    "do_status",
    "do_resume",
    "do_update",
    "do_partial",
    "do_probe",
    "do_verify",
    "do_reopen",
    "diagnose_comments",
    "api_get",
    "request_json",
    "with_retry",
    "LIMITER",
    "Fuse",
    "open_db",
    "save_posts",
    "log",
    "get_token",
]

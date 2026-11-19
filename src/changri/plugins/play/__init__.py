"""核心玩法：约会/电话/官约/微信（约会房间）、时间线、我的待回。（论坛、心愿已拆成可开关的官方扩展 plugins/forum.py、plugins/wishes.py）
规则和数据在 core/rooms.py、pool.py、wishes.py、pending.py；这里只管解析指令、回话、通知。
官方机器人建不了群：房间从「群池」里领一个空群，群号由管理员在设置平台填；群池用完就开不成，告诉玩家，不排队。

拆成几个文件（按依赖顺序导入；外面一律从 changri.plugins.play 取，名字和拆开前一样）：
  common.py  公用的：常量、找角色、往个人群 / 房间群推消息、邀请文案。
  finish.py  房间收尾：结束、强结、取消官约、到点提醒、清群、复盘、补录。
  launch.py  发起房间：约会 / 电话 / 踩点 / 微信，发起官约 / 官电，计划官约 / 执行官约。
  joinreq.py  申请加入别人的房间：申请加入 / 加入请求 / 同意加入 / 拒绝加入。
  timeline.py  时间线：查看、删除、修改 / 同意 / 拒绝时间线，我的待回、我的数量、弧长。
  ticks.py  定时任务（过期结束、补群、提前排的官约到点开房……）和 QQ 小号旁听到的房间指令。
  groups.py  备用群池管理（管理员）：设为备用群、转为小号群、放回、取消、查看；机器人进群时记一笔。
  record.py  复盘：官方机器人在房间群里收到的发言。
"""
from .common import (  # noqa: F401
    NEED_ROLE, NOT_ADMIN, SEP, _cmd, _me, _is_admin,
    _names, _resolve, _push, _qq_hint, _push_group, _to_group,
    _group_line, _slot, RP_FORMAT_TIP, _room_text,
    _invite, _why_missed, _missed_line, _announce_in_room,
)
from .finish import (  # noqa: F401
    _finish_room, _leave_group, remove_room, _warn_expiring, NO_GROUP_TEXT, _ensure_clean,
    settle_wish_room, _sweep_cleaning, review_cmd, REVIEW_LIST, _review_line, end_room,
    backfill_cmd, _room_arg, force_end, cancel_official,
)
from .launch import (  # noqa: F401
    start_room, notify_join_request, notify_join_answer, _start_room, private, phone,
    stakeout, wechat, _EMPTY_PLACE, parse_official, _official_fix, _send_official_invites,
    _launch_official, launch_official, OFFICIAL_USAGE, OFFICIAL_CALL_USAGE, official, official_phone,
    _plans, _plan_line, plan_cmd, run_plan,
)
from .joinreq import (  # noqa: F401
    join_cmd, join_list, join_yes, join_no, JOIN_OFF, _answer,
)
from .timeline import (  # noqa: F401
    tl, _timeline_text, del_slot, todo, chg, _when,
    CHG_USAGE, _vote_result, _propose_in_room, _decline, _vote, agree_tl,
    reject_tl, my_count, my_arc, see_arc,
)
from .ticks import (  # noqa: F401
    _tick, _tick_pool_alert, _tick_outbox, _tick_expired, _tick_assign, _defer_waiting_told,
    _report_official_missed, _tick_deferred, _receipt_all_here, _check_presence, _ROOM_CMD, _LEAD_AT,
    _cmd_text, _listen_cmd, _on_listened, _on_join, my_stats,
)
from .groups import (  # noqa: F401
    _track_entered, _added, set_pool, set_backup, _pool_args, _take_over,
    to_qq_cmd, release_pool, unset_pool, show_pool,
)
from .record import (  # noqa: F401
    _record_official,
)

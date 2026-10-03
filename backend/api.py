"""
暴露给前端的 API —— pywebview 会把本类的方法挂载到 window.pywebview.api，
前端以 Promise 形式调用（见 frontend/src/api.ts 的 OcApi 接口定义）。
"""
from __future__ import annotations

import platform

import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

from . import __version__
from .core import paths
from .core.config import config
from .core.monitor import monitor, query_public_ip
from .core.registry import tool_registry as registry
from .core.runner import launch_detached, open_in_explorer

AUTHOR = "HMUG12"
DESCRIPTION = "开源实用工具箱"

# 合法的展示名 ↔ 内部值
_VALID_THEMES = ("light", "dark", "system")


# 接口耗时告警阈值（秒）：超过就写一条 WARN 进运行日志
SLOW_CALL_SECONDS = 1.0


def timed(label: str):
    """给对外接口加耗时统计（只标注已知的耗时接口，不做全量拦截）。

    前端一次点击通常对应一个 API 调用；用户报「点了没反应 / 界面卡住」时，
    日志里能直接看出是哪个接口慢，比人工逐个复现快得多。
    """

    def decorator(func):
        import functools
        import time

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                cost = time.perf_counter() - started
                if cost >= SLOW_CALL_SECONDS:
                    try:
                        from .core.applog import log

                        log(f"慢调用：{label} 用了 {cost:.2f} 秒", "WARN")
                    except Exception:
                        pass

        return wrapper

    return decorator


class Api:
    """前端 ⇄ Python 的边界。所有方法返回值必须是 JSON 可序列化对象。"""

    def __init__(self) -> None:
        self._window: Any = None
        self.tray_available: bool = False
        self._lan_responder: Any = None
        registry.scan()
        monitor.start()

        # 若上次以学生机模式运行，启动后自动恢复连接（老师机模式需手动开启服务）
        try:
            if str(config.get("lan_mode", "single")) == "student":
                from .net.client import client

                client.start()
        except Exception:
            pass

        # 定时任务调度（A 端按计划下发指令，未启用时线程只是空转检查）
        try:
            from .net import scheduler

            scheduler.scheduler.start()
        except Exception:
            pass

    # pywebview 窗口创建后回调注入，用于窗口控制
    def attach_window(self, window: Any) -> None:
        self._window = window

    def attach_reveal(self, func: Any) -> None:
        """注入「首屏就绪后显示窗口」的回调（由 main 提供动画实现）。"""
        self._reveal = func

    def attach_tray(self, tray: Any) -> None:
        """注入托盘实例：需要"弹给用户看"的提示走系统气泡（窗口收在托盘也看得见）。"""
        self._tray = tray

    def _notify(self, title: str, message: str) -> None:
        """优先用系统托盘气泡提示，失败则退回运行日志（绝不静默丢失）。"""
        tray = getattr(self, "_tray", None)
        try:
            if tray is not None and tray.notify(title, message):
                return
        except Exception:
            pass
        try:
            from .core.applog import log

            log(f"{title}：{message}", "WARN")
        except Exception:
            pass

    def frontend_ready(self) -> dict[str, Any]:
        """前端首屏渲染完成 —— 此时才让窗口露面。

        窗口在 main 里是以 hidden 创建的：先加载、后显示，
        用户第一眼看到的就是渲染好的界面（不是白屏，也不是转圈）。
        """
        reveal = getattr(self, "_reveal", None)
        if callable(reveal):
            import threading

            threading.Thread(target=reveal, daemon=True, name="oc-reveal").start()
        return {"ok": True}

    # ══════════════════════════════════════════════════════
    # 元信息
    # ══════════════════════════════════════════════════════

    def get_info(self) -> dict[str, Any]:
        return {
            "name": "OpenClass-Box",
            "version": __version__,
            "author": AUTHOR,
            "description": DESCRIPTION,
            "portable": True,
            "rootDir": str(paths.app_root()),
            "toolDir": str(paths.tools_dir()),
            "pythonVersion": platform.python_version(),
            "platform": platform.system(),
        }

    # ══════════════════════════════════════════════════════
    # 工具管理
    # ══════════════════════════════════════════════════════

    def list_tools(self) -> list[dict[str, Any]]:
        if registry.count() == 0:
            registry.scan()
        return [t.to_dict() for t in registry.list()]

    def refresh_tools(self) -> int:
        registry.scan()
        return registry.count()

    def check_updates(self, force: bool = False) -> list[dict[str, Any]]:
        """检查各集成组件是否有新版本（联网查询，断网返回空列表）。"""
        from .core.updater import check_updates

        return check_updates(force=force)

    def check_url(self, url: str) -> dict[str, Any]:
        """对网址做安全评分，返回 score / level(safe|warn|danger) / reasons。"""
        from .core.url_guard import check_url

        return check_url(url)

    # ══════════════════════════════════════════════════════
    # 安全中心（自动检测：浏览器访问 / 剪贴板网址）
    # ══════════════════════════════════════════════════════

    def security_events(self, limit: int = 120) -> list[dict[str, Any]]:
        """最近的自动检测记录（新的在前）。"""
        from .core.security import events

        return events(limit)

    def security_stats(self) -> dict[str, Any]:
        """检测统计（总数 / 今日 / 今日风险 / 白名单数）。"""
        from .core.security import stats

        return stats()

    def security_clear(self) -> dict[str, Any]:
        """清空检测记录。"""
        from .core.security import clear

        return clear()

    def security_whitelist(self) -> list[str]:
        from .core.security import whitelist

        return whitelist()

    def security_add_whitelist(self, domain: str) -> dict[str, Any]:
        from .core.security import add_whitelist

        return add_whitelist(domain)

    def security_remove_whitelist(self, domain: str) -> dict[str, Any]:
        from .core.security import remove_whitelist

        return remove_whitelist(domain)

    def security_settings(self) -> dict[str, Any]:
        from .core.security import settings

        return settings()

    def set_security_settings(
        self, clipboard: bool | None = None, browser: bool | None = None
    ) -> dict[str, Any]:
        from .core.security import set_settings

        return set_settings(clipboard, browser)

    # ══════════════════════════════════════════════════════
    # 机房协同（局域网老师机 / 学生机）
    # ══════════════════════════════════════════════════════

    def lan_status(self) -> dict[str, Any]:
        """机房协同总状态：模式 + 服务端 + 客户端。"""
        from .net.client import client
        from .net.server import server

        return {
            "mode": str(config.get("lan_mode", "single") or "single"),
            "server": server.status(),
            "client": client.status(),
        }

    def lan_scan(self) -> list[dict[str, Any]]:
        """学生机：搜索局域网内的老师机（3 秒超时）。"""
        from .net.discovery import broadcast_search

        return broadcast_search(timeout=3.0)

    def lan_start_server(self, port: int = 38900) -> dict[str, Any]:
        """老师机：启动服务端，并开启 UDP 发现应答。"""
        import platform as _platform

        from .net.discovery import DiscoveryResponder
        from .net.server import server

        result = server.start(port)
        if result.get("ok"):
            config.set("lan_mode", "teacher")
            if self._lan_responder is None:
                responder = DiscoveryResponder(
                    server.port, _platform.node(), server.teacher_id
                )
                responder.start()
                self._lan_responder = responder
        return result

    def lan_stop_server(self) -> dict[str, Any]:
        from .net.server import server

        result = server.stop()
        if self._lan_responder is not None:
            self._lan_responder.stop()
            self._lan_responder = None
        config.set("lan_mode", "single")
        return result

    def lan_nodes(self) -> list[dict[str, Any]]:
        """老师机：已配对学生机列表（含在线状态与实时指标）。"""
        from .net.server import server

        return server.nodes()

    def lan_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """老师机：操作事件日志。"""
        from .net.server import server

        return server.events(limit)

    def lan_send(
        self, node_ids: list[str], action: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """老师机：向指定设备下发指令。"""
        from .net.server import server

        ids = [str(item) for item in (node_ids or [])]
        return server.send(ids, str(action or ""), payload or {})

    def lan_remove_node(self, node_id: str) -> dict[str, Any]:
        from .net.server import server

        return server.remove_node(str(node_id))

    def lan_reset_code(self) -> str:
        """老师机：重新生成配对码。"""
        from .net.server import server

        return server.reset_code()

    def lan_join(self, server_url: str = "", code: str = "") -> dict[str, Any]:
        """学生机：加入老师机（地址留空则局域网自动发现）。"""
        from .net.client import client

        result = client.start(server_url, code)
        if result.get("ok"):
            config.set("lan_mode", "student")
        return result

    def lan_leave(self) -> dict[str, Any]:
        """学生机：断开与老师机的连接。"""
        from .net.client import client

        result = client.stop()
        config.set("lan_mode", "single")
        return result

    def lan_schedule(self) -> dict[str, Any]:
        """A 端：定时任务计划（每天按时对在线设备下发指令）。"""
        from .net import scheduler

        return scheduler.plan()

    def lan_set_schedule(
        self,
        enabled: bool | None = None,
        time_str: str | None = None,
        action: str | None = None,
        groups: list[str] | None = None,
    ) -> dict[str, Any]:
        """A 端：设置定时任务。

        time_str 形如 "08:00"；groups 留空表示所有分组。
        """
        from .net import scheduler

        plan = dict(scheduler.plan())
        if enabled is not None:
            plan["enabled"] = bool(enabled)
        if time_str is not None:
            value = str(time_str).strip()
            parts = value.split(":")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                return {"ok": False, "message": "时间格式应为 HH:MM"}
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour < 24 and 0 <= minute < 60):
                return {"ok": False, "message": "时间超出范围"}
            plan["time"] = f"{hour:02d}:{minute:02d}"
        if action is not None:
            plan["action"] = str(action).strip()
        if groups is not None:
            plan["groups"] = [str(item) for item in groups]
        plan.pop("lastRun", None)   # 修改计划后允许今天重新触发一次
        config.set("lan_schedule", plan)
        return {"ok": True, "message": "定时计划已保存", "plan": plan}

    def lan_config(self) -> dict[str, Any]:
        """A/B 端身份与网络配置（端口 / 代理 / 服务器地址 / 自启动）。"""
        return {
            "role": str(config.get("lan_role", "") or ""),
            "mode": str(config.get("lan_mode", "single") or "single"),
            "port": int(config.get("lan_port", 38900) or 38900),
            "proxy": str(config.get("lan_proxy", "") or ""),
            "serverUrl": str(config.get("lan_server_url", "") or ""),
            "autoStart": bool(config.get("lan_auto_start", True)),
        }

    def lan_set_config(
        self,
        port: int | None = None,
        proxy: str | None = None,
        server_url: str | None = None,
        auto_start: bool | None = None,
    ) -> dict[str, Any]:
        """更新网络配置。

        代理留空 = 直连（机房局域网推荐）；填写后学生机经由该代理访问
        A 端 —— 配合端口映射 / 内网穿透即可跨网段使用，无需中心服务器。
        """
        if port is not None:
            try:
                config.set("lan_port", int(port))
            except (TypeError, ValueError):
                return {"ok": False, "message": "端口必须是数字"}
        if proxy is not None:
            config.set("lan_proxy", str(proxy).strip())
        if server_url is not None:
            config.set("lan_server_url", str(server_url).strip())
        if auto_start is not None:
            config.set("lan_auto_start", bool(auto_start))

        from .net.client import refresh_opener

        refresh_opener()
        return {"ok": True, "message": "网络配置已保存", "config": self.lan_config()}

    def apply_role(self, role: str) -> dict[str, Any]:
        """按启动角色初始化（安装包快捷方式带 --role=a / --role=b）。

        a    → A 端（服务端）：按自启动开关拉起服务；
        b    → B 端（本体）：恢复为学生机身份，已配对则自动连；
        auto → 沿用上次配置，不干预。
        """
        role = (role or "auto").strip().lower()
        if role not in ("a", "b", "auto"):
            return {"ok": False, "message": f"未知角色：{role}"}
        if role == "auto":
            return {"ok": True, "message": "沿用上次配置"}

        config.set("lan_role", role)
        if role == "a":
            from .net.server import server

            if bool(config.get("lan_auto_start", True)):
                result = server.start(int(config.get("lan_port", 38900) or 38900))
                return {"ok": True, "message": str(result.get("message") or "A 端服务已启动")}
            config.set("lan_mode", "teacher")
            return {"ok": True, "message": "已切换为 A 端（服务未自动启动）"}

        config.set("lan_mode", "student")
        try:
            from .net.client import client

            client.start()
        except Exception:
            pass
        return {"ok": True, "message": "已切换为 B 端"}

    def lan_pick_file(self) -> dict[str, Any]:
        """老师机：弹出文件选择框，返回待下发文件的路径。"""
        if self._window is None:
            return {"ok": False, "path": "", "message": "窗口未就绪"}
        try:
            import webview

            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("所有文件 (*.*)",),
            )
        except Exception as exc:
            return {"ok": False, "path": "", "message": f"打开文件选择框失败：{exc}"}
        if not result:
            return {"ok": False, "path": "", "message": "未选择文件"}
        return {"ok": True, "path": str(result[0]), "message": ""}

    def lan_push_file(self, node_ids: list[str], path: str) -> dict[str, Any]:
        """老师机：把本机文件下发给选中设备。"""
        from .net.server import server

        return server.push_file([str(item) for item in (node_ids or [])], str(path or ""))

    def lan_set_node_meta(
        self, node_id: str, alias: str = "", group: str = ""
    ) -> dict[str, Any]:
        """老师机：设置设备备注名与分组。"""
        from .net.server import server

        return server.set_meta(str(node_id), alias, group)

    def lan_inbox(self) -> list[dict[str, Any]]:
        """老师机：已回收的文件列表（收作业结果）。"""
        from .net.server import server

        return server.inbox_files()

    def lan_open_inbox(self) -> bool:
        """打开接收目录（收作业归档位置）。"""
        from .net.server import inbox_dir

        ok, _ = open_in_explorer(inbox_dir())
        return ok

    def lan_receive_dir(self) -> str:
        """学生机：下发文件的接收目录。"""
        from .net.client import receive_dir

        return str(receive_dir())

    def lan_open_receive_dir(self) -> bool:
        """打开学生机接收目录。"""
        from .net.client import receive_dir

        ok, _ = open_in_explorer(receive_dir())
        return ok

    # ══════════════════════════════════════════════════════
    # 便携与急救盘（U 盘随插随用）
    # ══════════════════════════════════════════════════════

    def portable_status(self) -> dict[str, Any]:
        """便携状态：是否在 U 盘上运行、数据目录位置、可用 U 盘列表。"""
        from .core.portable import portable_status

        return portable_status()

    def list_removable_drives(self) -> list[dict[str, Any]]:
        """列出当前接入的可移动磁盘。"""
        from .core.portable import list_removable_drives

        return list_removable_drives()

    def make_rescue_usb(self, drive: str) -> dict[str, Any]:
        """把精简后的程序复制到 U 盘，制作便携急救盘。"""
        from .core.portable import make_rescue_usb

        return make_rescue_usb(drive)

    # ══════════════════════════════════════════════════════
    # 手机 Web 控制台（局域网零安装）
    # ══════════════════════════════════════════════════════

    def webconsole_status(self) -> dict[str, Any]:
        """手机控制台状态（是否运行 / 地址 / 访问码 / 已登录设备数）。"""
        from .core.webconsole import status

        return status()

    def webconsole_start(self, port: int = 38610) -> dict[str, Any]:
        """启动手机控制台（手机在同一 WiFi 下即可访问）。"""
        from .core.webconsole import start

        return start(port)

    def webconsole_stop(self) -> dict[str, Any]:
        """停止手机控制台。"""
        from .core.webconsole import stop

        return stop()

    def webconsole_regenerate(self) -> dict[str, Any]:
        """更换访问码（已登录的手机全部失效）。"""
        from .core.webconsole import regenerate

        return {"ok": True, "code": regenerate()}

    # ══════════════════════════════════════════════════════
    # 课堂专属工具（投屏 / 触摸 / 教学软件 / 还原环境）
    # ══════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════
    # 设备衰退监测与维护清单
    # ══════════════════════════════════════════════════════

    def health_watch_status(self) -> dict[str, Any]:
        """上次扫描结果（打开界面就能看到，不必每次重扫）。"""
        from .core.health_watch import status

        return status()

    @timed("维护清单扫描")
    def health_watch_scan(self) -> dict[str, Any]:
        """完整扫描：硬盘可靠性 + 异常关机 / 蓝屏 + 温度 → 维护清单。

        只依据可解释的数值给结论，**不做寿命预测**（见模块文档的说明）。
        """
        from .core.health_watch import scan_and_cache

        return scan_and_cache()

    def restore_watch_check(self) -> dict[str, Any]:
        """还原保护状态：装了没有、**是否真的在工作**（服务停止/被禁用会报出来）。

        只读取状态，绝不代还原软件开关保护（见 core/restore_watch.py 的边界说明）。
        """
        from .core.restore_watch import detect

        return detect()

    @timed("课堂检测")
    def classroom_report(self) -> dict[str, Any]:
        """课堂检测汇总：投影拓扑 / 触摸 / 无线投屏 / 教学软件 / 还原环境。"""
        from .core.classroom import report

        return report()

    @timed("课前准备")
    def preflight(self) -> dict[str, Any]:
        """课前准备：一次点击回答"这台机器现在能不能上课"（体检 + 课堂 + 还原保护）。

        结论分三档：可以上课 / 可以上课但有建议 / 建议先处理再上课。
        """
        from .core.preflight import run

        return run()

    def preflight_last(self) -> dict[str, Any]:
        """上次课前准备的结论（首页卡片用）—— 只读缓存，不触发检测。"""
        from .core.preflight import last

        return last()

    @timed("生成诊断报告")
    def diagnostic_report(self) -> dict[str, Any]:
        """统一诊断报告：体检 + 课堂检测 + 维护清单 → 同一形状 + 可直接粘贴的报修文本。

        与「一键体检」的区别：体检只回答"现在能不能上课"；这份报告把所有检测
        汇到一起，并生成给维修人员看的文字 —— 老师报修用的是微信/钉钉，要的
        是能直接粘过去的一段话，不是一个需要对方装工具才能看的文件。

        会真实执行各项检测（几秒到几十秒），由用户点击触发。
        """
        import platform as _platform
        import time as _time

        from .core.diag_result import (
            from_classroom,
            from_health,
            from_watch,
            summarize,
            to_report,
        )

        sections: dict[str, list[dict[str, Any]]] = {}
        try:
            from .core.health import run_checks

            sections["一键体检"] = from_health(run_checks().get("items") or [])
        except Exception:
            sections["一键体检"] = []
        try:
            from .core.classroom import report as _classroom

            sections["课堂检测"] = from_classroom(_classroom().get("items") or [])
        except Exception:
            sections["课堂检测"] = []
        try:
            from .core.health_watch import scan

            sections["维护清单"] = from_watch(scan().get("actions") or [])
        except Exception:
            sections["维护清单"] = []

        everything = [one for group in sections.values() for one in group]
        return {
            "ok": True,
            "generatedAt": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "sections": sections,
            "summary": summarize(everything),
            "report": to_report(
                sections,
                machine=f"{_platform.node()}（{_platform.system()} {_platform.release()}）",
            ),
        }

    def refresh_teaching_apps(self) -> dict[str, Any]:
        """强制重新扫描教学软件（跳过 5 分钟缓存）。"""
        from .core.classroom import teaching_apps

        return teaching_apps(force=True)

    def open_touch_calibration(self) -> dict[str, Any]:
        """打开 Windows 触摸校准工具。"""
        from .core.classroom import open_touch_calibration

        return open_touch_calibration()

    def open_display_switch(self) -> dict[str, Any]:
        """打开投影模式切换面板（等同 Win+P）。"""
        from .core.classroom import open_display_switch

        return open_display_switch()

    # ══════════════════════════════════════════════════════
    # 课堂兼容性知识库（离线条目 + 环境匹配 + 累积分享）
    # ══════════════════════════════════════════════════════

    def kb_stats(self) -> dict[str, Any]:
        """知识库统计（条目数 / 分类 / 版本）。"""
        from .core.kb import stats

        return stats()

    def kb_search(self, keyword: str = "", category: str = "") -> list[dict[str, Any]]:
        """检索知识库。"""
        from .core.kb import search

        return search(keyword, category)

    def kb_match(self) -> dict[str, Any]:
        """与本机环境相关的已知问题（系统版本 + 已装软件 + 硬件能力）。"""
        from .core.kb import match_environment

        return match_environment()

    def kb_add(
        self,
        title: str,
        symptom: str,
        cause: str,
        solution: str,
        category: str = "其他",
        tags: str = "",
    ) -> dict[str, Any]:
        """把一条经验加进本地知识库。"""
        from .core.kb import add_entry

        return add_entry(title, symptom, cause, solution, category, tags)

    def kb_export_issue(self, description: str, category: str = "") -> dict[str, Any]:
        """导出标准化问题报告（含环境快照），便于分享或提 PR。"""
        from .core.kb import export_issue

        return export_issue(description, category)

    def kb_import(self, payload: str) -> dict[str, Any]:
        """导入别处分享的知识库条目（JSON 文本或文件路径）。"""
        from .core.kb import import_entries

        return import_entries(payload)

    # ══════════════════════════════════════════════════════
    # 学期模式与配置模板
    # ══════════════════════════════════════════════════════

    def term_modes(self) -> list[dict[str, Any]]:
        """可用的学期模式（开学 / 考试 / 假期）。"""
        from .core.profiles import MODES

        return MODES

    def run_term_mode(self, mode: str) -> dict[str, Any]:
        """执行一套学期模式检查（开学 / 考试 / 假期）。"""
        from .core.profiles import run_mode

        return run_mode(mode)

    def export_mode_report(self, mode: str) -> dict[str, Any]:
        """执行检查并把报告导出到桌面。"""
        from .core.profiles import export_report

        return export_report(mode)

    def export_profile(self, note: str = "") -> dict[str, Any]:
        """把当前设置导出成配置模板（.ocbprofile）到桌面。"""
        from .core.profiles import export_profile

        return export_profile(note)

    def import_profile(self, path: str) -> dict[str, Any]:
        """导入配置模板并应用。"""
        from .core.profiles import import_profile

        return import_profile(path)

    def market_list(self, refresh: bool = False) -> dict[str, Any]:
        """插件市场列表（JSON 索引 + 本机安装状态）。"""
        from .core.market import list_plugins

        return list_plugins(refresh)

    def market_install(self, plugin_id: str) -> dict[str, Any]:
        """下载并安装插件（https + sha256 校验后解压到 tools/）。"""
        from .core.market import install

        return install(plugin_id)

    def market_import_local(self) -> dict[str, Any]:
        """选择本地 zip 离线导入插件。"""
        if self._window is None:
            return {"ok": False, "message": "窗口未就绪"}
        try:
            import webview

            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("插件包 (*.zip)", "所有文件 (*.*)"),
            )
        except Exception as exc:
            return {"ok": False, "message": f"打开文件选择框失败：{exc}"}
        if not result:
            return {"ok": False, "message": "未选择文件"}
        from .core.market import import_local

        return import_local(str(result[0]))

    def market_uninstall(self, plugin_id: str) -> dict[str, Any]:
        """卸载插件（仅限 tools/ 下带 tool.json 的目录）。"""
        from .core.market import uninstall

        return uninstall(plugin_id)

    def market_index_url(self) -> dict[str, Any]:
        """插件索引地址（可改成自建镜像）。"""
        from .core.market import DEFAULT_INDEX_URL, index_url

        return {"url": index_url(), "default": DEFAULT_INDEX_URL}

    def market_set_index_url(self, url: str) -> dict[str, Any]:
        """修改插件索引地址。"""
        from .core.market import set_index_url

        return set_index_url(url)

    # ══════════════════════════════════════════════════════
    # A 端远程管理服务（配合内网穿透 → Web 管理端）
    # ══════════════════════════════════════════════════════

    def remote_admin_status(self) -> dict[str, Any]:
        """远程管理状态（运行中 / 访问地址 / 是否已设访问码 / 最近操作）。"""
        from .core.remote_admin import status

        return status()

    def remote_admin_start(self, port: int = 0, allow_public: bool | None = None) -> dict[str, Any]:
        from .core.remote_admin import start

        return start(port, allow_public)

    def remote_admin_stop(self) -> dict[str, Any]:
        from .core.remote_admin import stop

        return stop()

    def remote_admin_set_code(self, code: str) -> dict[str, Any]:
        """设置远程管理访问码（至少 8 位，不提供默认码）。"""
        from .core.remote_admin import set_code

        return set_code(code)

    def remote_admin_set_allow_public(self, value: bool) -> dict[str, Any]:
        from .core.remote_admin import set_allow_public

        return set_allow_public(value)

    def remote_admin_audit(self, n: int = 100) -> list[dict[str, Any]]:
        from .core.remote_admin import audit

        return audit(n)

    # ── 内网穿透套件 ──────────────────────────────────────

    def tunnel_status(self) -> dict[str, Any]:
        """穿透状态：方案 / 公网地址 / 输出 / 可执行文件是否就绪。"""
        from .core.tunnel import status

        return status()

    def tunnel_save(self, provider: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        from .core.tunnel import save_settings

        return save_settings(provider, fields)

    def tunnel_start(
        self, port: int = 0, provider: str = "", fields: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """启动穿透（默认把远程管理端口暴露出去）。"""
        from .core.remote_admin import DEFAULT_PORT, status as remote_status
        from .core.tunnel import start

        target = int(port or 0)
        if not target:
            target = int((remote_status() or {}).get("port") or DEFAULT_PORT)
        return start(target, provider, fields)

    def tunnel_stop(self) -> dict[str, Any]:
        from .core.tunnel import stop

        return stop()

    def pick_profile_file(self) -> dict[str, Any]:
        """弹出文件选择框选择 .ocbprofile。"""
        if self._window is None:
            return {"ok": False, "path": "", "message": "窗口未就绪"}
        try:
            import webview

            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("配置模板 (*.ocbprofile)", "所有文件 (*.*)"),
            )
        except Exception as exc:
            return {"ok": False, "path": "", "message": f"打开文件选择框失败：{exc}"}
        if not result:
            return {"ok": False, "path": "", "message": "未选择文件"}
        return {"ok": True, "path": str(result[0]), "message": ""}

    def url_alerts(self) -> list[dict[str, Any]]:
        """取出剪贴板监听产生的风险网址告警（取出即清空）。"""
        from .core.url_watch import alerts

        return alerts()

    def search_music(self, keyword: str = "") -> list[dict[str, Any]]:
        """搜索本地音乐库（空关键词返回全部，最多 200 条）。"""
        from .core.music import search

        return search(keyword)

    def music_url(self, path: str) -> str:
        """把本地音频路径转成前端可直接播放的 URL。"""
        from .core.music import media_url

        return media_url(path)

    def search_music_online(self, keyword: str, platform: str = "netease") -> list[dict[str, Any]]:
        """在线搜索音频（当前支持网易云）。"""
        from .core.music import search_online

        return search_online(keyword, platform)

    def fetch_music(self, song_id: str, platform: str = "netease") -> str:
        """把在线音频拉取到本地，返回本地路径（失败返回空串）。"""
        from .core.music import fetch_online

        return fetch_online(song_id, platform)

    def list_wallpapers(self, directory: str = "") -> dict[str, Any]:
        """列出可用壁纸（图片 / 动图 / 视频，含导入目录）。"""
        from .core.wallpaper import list_wallpapers

        return list_wallpapers(directory)

    def set_wallpaper(
        self, path: str, style: str = "fill", scale: int = 100
    ) -> dict[str, Any]:
        """设置静态图片壁纸（style 控制位置/大小，scale 为屏幕百分比）。"""
        from .core.wallpaper import set_wallpaper

        return set_wallpaper(path, style, scale)

    def random_wallpaper(self, directory: str = "") -> dict[str, Any]:
        """从目录随机更换壁纸。"""
        from .core.wallpaper import random_wallpaper

        return random_wallpaper(directory)

    def import_wallpaper(self, source: str) -> dict[str, Any]:
        """把外部壁纸文件导入程序数据目录。"""
        from .core.wallpaper import import_file

        return import_file(source)

    def pick_wallpaper_file(self) -> dict[str, Any]:
        """弹出文件选择框，返回用户选中的壁纸路径。"""
        if self._window is None:
            return {"ok": False, "path": "", "message": "窗口未就绪"}
        try:
            import webview

            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=(
                    "壁纸文件 (*.jpg;*.jpeg;*.png;*.bmp;*.webp;*.gif;*.mp4;*.webm;*.mkv;*.avi)",
                    "所有文件 (*.*)",
                ),
            )
        except Exception as exc:
            return {"ok": False, "path": "", "message": f"打开文件选择框失败：{exc}"}
        if not result:
            return {"ok": False, "path": "", "message": "未选择文件"}
        return {"ok": True, "path": str(result[0]), "message": ""}

    def set_dynamic_wallpaper(self, path: str, muted: bool = True) -> dict[str, Any]:
        """设置 GIF / 视频动态壁纸（需要 mpv）。"""
        from .core.wallpaper import set_dynamic

        return set_dynamic(path, muted)

    def stop_dynamic_wallpaper(self) -> dict[str, Any]:
        """停止动态壁纸。"""
        from .core.wallpaper import stop_dynamic

        return stop_dynamic()

    def dynamic_wallpaper_status(self) -> dict[str, Any]:
        """动态壁纸运行状态。"""
        from .core.wallpaper import dynamic_status

        return dynamic_status()

    def current_wallpaper(self) -> str:
        """当前桌面壁纸路径。"""
        from .core.wallpaper import current_wallpaper

        return current_wallpaper()

    @timed("一键体检")
    def run_health_checks(self) -> dict[str, Any]:
        """一键体检：网络 / 声音 / 显示 / 磁盘 / 内存（全部离线）。"""
        from .core.health import run_checks

        return run_checks()

    def list_repairs(self) -> list[dict[str, Any]]:
        """列出可用的修复动作。"""
        from .core.repair import list_repairs

        return list_repairs()

    def run_repair(self, key: str) -> dict[str, Any]:
        """执行一个修复动作（需要管理员的会弹 UAC 确认）。"""
        from .core.applog import log
        from .core.repair import run_repair

        result = run_repair(key)
        log(
            f"执行修复：{key}",
            "INFO" if result.get("ok") else "WARN",
            result=str(result.get("message", ""))[:160],
        )
        return result

    def export_report(self) -> dict[str, Any]:
        """导出报修信息报告到桌面，返回 {ok, path, content}。"""
        from .core.report import export

        return export()

    @timed("磁盘清理分析")
    def analyze_cleanup(self) -> dict[str, Any]:
        """统计可清理项及真实占用。"""
        from .core.cleanup import analyze

        return analyze()

    def run_cleanup(self, keys: list[str]) -> dict[str, Any]:
        """执行清理，返回真实释放量。"""
        from .core.cleanup import run

        return run(keys)

    def run_netdiag(self) -> dict[str, Any]:
        """网络分步诊断（本机 / 网关 / DNS / 外网）。"""
        from .core.netdiag import run_diagnostics

        return run_diagnostics()

    def list_packages(self, directory: str = "") -> dict[str, Any]:
        """列出离线软件目录里的安装包。"""
        from .core.software import list_packages

        return list_packages(directory)

    def install_package(self, path: str) -> dict[str, Any]:
        """启动一个安装包（交给系统安装向导）。"""
        from .core.software import install

        return install(path)

    def list_restore_points(self) -> dict[str, Any]:
        """列出系统还原点（只读）。"""
        from .core.restore import list_points

        return list_points()

    def create_restore_point(self, description: str = "") -> dict[str, Any]:
        """创建系统还原点（需要管理员确认）。"""
        from .core.restore import create_point

        return create_point(description)

    @timed("生成诊断包")
    def export_diagnostics(self) -> dict[str, Any]:
        """生成诊断包（系统信息 + 体检 + 事件日志）到桌面。"""
        from .core.applog import log
        from .core.logs import export

        result = export()
        log("生成诊断包", "INFO" if result.get("ok") else "WARN", path=result.get("path", ""))
        return result

    def app_log_tail(self, lines: int = 200) -> dict[str, Any]:
        """最近的运行日志（异常与关键操作，用于反馈问题）。"""
        from .core.applog import stats, tail

        return {"text": tail(lines), **stats()}

    def app_log_clear(self) -> dict[str, Any]:
        """清理运行日志。"""
        from .core.applog import clear

        return clear()

    def open_log_folder(self) -> bool:
        """打开日志目录。"""
        from .core.applog import log_dir

        ok, _ = open_in_explorer(log_dir())
        return ok

    def list_processes(self, limit: int = 40) -> dict[str, Any]:
        """按内存占用列出进程。"""
        from .core.procs import list_processes

        return list_processes(limit)

    def kill_process(self, pid: int) -> dict[str, Any]:
        """结束指定进程（系统关键进程会被拒绝）。"""
        from .core.procs import kill_process

        return kill_process(pid)

    def list_services(self, limit: int = 150) -> dict[str, Any]:
        """列出 Windows 服务。"""
        from .core.procs import list_services

        return list_services(limit)

    def list_startup(self) -> dict[str, Any]:
        """列出开机启动项。"""
        from .core.procs import list_startup

        return list_startup()

    @timed("硬件详情")
    def get_hardware_detail(self, quick: bool = False) -> dict[str, Any]:
        """详细硬件信息（CPU/显卡/内存/硬盘/主板/温度），全部本机实测。

        quick=True 立即返回秒级快照；完整数据随后台采集就绪（fullReady）。
        """
        from .core import hardware_detail

        return hardware_detail.collect(quick=quick)

    def check_self_update(self) -> dict[str, Any]:
        """检测本软件自身是否有新版本（GitHub Releases）。"""
        from .core.updater import check_self_update

        return check_self_update()

    def get_data_dir(self) -> str:
        """配置与运行时数据的实际存放目录（安装到 Program Files 时会回退到用户目录）。"""
        return str(paths.config_dir())

    def get_storage_info(self) -> dict[str, Any]:
        """数据存放位置详情（便携 / 安装模式、可写性、是否迁移过旧配置）。"""
        from .core.paths import IS_FROZEN, app_root, migration_note, portable_mode

        current = paths.config_dir()
        return {
            "dataDir": str(current),
            "appRoot": str(app_root()),
            "configFile": str(paths.config_file()),
            "portable": portable_mode(),
            "frozen": bool(IS_FROZEN),
            "writable": paths.is_writable(current),
            "migratedFrom": migration_note(),
        }

    def report_frontend_error(self, message: str) -> dict[str, Any]:
        """前端页面异常上报（错误边界调用），写入运行日志便于回查。"""
        from .core.applog import log

        log(f"前端页面异常：{str(message)[:800]}", "ERROR")
        return {"ok": True}

    def get_startup_mode(self) -> str:
        """启动时的窗口行为：window = 显示界面（默认），silent = 静默启动到托盘。"""
        return str(config.get("startup_mode", "window"))

    def set_startup_mode(self, mode: str) -> dict[str, Any]:
        """设置启动行为（下次启动生效）。"""
        value = "silent" if str(mode).lower() == "silent" else "window"
        ok = config.set("startup_mode", value)
        return {
            "ok": ok,
            "mode": value,
            "message": "已保存（下次启动生效）" if ok else "保存失败（数据目录不可写）",
        }

    # ── 渲染模式（GPU 合成）────────────────────────────────

    def get_render_mode(self) -> dict[str, Any]:
        """渲染模式：safe = 关闭 GPU 合成（避免黑屏，默认）；gpu = 启用（动画更顺）。"""
        mode = str(config.get("render_mode", "safe") or "safe").lower()
        if mode not in ("safe", "gpu"):
            mode = "safe"
        return {
            "mode": mode,
            "note": "改为「性能优先」后需重启程序生效；若出现界面黑屏请切回「兼容优先」",
        }

    def set_render_mode(self, mode: str) -> dict[str, Any]:
        value = "gpu" if str(mode).lower() == "gpu" else "safe"
        ok = bool(config.set("render_mode", value))
        return {
            "ok": ok,
            "mode": value,
            "message": (
                "已保存（下次启动生效）：渲染会更顺滑" if value == "gpu" else "已保存（下次启动生效）：优先保证不黑屏"
            )
            if ok
            else "保存失败（数据目录不可写）",
        }

    def get_startup_animation(self) -> bool:
        """启动时是否播放「从屏幕底部滑入」的窗口动画（默认开启）。"""
        return bool(config.get("startup_animation", True))

    def set_startup_animation(self, value: bool) -> bool:
        return bool(config.set("startup_animation", bool(value)))

    def webconsole_firewall(self) -> dict[str, Any]:
        """局域网放行状态（手机控制台 / 临时传输 / 机房协同 / 发现端口）。"""
        from .core.firewall import status

        return status()

    def firewall_status(self) -> dict[str, Any]:
        """统一的局域网放行状态。"""
        from .core.firewall import status

        return status()

    def firewall_allow(self) -> dict[str, Any]:
        """一次放行全部需要的端口（管理员权限，弹 UAC）。

        执行后会**复核规则是否真的加上**再返回结果 —— 旧版只要提权进程起得来
        就报成功，用户在 UAC 上点"否"也显示成功，于是"点了放行还是连不上"。
        """
        from .core.firewall import allow

        return allow()

    def firewall_set_private(self) -> dict[str, Any]:
        """把当前网络改为「专用」—— 教室网络常被 Windows 识别成"公用"，
        而放行规则只对专用/域网络生效（这是"放行了却还连不上"的常见原因）。"""
        from .core.firewall import set_private

        return set_private()

    def firewall_revoke(self) -> dict[str, Any]:
        """撤销放行规则。"""
        from .core.firewall import revoke

        return revoke()

    def webconsole_allow_firewall(self) -> dict[str, Any]:
        """一键放行防火墙（需要管理员权限，会弹 UAC 确认）。"""
        from .core.webconsole import allow_firewall

        return allow_firewall()

    def power_control_status(self) -> dict[str, Any]:
        """远程电源控制状态：开关 + 支持的动作 + 延迟范围（不执行任何操作）。"""
        from .core.power import (
            DEFAULT_DELAY,
            MAX_DELAY,
            MIN_DELAY,
            allowed,
            supported_actions,
        )

        return {
            "allowed": allowed(),
            "actions": supported_actions(),
            "minDelay": MIN_DELAY,
            "maxDelay": MAX_DELAY,
            "defaultDelay": DEFAULT_DELAY,
        }

    def set_allow_power(self, value: bool) -> dict[str, Any]:
        """开启/关闭「允许远程电源控制」（默认关闭）。"""
        from .core.power import set_allowed

        return set_allowed(value)

    # ══════════════════════════════════════════════════════
    # 临时聊天传输（局域网平等会话，用完即走）
    # ══════════════════════════════════════════════════════
    # 局域网临时文件传输
    # ══════════════════════════════════════════════════════
    #
    # 2026-10 起并入手机控制台：同一个端口（38610）、同一个访问码。
    # 原来这里是「临时聊天传输」——独立的 38620 服务 + 独立的房间码，
    # 现在两码合一，聊天功能从界面移除（后端接口保留）。

    def transfer_state(self) -> dict[str, Any]:
        """传输状态：是否开启接收、已收文件、占用与配额、接收目录。"""
        from .core.chat import transfer_state

        return transfer_state()

    def transfer_set_accepting(self, enabled: bool) -> dict[str, Any]:
        """开启 / 关闭本机接收。

        「开房间」这一步没有了：手机和其他电脑用**访问码**就能连，
        这里只决定"我现在愿不愿意收"。
        """
        from .core.chat import set_accepting

        return set_accepting(enabled)

    def chat_state(self) -> dict[str, Any]:
        """旧接口：等价于 transfer_state（保留以兼容已发布的前端）。"""
        return self.transfer_state()

    def chat_host(
        self, nickname: str = "我", room_name: str = "", password: str = ""
    ) -> dict[str, Any]:
        """旧接口：等价于「开启接收」（不再另起 38620 服务）。"""
        from .core.chat import host_room

        return host_room(nickname, room_name, password)

    def chat_join(
        self, host: str, code: str, nickname: str = "同事", password: str = ""
    ) -> dict[str, Any]:
        """连接另一台机器（电脑 ↔ 电脑）。``code`` 为对方的**访问码**。"""
        from .core.chat import join_room

        return join_room(host, code, nickname, password)

    def chat_send_text(self, text: str) -> dict[str, Any]:
        """发送一条文字消息。界面已不再暴露此功能，后端保留以便恢复。"""
        from .core.chat import send_text

        return send_text(text)

    def chat_send_file(self, path: str) -> dict[str, Any]:
        """发送一个文件。"""
        from .core.chat import send_file

        return send_file(path)

    def chat_pick_file(self) -> dict[str, Any]:
        """弹出文件选择框（选要发送的文件）。"""
        if self._window is None:
            return {"ok": False, "path": "", "message": "窗口未就绪"}
        try:
            import webview

            result = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
        except Exception as exc:
            return {"ok": False, "path": "", "message": f"打开文件选择框失败：{exc}"}
        if not result:
            return {"ok": False, "path": "", "message": "未选择文件"}
        return {"ok": True, "path": str(result[0]), "message": ""}

    def chat_save_file(self, file_id: str, name: str) -> dict[str, Any]:
        """把别人发的文件下载到本机。"""
        from .core.chat import save_file

        return save_file(file_id, name)

    def chat_leave(self, clear_files: bool = False) -> dict[str, Any]:
        """断开连接 / 关闭接收（可选择清空已收文件）。"""
        from .core.chat import leave

        return leave(clear_files)

    def chat_clear_received(self) -> dict[str, Any]:
        """清空传输收到的文件。"""
        from .core.chat import clear_received

        return clear_received()

    # ══════════════════════════════════════════════════════
    # 本机定时任务（命令 / 脚本 / 程序）
    # ══════════════════════════════════════════════════════

    def tasks_state(self) -> dict[str, Any]:
        """定时任务总览：总开关、任务列表、下次执行、最近记录。"""
        from .core import tasks

        data = tasks.state()
        return {
            "enabled": bool(data.get("enabled")),
            "items": data.get("items", []),
            "overview": tasks.runner.next_runs(),
            "logs": tasks.logs(30),
            "kinds": tasks.KINDS,
            "triggers": tasks.TRIGGERS,
            "weekdays": tasks.WEEKDAYS,
            "enabledCount": tasks.enabled_count(),
        }

    def tasks_set_enabled(self, value: bool) -> dict[str, Any]:
        """定时任务总开关（默认关闭，开启后任务才会真正执行）。"""
        from .core.tasks import set_enabled

        return set_enabled(value)

    def tasks_add(
        self,
        name: str,
        kind: str,
        target: str,
        args: str = "",
        workdir: str = "",
        trigger_type: str = "daily",
        run_time: str = "08:00",
        weekdays: str = "",
        once_at: str = "",
        boot_delay: int = 60,
    ) -> dict[str, Any]:
        """新增一个定时任务。"""
        from .core.tasks import add

        days: list[int] = []
        for piece in str(weekdays or "").replace("，", ",").split(","):
            piece = piece.strip()
            if piece.isdigit():
                days.append(int(piece))
        return add(
            name,
            kind,
            target,
            args,
            workdir,
            trigger_type,
            run_time,
            days,
            once_at,
            boot_delay,
        )

    def tasks_update(self, item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """修改任务（启用停用 / 改名 / 改时间等）。"""
        from .core.tasks import update

        return update(item_id, patch)

    def tasks_remove(self, item_id: str) -> dict[str, Any]:
        from .core.tasks import remove

        return remove(item_id)

    def tasks_run_now(self, item_id: str) -> dict[str, Any]:
        """立即执行一次（不等调度）。"""
        from .core.tasks import run_now

        return run_now(item_id)

    def tasks_pick_target(self) -> dict[str, Any]:
        """弹出文件选择框（选要执行的脚本或程序）。"""
        if self._window is None:
            return {"ok": False, "path": "", "message": "窗口未就绪"}
        try:
            import webview

            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=(
                    "可执行与脚本 (*.exe;*.bat;*.cmd;*.ps1;*.vbs)",
                    "所有文件 (*.*)",
                ),
            )
        except Exception as exc:
            return {"ok": False, "path": "", "message": f"打开文件选择框失败：{exc}"}
        if not result:
            return {"ok": False, "path": "", "message": "未选择文件"}
        return {"ok": True, "path": str(result[0]), "message": ""}

    # ══════════════════════════════════════════════════════
    # 安全扩展：篡改修复 / USB 防护 / 自启动 / 弹窗 / 高占用
    # ══════════════════════════════════════════════════════

    def guard_overview(self) -> dict[str, Any]:
        """安全扩展总览（五类检查一次拉齐）。"""
        from .core.guard import overview

        return overview()

    def guard_scan_hijack(self) -> dict[str, Any]:
        """扫描浏览器篡改（快捷方式尾巴 / hosts / 主页）。"""
        from .core.guard import scan_hijack

        return scan_hijack()

    def guard_fix_hosts(self, lines: list[int]) -> dict[str, Any]:
        """注释掉 hosts 中的可疑行（先备份）。"""
        from .core.guard import fix_hosts

        return fix_hosts([int(i) for i in (lines or [])])

    def guard_delete_shortcut(self, path: str) -> dict[str, Any]:
        """删除被加尾巴的快捷方式（先备份）。"""
        from .core.guard import delete_shortcut

        return delete_shortcut(path)

    def guard_usb_status(self) -> dict[str, Any]:
        """USB 存储策略状态。"""
        from .core.guard import usb_status

        return usb_status()

    def guard_set_usb(
        self, storage_enabled: bool | None = None, read_only: bool | None = None
    ) -> dict[str, Any]:
        """切换 USB 存储 / 只读策略（需要管理员）。"""
        from .core.guard import set_usb_policy

        return set_usb_policy(storage_enabled, read_only)

    def guard_startup(self) -> list[dict[str, Any]]:
        """开机自启项列表。"""
        from .core.guard import startup_items

        return startup_items()

    def guard_disable_startup(self, item_id: str) -> dict[str, Any]:
        """停用自启项（原值备份，可恢复）。"""
        from .core.guard import disable_startup

        return disable_startup(item_id)

    def guard_enable_startup(self, item_id: str) -> dict[str, Any]:
        """恢复被停用的自启项。"""
        from .core.guard import enable_startup

        return enable_startup(item_id)

    def guard_popup_scan(self) -> dict[str, Any]:
        """扫描按规则命中的推广/弹窗进程。"""
        from .core.guard import popup_scan

        return popup_scan()

    def guard_popup_kill(self, pid: int) -> dict[str, Any]:
        """结束命中的弹窗进程。"""
        from .core.guard import popup_kill

        return popup_kill(pid)

    def guard_high_usage(
        self, cpu_threshold: float | None = None, mem_mb: int | None = None
    ) -> dict[str, Any]:
        """找出异常高占用的进程（只读）。"""
        from .core.guard import high_usage

        return high_usage(cpu_threshold, mem_mb)

    def guard_kill_high(self, pid: int) -> dict[str, Any]:
        """结束高占用进程。"""
        from .core.guard import kill_high_usage

        return kill_high_usage(pid)

    def guard_settings(self) -> dict[str, Any]:
        """守护开关（弹窗拦截 / 高占用提醒）。"""
        from .core.guard import guard_settings

        return guard_settings()

    def set_guard_settings(
        self,
        popup_guard: bool | None = None,
        high_usage_guard: bool | None = None,
        cpu_threshold: float | None = None,
        mem_threshold_mb: int | None = None,
    ) -> dict[str, Any]:
        from .core.guard import set_guard_settings

        return set_guard_settings(popup_guard, high_usage_guard, cpu_threshold, mem_threshold_mb)

    def guard_killed_log(self) -> list[dict[str, Any]]:
        """守护自动拦截记录。"""
        from .core.guard import watchdog

        return watchdog.killed()

    def check_url_deep(self, url: str) -> dict[str, Any]:
        """深度检测一个网址（联网校验页面与域名年龄，较慢）。"""
        from .core.url_guard import check_url

        return check_url(url, deep=True, use_cache=False)

    def clear_url_cache(self) -> dict[str, Any]:
        """清空网址检测缓存。"""
        from .core.url_guard import clear_cache

        return {"ok": True, "cleared": clear_cache()}

    # ══════════════════════════════════════════════════════
    # 密码保护（关键页面防误改）
    # ══════════════════════════════════════════════════════

    def passcode_status(self) -> dict[str, Any]:
        """密码保护状态（是否启用 / 保护哪些页面 / 是否已解锁）。"""
        from .core.passcode import status

        return status()

    def passcode_check(self, page: str) -> dict[str, Any]:
        """进入某个页面前问一句：需要密码吗？"""
        from .core.passcode import needs_unlock

        return {"page": page, "need": needs_unlock(str(page or ""))}

    def passcode_verify(self, code: str) -> dict[str, Any]:
        """校验密码。"""
        from .core.passcode import verify

        return verify(str(code or ""))

    def passcode_set(
        self, current: str, new_code: str, protected: list[str] | None = None
    ) -> dict[str, Any]:
        """设置 / 修改密码（已有密码时需提供当前密码）。"""
        from .core.passcode import set_passcode

        pages = [str(p) for p in protected] if isinstance(protected, list) else None
        return set_passcode(str(current or ""), str(new_code or ""), pages)

    def passcode_set_protected(self, pages: list[str]) -> dict[str, Any]:
        """调整受保护页面。"""
        from .core.passcode import set_protected

        return set_protected([str(p) for p in (pages or [])])

    def passcode_lock(self) -> dict[str, Any]:
        """立刻重新上锁。"""
        from .core.passcode import lock

        return lock()

    def passcode_clear(self, current: str) -> dict[str, Any]:
        """关闭密码保护。"""
        from .core.passcode import clear

        return clear(str(current or ""))

    # ══════════════════════════════════════════════════════
    # 还原点：状态与回执
    # ══════════════════════════════════════════════════════

    def restore_protection(self) -> dict[str, Any]:
        """系统保护是否开启（创建还原点的前提）。"""
        from .core.restore import protection_enabled

        value = protection_enabled()
        return {
            "known": value is not None,
            "enabled": value,
            "message": ""
            if value is not False
            else "系统保护未开启：需先在「此电脑 → 属性 → 系统保护」里给系统盘开启",
        }

    def restore_last_result(self) -> dict[str, Any]:
        """查看上一次（提权）创建还原点的回执。"""
        from .core.restore import last_result

        return last_result()

    def get_close_to_tray(self) -> bool:
        """关闭窗口时是否最小化到托盘（默认开启）。"""
        return bool(config.get("close_to_tray", True))

    def set_close_to_tray(self, value: bool) -> bool:
        return bool(config.set("close_to_tray", bool(value)))

    # ── 外观自定义（配色 / 圆角 / 字号 / 毛玻璃）────────────

    def get_appearance(self) -> dict[str, Any]:
        """界面外观偏好（前端把它映射成 .oc-root 上的 data-* 属性）。"""
        return {
            "accent": str(config.get("appearance_accent", "default") or "default"),
            "radius": str(config.get("appearance_radius", "standard") or "standard"),
            "font": str(config.get("appearance_font", "standard") or "standard"),
            "glass": bool(config.get("appearance_glass", False)),
        }

    def set_appearance(
        self,
        accent: str | None = None,
        radius: str | None = None,
        font: str | None = None,
        glass: bool | None = None,
    ) -> dict[str, Any]:
        """保存外观偏好（每一项独立可选，如实返回落盘结果）。"""
        ok = True
        if accent is not None:
            ok = bool(config.set("appearance_accent", str(accent))) and ok
        if radius is not None:
            ok = bool(config.set("appearance_radius", str(radius))) and ok
        if font is not None:
            ok = bool(config.set("appearance_font", str(font))) and ok
        if glass is not None:
            ok = bool(config.set("appearance_glass", bool(glass))) and ok
        return {"ok": ok, "appearance": self.get_appearance()}

    def launch_tool(self, tool_id: str, file_path: str | None = None) -> dict[str, Any]:
        spec = registry.get(tool_id)
        if spec is None:
            return {"ok": False, "message": f"未找到工具：{tool_id}"}

        entry = spec.entry_path
        # 应用桥接类工具：每次启动都重新定位，支持使用期间安装/放置便携版
        if spec.app:
            from .core.app_locator import find_app

            exe = find_app(spec.app)
            if exe is None and spec.app == "openoffice":
                # 随包便携版在中文路径下无法运行：点击启动时按需迁移到英文目录
                from .core.app_locator import ensure_openoffice_portable

                exe, note = ensure_openoffice_portable()
                if exe is None and note:
                    spec.available = False
                    spec.reason = note
            if exe:
                entry = exe
                spec.available = True
                spec.reason = None
            else:
                spec.available = False
                if not spec.reason:
                    spec.reason = f"未检测到 {spec.app}，请安装后重试或在设置中指定路径"

        if not spec.available:
            return {"ok": False, "message": spec.reason or "工具当前不可用"}

        args = list(spec.args)
        if file_path:
            if any("{file}" in a for a in args):
                args = [a.replace("{file}", file_path) for a in args]
            else:
                args.append(file_path)

        ok, message = launch_detached(entry, args, spec.admin)
        if ok:
            return {"ok": True, "message": f"已启动「{spec.name}」"}
        return {"ok": False, "message": message}

    def open_file_with(self, path: str) -> dict[str, Any]:
        """按扩展名路由到集成工具并打开该文件（右键「打开方式」后端）。

        「双击文件没反应」是最容易被当成 bug 的体验问题：右键打开方式时窗口
        通常不在前台，光返回一个 message 用户根本看不到。所以失败（以及成功）
        都额外弹一次系统托盘提示，确保有反馈。

        失败要分清两种，因为下一步完全不同：
          · 这种文件类型不支持 → 没什么可做的（直接说类型）；
          · 支持但本机没装对应套件 → **去「工具」页装一个就行**（告诉他装哪个）。
        """
        from .core.app_locator import route_candidates, route_file

        tool_id = route_file(path)
        if not tool_id:
            suffix = Path(path).suffix.lower() or "（无扩展名）"
            candidates = route_candidates(path)
            if candidates:
                from .registry import registry

                names = "、".join(
                    str(getattr(registry.get(cid), "name", "") or cid) for cid in candidates
                )
                message = (
                    f"这台机器上还没有能打开 {suffix} 的集成套件"
                    f"（可用：{names}）。到「工具」页安装其中一个后再试。"
                )
            else:
                message = f"暂不支持以集成套件打开该类型：{suffix}"
            self._notify("打开方式", message)
            return {"ok": False, "message": message}

        result = self.launch_tool(tool_id, path)
        if not result.get("ok"):
            self._notify("打开失败", str(result.get("message") or "未知原因"))
        else:
            self._notify("已打开", str(result.get("message") or ""))
        return result

    def reveal_tool(self, tool_id: str) -> bool:
        """在资源管理器中定位工具所在位置。"""
        spec = registry.get(tool_id)
        if spec is None:
            return False

        target = spec.entry_path
        if target.is_file():
            if platform.system() == "Windows":
                subprocess.Popen(["explorer", "/select,", str(target)])
                return True
            ok, _ = open_in_explorer(target.parent)
            return ok

        target_dir = target if target.is_dir() else target.parent
        ok, _ = open_in_explorer(target_dir)
        return ok

    # ══════════════════════════════════════════════════════
    # 系统监测（主页数据源）
    # ══════════════════════════════════════════════════════

    def get_hardware(self) -> dict[str, Any]:
        """静态硬件信息。首次调用较慢（含 WMI 查询），之后走缓存。"""
        return monitor.hardware()

    def get_metrics(self) -> dict[str, Any]:
        """实时指标快照，含最近 60 秒的采样历史。"""
        return monitor.metrics()

    def get_network(self) -> dict[str, Any]:
        """网卡列表与网络拓扑（网关 / DNS）。"""
        return monitor.network()

    def get_ip(self) -> dict[str, Any]:
        """本机 IP 信息。不含公网查询，以保证离线环境下的响应速度。"""
        return monitor.ip(include_public=False)

    def query_public_ip(self) -> dict[str, Any]:
        """联网查询公网出口 IP。离线时返回 reachable=False。"""
        return query_public_ip()

    # ══════════════════════════════════════════════════════
    # 偏好与系统交互
    # ══════════════════════════════════════════════════════

    def get_theme(self) -> str:
        value = config.get("theme", "system")
        return value if value in _VALID_THEMES else "system"

    def set_theme(self, mode: str) -> bool:
        if mode not in _VALID_THEMES:
            return False
        # 如实返回落盘结果：写不进去时必须让界面知道，而不是显示"已保存"
        return bool(config.set("theme", mode))

    def config_diag(self) -> dict[str, Any]:
        """配置存储诊断（文件位置 / 上次保存结果 / 是否回退过）。"""
        return config.diag()

    # ── 配置备份与历史 ────────────────────────────────────

    def config_backup_status(self) -> dict[str, Any]:
        """备份列表与占用（设置页「配置备份」面板用）。"""
        from .core.config_backup import status

        return status()

    def config_backup_create(self) -> dict[str, Any]:
        """立即备份一份当前配置。"""
        from .core.config_backup import create_backup

        return create_backup("manual")

    def config_backup_restore(self, name: str) -> dict[str, Any]:
        """恢复到某份历史备份。

        恢复前会自动把**当前**配置另存一份（prerestore），恢复本身
        也可能是误操作，得让用户能退回来。
        """
        from .core.config_backup import restore

        return restore(name)

    def open_url(self, url: str) -> bool:
        try:
            webbrowser.open(url)
            return True
        except Exception:
            return False

    def open_tool_dir(self) -> bool:
        ok, _ = open_in_explorer(paths.tools_dir())
        return ok

    # ══════════════════════════════════════════════════════
    # 窗口控制（frameless 自绘标题栏）
    # ══════════════════════════════════════════════════════

    def window_minimize(self) -> None:
        from .system.wincontrol import minimize

        # Win32 直控优先：pywebview 在「最大化 → 最小化 → 恢复」后状态会失步
        if not minimize() and self._window is not None:
            self._window.minimize()

    def window_toggle_maximize(self) -> None:
        from .system.wincontrol import toggle_maximize

        if toggle_maximize():
            return
        if self._window is None:
            return
        try:
            if self._window.maximized:
                self._window.restore()
            else:
                self._window.maximize()
        except Exception:
            pass

    def window_is_maximized(self) -> bool:
        """供前端同步最大化按钮图标状态。"""
        from .system.wincontrol import is_maximized

        return is_maximized()

    def window_close(self) -> None:
        """关闭按钮 = 最小化到托盘（常驻后台）；无托盘时才是真正退出。"""
        if self._window is None:
            return
        if self.tray_available:
            self._window.hide()
        else:
            self._window.destroy()

    def window_start_drag(self) -> None:
        """标题栏拖动：交给 pywebview 内置方法。

        当 main.py 中 create_window(easy_drag=True) 时，拖动已由 Win32 层在
        frameless 下自动接管，此方法通常不再被调用（保留以防前端仍触发）。
        """
        if self._window is not None:
            try:
                self._window.start_drag()
            except Exception:
                pass

    def open_url(self, url: str) -> None:
        """未检测到外部应用时，打开官方下载页。"""
        import webbrowser

        try:
            webbrowser.open(str(url))
        except Exception:
            pass

    def window_quit(self) -> None:
        """真正退出（托盘菜单「退出」调用）。"""
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                import os

                os._exit(0)


    def get_autostart(self) -> bool:
        from .system.autostart import is_autostart

        return is_autostart()

    def set_autostart(self, enabled: bool) -> bool:
        from .system.autostart import set_autostart

        ok = set_autostart(bool(enabled))
        if ok:
            config.set("autostart", bool(enabled))
        return ok

    def set_openwith_registered(self, enabled: bool) -> bool:
        """注册/注销「打开方式」中的 OpenClass 入口（需在打包为 exe 后调用）。"""
        from .system.shell_integration import set_openwith

        return set_openwith(bool(enabled))

    def get_openwith_registered(self) -> bool:
        from .system.shell_integration import is_openwith

        return is_openwith()


def python_version_string() -> str:
    return ".".join(str(v) for v in sys.version_info[:3])

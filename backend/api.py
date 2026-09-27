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

    def classroom_report(self) -> dict[str, Any]:
        """课堂检测汇总：投影拓扑 / 触摸 / 无线投屏 / 教学软件 / 还原环境。"""
        from .core.classroom import report

        return report()

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

    def get_close_to_tray(self) -> bool:
        """关闭窗口时是否最小化到托盘（默认开启）。"""
        return bool(config.get("close_to_tray", True))

    def set_close_to_tray(self, value: bool) -> bool:
        config.set("close_to_tray", bool(value))
        return True

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
        """按扩展名路由到集成工具并打开该文件（右键「打开方式」后端）。"""
        from .core.app_locator import route_file

        tool_id = route_file(path)
        if not tool_id:
            return {"ok": False, "message": f"暂不支持以集成套件打开该类型：{Path(path).suffix}"}
        return self.launch_tool(tool_id, path)

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
        config.set("theme", mode)
        return True

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

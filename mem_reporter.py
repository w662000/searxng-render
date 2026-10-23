# SPDX-License-Identifier: AGPL-3.0-or-later
"""mem_reporter — SearXNG 容器内存监控插件（Render 免费档专用）

背景：Render 免费档 Dashboard 的 Metrics 页面看不了内存/CPU（带 PAID 标，要升级
付费套餐才给看），只有网络带宽图。这个插件在 SearXNG 同一个端口上暴露两个端点：

- ``/_mem``      网页，每 5 秒自动刷新，显示当前内存 / 上限 / 百分比 / 峰值
- ``/_mem.json`` 同样数据的 JSON

数据来源是 cgroup（容器内存限额的真实来源，v2/v1 兼容），另外统计全部进程的
RSS 合计（/proc/<pid>/status）。cgroup 的 current 含可回收的 page cache，
所以百分比接近 100% 才是危险区（OOM  Kill 发生在触及 memory.max 时）。

部署方式：本文件由 Dockerfile COPY 到官方镜像的 searx/plugins/ 目录，
在 settings.yml 的 plugins 段引用（见 settings.yml 内注释）。
"""

import os
import time

from flask import Response, jsonify

from searx.plugins import Plugin, PluginInfo

REFRESH_SECONDS = 5


def _read_int(path):
    """读文件里的一个整数，读不到返回 None（不抛异常）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _cgroup_memory():
    """读 cgroup 内存数据，返回 {current, limit, peak, source}（字节；读不到为 None）。

    cgroup v2: /sys/fs/cgroup/memory.current + memory.max + memory.peak
    cgroup v1: /sys/fs/cgroup/memory/memory.usage_in_bytes + limit + max_usage
    """
    current = _read_int("/sys/fs/cgroup/memory.current")
    if current is not None:
        return {
            "current": current,
            "limit": _read_int("/sys/fs/cgroup/memory.max"),
            "peak": _read_int("/sys/fs/cgroup/memory.peak"),
            "source": "cgroup-v2",
        }
    current = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if current is not None:
        return {
            "current": current,
            "limit": _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
            "peak": _read_int("/sys/fs/cgroup/memory/memory.max_usage_in_bytes"),
            "source": "cgroup-v1",
        }
    return {"current": None, "limit": None, "peak": None, "source": "unavailable"}


def _proc_rss_bytes():
    """全部进程 VmRSS 合计（字节）。读不到 /proc 时返回 0。"""
    total = 0
    try:
        pids = os.listdir("/proc")
    except OSError:
        return 0
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1]) * 1024
                        break
        except (OSError, ValueError):
            continue
    return total


def _snapshot():
    """采集一次内存快照。"""
    mem = _cgroup_memory()
    current = mem["current"]
    limit = mem["limit"]
    percent = None
    if current is not None and limit:
        percent = round(current * 100.0 / limit, 1)
    return {
        "ts": int(time.time()),
        "memory": mem,
        "proc_rss_bytes": _proc_rss_bytes(),
        "percent": percent,
    }


def _mb(nbytes):
    if nbytes is None:
        return "N/A"
    return f"{nbytes / 1024 / 1024:.1f} MB"


def _level(percent):
    """按百分比返回告警级别：ok / warn / danger。"""
    if percent is None:
        return "ok"
    if percent >= 90:
        return "danger"
    if percent >= 75:
        return "warn"
    return "ok"


_LEVEL_COLOR = {"ok": "#2f9e44", "warn": "#f08c00", "danger": "#e03131"}
_LEVEL_TEXT = {"ok": "安全", "warn": "偏高，留意", "danger": "危险，即将 OOM"}

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="%(refresh)d">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>内存监控 · searxng-render</title>
<style>
  body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: #f5f6f8; color: #222; margin: 0; padding: 24px; }
  .card { max-width: 560px; margin: 0 auto; background: #fff; border-radius: 12px;
          padding: 24px 28px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { color: #888; font-size: 12px; margin-bottom: 18px; }
  .big { font-size: 42px; font-weight: 700; line-height: 1; }
  .lvl { font-size: 13px; font-weight: 600; margin-left: 10px; }
  .bar { height: 14px; background: #e8eaed; border-radius: 7px; overflow: hidden; margin: 14px 0 4px; }
  .bar > div { height: 100%%; border-radius: 7px; }
  table { width: 100%%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }
  td { padding: 7px 0; border-bottom: 1px solid #f0f0f0; }
  td:last-child { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
  .note { background: #fff7e6; border: 1px solid #ffd591; border-radius: 8px;
          padding: 10px 14px; font-size: 12px; margin-top: 16px; color: #874d00; }
  .foot { margin-top: 16px; font-size: 11px; color: #aaa; text-align: center; }
</style>
</head>
<body>
<div class="card">
  <h1>内存监控 · searxng-render（Render 免费档 512MB）</h1>
  <div class="sub">数据来源：%(source)s · 每 %(refresh)d 秒自动刷新 · JSON: <a href="/_mem.json">/_mem.json</a></div>
  <div><span class="big">%(percent)s</span><span class="lvl" style="color:%(color)s">%(level_text)s</span></div>
  <div class="bar"><div style="width:%(width)s%%; background:%(color)s"></div></div>
  <table>
    <tr><td>当前用量（cgroup，含可回收缓存）</td><td>%(current)s</td></tr>
    <tr><td>容器上限</td><td>%(limit)s</td></tr>
    <tr><td>历史峰值（本次容器启动以来）</td><td>%(peak)s</td></tr>
    <tr><td>进程 RSS 合计（应用实际占用）</td><td>%(rss)s</td></tr>
  </table>
  <div class="note" id="note" style="display:%(note_display)s">
    <b>接近上限！</b>连续搜索会把内存推高，此时有 OOM 被杀风险。缓解办法：减少并发搜索、
    确认 granian 是单 worker（GRANIAN_WORKERS=1）、必要时按 README 减少引擎数量。
  </div>
  <div class="foot">Render 免费档 Dashboard 看不到内存图表（付费功能），故自建此端点 · 刷新于 %(hms)s</div>
</div>
</body>
</html>
"""


class SXNGPlugin(Plugin):
    """在 /_mem 暴露容器内存用量的 SearXNG 插件。"""

    id = "mem_reporter"

    def __init__(self, plg_cfg):
        super().__init__(plg_cfg)
        self.info = PluginInfo(
            id=self.id,
            name="Memory Reporter",
            description="在 /_mem 暴露容器内存用量（Render 免费档 Metrics 看不了内存）",
        )

    def init(self, app):
        """ SearXNG 启动时调用（webapp.py: searx.plugins.initialize(app)），
        在这里把内存端点注册到 SearXNG 的 Flask app 上，和搜索共用同一个端口。"""
        app.add_url_rule("/_mem", "mem_reporter_html", self._html, methods=["GET"])
        app.add_url_rule("/_mem.json", "mem_reporter_json", self._json, methods=["GET"])
        return True

    def _json(self):
        return jsonify(_snapshot())

    def _html(self):
        snap = _snapshot()
        percent = snap["percent"]
        level = _level(percent)
        width = 0 if percent is None else min(percent, 100)
        html = _PAGE % {
            "refresh": REFRESH_SECONDS,
            "source": snap["memory"]["source"],
            "percent": "N/A" if percent is None else f"{percent}%",
            "color": _LEVEL_COLOR[level],
            "level_text": _LEVEL_TEXT[level],
            "width": f"{width:.1f}",
            "current": _mb(snap["memory"]["current"]),
            "limit": _mb(snap["memory"]["limit"]),
            "peak": _mb(snap["memory"]["peak"]),
            "rss": _mb(snap["proc_rss_bytes"]),
            "note_display": "block" if level in ("warn", "danger") else "none",
            "hms": time.strftime("%H:%M:%S"),
        }
        return Response(html, mimetype="text/html")

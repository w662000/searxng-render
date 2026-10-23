"""
search_auth.py — SearXNG JSON 接口鉴权中间件（WSGI）

为什么需要
----------
SearXNG 开源版**没有 API token 机制**（官方文档明确：JSON/CSV/RSS 格式开启即公开）。
一旦公网域名被爬虫扫到，`/search?format=json` 会被无限刷，耗尽 Render 免费档的
带宽与 750 小时/月额度，服务可能被平台冻结。

本中间件在容器内给 SearXNG 包一层，**不改 SearXNG 一行源码、不加任何外部依赖、
内存代价约等于零**（一个 Python 对象）。

设计取舍
--------
1. **只对机器可读格式要求鉴权**（format=json/csv/rss）。
   html 全部放行——网页 UI 是本人自用，真正会被爬虫盯上的是结构化 JSON 接口。
   这样浏览器照常打开搜索页，/healthz、/static、/preferences 等也全部不受影响。
2. **GET 与 POST 都要查**。只看查询串的话，
   `curl -X POST -d 'q=x&format=json'` 就能绕过。所以 POST 会读 body 再判断，
   读完把 wsgi.input 换回 BytesIO，保证下游 SearXNG 仍能正常读到表单。
3. **未设置 SEARXNG_API_KEY 时完全放行**（行为与加本文件之前一致）。
   是否启用由环境变量决定，见 entrypoint.sh。
4. key 三种传法都认：`X-Search-Key` 头 / `Authorization: Bearer` / `?key=` 查询参数。
5. 用 hmac.compare_digest 做常数时间比较，避免时序侧信道。

生效方式
--------
官方入口 /usr/local/searxng/entrypoint.sh 最后一行是硬编码
    exec /usr/local/searxng/.venv/bin/granian searx.webapp:app
（且忽略 CMD 参数）。因此由 render-entrypoint.sh 在启动前把该行替换为
    exec /usr/local/searxng/.venv/bin/granian search_auth:app
并**校验替换成功，失败则拒绝启动**（fail-closed，绝不静默降级成无鉴权）。
"""

import hmac
import io
import os
import sys
from urllib.parse import parse_qs

# ---- 配置 ---------------------------------------------------------------

#: 只有设置了这个环境变量，鉴权才生效。空 = 完全放行（兼容旧行为）。
API_KEY = (os.environ.get("SEARXNG_API_KEY") or "").strip()

#: 机器可读格式才需要鉴权。html 不在其中 -> 网页 UI 永远放行。
MACHINE_FORMATS = ("json", "csv", "rss")

#: 这些路径永不鉴权（健康检查 / 静态资源 / 偏好与信息页 / 引擎错误统计）。
OPEN_PATHS = (
    "/healthz",
    "/static",
    "/theme",
    "/image_proxy",
    "/preferences",
    "/about",
    "/stats",
    "/config",
)

#: POST body 读取上限，超过则不解析（也就不用替换 wsgi.input）。
MAX_BODY = 65536

# ---- 内部实现 -----------------------------------------------------------


def _path_is_open(path):
    """判断路径是否在免鉴权白名单里。"""
    for p in OPEN_PATHS:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _qs_value(params, name):
    """从 parse_qs 结果里取第一个值，取不到返回 None。"""
    v = params.get(name)
    if not v:
        return None
    v = v[0]
    return v if v else None


def _form_from_body(environ):
    """
    读取 application/x-www-form-urlencoded 的 POST body 并解析。

    读完后必须把 environ['wsgi.input'] 换成一个新的 BytesIO，
    否则下游 SearXNG 读到的是空 body，POST 搜索会全部失效。
    超限或非表单类型时返回 None 且不碰 wsgi.input。
    """
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        return None
    if length <= 0 or length > MAX_BODY:
        return None

    ctype = (environ.get("CONTENT_TYPE") or "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return None

    stream = environ.get("wsgi.input")
    if stream is None:
        return None
    try:
        raw = stream.read(length)
    except Exception:
        return None
    # 关键：把读掉的 body 还回去
    environ["wsgi.input"] = io.BytesIO(raw)
    try:
        return parse_qs(raw.decode("utf-8", "replace"))
    except Exception:
        return None


def _requested_format(environ):
    """取出本次请求想要的输出格式；取不到返回 None（= html 默认）。"""
    fmt = _qs_value(parse_qs(environ.get("QUERY_STRING") or ""), "format")
    if fmt:
        return fmt.strip().lower()
    body = _form_from_body(environ)
    if body is not None:
        fmt = _qs_value(body, "format")
        if fmt:
            return fmt.strip().lower()
    return None


def _presented_key(environ):
    """从 header / query / bearer 三种位置取调用方给的 key。"""
    k = environ.get("HTTP_X_SEARCH_KEY")
    if k and k.strip():
        return k.strip()

    auth = environ.get("HTTP_AUTHORIZATION")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()

    k = _qs_value(parse_qs(environ.get("QUERY_STRING") or ""), "key")
    if k:
        return k.strip()
    return None


class JsonApiAuth:
    """WSGI 中间件：给非 html 格式的请求加 key 校验。"""

    def __init__(self, resolve_inner):
        # resolve_inner 是惰性解析函数，避免本模块在无 searx 的环境下无法 import
        # （本地单测就是靠这一点，用 stub app 直接测中间件逻辑）。
        self._resolve_inner = resolve_inner
        self._inner = None

    # -- WSGI 入口 ---------------------------------------------------------

    def __call__(self, environ, start_response):
        if not API_KEY:
            # 未配置 key：完全放行，行为与加本文件之前一致
            return self._inner_app()(environ, start_response)

        path = environ.get("PATH_INFO") or "/"

        # 白名单路径直接放行（健康检查/静态资源等）
        if _path_is_open(path):
            return self._inner_app()(environ, start_response)

        fmt = _requested_format(environ)
        if not fmt or fmt not in MACHINE_FORMATS:
            # html 或未指定格式：放行（网页 UI 自用）
            return self._inner_app()(environ, start_response)

        if self._authorized(environ):
            return self._inner_app()(environ, start_response)

        return self._reject(environ, start_response, path, fmt)

    # -- 辅助 -------------------------------------------------------------

    def _inner_app(self):
        if self._inner is None:
            self._inner = self._resolve_inner()
        return self._inner

    def _authorized(self, environ):
        given = _presented_key(environ)
        if not given:
            return False
        return hmac.compare_digest(given.encode("utf-8"), API_KEY.encode("utf-8"))

    def _reject(self, environ, start_response, path, fmt):
        # 只统计、不打日志到刷屏：爬虫噪声不记，"带了 key 但不对"才记
        # （后者基本是我们自己配置错了，必须能在 Render Logs 里看见）。
        global _REJECT_COUNT
        _REJECT_COUNT += 1
        given = _presented_key(environ)
        if given:
            sys.stderr.write(
                "[search_auth] 401 wrong key: path=%s format=%s\n" % (path, fmt)
            )
            sys.stderr.flush()
        elif _REJECT_COUNT % 50 == 1:
            sys.stderr.write(
                "[search_auth] 401 no key (count=%d): path=%s format=%s\n"
                % (_REJECT_COUNT, path, fmt)
            )
            sys.stderr.flush()

        body = (
            "unauthorized: machine-readable formats require a key. "
            "Send header 'X-Search-Key: <key>' or append '?key=<key>'."
        ).encode("utf-8")
        start_response(
            "401 Unauthorized",
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("WWW-Authenticate", 'Bearer realm="searxng-json-api"'),
            ],
        )
        return [body]


_REJECT_COUNT = 0


def _resolve_inner():
    """惰性导入 SearXNG 的 WSGI app（容器内一定成功）。"""
    from searx.webapp import app as searx_app

    return searx_app


#: granian 加载的目标：granian search_auth:app
app = JsonApiAuth(_resolve_inner)

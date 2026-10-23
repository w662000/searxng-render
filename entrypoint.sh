#!/bin/sh
# SearXNG on Render — 启动前预处理，然后交接给官方入口
#
# 官方入口 /usr/local/searxng/entrypoint.sh 负责：
#   - /etc/searxng、/var/cache/searxng 属主修复（root 下自动 chown 到 searxng:searxng）
#   - settings.yml 缺失时从模板生成并随机化 secret_key（我们已 COPY，不会触发）
#   - update-ca-certificates
#   - 最终 exec /usr/local/searxng/.venv/bin/granian searx.webapp:app
#     （granian 通过 GRANIAN_* 环境变量读取 host/port/workers 等配置）
#
# 注意：官方入口**忽略 CMD 参数**，且最后一行 granian 调用是硬编码的。
# 所以"换一个 WSGI app 来跑"只能靠启动前改写那一行，见下面第 4 步。
set -eu

SETTINGS="/etc/searxng/settings.yml"
OFFICIAL_ENTRYPOINT="/usr/local/searxng/entrypoint.sh"

# ---------- 1) secret_key ----------
# SearXNG 核心（webapp.py / settings_loader.py / entrypoint.sh）不解析环境变量占位符，
# 官方文档里的 ${SEARXNG_SECRET} 覆盖是其 DevOps 工具链的行为。
# 这里按整行重写 settings.yml 里的 secret_key 行：
#   - 设置了环境变量 SEARXNG_SECRET -> 用它的值（跨重启稳定，推荐）
#   - 未设置 -> 随机生成（与官方入口首次初始化同款行为，重启即失效）
if [ -n "${SEARXNG_SECRET:-}" ]; then
    case "$SEARXNG_SECRET" in
        *'|'*|*'&'*|*'"'*)
            echo "ERROR: SEARXNG_SECRET must not contain '|', '&' or double quotes." >&2
            echo "       Generate one with: python -c \"import secrets;print(secrets.token_urlsafe(32))\"" >&2
            exit 1
            ;;
    esac
    SECRET="$SEARXNG_SECRET"
    echo "[render-entrypoint] Using SEARXNG_SECRET from environment."
else
    SECRET="$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9')"
    echo "[render-entrypoint] SEARXNG_SECRET not set - generated an ephemeral secret_key (invalidated on restart)."
fi
sed -i "s|^  secret_key:.*|  secret_key: \"${SECRET}\"|" "$SETTINGS"

# ---------- 2) base_url ----------
# 设置了 SEARXNG_BASE_URL 就按行替换；未设置则删掉该行，
# SearXNG 退回默认行为（base_url 未设置 -> 生成相对链接，Render 反代下可用）。
if [ -n "${SEARXNG_BASE_URL:-}" ]; then
    sed -i "s|^  base_url:.*|  base_url: \"${SEARXNG_BASE_URL}\"|" "$SETTINGS"
else
    sed -i '/^  base_url:/d' "$SETTINGS"
fi

# ---------- 3) 端口 ----------
# Render 官方默认给每个 Web Service 注入 PORT=10000。官方入口只认
# SEARXNG_PORT -> GRANIAN_PORT 映射，不读 $PORT，所以这里显式导出；
# 未注入 PORT 时（如本地 docker run）也按官方默认回落 10000，行为与 Render 一致。
# 监听地址 0.0.0.0 与单 worker 已在 Dockerfile ENV 固定。
export GRANIAN_PORT="${PORT:-10000}"
echo "[render-entrypoint] SearXNG listening on 0.0.0.0:${GRANIAN_PORT} (workers=${GRANIAN_WORKERS:-1})"

# ---------- 4) JSON 接口鉴权 ----------
# SearXNG 开源版没有 API token 机制，/search?format=json 开启即公开。
# 公网域名一旦被爬虫扫到就会被无限刷，耗尽 Render 免费档带宽与 750 小时/月额度。
#
# 解法：在容器内加一层 WSGI 中间件 search_auth.py，只对机器可读格式
#       （json/csv/rss）要求 key，html 网页 UI 全部放行。
#       不改 SearXNG 一行源码、不加外部依赖、内存代价约等于零。
#
# 官方入口最后一行是硬编码的：
#     exec /usr/local/searxng/.venv/bin/granian searx.webapp:app
# 且它忽略 CMD，所以只能启动前改写那一行，改成加载 search_auth:app。
#
# 安全要求：**fail-closed**。替换没成功就拒绝启动，
# 绝不允许静默降级成一个无鉴权的公开 JSON 接口。
GRANIAN_TARGET='exec /usr/local/searxng/.venv/bin/granian searx.webapp:app'
GRANIAN_PATCHED='exec /usr/local/searxng/.venv/bin/granian search_auth:app'

if [ -n "${SEARXNG_API_KEY:-}" ]; then
    if grep -Fq "$GRANIAN_TARGET" "$OFFICIAL_ENTRYPOINT"; then
        sed -i "s|^${GRANIAN_TARGET}\$|${GRANIAN_PATCHED}|" "$OFFICIAL_ENTRYPOINT"
        if grep -Fq "$GRANIAN_PATCHED" "$OFFICIAL_ENTRYPOINT"; then
            echo "[render-entrypoint] JSON API auth ENABLED - non-html formats require X-Search-Key."
        else
            echo "ERROR: could not patch official entrypoint for JSON API auth." >&2
            echo "       Refusing to start an unauthenticated public JSON API." >&2
            exit 1
        fi
    else
        echo "ERROR: expected granian line not found in official entrypoint." >&2
        echo "       Refusing to start an unauthenticated public JSON API." >&2
        echo "       Inspect: grep -n granian $OFFICIAL_ENTRYPOINT" >&2
        exit 1
    fi
else
    echo "[render-entrypoint] WARNING: SEARXNG_API_KEY is not set - /search?format=json is OPEN to the internet." >&2
    echo "[render-entrypoint] WARNING: set SEARXNG_API_KEY in Render dashboard to enable auth." >&2
fi

exec /usr/local/searxng/entrypoint.sh

#!/bin/sh
# SearXNG on Render — 启动前预处理，然后交接给官方入口
#
# 官方入口 /usr/local/searxng/entrypoint.sh 负责：
#   - /etc/searxng、/var/cache/searxng 属主修复（root 下自动 chown 到 searxng:searxng）
#   - settings.yml 缺失时从模板生成并随机化 secret_key（我们已 COPY，不会触发）
#   - update-ca-certificates
#   - 最终 exec /usr/local/searxng/.venv/bin/granian searx.webapp:app
#     （granian 通过 GRANIAN_* 环境变量读取 host/port/workers 等配置）
set -eu

SETTINGS="/etc/searxng/settings.yml"

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

exec /usr/local/searxng/entrypoint.sh

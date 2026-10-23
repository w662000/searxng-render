# SearXNG on Render（免费档 512MB）— 基于官方镜像，只做 Render 适配，不魔改
#
# 适配点（均有官方文档/源码依据，核实日期 2026-10-22）：
#   1. 监听 0.0.0.0：官方镜像默认 GRANIAN_HOST="::"（IPv6），
#      Render 官方文档明确「Render doesn't support IPv6 addresses」，必须显式改 0.0.0.0。
#   2. 端口用官方默认 10000：Render 官方文档——每个 Web Service 默认注入
#      PORT=10000，服务必须绑 0.0.0.0:$PORT。官方入口脚本只把 SEARXNG_PORT
#      映射为 GRANIAN_PORT，并不读 $PORT，因此由 entrypoint.sh 在启动时
#      export GRANIAN_PORT=${PORT:-10000}（未注入时同样回落官方默认 10000）。
#   3. GRANIAN_WORKERS=1：免费档 512MB/0.1vCPU。granian 默认 worker 数就是 1，
#      这里显式钉死，防止将来镜像改默认值导致多 worker 直接 OOM
#      （2026-07-20 hermes-webui 在 Render 512MB 上 OOM 的教训）。
#   4. ${SEARXNG_SECRET} / ${SEARXNG_BASE_URL} 启动前替换：
#      SearXNG 核心（searx/webapp.py、searx/settings_loader.py、container/entrypoint.sh）
#      均不做环境变量插值——官方文档里的 ${} 覆盖是其 DevOps 工具链的行为。
#      不替换的话，密钥会变成字面量字符串 "${SEARXNG_SECRET}"。
FROM docker.io/searxng/searxng:latest

# settings.yml 复制到官方镜像约定的配置路径（镜像内 __SEARXNG_SETTINGS_PATH 即此路径）
COPY settings.yml /etc/searxng/settings.yml

# 内存监控插件（2026-10-23 新增）：Render 免费档 Metrics 看不了内存（付费功能），
# 用 SearXNG 官方插件机制（Plugin.init(app) 注册路由）在同端口暴露
# /_mem 网页 + /_mem.json。详见 mem_reporter.py 与 README 五-C。
COPY mem_reporter.py /usr/local/searxng/searx/plugins/mem_reporter.py

# 启动前预处理脚本（替换密钥/域名占位符、导出 GRANIAN_PORT），
# 做完后 exec 官方入口 /usr/local/searxng/entrypoint.sh，完全延续官方行为
COPY entrypoint.sh /usr/local/searxng/render-entrypoint.sh
RUN chmod 0755 /usr/local/searxng/render-entrypoint.sh

ENV GRANIAN_HOST=0.0.0.0
ENV GRANIAN_WORKERS=1

# 按 Render 文档建议在 Dockerfile 里标明 HTTP 端口（仅作声明，不影响功能）；
# 实际监听端口以 Render 注入的 $PORT 为准，官方默认 10000
EXPOSE 10000

# 官方镜像的 ENTRYPOINT 就是 /usr/local/searxng/entrypoint.sh，这里替换为包装脚本
ENTRYPOINT ["/usr/local/searxng/render-entrypoint.sh"]

# SearXNG 部署到 Render（免费档）— 完整操作指南

> 整理日期：2026-10-22 ｜ 全程网页鼠标操作，不需要 SSH、不需要敲服务器命令
> 前置条件：已有 Render 账号（用 GitHub 登录，无需信用卡）——你 7 月 20 日注册过，直接用
>
> 说明：最初的部署思路来自豆包版指南，**仅作参考**。本项目一切配置以 SearXNG 与
> Render 的官方文档、官方镜像源码为准；相对豆包版的修正逐条列在文末
> 「七、与豆包版指南的差异对照」。豆包版中与官方文档一致的部分（引擎选择、
> JSON 接口、保活思路、限额提醒）保留原意。

---

## 一、文件清单（GitHub 仓库就放这 4 个文件，放仓库根目录）

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 基于官方镜像 `docker.io/searxng/searxng:latest`，做 4 项 Render 适配（监听地址、端口、worker 数、密钥替换） |
| `entrypoint.sh` | 容器启动前的预处理：按整行替换 settings.yml 里的 `secret_key`/`base_url`，导出 `GRANIAN_PORT=$PORT`（未注入时回落官方默认 10000），然后交接给官方入口 |
| `settings.yml` | SearXNG 配置（禁用 Google、精简引擎、开 JSON 接口、简体中文界面） |
| `README.md` | 本文件 |

## 二、为什么不是草稿里那份 Dockerfile（先说清楚，避免部署到一半踩雷）

你草稿里的 Dockerfile 有 4 个会直接导致部署失败或配置失效的问题，均已修正：

1. **`CMD ["granian","--interface","asgi3","searxng.asgi:application",...]` 必崩**。
   SearXNG 仓库里没有 `searxng` 这个 Python 包（只有 `searx/` 和 `searxng_extra/`），
   `searxng.asgi:application` 这个模块不存在，granian 起来就 ModuleNotFoundError，容器反复重启。
   官方镜像的正确玩法是让它的入口 `/usr/local/searxng/entrypoint.sh` 跑，
   最终执行 `granian searx.webapp:app`（WSGI 接口）。
2. **写死 `--port 8080` 有隐患**。Render 官方文档：每个 Web Service 必须绑
   `0.0.0.0:$PORT`（官方默认注入 `PORT=10000`）；绑错端口"通常"能被检测到，
   检测失败则部署直接失败。本项目由 entrypoint.sh 读 `$PORT`，未注入时按官方默认
   回落 10000，本地 `docker run` 测试行为也一致。
3. **官方镜像默认绑 IPv6 `::`**（`GRANIAN_HOST="::"`），而 Render 文档明确
   "Render doesn't support IPv6 addresses"。本项目显式改成 `0.0.0.0`。
4. **`chown 1000:1000` + `USER 1000` 与官方镜像冲突**。官方镜像的文件属主是
   `977:977`（searxng 用户），且入口脚本会在 root 下自动修属主。乱改用户反而会引发
   缓存目录写不进去。本项目不加 USER，完全沿用官方属主体系。

## 三、部署步骤（全程网页）

### 第 1 步：建 GitHub 仓库并上传文件

1. 登录 GitHub → 右上角 **+** → **New repository**。
2. 仓库名随意，例如 `searxng-render`；可见性选 **Public**（Render 免费档拉公开仓库最省事；
   Private 也行，授权 Render 读权限即可）。
3. 创建后点 **Add file → Upload files**，把本目录 4 个文件（`Dockerfile`、
   `entrypoint.sh`、`settings.yml`、`README.md`）一起拖进去，**Commit changes**。
   - ⚠️ `entrypoint.sh` 必须是 LF 换行。GitHub 网页上传保留原字节，只要本地文件不是
     CRLF 就行（本项目文件已是 LF）。若你用记事本编辑过，另存为时选 UTF-8、保持 LF。
   - `settings.yml` 里有中文注释，上传后确认 GitHub 页面显示不是乱码（UTF-8 正常）。

### 第 2 步：生成密钥（两种方式二选一）

- **方式 A（推荐，零命令行）**：Render 创建服务时，环境变量那一栏点值输入框旁的
  **Generate** 按钮，让 Render 生成随机值。
- **方式 B（本地生成）**：本机 PowerShell 或终端跑：

  ```bash
  python -c "import secrets;print(secrets.token_urlsafe(32))"
  ```

  复制输出的一串字符（纯字母数字和 `-_`，不要含 `|` 和 `&`，入口脚本会拒绝）。

### 第 3 步：Render 控制台创建 Web Service

1. 打开 https://dashboard.render.com → 右上角 **New +** → **Web Service**。
2. 点 **Connect GitHub**（首次需授权 Render 读仓库），选中刚建的仓库。
3. 配置页逐项填：
   - **Name**：填服务名，域名就是 `名字.onrender.com`（重名会被加随机后缀，以创建后
     Dashboard 显示的实际域名为准，下面第 4 步要用）。
   - **Region**：Singapore（离你近；Oregon 也行）。
   - **Branch**：`main`。
   - **Runtime**：确认自动识别为 **Docker**（仓库根目录有 Dockerfile 就会识别；
     没识别就手动选 Docker）。
   - **Build Command**：**留空**（Dockerfile 内部定义一切）。
   - **Start Command**：**留空**（启动命令由 Dockerfile 的 ENTRYPOINT 定义）。
   - **Instance Type**：选 **Free（512MB）**。
4. 页面中部找到 **Health Check Path**（没有就点 Advanced 展开），填：

   ```
   /healthz
   ```

   （SearXNG 自带该端点，返回纯文本 `OK`；不填则用默认 TCP 探测，也行，但填上更好。）

### 第 4 步：加环境变量（Advanced → Environment Variables）

| Key | Value | 说明 |
|---|---|---|
| `SEARXNG_SECRET` | 第 2 步生成的密钥（或 Render 的 Generate） | **必填**。不填则每次重启随机生成，cookie/偏好会失效 |
| `SEARXNG_BASE_URL` | `https://你的实际域名.onrender.com/` | 域名以 Dashboard 实际显示为准，**末尾带斜杠**。不填则用相对链接，也能用 |

- 不需要填 `PORT`——Render 官方默认自动注入 `10000`，entrypoint.sh 直接读取；
  未注入时也按官方默认回落 10000，行为一致。
- 不要加草稿里的 `SEARXNG_LIMITER_ENABLED`：SearXNG 没有这个环境变量，加了也没用
  （限流的正确打开方式见「六、后续可选」）。

### 第 5 步：创建并等待部署

1. 拉到最底，确认 **Free** 套餐，点 **Create Web Service**。
2. Render 自动拉代码 → 构建镜像（拉官方镜像 + COPY 两个文件，通常 1-3 分钟）→
   启动容器。在 **Logs** 标签页能看到：
   - `[render-entrypoint] Using SEARXNG_SECRET from environment.`
   - `[render-entrypoint] SearXNG listening on 0.0.0.0:10000 (workers=1)`
   - 官方入口的 `SearXNG 2026.x.x-xxxx` 版本行
3. 看到监听日志后，Dashboard 顶部会出现绿色 Live 和域名，点开即用。

### 第 6 步：验证

```bash
# 1) 健康检查（应返回 OK）
curl "https://你的域名.onrender.com/healthz"

# 2) JSON 搜索接口（原指南的测试命令，GET 可用）
curl "https://你的域名.onrender.com/search?q=%E6%B5%8B%E8%AF%95&format=json"

# 3) 看引擎加载情况（应只有 bing/duckduckgo/brave/wikipedia/github 五个，无 google）
curl "https://你的域名.onrender.com/config"

# 4) 引擎报错监控（哪个引擎被 429/验证码了看这里）
curl "https://你的域名.onrender.com/stats/errors"
```

浏览器打开域名则是完整搜索页面（simple 主题、简体中文、无限滚动）。

## 四、保活（免费档 15 分钟无访问会休眠）

1. 注册 UptimeRobot（免费）→ **Add New Monitor**：
   - Monitor Type：`HTTP(s)`
   - URL：`https://你的域名.onrender.com/healthz`（**ping /healthz，不要 ping /search**）
   - Monitoring Interval：**every 10 minutes**（10-12 分钟都行）
2. 风险提示（草稿里说得对，保留）：Render 可能识别并屏蔽监控请求，休眠不能 100% 防住；
   冷启动约 30-80 秒。**别拿它当 7×24 生产 AI 后端**，个人自用/测试足够。

## 五、免费档限额与避坑（草稿内容核实后保留）

1. **750 小时/月**：一个实例跑满整月约 720 小时，刚好够用——**不要同时开第二个免费实例**，
   第二个会开始按 $7/月左右计费（以 Render 官网当时价格为准）。
2. **内存 512MB**：SearXNG 单 worker + 5 引擎的内存占用远低于 512MB（量级约一两百 MB，
   具体以 Render 控制台 Metrics 面板实时显示为准），够用。
   这正是 7 月 20 日 hermes-webui（双进程合计 400MB+）OOM 反面的例子——轻量应用才适合免费档。
3. **不要公开 JSON 接口**：`/search?format=json` 无鉴权，被爬虫刷爆会被 Render 限流停机。
   只在自己的工具/脚本里用，别提在公开场合。
4. **不要开 Google 引擎**：Render 出口是共享公网 IP，Google 必 429。配置里根本没加载它。
5. **磁盘是临时的**：容器重启/重建后，favicon 缓存等会丢，配置无状态（配置在仓库里），不影响使用。
6. **修改配置的流程**：改 GitHub 仓库里的 `settings.yml` → push → Render 自动重新部署
   （Auto Deploy 默认开）。改环境变量则在 Dashboard 里改，会触发重启。

## 六、后续可选

- **开启限流（limiter）**：官方文档明确限流需要 valkey 数据库。Render 上有 Key Value
  （Redis 兼容）服务，是否仍有免费额度以 Render 官网当时政策为准——如需开启：
  建一个同 Region 的 Key Value 服务拿到内部 URL，然后 `settings.yml` 里
  `limiter: true` 并加 `valkey: url: <内部URL>`，重启生效。个人自用场景不开也行。
- **加引擎**：`settings.yml` 里 `keep_only` 列表加上名字即可（引擎按名字合并到官方默认
  定义上，内存代价很小，比如 `stackoverflow`、`arxiv`、`wikidata`、`mojeek`）。
- **恢复全部默认引擎**：删掉 `use_default_settings.engines.keep_only` 整段，
  再把 `engines` 段里 `- name: google / disabled: true` 加回去。

## 七、与豆包版指南的差异对照（为什么改；豆包版仅供参考，本表以官方文档为准）

| 草稿内容 | 问题 | 本项目做法 |
|---|---|---|
| `CMD granian --interface asgi3 searxng.asgi:application` | 模块不存在，容器起不来 | 沿用官方入口（内部 `granian searx.webapp:app` WSGI） |
| 写死 `--port 8080` | Render 要求绑 `$PORT`（官方默认 10000），检测失败即部署失败 | entrypoint.sh 导出 `GRANIAN_PORT=${PORT:-10000}`（未注入也按官方默认回落） |
| （未处理监听地址） | 官方镜像默认 IPv6 `::`，Render 不支持 IPv6 | `ENV GRANIAN_HOST=0.0.0.0` |
| `chown 1000:1000` + `USER 1000` | 与官方镜像 977 属主体系冲突 | 不设 USER，官方入口自动管属主 |
| `search.max_search_time: 8` | 官方无此字段，静默失效 | `outgoing.request_timeout: 6.0` + `max_request_timeout: 8.0` |
| `search.lang: zh-CN` | 官方无 `lang` 字段 | `search.default_lang: "zh-CN"` |
| `server.metrics: false` | 官方无此字段（指标在 `general.enable_metrics`） | `general.enable_metrics: false` |
| `ui.theme: simple` | 官方无 `theme` 字段 | `ui.default_theme: simple` |
| `ui.infinite_scroll: true` | 官方无此字段，无限滚动是插件 | `plugins` 段打开 `searx.plugins.infinite_scroll.SXNGPlugin` |
| `botdetection: limiter: true` | 新版 SearXNG 已把 botdetection 移出 settings.yml（改到 limiter.toml） | `server.limiter: false`（无 valkey 不开限流，见第六节） |
| 环境变量 `SEARXNG_LIMITER_ENABLED` | SearXNG 无此变量 | 不设；限流走 `server.limiter` + valkey |
| `secret_key: "${SEARXNG_SECRET}"` 直接靠插值 | SearXNG 核心不做插值，密钥会变成字面量 | entrypoint.sh 启动前替换，未设则随机生成 |
| `"zh‑CN"`（非 ASCII 连字符） | U+2011 不可见字符，locale 匹配不上 | ASCII 连字符 `zh-CN`；UI locale 用 `zh_Hans_CN` |
| 引擎列表只列 6 个（google disabled） | `use_default_settings` 下未列的引擎仍会加载 | `keep_only` 白名单，google 完全不加载 |
| 健康检查未提及 | 休眠/重启时 TCP 探测太糙 | Health Check Path 填 `/healthz` |

## 八、参考资料（均为官方一手来源）

- SearXNG 官方文档 — 容器安装：https://docs.searxng.org/admin/installation-docker.html
- SearXNG 官方文档 — settings.yml（use_default_settings / 引擎合并）：https://docs.searxng.org/admin/settings/settings.html
- SearXNG 官方文档 — server 节（limiter 需 valkey）：https://docs.searxng.org/admin/settings/settings_server.html
- SearXNG 官方文档 — limiter：https://docs.searxng.org/admin/searx.limiter.html
- SearXNG 镜像入口脚本：https://github.com/searxng/searxng/blob/master/container/entrypoint.sh
- Render 官方文档 — Web Services（PORT/IPv4/健康检查）：https://render.com/docs/web-services
- Render 官方文档 — 部署排障（OOM/端口）：https://render.com/docs/troubleshooting-deploys

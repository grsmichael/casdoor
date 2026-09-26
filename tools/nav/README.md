# Casdoor 本地开发服务导航

仿照 MaxKey `tools/nav` 的一套东西：一个零依赖的 Python 小服务 + 一个导航页，
把本地调试要点的地址、账号、健康状态集中到一页。

```
tools/nav/
├── services.json      ← 【唯一数据源】服务清单 + 默认账号 + 快速上手
├── serve_nav.py       ← 托管导航页 + 3 个只读 JSON 端点（零第三方依赖）
├── 服务导航.html       ← 导航页本体（不含任何硬编码账号/host）
└── README.md
```

## 用法

```bash
python tools/nav/serve_nav.py              # 默认 8899，被占自动顺延（8898/8897/8896）
python tools/nav/serve_nav.py --port 9000  # 指定端口
python tools/nav/serve_nav.py --bind 127.0.0.1   # 只给本机访问
python tools/nav/serve_nav.py --list       # 只打印本机内网 IP 后退出
python tools/nav/serve_nav.py --open       # 启动后自动开浏览器
```

然后打开 http://localhost:8899/ ，Ctrl+C 停止。
⚠️ 默认监听 `0.0.0.0`，同网段设备也能看到（页面上有端口和账号），不需要时停掉或加 `--bind 127.0.0.1`。

**本机坑：环境里有 `http_proxy`/`HTTPS_PROXY`（指向 127.0.0.1 的沙箱代理），
`curl http://127.0.0.1:8899/` 会被代理接走返回 502。** 命令行验证请加 `curl --noproxy '*'`；
浏览器一般不受影响，若打不开就检查系统代理设置。脚本内部探测也已强制直连（绕开代理）。

## 三个端点

| 端点 | 用途 |
|---|---|
| `/__services` | 下发 `services.json`（服务清单 + 默认账号），页面靠它渲染卡片 |
| `/__sysinfo` | 服务端探测本机内网 IP（浏览器 WebRTC 已拿不到真实内网 IP） |
| `/__probe` | 服务端并发探测各服务健康 + docker 容器状态（规避跨端口 CORS） |

## 服务清单（`services.json`）

加服务 / 改端口 / 改账号，**只改这一个文件**，探测、卡片、账号面板自动同步。
字段说明：

| 字段 | 说明 |
|---|---|
| `port` | 端口号，卡片链接与探测都用它 |
| `kind` | `http`（GET 探测 `probe` 路径）/ `tcp`（connect 成功即算在线）/ `udp`（**不探测**，状态恒灰） |
| `probe` | http 探测路径。2xx 判在线；301/302/401/403 也算活着（要登录/要跳转而已） |
| `container` | 可选，只用于附加展示 docker 容器状态，不参与在线判定 |
| `links` | 卡片上的快捷链接（`port` + `path` 拼成，host 跟随页面当前「服务链接主机」） |
| `creds` | 卡片底部的账号小标签，可一键复制 |

当前登记的 6 个服务：

| 端口 | 类型 | 说明 |
|---|---|---|
| 8000 | http | casdoor 后端（Go/Beego），OIDC/SAML/CAS/SCIM/MFA/LDAP/RADIUS 都在这一进程 |
| 7001 | http | casdoor web（vite 开发服务器），已反代 /api、/swagger、/.well-known、/scim、/cas 到 8000 |
| 7002 | http | web-old 旧前端（只读对照）——默认脚本端口也是 7001，需 `PORT=7002 yarn start` |
| 3306 | tcp | MySQL 8.0.25（docker compose db），root / 123456，库 casdoor |
| 389 | tcp | 后端进程内置 LDAP，绑定 DN `cn=buildin,dc=example,dc=com` / 123 |
| 1812 | udp | 后端进程内置 RADIUS，共享密钥 `secret`，**不探测** |

## 默认账号

- 管理员：`admin` / `123`（组织 `built-in`，全局管理员，来自 `object/init.go:initBuiltInUser()`）
- MySQL：`root` / `123456`（来自 `docker-compose.yml`）
- LDAP：`cn=buildin,dc=example,dc=com` / `123`，baseDn `ou=BuildIn,dc=example,dc=com`
- RADIUS：共享密钥 `secret`（`conf/app.conf` 的 `radiusSecret`），默认组织 `built-in`

⚠️ 全部是开发用途的硬编码默认值，切勿用于生产。

## 起服务（本机环境现状）

```bash
docker compose up -d db            # 起 MySQL（等价 make deps）
go run main.go                     # 起后端（首次启动自动建库 + 写内置数据）
cd web && yarn && yarn dev         # 起前端 → http://localhost:7001
docker compose up -d               # 或者整体容器化（后端会自己 build 镜像）
```

本机现状（2026-09-26 实测）：

- ❌ **没有 Go 运行时**（`go version` 命令不存在），go.mod 要求 **go 1.25.0 / toolchain go1.25.8**。
  要跑 `go run main.go` 得先装 Go 1.25+，或者走 `docker compose up -d` 让镜像里构建。
- ❌ **没有 yarn**（`corepack` 可用：`corepack enable yarn` 即可）。`web/package.json` 有
  preinstall 强校验，用 npm 装会被拦下来。
- ✅ docker（含 compose v5.5.1）可用，守护进程在跑，当前无容器。
- ⚠️ 3306/8000/7001 目前都没有进程监听，所以导航页现在一片红——属正常，起服务后点「刷新状态」。

## 链接准确性说明

MaxKey 那套的规矩是「导航页挂的链接逐个实测」。这份 casdoor 清单**还没实测**：
后端链接按 `routers/router.go` 整理，前端链接按 `web/src/App.tsx` 的路由整理，
卡片的 `desc` 里已标注这一点。**等后端跑起来后，请逐个点一遍，不灵的就回来改 `services.json`。**

已知两处需要留意：

1. `/api/saml/metadata?application=admin/app-built-in` —— 参数要传应用 id（`owner/name`），
   id 写错会返回 JSON 错误而不是元数据。
2. `web-old` 的端口是人为改成 7002 的（默认脚本是 7001，会和 web 撞车）。

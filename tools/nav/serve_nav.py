# -*- coding: utf-8 -*-
"""Casdoor 本地开发导航服务：托管 服务导航.html，并暴露 3 个只读 JSON 端点。零第三方依赖。

   /__services  —— 返回 services.json（服务清单唯一数据源，页面靠它渲染卡片）
   /__sysinfo   —— 服务端探测「本机内网 IP」（绕过浏览器 WebRTC 隐藏本机 IP 的限制）
   /__probe     —— 服务端并发探测各服务健康 + docker 容器状态

为什么需要 nav 服务（而非直接双击 HTML）
----------------------------------------
1) Chrome/Edge 76+ 起**默认对 WebRTC 做隐私处理**：host 候选真实内网 IP 被替换成随机
   `<uuid>.local`（mDNS 混淆），srflx 候选的 `raddr` 被置 `0.0.0.0`。纯前端探测必然失败——
   这不是代码问题，是浏览器设计。本脚本在**服务端**用 UDP-connect 法可靠取内网 IP
   （不产生实际流量），页面同源 `fetch('/__sysinfo')` 即可，离线可用。
2) 导航页跑在 :8899，而 Casdoor 各服务跑在 8000/7001/3306……——跨端口，浏览器会因 CORS
   拦掉前端 fetch。放服务端探测就没有 CORS 问题。

服务清单只有一份：services.json
------------------------------
旧写法要在 Python 里维护 PROBES、在 HTML 里维护 SERVICES，还要人工保证两个数组顺序一致
（下标对不上状态点就错位）。现在两边都读同一个 services.json，加服务/改端口只改一处。

健康判据
--------
* http：校验 HTTP 状态码，2xx/3xx 视为 up；301/302/401/403 也判 up（服务活着，只是要登录或跳转）
* tcp：connect 成功即 up（mysql、ldap 走这条）
* udp：UDP 无连接语义，不做探测，状态恒为 unknown（灰）——RADIUS 属于这一类
* 附加 `container` 字段（docker 容器状态，仅展示，不参与 up/down 判定）；docker 不可用时静默降级

用法：
  python tools/nav/serve_nav.py                    # 默认 8899，被占则自动顺延
  python tools/nav/serve_nav.py --port 9000
  python tools/nav/serve_nav.py --bind 127.0.0.1   # 仅本机可访问
  python tools/nav/serve_nav.py --list             # 只打印本机内网 IP 后退出
  python tools/nav/serve_nav.py --open             # 启动后自动打开默认浏览器

停止：Ctrl+C（本脚本前台常驻；请勿放到「跑完即退」的一次性管道里）。

⚠️ 仅用于本地开发环境：会把服务导航页（含端口/账号信息）暴露给同网段设备。
   不需要时请停掉，或用 --bind 127.0.0.1 限制为本机。
"""
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:  # pragma: no cover
    print("[err] 需要 Python 3")
    raise SystemExit(2)

HERE = os.path.dirname(os.path.abspath(__file__))        # tools/nav/
ROOT = os.path.dirname(os.path.dirname(HERE))            # 仓库根
NAV_FILE = os.path.join(HERE, "服务导航.html")
SERVICES_FILE = os.path.join(HERE, "services.json")

# 8899 是这类导航服务的常用端口（本机可能已被别的项目的 nav 占用，MaxKey 就占了 8898），
# 未显式指定 --port 时按顺序自动顺延。
DEFAULT_PORTS = (8899, 8898, 8897, 8896)
DEFAULT_BIND = "0.0.0.0"

PRIVATE_RE = re.compile(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)")

# ⚠ 必须绕开系统/环境代理再探测本机端口。
# 本机若设了 http_proxy / HTTPS_PROXY（企业代理、IDE 代理、沙箱代理都算），
# urllib 默认会把 http://127.0.0.1:8000/ 这种请求也丢给代理，于是：
#   * 目标服务没起时拿到代理返回的 502，而不是真实的 connection refused
#   * 目标服务起了但代理不放行本机地址时误报 down
# 用一个空的 ProxyHandler 强制直连。
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------- 服务清单

def load_services():
    """读 services.json。文件缺失/损坏时给出明确报错，不静默降级成空列表。"""
    if not os.path.exists(SERVICES_FILE):
        print("[err] 找不到服务清单：%s" % SERVICES_FILE)
        raise SystemExit(2)
    try:
        with io.open(SERVICES_FILE, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as e:
        print("[err] services.json 解析失败：%s" % e)
        raise SystemExit(2)
    if not isinstance(cfg.get("services"), list) or not cfg["services"]:
        print("[err] services.json 里 services 数组为空")
        raise SystemExit(2)
    return cfg


# ---------------------------------------------------------------- 内网 IP 探测

def detect_lan_ip(probe_host="223.5.5.5", probe_port=80):
    """默认路由出口地址：UDP connect 到一个可达地址，读本地端点（不产生实际流量）。

    默认探测目标用阿里公网 DNS；连不通时兜底 8.8.8.8。
    """
    for host, port in ((probe_host, probe_port), ("8.8.8.8", 53)):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((host, port))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            continue
    return None


def classify(ip):
    if ip.startswith("127."):
        return "loopback"
    if ip.startswith("169.254."):
        return "link-local"
    if re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip):
        return "可能是 VPN/虚拟网卡段"
    return "lan"


def lan_ips():
    """返回 [{'ip','primary','guess'}]，primary（默认路由口）排第一。"""
    seen, out = [], []
    primary = detect_lan_ip()

    def add(ip):
        if not ip or not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            return
        if ip.startswith("127.") or ip.startswith("0.") or ip in seen:
            return
        if not PRIVATE_RE.match(ip):
            return
        seen.append(ip)

    if primary:
        add(primary)
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            add(ip)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except Exception:
        pass

    for ip in seen:
        out.append({"ip": ip, "primary": ip == primary, "guess": classify(ip)})
    return out


def sysinfo(port, bind):
    ips = lan_ips()
    primary = next((x["ip"] for x in ips if x["primary"]), ips[0]["ip"] if ips else None)
    return {
        "hostname": socket.gethostname(),
        "primary": primary,
        "lanIps": ips,
        "navPort": port,
        "navBind": bind,
        "navFile": os.path.relpath(NAV_FILE, ROOT).replace("\\", "/"),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


# ---------------------------------------------------------------- 健康探测

def docker_states():
    """一次性读取所有相关容器的运行状态；docker 不可用/无容器时返回 {}。"""
    try:
        out = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, timeout=8)
        if out.returncode != 0:
            return {}
        states = {}
        for line in (out.stdout or b"").decode("utf-8", "replace").splitlines():
            if "\t" not in line:
                continue
            n, st = line.split("\t", 1)
            states[n] = st.strip()
        return states
    except Exception:
        return {}


def probe_one(svc, states=None):
    """按 services.json 里的一条服务定义做探测。"""
    name = svc.get("name", "?")
    res = {"name": name, "status": "unknown", "detail": ""}
    container = svc.get("container")
    if container and states:
        res["container"] = states.get(container, "(容器不存在)")

    kind = svc.get("kind", "http")

    # UDP 没有「连接是否建立」这回事：sendto 永远成功，收不到应答也可能是服务端
    # 故意丢弃（RADIUS 就是这种）。与其给个假的绿灯/红灯，不如明确不探测。
    if kind == "udp":
        res["status"] = "unknown"
        res["detail"] = "UDP 服务，脚本不探测（%s/udp）" % svc.get("port")
        return res

    if kind == "tcp":
        try:
            s = socket.create_connection(("127.0.0.1", svc["port"]), timeout=2)
            s.close()
            res["status"] = "up"
            res["detail"] = "tcp connect ok"
        except Exception as e:
            res["status"] = "down"
            res["detail"] = str(e)[:80]
        return res

    # http：路径缺省就用 context 根 "/"
    path = svc.get("probe") or "/"
    url = "http://127.0.0.1:%d%s" % (svc["port"], path)
    try:
        req = urllib.request.Request(url, method="GET",
                                     headers={"Cache-Control": "no-store"})
        with NO_PROXY_OPENER.open(req, timeout=3) as r:
            code = r.getcode()
            res["status"] = "up" if 200 <= code < 400 else "down"
            res["detail"] = "HTTP %d" % code
    except urllib.error.HTTPError as e:
        # 要登录/要跳转也算活着，避免把 401/302 误判成故障
        res["status"] = "up" if e.code in (301, 302, 303, 307, 308, 401, 403) else "down"
        res["detail"] = "HTTP %d" % e.code
    except Exception as e:
        res["status"] = "down"
        res["detail"] = str(e)[:80]
    return res


def probe_all(cfg):
    states = docker_states()
    services = cfg["services"]
    results = [None] * len(services)

    def run(i):
        results[i] = probe_one(services[i], states)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(services))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "probes": results}


# ---------------------------------------------------------------- HTTP 服务

PAGE_ALIASES = ("", "/index.html", "/nav.html", "/服务导航", "/服务导航.html")
PAGE_NAMES = ("index.html", "nav.html", "服务导航.html")   # 任意挂载深度下的页名


class Handler(BaseHTTPRequestHandler):
    server_version = "NavServer/2.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False, indent=2),
                   "application/json; charset=utf-8")

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        raw = self.path.split("?", 1)[0]
        try:
            from urllib.parse import unquote
            path = unquote(raw)
        except Exception:
            path = raw
        tail = path.rstrip("/").rsplit("/", 1)[-1]

        if tail == "__services":
            cfg = self.server.nav_cfg
            self._json({
                "project": cfg.get("project", "Casdoor"),
                "version": cfg.get("version", ""),
                # 默认账号区块也走这里下发，页面不再硬编码任何账号（单一数据源）
                "credentials": cfg.get("credentials") or {},
                "services": cfg["services"],
            })
            return

        if tail == "__sysinfo":
            self._json(sysinfo(self.server.nav_port, self.server.nav_bind))
            return

        if tail == "__probe":
            self._json(probe_all(self.server.nav_cfg))
            return

        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        if path.rstrip("/") in PAGE_ALIASES or tail in PAGE_NAMES:
            try:
                with io.open(NAV_FILE, "r", encoding="utf-8") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, "读取导航页失败：%s\n%s" % (NAV_FILE, e),
                           "text/plain; charset=utf-8")
            return

        self._send(404,
                   "404 —— 本服务只提供：/ （导航页）、/__services、/__sysinfo、/__probe\n",
                   "text/plain; charset=utf-8")


def port_busy(port, host="127.0.0.1", timeout=0.6):
    """真·占用检测：能连上说明已经有别的进程在监听。

    ⚠ Windows 坑：Python 的 HTTPServer 默认 allow_reuse_address=True，而 Windows 的
    SO_REUSEADDR 语义是「允许多个套接字绑同一端口」（Linux 完全不同）。实测在 Windows 上
    即使 8899 已被另一个 nav 服务监听，bind 依然会「成功」，随后请求被别的进程接走——
    看起来一切正常，其实启了个空壳。所以不能只看 bind 是否抛异常，必须先 connect 探一次。
    """
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def bind_server(bind, ports):
    """依次尝试 ports，返回 (server, port)；全部失败返回 (None, None)。"""
    last_err = None
    for port in ports:
        if port_busy(port):
            print("      [skip] %d 已被其它进程占用，顺延" % port)
            last_err = "port %d busy" % port
            continue
        try:
            srv = ThreadingHTTPServer((bind, port), Handler)
            return srv, port
        except OSError as e:
            last_err = e
    print("[err] 无法绑定 %s，已尝试端口 %s —— %s" % (bind, list(ports), last_err))
    print("      端口都被占用？显式指定一个空闲端口：python tools/nav/serve_nav.py --port 9000")
    return None, None


def main():
    try:  # 被重定向到文件时也能立刻看到启动信息
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    argv = sys.argv[1:]

    def opt(name, default=None):
        if name in argv:
            i = argv.index(name)
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                return argv[i + 1]
        return default

    if "--list" in argv:
        info = sysinfo(DEFAULT_PORTS[0], DEFAULT_BIND)
        print("hostname =", info["hostname"])
        for x in info["lanIps"]:
            print("  %-16s primary=%-5s %s" % (x["ip"], x["primary"], x["guess"]))
        if not info["lanIps"]:
            print("  (未探测到内网 IP)")
        return 0

    cfg = load_services()
    bind = opt("--bind", DEFAULT_BIND)
    port_arg = opt("--port")
    ports = (int(port_arg),) if port_arg else DEFAULT_PORTS

    if not os.path.exists(NAV_FILE):
        print("[err] 找不到导航页：%s" % NAV_FILE)
        return 1

    print("[1/4] 载入服务清单 %s（%d 个服务，版本 %s）"
          % (os.path.relpath(SERVICES_FILE, ROOT), len(cfg["services"]),
             cfg.get("version", "?")))
    print("[2/4] 探测本机内网 IP ...")
    info = sysinfo(ports[0], bind)
    for x in info["lanIps"]:
        print("      %-16s %s%s" % (x["ip"], x["guess"], "   <- 默认路由口" if x["primary"] else ""))

    srv, port = bind_server(bind, ports)
    if srv is None:
        return 1

    srv.nav_cfg = cfg
    srv.nav_port = port
    srv.nav_bind = bind
    srv.daemon_threads = True

    print("[3/4] 托管 %s" % os.path.relpath(NAV_FILE, ROOT))
    print("[4/4] 打开以下任一地址（Ctrl+C 停止）：")
    print("      本机   : http://localhost:%d/" % port)
    print("      本机   : http://127.0.0.1:%d/" % port)
    if info["primary"]:
        print("      局域网 : http://%s:%d/   <- 分享给手机/同事" % (info["primary"], port))
    else:
        print("      [warn] 未探测到内网 IP，局域网地址不可用")

    if "--open" in argv:
        try:
            webbrowser.open("http://localhost:%d/" % port)
        except Exception:
            pass

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[stop] 已停止")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

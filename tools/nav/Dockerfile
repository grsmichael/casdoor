# Casdoor 本地开发服务导航（tools/nav）的容器镜像。
#
# 服务本体是零第三方依赖的纯标准库 Python，所以镜像只需要一个 Python 运行时；
# 额外的 docker-cli 是为了让「容器状态」那一栏能真的执行 `docker ps`——它需要
# compose 里挂载 /var/run/docker.sock（只有展示用途，探测判定不依赖它）。
FROM python:3.12-alpine

RUN apk add --no-cache docker-cli curl

WORKDIR /nav

# 三个文件就是全部：脚本、唯一数据源、导航页本体
COPY serve_nav.py services.json 服务导航.html ./

# 容器内 127.0.0.1 是容器自己，必须把探测目标指到宿主机，否则全部服务误报 down。
# Docker Desktop 上 host.docker.internal 开箱可用；Linux 需在 compose 里加
# extra_hosts: "host.docker.internal:host-gateway"（本目录的 compose 已加，两条都写全，
# 换机器不用改）。NAV_HOST_IP 让页面上的「共享给其他设备」显示宿主机 IP 而不是容器 IP。
ENV NAV_PORT=8899 \
    NAV_BIND=0.0.0.0 \
    NAV_PROBE_HOST=host.docker.internal

EXPOSE 8899

# -u 无缓冲输出，保证 docker logs 能实时看到启动信息
CMD ["python", "-u", "serve_nav.py"]

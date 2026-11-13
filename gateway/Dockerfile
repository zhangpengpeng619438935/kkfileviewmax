# 基础镜像用完整仓库路径: 内网加速器 docker.m.daocloud.io 会返回 502,
# buildkit 解析 python:3.11-slim 的元数据会失败; 换成本地已缓存/可拉的源。
# 如需切回官方镜像, 改回 python:3.11-slim 即可(要能连上镜像源)。
FROM docker.1ms.run/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 网关要直接调新版 LibreOffice 转 PDF(绕开 kkFileView 的 jodconverter),
# 这里补上 soffice 运行所需的图形库与中文字体。缺任何一个都会报
# "error while loading shared libraries: libXinerama.so.1" 之类, 转码直接失败。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libxinerama1 libxext6 libxrender1 libsm6 libice6 \
      libx11-6 libx11-xcb1 libxcb1 libxau6 libxdmcp6 \
      libcups2 libdbus-1-3 libglib2.0-0 libfontconfig1 libfreetype6 \
      libcairo2 libpango-1.0-0 libnss3 libxslt1.1 libxml2 \
      fonts-wqy-zenhei fonts-wqy-microhei \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
# 内网直连 pypi.org 不稳定(多次构建卡在 pip install), 用国内镜像源 + 重试
RUN pip install --no-cache-dir --retries 5 --timeout 60 \
      -i https://mirrors.aliyun.com/pypi/simple \
      -r /app/requirements.txt

COPY app /app/app
COPY static /app/static

EXPOSE 9012

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:9012/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9012", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]

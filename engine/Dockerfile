# 在官方 kkFileView 镜像基础上补齐 Office 常用字体, 改善 Excel/Word 转 PDF 的排版还原度
FROM kkfileview:local

USER root

# Carlito/Caladea 与 Calibri/Cambria 度量兼容(等宽替换, 不改变列宽与分页)
# fonts-liberation 提供 Arial/Times New Roman 的度量兼容替代
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      fonts-croscore \
      fonts-liberation \
      fonts-liberation2 \
      fontconfig \
 && rm -rf /var/lib/apt/lists/*

# 把 Office 里常见但 Linux 上没有的字体映射到已装字体(保证度量接近, 排版不乱)
COPY fonts.conf /etc/fonts/local.conf

RUN fc-cache -f >/dev/null 2>&1 || true

# 保持官方镜像的启动方式
USER root
EXPOSE 8012

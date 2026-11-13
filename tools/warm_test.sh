#!/bin/sh
# 验证: 常驻 soffice 监听进程能否让后续转换免去冷启动
set -e
LO=/opt/libreoffice26.8/program/soffice
PROF=file:///tmp/lo_warm
export HOME=/tmp

pkill -f soffice 2>/dev/null || true
sleep 2
rm -rf /tmp/lo_warm /tmp/warm_out
mkdir -p /tmp/warm_out /tmp/warm_src
# 源文件由宿主机 docker cp 进来(gateway 容器没有 samples 目录)

echo "=== 1. 冷启动单次转换(无监听进程) ==="
S=$(date +%s%N)
$LO --headless --norestore -env:UserInstallation=file:///tmp/lo_cold \
    --convert-to pdf --outdir /tmp/warm_out /tmp/warm_src/real.docx >/dev/null 2>&1
E=$(date +%s%N)
echo "docx 冷启动: $(( (E-S)/1000000 )) ms"
rm -rf /tmp/lo_cold /tmp/warm_out/*

echo
echo "=== 2. 启动常驻监听进程 ==="
$LO --headless --norestore --nologo --nodefault --nofirststartwizard \
    -env:UserInstallation=$PROF \
    --accept='socket,host=127.0.0.1,port=2103;urp;StarOffice.ComponentContext' \
    >/tmp/lo_warm.log 2>&1 &
sleep 8
echo "监听进程数: $(pgrep -f 'port=2103' | wc -l)"

echo
for f in real.docx real.pptx bom_test.xlsx; do
  S=$(date +%s%N)
  $LO --headless --norestore -env:UserInstallation=$PROF \
      --convert-to pdf --outdir /tmp/warm_out /tmp/warm_src/$f >/dev/null 2>&1
  E=$(date +%s%N)
  echo "$f 走常驻进程: $(( (E-S)/1000000 )) ms"
  rm -f /tmp/warm_out/*.pdf
done

echo
echo "=== 3. 再跑一轮(验证稳定) ==="
for f in real.docx real.pptx; do
  S=$(date +%s%N)
  $LO --headless --norestore -env:UserInstallation=$PROF \
      --convert-to pdf --outdir /tmp/warm_out /tmp/warm_src/$f >/dev/null 2>&1
  E=$(date +%s%N)
  echo "$f 第二轮: $(( (E-S)/1000000 )) ms"
  rm -f /tmp/warm_out/*.pdf
done

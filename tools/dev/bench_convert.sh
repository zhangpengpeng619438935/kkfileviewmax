#!/bin/sh
# 对比 LibreOffice 转换耗时: 默认 vs 降图像质量 vs 关闭图片重采样
LO=/opt/libreoffice26.8/program/soffice
export HOME=/tmp
D=/tmp/verify
O=/tmp/conv_out
mkdir -p $O

run() {  # $1=标签 $2=源文件 $3...=额外参数
  label="$1"; src="$2"; shift 2
  rm -rf /tmp/lo_prof; mkdir -p $O; rm -f $O/*.pdf
  S=$(date +%s%N)
  $LO --headless --norestore --nologo --nodefault --nofirststartwizard \
      -env:UserInstallation=file:///tmp/lo_prof \
      --convert-to "$@" --outdir $O "$src" >/dev/null 2>&1
  E=$(date +%s%N)
  sz=$(find $O -name '*.pdf' -printf '%s\n' 2>/dev/null | head -1)
  echo "$label: $(( (E-S)/1000000 )) ms  PDF=${sz:-0} 字节"
}

XLSX="/tmp/verify/01 ：X6M  APQP   周会 （车厂& 示例 ）（O）---20260325.xlsx"
BOM="/tmp/verify/C-HUD-AR017A_总成BOM_V5.2.xlsx"
PPTX="/tmp/verify/15公司级EHS培训.pptx"

echo "=== XLSX 默认 ==="
run "APQP 默认" "$XLSX" pdf
run "BOM  默认" "$BOM" pdf

echo
echo "=== XLSX 降图像质量 ==="
F='pdf:calc_pdf_Export:{"UseLosslessCompression":{"type":"boolean","value":"false"},"ReduceImageResolution":{"type":"boolean","value":"true"},"MaxImageResolution":{"type":"long","value":"96"},"Quality":{"type":"long","value":"60"}}'
run "APQP 降质" "$XLSX" "$F"
run "BOM  降质" "$BOM" "$F"

echo
echo "=== PPTX 默认 (可能较久) ==="
run "EHS 默认" "$PPTX" pdf

echo
echo "=== PPTX 降图像质量 ==="
P='pdf:impress_pdf_Export:{"UseLosslessCompression":{"type":"boolean","value":"false"},"ReduceImageResolution":{"type":"boolean","value":"true"},"MaxImageResolution":{"type":"long","value":"96"},"Quality":{"type":"long","value":"60"}}'
run "EHS 降质" "$PPTX" "$P"

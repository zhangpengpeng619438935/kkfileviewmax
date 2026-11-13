"""扫描受控文件里的文本类文件与 zip 条目，统计编码风险面。

输出三类：
1. 文本文件（txt/log/csv/html/xml/md/json…）里有多少不是 UTF-8（GBK 等）；
2. zip 包里有多少条目没设 UTF-8 标志（GBK 打包）；
3. 命中的样例路径，便于抽查。
"""
import os
import sys
import zipfile

ROOT = "/cephfs_data/fileManagement/files/示例事业部/文档中心/受控文件"
TEXT_EXTS = {"txt", "log", "csv", "html", "htm", "xml", "md", "json",
             "sql", "ini", "conf", "yaml", "yml", "bat", "cmd", "ps1"}
MAX_READ = 4 * 1024 * 1024      # 文本文件最多读 4MB

stat = {}
samples = {"text": [], "zip": []}
zip_total = zip_nonutf8 = 0

for dp, dns, fns in os.walk(ROOT):
    if "旧版本" in dp:
        dns[:] = []
        continue
    for fn in fns:
        low = fn.lower()
        ext = low.rsplit(".", 1)[-1] if "." in low else ""
        full = os.path.join(dp, fn)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if ext in TEXT_EXTS:
            s = stat.setdefault(ext, [0, 0, 0])      # 总数 / 非UTF8 / 可GBK解
            s[0] += 1
            try:
                with open(full, "rb") as f:
                    data = f.read(MAX_READ)
            except OSError:
                continue
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                s[1] += 1
                try:
                    data.decode("gb18030")
                    s[2] += 1
                except UnicodeDecodeError:
                    pass
                if len(samples["text"]) < 12:
                    samples["text"].append(full.replace(ROOT, "…"))
        elif ext == "zip" and size <= 32 * 1024 * 1024:
            zip_total += 1
            try:
                z = zipfile.ZipFile(full)
                bad = [i for i in z.infolist() if not (i.flag_bits & 0x800)]
                if bad:
                    zip_nonutf8 += 1
                    if len(samples["zip"]) < 8:
                        samples["zip"].append("%s（%d/%d 条目无 UTF-8 标志）"
                                               % (full.replace(ROOT, "…"), len(bad),
                                                  len(z.infolist())))
                z.close()
            except Exception:
                pass

print("== 文本类文件编码 ==")
print("%-6s %8s %10s %10s" % ("扩展名", "总数", "非 UTF-8", "可 GBK 解"))
for ext, (n, bad, gbk) in sorted(stat.items(), key=lambda x: -x[1][0]):
    print("%-6s %8d %10d %10d" % (ext, n, bad, gbk))
print()
print("== zip 打包编码 ==")
print("检查 %d 个（≤32MB），其中 %d 个含未设 UTF-8 标志的条目" % (zip_total, zip_nonutf8))
print()
if samples["text"]:
    print("== 非 UTF-8 文本样例 ==")
    for p in samples["text"]:
        print("  " + p)
if samples["zip"]:
    print("== GBK 打包的 zip 样例 ==")
    for p in samples["zip"]:
        print("  " + p)

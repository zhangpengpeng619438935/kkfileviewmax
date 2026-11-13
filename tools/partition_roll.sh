#!/usr/bin/env bash
# access_log / audit_log 按天分区滚动。
#
# 真正的逻辑在 partition_roll.py（逐日核对，能补历史缺口）。
# 这个文件只保留 crontab 的调用入口，免得改定时任务配置。
#
# 原版用 MAX(PARTITION_DESCRIPTION) 判断"覆盖到哪天"，只要最后一天够远就认为健康，
# 中间缺的天永远补不上 —— 实测缺 10-27 / 11-13 / 11-15 / 11-17 四天，
# 缺的那天被前一个分区连带装下（p20261028 里同时有 10-27 的 426 行和 10-28 的 539 行），
# 按天 DROP PARTITION 会删错数据，分区裁剪对那天也失效。详见 .py 文件头。
#
# crontab: 10 2 * * * cd /opt/kkfileview-service && flock -n /tmp/kk_partition.lock \
#            bash tools/partition_roll.sh >> /tmp/kk_partition.log 2>&1
set -uo pipefail
cd "$(dirname "$0")/.."
echo "$(date '+%F %T') ---- 分区滚动开始 ----"
/usr/bin/python3 tools/partition_roll.py "$@"
rc=$?
echo "$(date '+%F %T') ---- 分区滚动结束 (rc=$rc) ----"
exit $rc

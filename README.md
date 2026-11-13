# kkFileViewMax

给业务系统用的**文件在线预览网关**：业务方只提供一个可下载的文件 URL 和一个工号，
网关负责把文件转成浏览器能看的样子，全程带鉴权、限流、水印、审计和统计。

底层转换内核是 [kkFileView](https://github.com/kekingcn/kkFileView)，本仓库在它前面
加了一层 FastAPI 网关，补齐了把它直接暴露给业务系统时会缺的东西。

---

## 为什么需要这层网关

kkFileView 本身是一个"转换服务"：给它文件地址，它返回渲染好的页面。但真正接入
OA / PLM / MES 这类系统时，会立刻遇到几个问题：

| 问题 | 直接暴露 kkFileView | 本项目的做法 |
|---|---|---|
| 谁能看 | 无鉴权，知道地址就能看 | `appId + appSecret` 换 token，请求必须带工号 |
| 看多少 | 无限制，一个脚本就能刷满 CPU | 三级限流：全局 / 单系统 / 单工号 |
| 谁看过 | 无记录 | 全量审计：人、时间、来源系统、文件名、结果 |
| 性能 | 每次打开都重新转换 | 产物缓存 + 指纹校验 + 定时预热，热路径 10~40ms |
| 可观测 | 只能看容器日志 | 统计看板（失败原因/延迟分布/热点文件）+ 服务告警 |
| 泄露 | 下载即原图 | 预览页叠加水印 |

网关把这些收敛到一个入口，转换引擎退到内网、不再对外暴露。

---

## 主要特性

**格式覆盖（30+）**

Office（doc/docx/xls/xlsx/ppt/pptx/wps/et/dps）、ODF、PDF、图片、CAD 图纸
（dwg/dxf/dwf/plt）、3D 模型（step/stp/iges）、压缩包（zip/rar/7z/tar）、
邮件（eml/msg）、电子书（epub）、流程图（bpmn/drawio）、代码与纯文本、音视频。

**渲染方式按文件特性分流**，不是一律转 PDF：

- 表格 → 生成 [Univer](https://github.com/dream-num/univer) 快照，可滚动、可切工作表（毫秒级）
- 3D STEP → Three.js 交互预览，支持爆炸图、两点测距、标准视角、线框/实体切换
- 纯文本 → 网关直接输出 `<pre>`，不再过引擎（实测 13 秒 → 3 毫秒）
- 其余 → kkFileView 转 PDF / 图片

**缓存治理**

- 产物按「文件身份」寻址：签名参数、路径里的一次性令牌、同一文件的不同 URL 形态，
  都归一到同一个键，避免"同一份文件被当成 N 个文件各转一遍"
- 每次打开都做指纹比对（大小 + mtime / ETag）：源文件变了立刻重转，没变就续期
- 定时回收：按 mtime 淘汰 + 容量上限，删除产物时同步清理 Redis 状态
- 预转换 + 热点预热：把 DWG（一次 20~30 秒）和热门 STEP 提前转好

**可观测**

- 看板：访问量 / 成功率 / 延迟分位 / 失败原因下钻 / 审计明细 / 导出 CSV
- 服务告警 11 条规则（限流风暴、排队超时、CPU/内存/磁盘、缓存超量、转换异常…）
- 定时任务（数据库备份、分区滚动、缓存回收）失败也会落进同一张告警表

---

## 架构

```
   业务系统                    浏览器
      │                          │
      │ ① POST /preview/url      │ ④ 打开 previewUrl
      │    (appId+secret+工号)    │
      ▼                          ▼
  ┌──────────────────────────────────────┐
  │  kk-gateway  (FastAPI · 9012)        │
  │  鉴权 · 限流 · 审计 · 统计 · 缓存      │
  └───────┬──────────────┬───────────────┘
          │              │
          ▼              ▼
   ┌────────────┐  ┌──────────┐  ┌─────────┐
   │ kkfileview │  │  MySQL 8 │  │ Redis 7 │
   │ 转换内核    │  │ 审计/统计 │  │ 缓存索引 │
   │ +LibreOffice│  │          │  │ 限流计数 │
   └────────────┘  └──────────┘  └─────────┘
```

转换引擎只在容器内网可达，浏览器拿不到它的地址。

---

## 快速开始

```bash
git clone https://github.com/<your-name>/kkfileviewmax.git
cd kkfileviewmax

cp .env.example .env
# 至少改这三项：ADMIN_TOKEN、SECRET_KEY、MYSQL_ROOT_PASSWORD
vi .env

docker compose up -d
curl http://127.0.0.1:9012/health
```

看板：`http://127.0.0.1:9012/`，用 `.env` 里的 `ADMIN_TOKEN` 登录。

> 首次启动后请立刻到「管理接入系统」里把示例账号的密钥改掉；
> `sql/002_init_data.sql` 里的 `CHANGE-ME-*` 只是占位符，不是可用凭据。

---

## 接口速览

```bash
# 1) 用 appId/appSecret 换 token（有效期 2 小时）
curl -X POST http://127.0.0.1:9012/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"appId":"kk-demo","appSecret":"<your-app-secret>"}'

# 2) 提交文件地址 + 工号，拿到预览地址
curl -X POST http://127.0.0.1:9012/api/v1/preview/url \
  -H 'X-App-Id: kk-demo' \
  -H 'Authorization: Bearer <accessToken>' \
  -H 'Content-Type: application/json' \
  -d '{"empNo":"EMP0001","empName":"张三","dept":"研发部",
       "fileUrl":"http://files.example.com/a/b/report.docx",
       "fileName":"report.docx"}'

# 3) 浏览器打开返回的 data.previewUrl
```

完整参数、错误码、Java / Python / 前端示例见
[`docs/API说明文档.md`](docs/API说明文档.md) 和 [`docs/系统接入指南.md`](docs/系统接入指南.md)。

---

## 目录结构

```
gateway/app/         网关主体（FastAPI）
  engine.py            转换调度、缓存寻址、指纹校验
  filekey.py           文件身份（缓存键）算法 —— 网关与预转脚本共用同一份
  sheet.py             表格快照 / 静态表格渲染
  univer_data.py       Univer 工作簿快照
  step_view.py         3D 查看器页面（Three.js）
  alerts.py            服务告警规则与生命周期
  stats_api.py         看板接口
  collector.py         埋点采集、聚合、明细清理
  security.py          鉴权、令牌、IP 白名单
  ratelimit.py         三级限流
gateway/static/      看板前端与离线依赖（Univer / Three.js / ECharts）
engine/              转换内核侧的补充资源
sql/                 建表与初始化数据
tools/               运维脚本：预转、预热、缓存回收、备份、分区滚动…
docs/                对接文档
deploy/              systemd unit 与部署辅助
```

---

## 运维要点

几条容易被忽略、但踩过坑的地方：

- **分区表**：`access_log` / `audit_log` 按天分区，`tools/partition_roll.py` 每日核对
  「最早那天 ~ 今天+7」有没有独立分区，缺哪天就把装它的分区拆开。只看"最大上界"
  的老写法会漏掉中间的空洞，导致某一天的数据被前一个分区连带装下。
- **备份**：`tools/backup_db.py` 每天 dump 并校验（能解开 + 含关键表），失败进告警表。
- **缓存上限**：`tools/gc_cache.py` 按 mtime + 容量回收，删产物时同步清 Redis 键。
- **定时任务失败**：`tools/cron_run.py` 包一层，失败自动记进「服务告警」，成功自动恢复。
- **限流阈值**要和实际部署匹配：`GLOBAL_QPS` / `DEFAULT_APP_QPS` / `EMP_QPS`。

---

## 许可

本项目基于 [kkFileView](https://github.com/kekingcn/kkFileView)（Apache-2.0）构建；
前端离线依赖 Univer / Three.js / ECharts 分别遵循其原始许可。

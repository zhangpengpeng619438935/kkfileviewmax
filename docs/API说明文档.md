# 文件预览服务 · 对外 API 说明文档

| 项 | 内容 |
|---|---|
| 文档版本 | v1.2 |
| 更新日期 | 2026-10-22 |
| 引擎版本 | 自研预览引擎（内核：kkFileView） |
| 适用对象 | 需要接入文件在线预览能力的业务系统（OA / PLM / MES / 质量系统等） |
| 服务地址 | `http://127.0.0.1:9012` |
| API 基址 | `http://127.0.0.1:9012/api/v1` |

---

## 一、服务简介

业务系统只需要提供一个**可直接下载的文件 URL**，本服务负责把文件转成浏览器能看的样子，并返回一个预览地址。业务系统不用关心 Office 转换、字体、分页、水印这些细节。

**支持 30+ 类格式**：Office（Word/Excel/PPT/WPS/ODF）、PDF、图片、CAD 图纸、3D 模型、流程图（BPMN/draw.io）、电子书、邮件、压缩包、代码、音视频等，完整清单见 **第六章**。

```
业务系统 ──(1) 换 token──▶ 预览网关
        ──(2) 提交文件 URL + 工号──▶ 预览网关
        ◀──(3) 预览地址 previewUrl
浏览器 ──(4) 打开 previewUrl──▶ 预览网关 ──▶ 返回渲染好的页面（PDF / 表格 / 图片 / 文本）
```

特点：

- 预览引擎（kkFileView + LibreOffice）**不对外暴露**，只有网关一个入口
- 自动加水印（"内部资料 禁止外传"）
- 全量审计：谁、什么时候、从哪个系统、看了哪个文件
- 转码结果缓存 **30 分钟**（可配 `CACHE_TTL_MINUTES`），有效期内同一文件第二次打开基本是 0 秒

---

## 二、接入准备

### 2.1 获取 appId / appSecret

> **appSecret 的获取方式：找系统管理员，按"要接入的系统名称"生成。**

具体流程：

1. 向系统管理员提供：**系统名称**（如"仓储管理系统"）、建议的英文标识（如 `wms`）、用途、来源服务器 IP、联系人
2. 管理员在管理后台创建账号，系统生成 `appId` 与 `appSecret`
3. `appSecret` 只在**创建时和重置时**展示一次，请立即保存到配置中心/密钥管理，不要写进前端代码或代码仓库
4. 如怀疑泄露，联系管理员**重置密钥**，旧密钥立即失效

### 2.2 文件地址要求

`fileUrl` 必须满足：

| 要求 | 说明 |
|---|---|
| 可直接下载 | 返回文件二进制内容，不能是登录页、HTML 跳转页或需要交互的页面 |
| 网关可访问 | 网关所在服务器能访问到该地址；默认只允许 `TRUST_HOSTS` 里的域名，新域名需管理员加白 |
| 签名有效 | 如果用的是带签名的临时链接（OSS/S3/业务系统签名），有效期要覆盖用户查看时间，建议 ≥ 30 分钟 |
| 特殊字符编码 | 文件名含空格、`&`、`+`、中文时，`fileUrl` 必须做 URL 编码，否则服务端下载会失败 |
| 不支持加密文档 | 需要密码才能打开的 Office/PDF 暂不支持 |

### 2.3 网络

- 业务系统服务器 → `<网关地址>:9012` 需放通
- 浏览器 → `<网关地址>:9012` 需放通（预览页是浏览器直接打开的）
- 如需 IP 白名单，把业务系统出口 IP 提供给管理员

---

## 三、鉴权

| 项 | 说明 |
|---|---|
| `X-App-Id` | 调用方系统标识，**所有业务接口必传**（请求头） |
| `Authorization` | `Bearer <accessToken>`，由 `/auth/token` 换取，有效期 **2 小时** |
| 工号 `empNo` | 业务接口必填，3–32 位字母/数字/下划线，用于审计追溯 |
| 预览地址有效期 | `previewUrl` 里的令牌 **10 分钟**有效，过期重新申请 |
| 限流 | 全局 120 QPS / 单系统 60 QPS / 单工号 20 QPS，超限返回 429 |

> 令牌只用于服务端调用；**不要把 accessToken 下发到浏览器**。浏览器只需要 `previewUrl`。

---

## 四、快速开始

```bash
# 1. 换令牌（appId/appSecret 找管理员申请）
curl -X POST http://127.0.0.1:9012/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"appId":"<APP_ID>","appSecret":"<APP_SECRET>"}'

# 2. 申请预览地址
curl -X POST http://127.0.0.1:9012/api/v1/preview/url \
  -H 'Content-Type: application/json' \
  -H 'X-App-Id: <APP_ID>' \
  -H 'Authorization: Bearer <ACCESS_TOKEN>' \
  -d '{"fileUrl":"https://your-system/files/report.docx","empNo":"C0000002","empName":"张三"}'

# 3. 把返回的 data.previewUrl 交给浏览器打开
```

```html
<!-- 前端：新窗口或 iframe 均可 -->
<iframe src="<previewUrl>" width="100%" height="800" frameborder="0"></iframe>
```

---

## 五、接口详情

### 5.1 获取访问令牌

`POST /api/v1/auth/token`

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| appId | string | 是 | 系统标识 |
| appSecret | string | 是 | 系统密钥 |

响应：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "accessToken": "eyJhcHAiOiJ3bXMiLCJleHAiOjE3OTI1NzY5MDEs...",
    "expiresIn": 7200,
    "qpsLimit": 60
  }
}
```

> 建议在服务端缓存 token，过期前 5 分钟再刷新，不要每次预览都换一次。

---

### 5.2 申请预览地址（主接口）

`POST /api/v1/preview/url`

请求头：`X-App-Id`、`Authorization: Bearer <token>`

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| fileUrl | string | 是 | 文件下载地址，见 2.2 |
| empNo | string | 是 | 查看人工号 |
| empName | string | 否 | 姓名，写入审计 |
| dept | string | 否 | 部门 |
| fileName | string | 否 | 展示用文件名；缺省从 URL 推断 |
| wait | bool | 否 | 是否同步等待转码完成，默认 `true` |
| waitSeconds | int | 否 | 最多等多少秒，默认 30；`0` 表示用服务端配置 |

响应（文件已就绪，可以直接打开）：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "status": "ready",
    "fileKey": "10b757e97d811213e2b93aaed73296c9...",
    "fileName": "2026年体系文件清单.xlsx",
    "fileType": "office",
    "previewUrl": "http://127.0.0.1:9012/api/v1/preview/page?token=eyJmayI6...",
    "expiresIn": 600
  }
}
```

响应（超过 2.5 秒还没转完）：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "status": "converting",
    "fileKey": "c3f9...",
    "taskId": "c3f9b1...",
    "previewUrl": "http://127.0.0.1:9012/api/v1/preview/page?token=...",
    "expiresIn": 600,
    "waitedSeconds": 2.5,
    "pollUrl": "http://127.0.0.1:9012/api/v1/preview/status?taskId=c3f9b1..."
  }
}
```

> **首次响应承诺：任何格式 ≤ 3 秒**（默认只同步等 `PREVIEW_WAIT_SECONDS=2.5` 秒）。
> 超时会返回 `converting` **并同时给出 `previewUrl`**——直接打开这个地址即可，
> 页面会显示"转换中（已等待 N 秒）"并在转好后自动切换到内容；
> 也可以轮询 `pollUrl`，两种方式都支持。

`fileType` 取值：`office` / `pdf` / `image` / `txt` / `zip` / `cad` / `media` / `other`。

**关于 PPT/PPTX**：PPT 首次打开会立即返回 `status=ready` 并带 `provisional: true`，
预览页先展示"快速内容版"（按页列出文字和配图，约 0.2 秒可见），
后台继续生成高清 PDF，完成后页面自动切换，不需要调用方做任何处理。

> **同一个文件重复调用不会重复转码**（按文件身份缓存，默认 30 分钟）。
> 文件内容原地更新但 URL 不变时，**超过缓存有效期再次打开会自动重新下载并重新转换**，
> 用户看到的一定是当前最新内容，不需要管理员介入。

---

### 5.3 查询转码状态

`GET /api/v1/preview/status?taskId=<taskId>`

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "taskId": "c3f9b1...",
    "status": "ready",
    "fileName": "培训课件.pptx",
    "previewUrl": "http://127.0.0.1:9012/api/v1/preview/page?token=..."
  }
}
```

状态流转：`queued` → `converting` → `ready` / `failed`。
`failed` 时 `errMsg` 会给出原因（如源站 403、文件为空、格式不支持）。

---

### 5.4 打开预览页

`GET /api/v1/preview/page?token=<previewToken>`

- 浏览器直接打开，会 302 到对应的查看器（pdf.js / 表格页 / 图片页 / 文本页）
- 令牌 10 分钟有效，可重复打开（不是一次性的）
- 页面已带水印，无需业务系统额外处理

> 前端如果放在 iframe 里，请确认业务系统的 CSP `frame-src` 放通了预览服务域名。

**页面自身会处理两件事，调用方不用管：**

| 场景 | 页面行为 |
|---|---|
| 大 PPT 先出前 15 页（`partial`） | 顶部横幅提示进度；整本转好后**自动刷新并停在同一页**，不需要用户按 F5 |
| 缓存超过 30 分钟 | 重新打开时自动作废旧产物、重新下载源文件并重新转换，保证看到最新内容 |

调试用接口（页面内部使用，调用方一般不需要）：

`GET /api/v1/preview/partial?f=<fileKey>&kkt=<previewToken>`

```json
{"partial": "15/119", "ready": true, "ver": 1792650258,
 "cacheAgeSeconds": 420, "cacheTtlSeconds": 1800}
```

| 字段 | 含义 |
|---|---|
| `partial` | 当前只转出了前 N 页时的进度；完整版为空串 |
| `ready` | PDF 是否可读 |
| `ver` | PDF 文件的 mtime（秒）。变化即代表后台换过文件（补全 / 过期重建） |
| `cacheAgeSeconds` | 当前缓存的年龄（秒）；`-1` 表示该类型没有缓存产物 |
| `cacheTtlSeconds` | 缓存有效期（默认 1800 秒，由 `CACHE_TTL_MINUTES` 控制） |

---

### 5.5 预热（推荐给列表/详情页用）

`POST /api/v1/preview/warm`

在用户真正点击之前先转好，点开就是 0 秒。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| fileUrl | string | 是 | 文件地址 |
| empNo | string | 是 | 工号（写审计） |
| fileName | string | 否 | 文件名 |

响应：

```json
{"code":0,"data":{"status":"warming","fileKey":"...","taskId":"...",
 "pollUrl":"http://127.0.0.1:9012/api/v1/preview/status?taskId=..."}}
```

如果已经缓存，直接返回 `{"status":"ready"}`。预热是**异步**的，接口立即返回。

> 典型用法：文件列表渲染时，对当前页的 10~20 个文件各调一次 warm；
> 用户点开任意一个都是秒开，同时避免一次性预热全库。

---

### 5.6 健康检查（免鉴权）

`GET /health` → `{"status":"up","service":"kkfileview-gateway",...}`

业务系统可用它做探活。

---

## 六、支持的文件格式

> 引擎：自研预览引擎（内核：kkFileView） · 转换：LibreOffice **26.8.1** · 更新日期 2026-10-22

### 6.1 格式总览

接入方不用关心走哪条预览链路，但了解"大概多快"有助于设计交互
（比如列表页要不要先 `warm` 预热）。

| 类别 | 扩展名 | 预览方式 | 首次打开参考 |
|---|---|---|---|
| **Excel（新）** | `xlsx`、`xlsm` | **内置 Univer 表格**：单元格/公式/字体/填充/边框/合并/列宽行高/冻结窗格全还原，可切工作表、可选中复制（只读）；单元格内嵌图片与图表暂不显示 | **1.3 秒**（43KB / 4 表） |
| **Excel（旧 97-2003）** | `xls` | 先由 LibreOffice 转 xlsx，再进内置 Univer 表格 | **3.2 秒**（含 2 秒转换） |
| **WPS 表格** | `et`、`ett` | 转 PDF | 0.6~3 秒 |
| **CSV / TSV** | `csv`、`tsv` | 原生表格页（可排序、不截断） | 0.1 秒 |
| **Word** | `doc`、`docx`、`docm`、`dot`、`dotx`、`dotm` | 转 PDF 后用 pdf.js 查看 | 2~5 秒 |
| **WPS 文字** | `wps`、`wpt` | 转 PDF | 2~5 秒 |
| **PowerPoint（新）** | `pptx`、`pptm`、`ppsx`、`ppsm`、`potx` | 本地 LibreOffice 转 PDF → pdf.js：**矢量**、左侧页缩略图、页码跳转、缩放/演示；大文件先出前 N 页再补全 | 6 页约 **3 秒**（含转换）；已转换后 0.6 秒 |
| **PowerPoint（旧 97-2003）** | `ppt`、`pps`、`pot` | 先秒开文字内容页（内部升级为 pptx 再解析），随后两阶段出真实排版 | **1.5 秒**内容页 / 43 秒完整（119 页 / 46MB） |
| **WPS 演示** | `dps` | 老版二进制：先出内容页再转 PDF（同 `.ppt`） | 1.5~10 秒看内容 / 5~20 秒完整 |
| **ODF 办公套件** | `odt`、`ods`、`odp`、`ott`、`ots`、`otp`、`fodt`、`fods` | 转 PDF | 1~6 秒 |
| **RTF** | `rtf` | 转 PDF | 1~3 秒 |
| **Markdown** | `md`、`markdown` | **彩色 HTML 直读**：标题/引用/表格/代码块（含语法高亮）都有颜色，链接可点、图片可显示 | **0.02 秒**（29KB 文档实测 12ms 渲染） |
| **PDF** | `pdf` | pdf.js：缩放、翻页、文本选择、水印 | **0.01 秒** |
| **图片** | `jpg`、`jpeg`、`png`、`gif`、`bmp`、`webp`、`ico`、`jfif` | 图片查看器，可缩放 | 0.01 秒 |
| **TIFF** | `tif`、`tiff` | 图片查看器（多页 TIFF 逐页看） | 0.01 秒 |
| **SVG** | `svg` | 矢量图查看器（放大不失真） | 0.1 秒 |
| **CAD 图纸** | `dwg`、`dxf`、`dwf`、`dwt`、`dwfx`、`dng`、`cf2`、`plt` | **服务端出图**：引擎转 PDF 后由 PDFium 渲染成位图，浏览器直接显示，可切换 100%~300% 档位（pdf.js 渲不动这类图纸：444 万条线段要 4 分钟以上） | 接口 ≤2.5 秒；转换 18~30 秒后出图 **0.06~2.2 秒**，二次打开 0.07 秒 |
| **3D 模型** | `glb`、`gltf`、`obj`、`fbx`、`dae`、`3ds`、`ply`、`stl`、`3mf`、`ifc`、`step`、`stp`、`iges`、`brep`、`wrl`、`off`、`3dm`、`fcstd` | 浏览器内 3D 查看器（OpenCascade WASM 解析，可旋转/缩放、看网格树与顶点数、量尺寸）；**源文件按 gzip 传输**（STEP 文本压缩 3.7 倍），5 分钟内重开走浏览器缓存 | 页面 0.2 秒；**14MB 装配体 0.95 秒出模型**，47MB 需数分钟（终端 CPU 决定） |
| **流程图** | `bpmn`、`drawio` | 在线查看，节点与连线可读 | 0.2 秒 |
| **思维导图** | `xmind` | 在线查看（XMind Zen 格式） | 0.2 秒 |
| **电子书** | `epub` | 分章阅读 | 0.2 秒 |
| **邮件** | `eml` | 显示主题、收发件人、正文、附件列表 | 0.1 秒 |
| **版式文档（国产）** | `ofd` | 转图片 / PDF | 1~3 秒 |
| **医学影像** | `dcm` | 在线查看 | 1~3 秒 |
| **压缩包** | `zip`、`jar`、`war`、`tar`、`tgz`（网关直读目录）；`rar`、`7z`、`gz`（引擎树形浏览） | 列出包内文件清单（名称/原始大小/压缩后/修改时间），不提供在线解压下载 | **0.17 秒**（zip 直读）；7z/rar 0.3~3 秒 |
| **代码** | `java`、`py`、`js`、`css`、`html`、`php`、`go`、`c`、`cpp`、`cs`、`h`、`sql`、`json`、`yaml`、`yml`、`sh`、`rb`、`lua`、`jsp`、`aspx`、`ftl` | 语法高亮显示 | 0.1 秒 |
| **XML** | `xml`、`xbrl` | 树形结构查看 | 0.1 秒 |
| **纯文本** | `txt`、`log`、`ini`、`properties`、`conf`、`bat`、`cmd`、`gitignore`、`prg`、`bas`、`m` | 等宽文本显示 | 0.1 秒 |
| **音视频** | `mp4`、`mp3`、`wav`、`m4a`、`mpeg`、`ts`、`m3u8`、`mpd`、`flv` | HTML5 播放器（拖动进度条） | 0.1 秒 |
| **旧版视频（需转码）** | `avi`、`mov`、`wmv`、`mkv`、`3gp`、`rm` | **当前不可用**：容器内没有 ffmpeg | — |
| **其他任意后缀** | — | 提示"暂不支持在线预览"，提供下载原文件 | — |

### 6.2 大文件的预期时间（重要）

PPT 是最慢的格式，因为它要逐页渲染。以 **42MB / 119 页** 的课件实测：

| 时间点 | 状态 |
|---|---|
| 0.02 秒 | 接口返回，先显示文字内容页（能看标题和要点） |
| **12 秒** | **前 15 页真实排版可看**（顶部黄条提示"共 119 页，正在补全"） |
| 68 秒 | 完整 119 页；用户刷新即看到全部 |

其他格式的经验值（首次打开，未命中缓存）：

| 文件规模 | 耗时 |
|---|---|
| docx ≤ 5MB | 2~3 秒 |
| docx 20MB | 5~8 秒 |
| xlsx（走表格页） | 0.1~0.6 秒，与行列数相关 |
| PDF / 图片 | 0.01 秒（不转码，直接读） |
| 第二次打开同一文件（30 分钟内） | **0.01 秒**（命中缓存） |
| 隔一会儿再次打开，**源文件没变** | **约 0.7 秒**（本地 stat 比对指纹，产物续期，不重转） |
| 再次打开，**源文件已换** | 与首次相同（重新下载 + 重新转换） |

> **想让首屏更快**：在列表页/详情页先调 `POST /api/v1/preview/warm` 预热，
> 用户真正点开时通常已经转好了。

### 6.3 实测状态说明

上表格式分两类，接入前请注意：

| 状态 | 格式 |
|---|---|
| **已实测通过**（本环境真实文件转出并浏览器验证） | pdf、jpg/jpeg、png、gif、bmp、svg、txt、log、**zip**、**7z**、**md/markdown**、xlsx、xls、csv、docx、pptx、**ppt（97-2003 二进制）**、json、bpmn、drawio、epub、eml、gltf（3D）、tif、dxf、**dwg**、**step/stp（3D 装配体）**、**fcstd（FreeCAD 装配体）** |
| **引擎声明支持、本环境尚未用真实文件验证** | `ofd`、`dcm`、`xmind`（需 XMind Zen 完整结构）、`rar`/`gz`（只验了 7z 与 zip）、`iges/brep`（库内暂无该类文件，链路已通） |

未验证不代表不能用——是还没来得及拿真实文件跑。如果你要接入这几类，
建议先拿一个样本走一遍 `preview/url` 确认，再批量上线。

### 6.3.1 本轮验证记录（2026-10-22，真实文件）

用业务方提供的样本逐个体检，浏览器端用 Chrome 实测（含首屏绘制与内容出现时刻）：

| 文件 | 大小 | 结果 | 接口响应 | 内容可见 | 说明 |
|---|---|---|---|---|---|
| `427C-SUV室外台架-装配体.STEP` | 14MB | **正常** | 0.19s | 约 2.4s | 浏览器内 3D：网格树 + 136,184 顶点 / 136,802 三角面，可旋转缩放、量尺寸 |
| `427-C SUV-250326.stp` | 30MB | 正常（解析较久） | 0.03s | 35s 时仍在解析 | 同一条 WASM 解析路径，耗时随体积线性增长 |
| `427C-SUV目视台架（通用HY11）-装配体.STEP` | 47MB | **正常但很慢** | 0.18s | **约 2~5 分钟**（110s 时仍在解析，300s 内出模型） | 596,927 顶点 / 617,644 三角面；耗时是浏览器单线程 WASM 解析，与终端 CPU 直接相关 |
| `自动投图检测设备（手动目视投图）-布局平面图.DWG` | 7.1MB | **正常** | 2.5s（转壳） | 约 26s | 转 PDF 46MB（旧 SVG 模式为 191MB）；转换约 16s + 下载 9s |
| `80021585-…外壳后钣金件-展开.dwg` | 151KB | **正常** | 1.5s | 0.6s | 转 PDF 50KB，秒开 |
| `80021788-…框架上盖挡板A-展开下料图.DWG` | 150KB | 正常 | ≤2.5s | 约 1s | 同上 |
| `W026A金属风挡加工资料.7z` | 527KB | **正常** | 0.03s | 0.2s | 引擎树形浏览（zip 走网关直读更快） |
| `系统接入指南.md` | 18KB | **正常（彩色）** | 0.03s | 0.04s | 标题/代码/表格/链接全彩，渲染 3ms |
| `运维手册.md` | 75KB | **正常（彩色）** | 0.05s | 0.26s | 43 张表格、35 段代码块，渲染 12ms |
| `SK-F109 软件评审计划 Software Review Plan A1.xlsx` | 85KB | **正常** | 0.02s | 0.06s | 5 个表（1 个隐藏）、封面含 Logo 图片与 Wingdings 勾选框；列宽不再整体错位、符号乱码转 ☐/☑、按 Excel 的 activeTab 打开 |

**结论**

- **DWG**：PDF 模式可用，复杂的布局图 26 秒（首次），之后 0.6 秒（缓存）；小零件图亚秒级。
- **STEP/STP**：14MB 以内体验良好（约 2 秒出模型）；30MB 需等待数十秒；47MB 大装配体
  要 2~5 分钟——这是浏览器端 WASM 解析的物理限制（与终端 CPU 相关），不是预览服务排队。
  接口本身始终 <0.3 秒，页面也会一直显示"加载中"，不会白屏。
- **7z**：可正常树形浏览；zip/jar/tar 已改由网关直读（0.17s）。
- **Markdown**：不再是黑白 PDF，改为彩色 HTML（代码高亮、表格斑马纹、可点链接）。

### 6.3.2 本轮性能整改记录（2026-10-22，DWG / STEP）

| 项 | 改前 | 改后 | 说明 |
|---|---|---|---|
| 7.1MB 布局图 DWG 首开 | 45MB PDF + **246s 仍空白** | **30s 看到整张图**（转换 28.4s + 出图 1.8s） | pdf.js 逐条描边 444 万段跑不完，改由 PDFium 出图 |
| 同上二次打开 | 又下 45MB + 再渲染 | **0.07s** | 位图按档位缓存 |
| 150KB 零件图 DWG | 约 12s | **0.36s**（转换 0.3s + 出图 0.06s） | 同上 |
| 14MB STEP 装配体 | 模型 2.4s 可见 | **0.95s** | 源文件 gzip（14MB→约 4MB）+ 缓存 |
| 47MB STEP 装配体 | 下载 5.74s | 下载 **1.70s**（47MB→13.2MB） | 解析仍是终端 WASM 的物理耗时 |
| 重复打开 3D 模型 | 每次重下 | 5 分钟内走浏览器缓存 | `Cache-Control: private, max-age=300` |

新增接口（页面内部使用，调用方一般不需要）：

| 接口 | 用途 |
|---|---|
| `GET /cadview/<fileKey>?kkt=` | CAD 位图查看器（缩放/平移/页码） |
| `GET /cadpng/<fileKey>?kkt=&w=&p=` | 图纸位图，`w` 取 1600/2400/3600/5400/8000 |

### 6.3.3 Univer 预览（表格 + 幻灯片，2026-10-23 恢复幻灯片）

xlsx / xls / ppt / pptx 打开内置 Univer 页面，`previewUrl` 落在 `/univer/<fileKey>?kkt=...`。

| 接口 | 用途 |
|---|---|
| `GET /univer/<fileKey>?kkt=` | Univer 前端页面（单页应用，表格与幻灯片共用） |
| `GET /api/v1/univer/data?kkt=&vw=&vh=` | 返回快照 `{kind:"sheets"\|"slides", fileName, data, partial?}` |
| `GET /api/v1/univer/page/<fileKey>?kkt=&p=&w=` | 幻灯片第 p 页的 JPEG 位图（按需取，`w` 默认 1600） |

**大表格会返回 `kind:"redirect"`**：被整行格式刷到 XFD 列的表格（dimension 报 16384 列），
openpyxl 会按这个范围建对象（实测 1.6MB 文件 28 秒），此时 `data` 变成

```json
{"kind": "redirect", "url": "/pdfview/<fileKey>?kkt=..."}
```

前端 `location.replace(url)` 即可，落到 pdf.js 看 LibreOffice 转出的 PDF（同一文件 3 秒 8 页）。
阈值：`SHEET_HEAVY_COLS`（默认 1024 列）、`SHEET_HEAVY_CELLS`（默认 40 万格）。

`vw` / `vh` 是浏览器容器的 CSS 像素尺寸，只有幻灯片用：Univer 的幻灯片画布是 1:1
画像素的，页面给大了会被裁、给小了浪费空间，所以由浏览器量好容器、服务端按
`min((vw-48)/页宽, (vh-56)/页高)` 反算页面像素尺寸，两端用同一把尺子。

**幻灯片方案**：LibreOffice 26.8 把 ppt/pptx 转成 PDF，PDFium 按页渲染成 JPEG，
每页作为一张图片元素塞进 Univer Slides 的页面。翻页、缩放、全屏由 Univer 提供；
左侧"页面列表"栏默认关闭（`IUIPartsService.setUIVisible("left-sidebar", false)`），
翻页改由右下角悬浮控件 + 滚轮 + 键盘（←/→/PgUp/PgDn/Home/End）承担。

**位图为什么不换成形状解析**：Univer 1.1.0 的形状渲染在 `@univerjs-pro/slides`
（商业授权），自己解 pptx 的形状树做不到 SmartArt/母版继承的版式还原；
而 LibreOffice 已经排好版，位图与导出 PDF 完全一致。

**大 PPT 两阶段**：页数 > `PPT_PARTIAL_MIN`（默认 30）时先只转前 `PPT_PARTIAL_PAGES`
（默认 15）页，`data` 里带 `partial:"15/119"`；前端每 4 秒查一次
`/api/v1/preview/partial`，转完自动重载换成完整版。

**为什么要多一个快照接口**：Univer 1.1.0 的官方文件导入（XLSX/PPTX → snapshot）
属于 `@univerjs-pro` / Server SDK 的商业授权；免费 Web SDK 只接受原生快照。
网关用 openpyxl / PDFium 自己生成快照。

已知差异：Univer 表格不显示单元格内嵌图片与图表（需要 drawings 插件与图片资源）；
表格为只读。`UNIVER_PREVIEW=0` 可整体回退到旧的 HTML 表格 / pdf.js 通道，
`UNIVER_SLIDE_EXTS` 置空则只回退幻灯片。

### 6.3.4 加载耗时的口径（2026-10-22 修正）

**先说结论：以前看板上的"耗时"不是用户感受到的时间，现在改了。**

| | 旧口径 | 新口径 |
|---|---|---|
| 统计对象 | `/api/v1/preview/url` 这一个 HTTP 请求的处理时间 | 从申请预览地址到**首屏内容真正可见**的全过程 |
| DWG 7.1MB 冷启动 | **0.4 秒** | **约 20 秒**（实测 33.9 秒含引擎冷启动） |
| STEP 14MB | 0.19 秒 | 4~5 秒（本机实测 2MB 样例 4.7 秒） |
| 缓存命中 | 0.02 秒 | 0.7 秒（页面打开 + 位图出来） |

为什么差这么多：`preview/url` 只做三件事——校验令牌、查一下缓存标记、把预览地址拼出来，
所以恒定 0.2~0.4 秒。用户等的是**后面**那一段：转码 → 下载 → 首屏渲染。
旧口径把它整个漏掉了，于是看板写 0.4 秒、用户体感 20 秒，两边说的不是一件事。

**怎么统计的**

```
preview/url         → 记下"申请时刻"（Redis，30 分钟有效）
预览页内容可见      → 页面 sendBeacon 报 POST /api/v1/preview/loaded
网关                → 上报时刻 − 申请时刻，回填这条记录的 cost_ms
```

"可见"不是猜的：页面每 250ms 检查①大图 `naturalWidth>200` ②画布真的画出了东西
（缩到 16×16 采样、颜色种类 > 3）③3D 查看器的 `Vertices/Triangles` 面板出现，
任一命中才上报。所以这个数字就是用户看到画面的时刻。

`POST /api/v1/preview/loaded`（页面内部使用，调用方不用管）：

```json
// 请求
{"kkt": "<预览令牌>", "phase": "img|canvas|mesh", "ms": 1234}
// 响应
{"code": 0, "data": {"ok": true, "elapsedMs": 20114}}
```

看板取值规则：`/api/v1/stats/*` 里所有 avg/max/min 只统计 **`e2e=1`** 的记录
——也就是"页面真的报回了可见时刻"的那批。`open`（302 跳转）、文件流、
以及升级前只有接口耗时的历史记录都不参与平均，所以数字不会再被 0.4 秒稀释。
没有样本时看板显示 `—` 而不是 `0 ms`。
`/api/v1/stats/summary` 多返回 `loadMetricFrom`（口径开始生效的时刻）与 `e2e_cnt`（样本数）。

### 6.3.5 缓存过期不再无条件重转（2026-10-22）

原来 30 分钟一到就把产物删掉重转。CAD 转一次 19~30 秒，用户"上午看一眼、下午再看一眼"
正好每次都撞在过期上，于是体感就是"永远 20 秒"。

现在过期只代表"该确认一下源文件有没有变"：

| 源文件 | 行为 | 用户耗时 |
|---|---|---|
| 没变（本地文件的大小 + mtime 一致） | 产物续期，不重转 | **0.7 秒** |
| 变了 | 照旧重转 | 首次量级（DWG 约 20 秒） |
| 判断不了（签名过期、源站暂时不可达） | **保留旧缓存，不删不转** | 打开时间不变 |

另外后台每 20 分钟会给"最近 7 天转好、且即将过期"的文件做一次同样的比对（`CAD_KEEPALIVE`），
把重转挡在用户点开之前。**保活只续期不换内容**，源文件真变了它什么都不做，等下次打开时重转。

### 6.3.6 3D 实体（STEP/IGES/FCStd）预览（2026-10-22 起）

STEP/STP/IGES/FCStd 不再全部交给引擎的 O3DV（浏览器现场 WASM 解析，14MB 要 21 秒、
47MB 要 2~5 分钟）。预解析过网格的文件打开 `/stepview/<fileKey>`：

**FreeCAD 文档（.FCStd）**走同一条链路：解包 zip → 取所有 `*.Shape.brp` →
按 `Document.xml` 的父子链合成位姿 → 合并成一个网格。位姿**是否真的叠加由数据判定**
（比较两种假设下零件分布的紧密度，取更紧凑的），因为有的文档已把全局坐标烘进 brp，
再叠一次会让零件散开（实测最远零件 145mm → 1529mm）。36MB 文档：解析 49 秒、
网格 6.33MB(gz)、**打开 2.0 秒**。

| 接口 | 用途 |
|---|---|
| `GET /stepview/<fileKey>?kkt=` | 自研 3D 查看器（three.js，实体/线框切换、复位视角） |
| `GET /stepdata/<fileKey>?kkt=` | 预解析网格（gzip，`Content-Encoding: gzip`） |
| `GET /stepempty/<fileKey>?kkt=` | 模型里没有实体面时的结论页（2026-11-03 起） |

网格由 `tools/stepmesh/step2mesh.js` 离线生成（同一套 OCCT），14MB STEP →
4.71MB 网格 → gzip 1.31MB。实测端到端 **1.5 秒**（原先 21 秒）。

没有网格时自动退回引擎的 O3DV 页面，行为与升级前一致。`previewUrl` 的落点会
按有没有网格自动选择，调用方不用区分。

**例外：预解析过、但模型里没有实体面**（空装配体、只有曲线/曲面/基准面）。
这类文件交给 O3DV 也画不出东西，只会白解析几十秒再报
`The model doesn't contain any meshes.`（实测 56 秒）。现在落到 `/stepempty`：

```
该模型没有可显示的实体面，无法 3D 预览
预解析结果为空：模型里没有实体面（只有曲线、曲面或基准面）
3D 预览只画实体面；曲线、曲面、基准面不参与渲染。
需要查看结构请用 CAD 软件打开原文件
```

标记由离线预解析写入（`<fileKey>.stepmesh.empty`），源文件更新后 `invalidate`
会一并清掉，重新解析拿到网格就恢复正常。

### 6.3.7 3 秒加载的实现方式与边界（2026-10-22）

DWG 现场转换的物理下限是 9 秒（解析 4 秒 + 光栅 5.3 秒，实测降到 400px 宽也不变），
所以"3 秒"靠的是**预转换**而不是更快的转换：

| 类型 | 预转命令 | 预转后 | 未预转（首次） |
|---|---|---|---|
| DWG/DXF（33 个） | `preconvert.py --ext dwg` | **0.7 秒** | 17~20 秒 |
| STEP/IGES（1082 个） | `preconvert_step.py --max-mb 5` | **0.6~1.5 秒** | 4~21 秒（视体积） |
| FCStd（数量少、单个体积大） | `preconvert_step.py --max-mb 50` | **2.0 秒** | 引擎不支持（无限转圈） |

调用方建议：**在文件列表/详情页调 `POST /api/v1/preview/warm`**（立即返回，后台转），
用户真正点击时通常已命中缓存。这是让"从未打开过的文件"也进 3 秒的唯一办法。

### 6.4 明确不支持

**格式不支持时，页面只显示一句话**：`格式暂时不支持预览`（白底居中，没有品牌、
没有插图、没有原因说明、不注入水印与加载计时）。判断依据是引擎返回的
"系统暂不支持在线预览"页，网关代理时把它整页换掉，所以不用维护扩展名清单 ——
引擎不认什么，这里就提示什么。

| 场景 | 原因 |
|---|---|
| 加密/带密码的 Office、PDF | LibreOffice 无法在无人值守下解密 |
| **企业文档加密系统包裹的文件**（首字节 `%TSD-Header-###%` 等） | 服务器侧只能拿到密文，见下方说明 |
| 需要登录才能下载的文件地址 | 服务端拉不到源文件（401/403） |
| 带鉴权签名的地址且已过期 | 源站返回 403，接口会返回 400 并指明原因 |
| 可执行文件（`exe`、`dll`、`bat`、`msi`、`sh`） | 安全策略禁用预览 |
| 源站返回 0 字节的文件 | 接口返回 400「文件内容为空」 |
| `avi`/`mov`/`wmv`/`mkv`/`3gp`/`rm` 视频 | 容器无 ffmpeg，转码不可用 |
| SolidWorks（`sldprt`/`sldasm`/`slddrw` 等，库内 6223 个） | 引擎无对应转换器，显示上述统一提示 |
| 三坐标测量程序（`.cad`） | 同上 |

**企业文档加密（TSD）文件**：这类文件在装了加密客户端的电脑上打开是透明的，
但源站（个人/共享盘）吐给服务器的字节流是密文——`zipfile` 都认不出来，LibreOffice
只会报 `source file could not be loaded`。2026-10-23 起，网关在拿到源文件前 80 字节时
就判定（`preview/url` 随机 422，耗时 < 0.1 秒），不再排进转换队列。

**判定不按扩展名限制**（2026-10-29 起）。之前白名单只含 Office，导致 3D 模型漏判：
`CA_C318_ARHUD_3.1INCH_V2.0_20241220.stp`（TSD 密文）被放行给引擎的 O3DV，
浏览器解析后拿不到任何网格，报 `Failed to import model. The model doesn't contain any meshes.`，
还白等 56 秒。现在任何类型都看头 80 字节，只认魔数
（`%TSD-Header-###%` / `%ESAFENET` / `%Sangfor` / `%DLP`），所以文本类文件不会被误判。

```json
HTTP 422
{"detail": "该文件被TSD 文档加密保护，服务器无法解密预览"}
```

预览页（`/api/v1/preview/page`、`/prog`）直接显示**同一句话**，不写技术细节：

```
该文件被TSD 文档加密保护，服务器无法解密预览
请在安装了加密客户端的电脑上打开原文件
```

接口的 `detail` 与页面正文由同一个函数生成（`encrypted_msg()`），不会两边说法不一致。
调用方拿到 422 后把 `detail` 原样弹给用户即可，不用自己拼文案。

实测把 `KO11-壳体按压异响-260928.pptx`（13MB、整目录都是密文）从 **33.9 秒白等**
压到 **0.05 秒出结论**；`H77-ARHUD结构技术方案-20241206.pptx` 同理。

### 6.5 页面品牌（`BRAND_NAME`）

引擎自带 `kkFileView` 字样（压缩包清单页的 `<h1>` 等），网关代理 HTML 时统一替换：

```bash
BRAND_NAME=自研预览引擎
```

只替换文本节点（`>kkFileView<`），不会改到 `kkFileView-4.4.0.jar` 这类资源路径。
置空则保持引擎原样。

### 6.6 压缩包内的文件预览（2026-10-23 新增）

压缩包的 `previewUrl` 落在 `/sheet/<fileKey>`，页面是清单：文件名可点、顶部可按名字过滤。
点开后按扩展名分流，全部在网关侧完成，不再"只给清单不给内容"。

| 接口 | 用途 |
|---|---|
| `GET /archive/<fileKey>/view?p=<条目>&chain=&kkt=` | 点开清单里的一个文件（`p` 为空则列当前层清单） |
| `GET /archive/<fileKey>/raw?p=<条目>&chain=&kkt=` | 下载该条目原文件（`Content-Disposition: attachment`） |

`chain` 是嵌套压缩包的路径（base64url 编码的 JSON 数组），只在"包里还有包"时出现；
页面的"← 返回上级"靠它回退。

**支持的压缩格式**：`zip/jar/war/ear/apk/epub`（标准库）、`tar/tgz/tbz2/txz/gz/bz2/xz`
（标准库）、`rar/r00/7z/cab/iso`（7-Zip 静态二进制）。rar/7z 的清单与条目读取
依赖容器内的 `/opt/kk-tools/7zz`，缺失时自动退回引擎的只读清单页。

**条目怎么渲染**

| 类型 | 渲染方式 |
|---|---|
| 图片（jpg/png/gif/bmp/webp/svg/ico/avif） | 直接出字节，`image/*` |
| PDF | 直接出字节，浏览器内置查看器 |
| 文本/源码/日志（txt/log/csv/json/xml/py/js/…） | 等宽文本页（含"下载原文件"，超 3MB 截断） |
| xlsx / xlsm | 原生 HTML 表格（不受打印区域裁列） |
| doc/docx/rtf/wps/odt | LibreOffice 转 PDF 后内联 |
| ppt/pptx/pps/odp | 同上 |
| mp4/webm/mp3/wav/m4a 等 | 直接出字节，浏览器播放 |
| 其它（CAD/3D/可执行…） | 提示页 + 下载按钮 |

**边界**

- 单个条目解压上限 `ARCHIVE_ENTRY_MAX_MB`（默认 300MB），超出返回 413；
- 压缩包本体会缓存在 `<fileKey>.arc`，点开条目时直接读它；
  仓库里最大的是 187MB 的作业指导书包，缓存总量超过 `ARCHIVE_CACHE_TOTAL_MB`
  （默认 15GB）时按最旧删除（含包内 Office 转出的 `.arc-*.pdf`）；
- 缓存时长与其它产物一致（`CACHE_TTL_MINUTES`，默认 1 分钟），过期后重新下载取最新；
- 内层嵌套只支持 zip/tar（rar/7z 需要落盘，内存里读不了），嵌套层数不限。

---

## 七、限流与配额

| 维度 | 默认上限 | 超限返回 |
|---|---|---|
| 全局 | 120 QPS | HTTP 429 |
| 单系统（appId） | 60 QPS | HTTP 429 |
| 单工号（empNo） | 20 QPS | HTTP 429 |

三个维度是**与**关系：任何一维超限都会被拒。滑动窗口（最近 1 秒计数），不是自然秒。

### 7.1 批量任务请用系统工号

系统的批处理（归档、批量导出、巡检）不要把某个真人工号当服务账号用：那个工号
20 QPS 一满，同系统里真人打开预览也会被 429。

改用 `SYS_` 前缀的工号，例如 `SYS_PLM`、`SYS_WMS`：

```json
{"fileUrl": "http://...", "empNo": "SYS_PLM", "empName": "PLM 批处理"}
```

这类工号**不参与单工号限流**，只受 appId 配额约束（默认 60 QPS，可按系统调整）。
`empName` 建议写清用途，审计日志里能分辨是人还是批处理。

前缀可通过 `SYS_EMP_PREFIX` 改（默认 `SYS`）。

### 7.2 调整单个系统的配额

```bash
curl -X POST http://127.0.0.1:9012/api/v1/admin/app/plm/qps \
  -H 'X-Admin-Token: <ADMIN_TOKEN>' \
  -H 'Content-Type: application/json' \
  -d '{"qpsLimit": 60}'
```

返回 `{"appId":"plm","qpsLimit":60}`。批量归档窗口期可以临时抬到 60，平时压回 20~30
保护其它系统；所有系统共享全局 120 QPS。

### 7.3 观测

`GET /metrics`（Prometheus 文本，仅内网 IP 可访问）里有
`kk_ratelimited_total`（被限流总数）与 `kk_requests_total{group,status}`，
可以按系统看出是谁在被限。告警阈值见运维手册「二·三十七」。

---

## 七·四 预览失败原因接口

```bash
GET /api/v1/stats/failures
```

> 这个接口回答「**哪个文件、为什么打不开**」；服务压力类的告警在七·五。
> 2026-11-03 之前它挂在 `/stats/alerts` 上，名字容易和「服务告警」混，已改名。

| 参数 | 说明 |
|---|---|
| `days` | 统计天数，默认 180，上限 180（更早的数据已被 `RETENTION_DAYS` 清理） |
| `appId` / `empNo` | 按系统 / 工号过滤（普通账号只能看自己的系统） |
| `status` | 状态码，逗号分隔，如 `422,429` |
| `kind` | 失败类型：限流 / 加密文件 / 源文件不可达 / 超时 / 服务异常 / 转换失败 / 其它 |
| `keyword` | 文件名或原因关键字 |
| `page` / `size` | 分页，`size` 上限 200 |
| `export=csv` | 导出（最多 5 万条，UTF-8 BOM） |

返回：

```json
{"code":0,"msg":"ok","data":{
  "days":180,"total":5977,"page":1,"size":50,
  "summary":{"total":5977,"files":217,"apps":8,"emps":28,"today":1023},
  "byDay":[{"d":"2026-11-03","n":1021}],
  "byKind":[{"kind":"源文件不可达","n":5612}],
  "byStatus":[{"status_code":400,"n":5785}],
  "byApp":[{"app_id":"kk-demo","app_name":"演示系统","n":1234}],
  "list":[{"id":1,"created_at":"2026-11-03 20:09:11","app_id":"kk-demo",
           "emp_no":"EMP0001","file_name":"x.pdf","file_ext":"pdf",
           "kind":"限流","status_code":429,"error_msg":"限流：该分钟首次被拒",
           "client_ip":"127.0.0.1"}]}}
```

口径：一次失败（`success=0`）算一条。**429 限流按「每分钟 × 每系统」采样一条**，
不是逐条记录（防限流风暴压垮存储）；真实的限流次数在 Redis 的分钟桶里，
服务告警用它判断（见七·五）。

---

## 七·五 服务告警接口

```bash
GET /api/v1/stats/alerts
```

只看「**服务扛不扛得住**」：多系统同时被限流、排队/加载超 5 分钟、CPU/内存/磁盘逼近限额、
转换异常。一条告警从触发到恢复只有一个生命周期（所以能算持续时长与峰值），
与「一次失败一条」的预览失败原因完全不同。

| 参数 | 说明 |
|---|---|
| `days` | 统计天数，默认 30，上限 180 |
| `kind` | 规则标识：`rate_multi` / `rate_storm` / `queue_timeout` / `load_timeout` / `cpu_high` / `mem_high` / `disk_low` / `cache_big` / `queue_backlog` / `engine_error` |
| `level` | `critical`（严重）/ `warn`（警告） |
| `status` | `open`（未恢复）/ `resolved`（已恢复） |
| `keyword` | 对象或详情关键字 |
| `page` / `size` | 分页，`size` 上限 200 |
| `export=csv` | 导出（最多 5 万条，UTF-8 BOM） |

返回：

```json
{"code":0,"msg":"ok","data":{
  "days":30,"page":1,"size":50,"total":29,
  "summary":{"open":2,"openCritical":1,"total":29,"today":3,
             "critical":7,"longestMin":86},
  "byDay":[{"d":"2026-11-03","n":3,"crit":1}],
  "byKind":[{"kind":"cpu_high","title":"CPU 逼近限额","level":"critical",
             "n":1,"open_n":1}],
  "list":[{"id":12,"kind":"queue_timeout","level":"critical","title":"预览排队超时",
           "target":"全局","value":"3","peak":"5","threshold":"5","unit":"个/分钟",
           "detail":"3 个任务排队超过 5 分钟未开始转换，最久已等 12 分钟",
           "status":"open","first_at":"2026-11-03 09:12:00",
           "last_at":"2026-11-03 09:58:00","resolved_at":null,
           "durationMin":46,"hits":46}]}}
```

`summary.open` / `openCritical` **不受 `days` 限制**：三个月前触发、现在还没恢复的告警
必须一直看得见，否则最新的窗口会把最严重的问题过滤掉。

判定与阈值见运维手册「二·四十」，各规则的触发条件也在那里列表。

## 八、错误响应

成功统一为 `{"code":0,"msg":"ok","data":{...}}`；
失败使用 **HTTP 状态码 + `{"detail":"原因"}`**：

| HTTP | 含义 | 处理建议 |
|---|---|---|
| 400 | 参数缺失 / 工号格式不合法 / 文件地址为空 | 检查请求体 |
| 401 | 缺少 `X-App-Id`、token 缺失/过期、预览地址过期、appSecret 错误 | 重新换 token / 重新申请预览地址 |
| 403 | 文件域名不在信任白名单、IP 不在白名单、文件类型被禁 | 联系管理员加白 |
| 404 | 任务不存在 / 预览文件已失效 | 检查 taskId，重新申请 |
| 422 | 转码失败（文件损坏、源站返回空、格式不支持） | 看 `detail` 里的原因，源文件需重新上传 |
| 429 | 触发限流 | 退避重试（建议指数退避） |
| 502 | 预览引擎不可达 | 联系管理员 |

示例：

```json
{"detail": "文件地址不可访问(HTTP 403)。请检查: 链接是否有效或已过期、是否需要鉴权、文件名中的 & 空格等特殊字符是否已转义。"}
```

每次响应带 `X-Request-Id` 请求头，报障时请提供。

---

## 九、接入示例

> 完整可运行示例都在 `examples/` 目录：
> `KkFilePreviewClient.java`（Java）、`preview_demo.py`（Python）、
> `FilePreview.vue`（Vue 3）、`KkPreviewService.cs`（ASP.NET Core）、
> `preview.js`（原生 JS）、`kkfileview.postman_collection.json`（Postman）

### 9.1 Java（Spring Boot / HttpClient）

```java
// 1) 换 token 并缓存（有效期 2 小时）
String body = "{\"appId\":\"" + APP_ID + "\",\"appSecret\":\"" + APP_SECRET + "\"}";
HttpRequest tokenReq = HttpRequest.newBuilder()
    .uri(URI.create(BASE + "/auth/token"))
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(body))
    .build();
String token = objectMapper.readTree(client.send(tokenReq,
        HttpResponse.BodyHandlers.ofString()).body())
    .path("data").path("accessToken").asText();

// 2) 申请预览地址
String payload = objectMapper.writeValueAsString(Map.of(
    "fileUrl", fileUrl, "empNo", empNo, "empName", empName));
HttpRequest previewReq = HttpRequest.newBuilder()
    .uri(URI.create(BASE + "/preview/url"))
    .header("Content-Type", "application/json")
    .header("X-App-Id", APP_ID)
    .header("Authorization", "Bearer " + token)
    .POST(HttpRequest.BodyPublishers.ofString(payload))
    .build();
JsonNode data = objectMapper.readTree(client.send(previewReq,
        HttpResponse.BodyHandlers.ofString()).body()).path("data");

// 3) 返回给前端
if ("ready".equals(data.path("status").asText())) {
    return data.path("previewUrl").asText();
}
return data.path("pollUrl").asText();   // converting：让前端轮询
```

### 9.2 Python

```python
import requests

BASE = "http://127.0.0.1:9012/api/v1"
tok = requests.post(f"{BASE}/auth/token", json={
    "appId": APP_ID, "appSecret": APP_SECRET}, timeout=10).json()["data"]["accessToken"]

r = requests.post(f"{BASE}/preview/url",
                  headers={"X-App-Id": APP_ID, "Authorization": f"Bearer {tok}"},
                  json={"fileUrl": file_url, "empNo": emp_no,
                        "empName": emp_name}, timeout=60).json()
preview_url = r["data"].get("previewUrl")
```

### 9.3 前端（iframe 直接嵌入）

拿到 `previewUrl` 后，用 iframe 或新窗口打开即可，不需要再带任何请求头
（预览地址里的 `token` 本身就是凭证）：

```html
<!-- 高度按页面布局调整；预览页内部自适应，不产生双滚动条 -->
<iframe src="{previewUrl}" style="width:100%;height:85vh;border:0"></iframe>
```

> `previewUrl` 有效期 **10 分钟**，页面停留时间可能很长，**不要写进缓存/数据库**。
> 每次打开都重新调一次 `preview/url`（同一文件走缓存，响应是毫秒级）。

### 9.4 Vue 3.0（组合式 API）

**关键前提：`appSecret` 只能放后端，绝不能打进前端包**（F12 就能看到）。
所以 Vue 里不直连预览服务，而是调**你自己系统的后端**，由后端换 token 并申请预览地址。

约定的后端接口（本文 9.5 有 C# 版实现，Java/Python 同理）：

```
GET /api/file/preview?fileId=123&empNo=C0000002
→ { "code": 0, "data": { "status": "ready", "previewUrl": "http://..." } }
→ { "code": 0, "data": { "status": "converting" } }   // 还在转码，继续轮询
```

组件（完整可运行版本见 `examples/FilePreview.vue`）：

```vue
<template>
  <div class="kk-preview">
    <iframe v-if="url" :src="url" class="kk-frame" allowfullscreen />
    <div v-else-if="error" class="kk-state">
      <p>{{ error }}</p>
      <button @click="load">重新加载</button>
    </div>
    <div v-else class="kk-state">
      <span class="kk-spinner" />
      <p>{{ status === 'converting' ? '文档转换中，请稍候…' : '正在加载…' }}</p>
      <p class="kk-tip">已等待 {{ waited }} 秒</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'

const props = defineProps({
  fileId: { type: [String, Number], required: true },
  empNo: { type: String, required: true },
  empName: { type: String, default: '' },
  pollInterval: { type: Number, default: 1500 },
  maxWaitSeconds: { type: Number, default: 120 }
})

const url = ref('')
const status = ref('loading')
const error = ref('')
const waited = ref(0)

let cancelled = false
let runId = 0
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function fetchPreview() {
  const qs = new URLSearchParams({
    fileId: String(props.fileId), empNo: props.empNo, empName: props.empName
  })
  const res = await fetch('/api/file/preview?' + qs, {
    headers: { Accept: 'application/json' },
    credentials: 'include'            // 带自己系统的登录态
  })
  if (!res.ok) throw new Error('预览接口返回 HTTP ' + res.status)
  const json = await res.json()
  if (json.code !== 0) throw new Error(json.msg || '接口异常')
  return json.data
}

async function load() {
  const myRun = ++runId                  // 快速切换文件时，丢弃旧请求的结果
  cancelled = false
  url.value = ''; error.value = ''; status.value = 'loading'; waited.value = 0
  const startedAt = Date.now()

  try {
    while (!cancelled && myRun === runId) {
      const d = await fetchPreview()
      if (myRun !== runId) return
      status.value = d.status
      waited.value = Math.round((Date.now() - startedAt) / 1000)

      if (d.status === 'ready' && d.previewUrl) { url.value = d.previewUrl; return }
      if (d.status === 'failed') throw new Error(d.message || '文档无法预览')
      if (waited.value >= props.maxWaitSeconds) throw new Error('转换超时，请稍后重试')
      await sleep(props.pollInterval)
    }
  } catch (e) {
    if (myRun === runId) error.value = e.message || '加载失败'
  }
}

onMounted(load)
watch(() => props.fileId, load)
onBeforeUnmount(() => { cancelled = true; runId++ })
</script>
```

用法：

```vue
<FilePreview file-id="123" emp-no="C0000002" emp-name="张三" />
```

三个容易踩的点：

| 点 | 说明 |
|---|---|
| 轮询发给谁 | 发给**自己的后端**。`pollUrl`（`/api/v1/preview/status`）需要 `X-App-Id` + Bearer，浏览器直连会 401 |
| 切换文件 | 用 `runId` 丢弃旧请求；否则 A 文件的响应会覆盖 B 文件的预览 |
| 重复打开同一文件 | 后端按文件身份缓存 30 分钟，期内是毫秒级，不用担心轮询压力 |

### 9.5 Web API（ASP.NET Core / C#）

同样遵循「前端不碰密钥、后端中转」，服务端分三层：配置 → 服务（token 缓存）→ 控制器。
完整可编译版本见 `examples/KkPreviewService.cs`。

**① 配置类与注册（`Program.cs`）** — 用单例 + `IHttpClientFactory`，token 缓存才有效：

```csharp
public sealed class KkPreviewOptions
{
    public const string SectionName = "KkPreview";
    public string BaseUrl { get; set; } = "http://127.0.0.1:9012";
    public string AppId { get; set; } = "";
    public string AppSecret { get; set; } = "";
    public int WaitSeconds { get; set; } = 20;
}

builder.Services.Configure<KkPreviewOptions>(
    builder.Configuration.GetSection(KkPreviewOptions.SectionName));

builder.Services.AddHttpClient("kk-preview", c =>
{
    c.Timeout = TimeSpan.FromSeconds(90);   // 转码要等，默认 100 秒偏紧
});

builder.Services.AddSingleton<KkPreviewService>(sp =>
    new KkPreviewService(
        sp.GetRequiredService<IHttpClientFactory>().CreateClient("kk-preview"),
        sp.GetRequiredService<IOptions<KkPreviewOptions>>()));
```

```json
// appsettings.json —— 生产环境请用环境变量 KkPreview__AppSecret 覆盖
{
  "PublicBaseUrl": "http://your-system",
  "KkPreview": {
    "BaseUrl": "http://127.0.0.1:9012",
    "AppId": "mes",
    "AppSecret": "kk-管理员分配的密钥",
    "WaitSeconds": 20
  }
}
```

**② 传输模型** — 服务端统一返回 `{ code, msg, data }`，用 camelCase 映射：

```csharp
internal sealed class ApiEnvelope<T>
{
    [JsonPropertyName("code")] public int Code { get; set; }
    [JsonPropertyName("msg")] public string? Msg { get; set; }
    [JsonPropertyName("data")] public T? Data { get; set; }
}

public sealed class TokenData
{
    [JsonPropertyName("accessToken")] public string AccessToken { get; set; } = "";
    [JsonPropertyName("expiresIn")] public int ExpiresIn { get; set; }
}

public sealed class PreviewData
{
    [JsonPropertyName("status")] public string Status { get; set; } = "";      // ready/converting/failed
    [JsonPropertyName("previewUrl")] public string? PreviewUrl { get; set; }    // 10 分钟有效
    [JsonPropertyName("pollUrl")] public string? PollUrl { get; set; }          // 需鉴权，后端轮询用
    [JsonPropertyName("fileKey")] public string? FileKey { get; set; }
    [JsonPropertyName("expiresIn")] public int ExpiresIn { get; set; }
    [JsonPropertyName("provisional")] public bool Provisional { get; set; }      // PPT 快速内容版
}
```

**③ 服务层** — token 缓存 + 并发只换一次 + 401 自动重试：

```csharp
public sealed class KkPreviewService
{
    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web);
    private readonly HttpClient _http;
    private readonly KkPreviewOptions _opt;
    private readonly SemaphoreSlim _lock = new(1, 1);
    private string? _token;
    private DateTimeOffset _tokenExpireAt = DateTimeOffset.MinValue;

    public KkPreviewService(HttpClient http, IOptions<KkPreviewOptions> opt)
    { _http = http; _opt = opt.Value; }

    private async Task<string> GetTokenAsync(CancellationToken ct)
    {
        if (_token is not null && DateTimeOffset.UtcNow < _tokenExpireAt) return _token;

        await _lock.WaitAsync(ct);
        try
        {
            if (_token is not null && DateTimeOffset.UtcNow < _tokenExpireAt) return _token;

            using var resp = await _http.PostAsJsonAsync(
                $"{_opt.BaseUrl}/api/v1/auth/token",
                new { appId = _opt.AppId, appSecret = _opt.AppSecret }, ct);
            var raw = await resp.Content.ReadAsStringAsync(ct);
            var body = JsonSerializer.Deserialize<ApiEnvelope<TokenData>>(raw, JsonOpts);

            if (!resp.IsSuccessStatusCode || body?.Data is null)
                throw new InvalidOperationException(
                    $"换取令牌失败: HTTP {(int)resp.StatusCode} {raw[..Math.Min(200, raw.Length)]}");

            _token = body.Data.AccessToken;
            // 提前 60 秒过期，避开"刚好在过期瞬间调用"
            _tokenExpireAt = DateTimeOffset.UtcNow.AddSeconds(
                body.Data.ExpiresIn > 120 ? body.Data.ExpiresIn - 60 : 60);
            return _token;
        }
        finally { _lock.Release(); }
    }

    public async Task<PreviewData> GetPreviewAsync(
        string fileUrl, string empNo, string? empName = null, CancellationToken ct = default)
    {
        for (var attempt = 0; attempt < 2; attempt++)      // 令牌失效则重试一次
        {
            var token = await GetTokenAsync(ct);

            using var req = new HttpRequestMessage(
                HttpMethod.Post, $"{_opt.BaseUrl}/api/v1/preview/url")
            {
                Content = JsonContent.Create(new
                {
                    fileUrl, empNo, empName = empName ?? "",
                    wait = true, waitSeconds = _opt.WaitSeconds
                })
            };
            req.Headers.Add("X-App-Id", _opt.AppId);
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);

            using var resp = await _http.SendAsync(req, ct);
            if (resp.StatusCode == HttpStatusCode.Unauthorized && attempt == 0)
            {
                _token = null;
                _tokenExpireAt = DateTimeOffset.MinValue;
                continue;
            }

            var raw = await resp.Content.ReadAsStringAsync(ct);
            var body = JsonSerializer.Deserialize<ApiEnvelope<PreviewData>>(raw, JsonOpts);
            if (!resp.IsSuccessStatusCode || body?.Data is null)
                throw new InvalidOperationException(
                    $"申请预览失败: HTTP {(int)resp.StatusCode} {raw[..Math.Min(200, raw.Length)]}");
            return body.Data;
        }
        throw new InvalidOperationException("申请预览失败: 令牌反复失效");
    }
}
```

**④ 控制器** — 前端只调这一个：

```csharp
[ApiController]
[Route("api/file")]
public sealed class FilePreviewController : ControllerBase
{
    private readonly KkPreviewService _kk;
    private readonly string _publicBaseUrl;

    public FilePreviewController(KkPreviewService kk, IConfiguration cfg)
    {
        _kk = kk;
        _publicBaseUrl = (cfg["PublicBaseUrl"] ?? "").TrimEnd('/');
    }

    [HttpGet("preview")]
    public async Task<IActionResult> Preview(
        [FromQuery] string fileId, [FromQuery] string empNo,
        [FromQuery] string? empName = null, CancellationToken ct = default)
    {
        // ↓ 换成你系统真实的下载地址（预览服务要能直接 GET 到）
        var fileUrl = $"{_publicBaseUrl}/api/files/{Uri.EscapeDataString(fileId)}/download";
        var d = await _kk.GetPreviewAsync(fileUrl, empNo, empName, ct);
        return Ok(new { code = 0, msg = "ok",
            data = new { status = d.Status, previewUrl = d.PreviewUrl,
                         provisional = d.Provisional, expiresIn = d.ExpiresIn } });
    }
}
```

三个容易踩的点：

| 点 | 说明 |
|---|---|
| `AddSingleton` vs `AddHttpClient<T>` | 后者注册成 **Transient**，每次请求都新建实例，token 缓存形同虚设，会频繁换令牌 |
| JSON 大小写 | 服务端返回 `appId`/`accessToken` 这类 camelCase；用 `new JsonSerializerOptions(JsonSerializerDefaults.Web)` 或 `[JsonPropertyName]` |
| 401 处理 | 密钥被重置时旧 token 才失效，**不要**每次请求都重新换 token |

---

## 十、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 401 `缺少 X-App-Id 请求头` | 忘记加 `X-App-Id` |
| 401 `appId 或 appSecret 不正确` | 密钥错或已被重置，找管理员确认 |
| 401 预览页打不开 | `previewUrl` 超过 10 分钟，重新调用 `/preview/url` |
| 403 `文件来源不在信任列表` | 文件域名需要管理员加白 |
| 403 `文件地址不可访问` | 源站拒绝（签名过期 / 需要鉴权 / 文件名特殊字符未编码），用 curl 直接访问该 URL 验证 |
| 422 转码失败 | 文件损坏、源站返回 0 字节、或含引擎不支持的元素；看 `detail` |
| 打开是白屏 | 浏览器/网络问题，或 iframe 被 CSP 拦截；先在浏览器直接打开 `previewUrl` 验证 |
| Excel 里有几行显示不出来 | 源文件在 Excel 里就把这些行**隐藏**了，预览按 Excel 原样隐藏，标题栏会提示隐藏了多少行 |
| 想看到文件最新内容 | 直接重新打开即可：缓存有效期 30 分钟，过期后自动重新下载源文件并重新转换 |

---

## 十一、安全与合规

- 预览页统一带水印，水印文字由管理员配置
- 所有预览、预热请求都会记录：系统、工号、文件、时间、IP、耗时、结果
- 审计日志保留 180 天，统计明细保留 30 天
- 预览引擎端口不对外开放，只能通过网关访问
- `appSecret` 请存放在服务端配置/密钥管理，**不要提交到代码仓库、不要下发到浏览器**

---

## 附：测试账号

联调阶段可向管理员申请测试账号（示例环境内置 `kk-demo`，仅限内网测试，不要用于生产）。
正式接入请使用按系统名称申请的生产账号。

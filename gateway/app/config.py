import os


def _env(key, default=""):
    return os.environ.get(key, default)


def _env_int(key, default=0):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key, default=False):
    return os.environ.get(key, "1" if default else "0").lower() in ("1", "true", "yes")


# ---------------- 服务自身 ----------------
SERVICE_NAME = "kkfileview-gateway"
HOST = _env("GATEWAY_HOST", "0.0.0.0")
PORT = _env_int("GATEWAY_PORT", 9012)
WORKERS = _env_int("GATEWAY_WORKERS", 4)

# 对外访问基址(用于生成预览地址)
PUBLIC_BASE = _env("GATEWAY_PUBLIC_BASE", "http://127.0.0.1:9012").rstrip("/")

# ---------------- 数据库 ----------------
MYSQL_HOST = _env("MYSQL_HOST", "kk-mysql")
MYSQL_PORT = _env_int("MYSQL_PORT", 3306)
MYSQL_USER = _env("MYSQL_USER", "kkfileview")
MYSQL_PASSWORD = _env("MYSQL_PASSWORD", "change-me-db-password")
MYSQL_DB = _env("MYSQL_DB", "kkfileview")
MYSQL_POOL_MIN = _env_int("MYSQL_POOL_MIN", 4)
MYSQL_POOL_MAX = _env_int("MYSQL_POOL_MAX", 20)

# ---------------- Redis ----------------
REDIS_HOST = _env("REDIS_HOST", "kk-redis")
REDIS_PORT = _env_int("REDIS_PORT", 6379)
REDIS_PASSWORD = _env("REDIS_PASSWORD", "")
REDIS_DB = _env_int("REDIS_DB", 3)

# ---------------- 预览引擎 kkFileView ----------------
KK_BASE = _env("KK_BASE", "http://kkfileview:8012").rstrip("/")
# pdf.js 静态资源的版本戳。viewer.js / viewer.css 的 URL 是固定的，
# 升级引擎后浏览器会继续用旧缓存，导致「新 HTML + 旧 JS」→ DOM 结构不匹配，
# 表现就是 SecondaryToolbar 取不到元素、报 null.addEventListener。
# 每次升级引擎改这里的值即可让所有客户端强制拉新文件。
ASSET_VER = _env("ASSET_VER", "440-1")
KK_USERNAME = _env("KK_USERNAME", "")          # 引擎访问密码(未设置则留空)
KK_PASSWORD = _env("KK_PASSWORD", "")
# 允许预览的文件来源域名/IP 前缀,逗号分隔,为空表示不限制
TRUST_HOSTS = [h.strip() for h in _env("TRUST_HOSTS", "").split(",") if h.strip()]
# 禁止预览的扩展名
BLOCK_EXTS = set(e.strip().lower() for e in
                 _env("BLOCK_EXTS", "exe,dll,bat,sh,msi,com,scr").split(",") if e.strip())
# 单个源文件的大小上限（MB）。超过直接 413，不下载、不转换。
# 这些路径会把整个文件读进内存（压缩包、Office/表格转换），几百 MB 会吃满 worker。
# 阈值按库内实测校准：受控文件里最大 901MB（培训视频），>200MB 的有 42 个，
# 其中包含能预览的 pptx/xls/STEP。设 200 会把它们全打死，所以取 1GB 留余量。
MAX_FILE_SIZE_MB = _env_int("MAX_FILE_SIZE_MB", 1024)

# 需要转换的类型(走异步队列); 其余类型直接预览
CONVERT_EXTS = set(e.strip().lower() for e in _env(
    "CONVERT_EXTS",
    "doc,docx,xls,xlsx,xlsm,ppt,pptx,wps,et,dps,odt,ods,odp,rtf,md,markdown,"
    "dwg,dxf,dwf,plt,cf2,epub,xmind,bpmn,drawio,dcm"
).split(",") if e.strip())

# 由网关直接调 LibreOffice 命令行转 PDF 的扩展名(绕开 kkFileView 的 jodconverter:
# 它 4.1.0 版本与 LibreOffice 26.x 的 UNO 不兼容, 转换一律失败)。
# CAD/压缩包/邮件等不在此列, 仍交给引擎(引擎本身支持或直接报不支持)。
LOCAL_CONVERT_EXTS = set(e.strip().lower() for e in _env(
    "LOCAL_CONVERT_EXTS", ""
).split(",") if e.strip())

# 引擎转换失败后的兜底扩展名: 用网关挂载的 LibreOffice 26.8 再试一次
# (引擎内置 7.3 对"多页 + 多 SmartArt"的 PPT 会 SIGABRT 崩溃)
LOCAL_FALLBACK_EXTS = set(e.strip().lower() for e in _env(
    "LOCAL_FALLBACK_EXTS",
    "doc,docx,xls,xlsx,ppt,pptx,wps,et,dps,odt,ods,odp,rtf,md,markdown"
).split(",") if e.strip())

# 所有"Office 类"扩展名。这些**一律不交给预览引擎**：
# 引擎内置 LibreOffice 7.3（kkFileView 4.1.0 的 jodconverter 只认它），
# 而项目统一要求用 26.8.1，所以 Office 转换只在网关侧做，失败就文字降级。
OFFICE_EXTS = set(e.strip().lower() for e in _env(
    "OFFICE_EXTS",
    "doc,docx,docm,dot,dotx,dotm,xls,xlsx,xlsm,xlt,xltx,xltm,csv,"
    "ppt,pptx,pptm,pot,potx,potm,pps,ppsx,ppsm,"
    "odt,ods,odp,odg,ott,ots,otp,rtf,md,markdown,wps,et,dps,vsd,vsdx"
).split(",") if e.strip())

# 表格模式: Excel/CSV 不转 PDF, 直接按行列渲染成网页表格(更接近本地 Excel)
# 转 PDF 会按 A4 分页, 宽表常被切成几十页、列被截断, 与 Excel 原貌差异大
SHEET_PREVIEW = _env_bool("SHEET_PREVIEW", True)
# xlsx 直接渲染成原生 HTML 表格(全行列、不受打印区域限制)。
# 与 SHEET_PREVIEW(Luckysheet 在线表格组件)相互独立:
#   SHEET_PREVIEW=0 + XLSX_HTML=1 → 轻量表格页, 不卡浏览器, 也不裁列
XLSX_HTML = _env_bool("XLSX_HTML", True)
# 表格渲染引擎: lucky=在线表格组件(接近 Excel 观感), simple=简易 HTML 表格
SHEET_RENDER = _env("SHEET_RENDER", "lucky").strip().lower()

# ---- 大表格改走轻量视图 ----
# Univer 快照要 openpyxl 建对象，而很多 Excel 把整行格式刷到了 XFD 列
# （dimension = A1:XFD53），openpyxl 会按这个范围建 53×16384 个单元格。
# 实测 1.6MB 的《ZL-F32 质量门评审表》要 28 秒；同一文件走 xlsx_html 只要 2.1 秒。
# 命中下面任一阈值就不进 Univer，直接给轻量 HTML 表格（工作表标签、隐藏行列都在）。
# 阈值取 1024：Excel 最大 16384 列，257 这种是真实宽表（Univer 能滚），
# 上千列几乎只可能是"整行格式刷"留下的空列。实测《ZL-F32》是 16384 列。
SHEET_HEAVY_COLS = _env_int("SHEET_HEAVY_COLS", 1024)
SHEET_HEAVY_CELLS = _env_int("SHEET_HEAVY_CELLS", 400000)  # 全簿 dimension 面积之和
# 快照生成的整体超时（秒）。超时降级到轻量视图，不把 worker 拖死。
SHEET_SNAPSHOT_TIMEOUT = _env_int("SHEET_SNAPSHOT_TIMEOUT", 20)
# 预热阶段生成 Univer 表格快照的超时。比用户等待的 20 秒宽松得多：
# 这时没人盯着屏幕，慢一点换来"用户打开直接读快照"，比让他现场等 20 秒再掉 PDF 强。
UNIV_SNAP_PREWARM_TIMEOUT = _env_int("UNIV_SNAP_PREWARM_TIMEOUT", 120)
# 被判"大表"的文件在用户打开时，最多试多少秒快照。
# dimension 到 XFD 多半是格式刷出来的（实测 16384 列的 BOM 表解析只要 0.4 秒），
# 一判就转 PDF 会把好好的表格压成不能选中的版面；给 8 秒试一下，真不行再退。
UNIV_SNAP_TRY_TIMEOUT = _env_int("UNIV_SNAP_TRY_TIMEOUT", 8)
SHEET_EXTS = set(e.strip().lower() for e in
                 _env("SHEET_EXTS", "xlsx,xlsm,csv").split(",") if e.strip())

# 大 PPT 两阶段转换: 先只转前 N 页让用户尽快看到真实排版, 再后台补全。
# 实测 42MB/119 页的 PPT: 前 15 页约 11 秒, 全部 56 秒。
# 页数不超过 PPT_PARTIAL_MIN 的文件直接一次转完(没有拆分的必要)。
# PPT_PARTIAL_PAGES=0 表示关闭该特性, 一律整本转换。
PPT_PARTIAL_PAGES = _env_int("PPT_PARTIAL_PAGES", 15)
PPT_PARTIAL_MIN = _env_int("PPT_PARTIAL_MIN", 30)

# 老版二进制 .ppt/.doc 没有 zip 目录结构, 生成"快速内容页"前要先让
# LibreOffice 升级成 pptx/docx(实测 21MB/14 页 1.3 秒, 46MB/119 页 9 秒)。
# 这一步是同步的(浏览器请求会等着), 所以体积超过阈值的直接跳过内容页,
# 改用"转换中"等待页, 避免请求长时间挂着。
LEGACY_OUTLINE_MAX_MB = _env_int("LEGACY_OUTLINE_MAX_MB", 60)

# ---- 表格模式的"提前放弃"阈值 ----
# 表格组件在浏览器里是同步初始化的, 一旦卡住前端兜底(超时/降级)根本执行不到,
# 所以只能在服务端提前判断: 命中任一阈值就不渲染表格, 直接回退 PDF 预览。
SHEET_DIRECT_MAX_MB = _env_int("SHEET_DIRECT_MAX_MB", 6)     # 源文件体积上限
SHEET_MAX_PAGE_MB = float(_env("SHEET_MAX_PAGE_MB", "1.5") or 1.5)  # 渲染页面体积上限
SHEET_MAX_BUILD_SEC = _env_int("SHEET_MAX_BUILD_SEC", 15)     # 解析耗时上限(秒)

# SVG: kkFileView 4.1.0 不支持 svg 预览, 由网关直接把矢量图内嵌到页面里渲染
SVG_PREVIEW = _env_bool("SVG_PREVIEW", True)
SVG_EXTS = set(e.strip().lower() for e in _env("SVG_EXTS", "svg").split(",") if e.strip())

# ---- Markdown 渲染方式 ----
# 1 = 网关自己转成彩色 HTML(标题/代码/表格都有颜色, 几十毫秒)
# 0 = 旧行为: 交给 LibreOffice 转 PDF(黑白稿)
MD_HTML = _env_bool("MD_HTML", True)
MD_EXTS = set(e.strip().lower() for e in _env(
    "MD_EXTS", "md,markdown"
).split(",") if e.strip())

# ---- 纯文本类：网关直接出页面，不交给引擎 ----
# 引擎对 txt/json/log 这类文件要起一次转换流程：实测 txt 端到端均值 13.3 秒、
# 最慢 153 秒（近 7 天 36 次），而文件本身只有几十 KB。网关自己读字节 + 猜编码 +
# 套一个 <pre> 页面是毫秒级，和压缩包内文本预览用的是同一套渲染。
TEXT_DIRECT = _env_bool("TEXT_DIRECT", True)
TEXT_EXTS = set(e.strip().lower() for e in _env(
    "TEXT_EXTS",
    "txt,log,json,xml,yml,yaml,ini,cfg,conf,sql,sh,bat,ps1,"
    "py,js,ts,css,scss,less,java,c,cc,cpp,h,hpp,cs,go,rs,php,rb,pl,kt,swift"
).split(",") if e.strip())
# 最多读多少字节来显示（超出部分截断，页面上会写明）。防止有人点开 2GB 的日志。
# 默认 2MB：再大会让浏览器渲染 <pre> 变得很慢（10MB 日志 → 8MB HTML），
# 而受控文件里的 txt/log 绝大多数只有几十 KB。
TEXT_DIRECT_MAX_MB = float(_env("TEXT_DIRECT_MAX_MB", "2") or 2)

# ---- 压缩包清单(网关自己解析中央目录，不经引擎) ----
# 引擎渲染压缩包要 300~3000ms，而 zipfile/tarfile 只读目录只要几十毫秒。
# rar/7z 标准库解析不了，仍然交给引擎。
ARCHIVE_RENDER = _env_bool("ARCHIVE_RENDER", True)
# 清单页由网关自己出（zip/tar 走标准库；rar/7z/gz 走 7-Zip 静态二进制
# /opt/kk-tools/7zz，见 archive_browse）。清单里的文件可点开预览。
ARCHIVE_EXTS = set(e.strip().lower() for e in _env(
    "ARCHIVE_EXTS",
    "zip,jar,war,ear,apk,epub,tar,tgz,tbz2,txz,gz,bz2,xz,rar,r00,7z,cab,iso"
).split(",") if e.strip())
# 压缩包缓存（包本体 + 包内 Office 转出的 PDF）的总量上限（MB）。
# 超出后按最后修改时间删最旧的，防止"一天点开上百个几百 MB 的包"把磁盘吃满。
ARCHIVE_CACHE_TOTAL_MB = _env_int("ARCHIVE_CACHE_TOTAL_MB", 15000)

# CAD 类: 引擎的 CAD 页面会同步完成 DWG→PDF/SVG 转换(实测 146KB 图纸 12.7 秒)，
# 所以这几类必须先转好再让浏览器进引擎页, 否则用户会盯着白屏等。
# CAD 出图方式：1 = 网关用 PDFium 把 PDF 渲染成位图（默认，整页 6000px 约 0.8s）
# 0 = 退回引擎的 pdf.js 页（复杂图纸几十秒到几分钟都画不出来，仅作应急开关）
CAD_RASTER = _env_bool("CAD_RASTER", True)

# ---- Univer 预览（xlsx/xls 走 Sheets，ppt/pptx 走 Slides）----
# 1 = 打开；0 = 回退到原来的 HTML 表格 / pdf.js
# 幻灯片：每页一张由 LibreOffice + PDFium 渲染的位图，塞进 Univer Slides 的页面。
# 2026-10-23 曾因"页面右侧被裁 66px"整体撤回 pdf.js；根因是 Univer 的幻灯片
# 画布宽度按「窗口宽 - 左侧页面列表栏 256px」算，页面却按整个窗口宽居中。
# 现在改为：前端先关掉左侧页面列表（IUIPartsService.setUIVisible("left-sidebar")），
# 再按容器尺寸算好页面像素宽度传给后端出图，画布与页面用同一把尺子，不再有偏移。
UNIVER_PREVIEW = _env_bool("UNIVER_PREVIEW", True)
UNIVER_SHEET_EXTS = set(e.strip().lower() for e in _env(
    "UNIVER_SHEET_EXTS", "xlsx,xlsm,xls"
).split(",") if e.strip())
UNIVER_SLIDE_EXTS = set(e.strip().lower() for e in _env(
    "UNIVER_SLIDE_EXTS", "ppt,pptx,pptm,pps,ppsx,ppsm,pot,potx,potm,odp"
).split(",") if e.strip())
# 幻灯片页在容器里的留白（CSS 像素）。
# **对称**：Univer 把页面在画布里垂直居中，所以"顶部扣多少"并不等于
# "顶部留多少"——原来按 (高 - 48 - 8) 算，居中后顶部实际只剩 8px，
# 工具条（40px）稍微变高一点就盖住标题，用户看到的是"上面少一块"。
# 现在按对称留白算：页面四周各 UNIV_SLIDE_PAD_Y，顶部必然留够。
# 顶部工具条已在预览里隐藏（header:false，只读页不需要撤销/字体那些按钮），
# 万一把它打开，用 UNIV_SLIDE_TOP_RESERVE 把这块高度补回去（默认 40）。
UNIV_SLIDE_PAD_X = _env_int("UNIV_SLIDE_PAD_X", 24)
UNIV_SLIDE_PAD_Y = _env_int("UNIV_SLIDE_PAD_Y", 24)
UNIV_SLIDE_TOP_RESERVE = _env_int("UNIV_SLIDE_TOP_RESERVE", 0)
# 幻灯片位图的出图宽度（px）。1600 对 1080p 全屏足够清晰，再大只是白传字节。
UNIV_SLIDE_RENDER_W = _env_int("UNIV_SLIDE_RENDER_W", 1600)

# 纯文本类源文件：传输时 gzip（STEP 文本压缩比通常 6~9 倍），浏览器可直接解压
TEXT_SOURCE_EXTS = set(e.strip().lower() for e in _env(
    "TEXT_SOURCE_EXTS",
    "step,stp,iges,igs,brep,obj,dae,3ds,wrl,off,stl,gltf,ply,x3d,asc"
).split(",") if e.strip())

CAD_EXTS = set(e.strip().lower() for e in _env(
    "CAD_EXTS", "dwg,dxf,dwf,dwt,dwfx,plt,cf2"
).split(",") if e.strip())

# 3D 实体格式：有预解析网格时走网关自己的 three.js 查看器（1~2 秒），
# 没有就退回引擎的 O3DV（浏览器现场 WASM 解析，14MB 要 19.5 秒）。
# 网格由 tools/stepmesh/step2mesh.js 离线生成（同一套 OCCT WASM）。
# fcstd：FreeCAD 文档（zip 内含多个 .brp），由 tools/preconvert_step.py 解包后
# 走同一套 OCCT 解析，所以和 STEP 共用 3D 查看器。
STEP_EXTS = set(e.strip().lower() for e in _env(
    "STEP_EXTS", "step,stp,iges,igs,brep,fcstd,fcstd1,x_t,x_b"
).split(",") if e.strip())

# 由网关自己渲染(不经 LibreOffice 转码)的扩展名
DIRECT_EXTS = SHEET_EXTS | SVG_EXTS

# 首次预览时, 接口是否同步等待转码完成后再返回预览地址
# 开启后调用方不用自己轮询; 超时才返回 converting + taskId
PREVIEW_WAIT = _env_bool("PREVIEW_WAIT", True)
# 首次响应预算(秒)：接口最多同步等这么久，超时立即返回 converting/provisional，
# 由预览页自己轮询。
# 2.5 → 0.5：DWG 冷转要 20~30 秒，接口干等 2.5 秒毫无意义，反而让用户在前 2.5 秒
# 看不到任何界面。现在 0.5 秒就交出 previewUrl，页面立刻显示"正在转换 + 已等待 N 秒"。
# 小文件（xlsx/docx 0.1~0.6 秒转完）仍会在预算内直接返回 ready。
PREVIEW_WAIT_SECONDS = float(_env("PREVIEW_WAIT_SECONDS", "0.5") or 0.5)
# 端到端"加载耗时"统计：预览页在首屏内容真正可见时上报，回填这条记录的 cost_ms。
# 关掉就退回旧口径（只算 /preview/url 接口本身的耗时，DWG 会显示 0.4 秒而用户等 20 秒）。
LOAD_METRIC = _env_bool("LOAD_METRIC", True)
# 转码前探测文件源站的超时(秒)。超时不阻塞接口, 交给后台任务处理
PROBE_TIMEOUT = _env_int("PROBE_TIMEOUT", 3)

# 同时进行的转换任务数(削峰, 保护 LibreOffice)
MAX_CONCURRENT_CONVERT = _env_int("MAX_CONCURRENT_CONVERT", 4)
CONVERT_TIMEOUT = _env_int("CONVERT_TIMEOUT", 300)

# 预览页背景改为纯白(引擎默认为灰底), 0 关闭
WHITE_BACKGROUND = _env_bool("WHITE_BACKGROUND", True)

# 水印(与引擎共用同一套配置; 表格模式的水印由网关自己绘制)
# 预览页上的产品名（压缩包清单页的 <h1>、引擎页面的标题）。
# 引擎自带 "kkFileView" 字样，代理时统一换成这个。
BRAND_NAME = _env("BRAND_NAME", "自研预览引擎")
WATERMARK_TXT = _env("WATERMARK_TXT", "内部资料 禁止外传")
WATERMARK_ALPHA = _env("WATERMARK_ALPHA", "0.05")
WATERMARK_FONT = _env("WATERMARK_FONT", "Microsoft YaHei")
WATERMARK_FONTSIZE = _env("WATERMARK_FONTSIZE", "18px")
WATERMARK_ANGLE = _env("WATERMARK_ANGLE", "10")

# 允许内嵌(iframe)本服务页面的业务系统域名, 逗号分隔, 形如 oa.corp.com,plm.corp.com
# 留空时仍允许预览服务自身域名内嵌(跨站 iframe 必需)
EMBED_ALLOWED_HOSTS = _env("EMBED_ALLOWED_HOSTS", "")

# ---------------- 安全 ----------------
SECRET_KEY = _env("SECRET_KEY", "change-me-please-kkfileview-2026")
TOKEN_TTL = _env_int("TOKEN_TTL", 7200)          # 访问令牌有效期(秒)
PREVIEW_TOKEN_TTL = _env_int("PREVIEW_TOKEN_TTL", 600)   # 预览地址有效期(秒)

# ---- 预览缓存有效期 ----
# 转换产物(PDF / 表格 HTML)保留多久。超过后再次打开会**重新下载源文件并重新转换**,
# 保证用户看到的是最新内容(源站文件被替换后, 旧缓存不会一直顶着用)。
# 0 表示不做时效检查(产物一直复用, 直到磁盘清理)。
#
# 默认 30 分钟: 对外文档一直写的是"缓存 30 分钟, 期内再开是 0 秒"。
# 曾经默认 1 分钟(compose 里也是 :-1), 结果同一个 xlsx 用户过两分钟再开就要
# 重新下载+解析 3 秒、DWG 要 20 秒, 而且每分钟都去校验一次源站。
# 真要"永远看最新", 调小这个值即可; 但别低于 10, 否则重转频率会比访问频率还高。
CACHE_TTL_MINUTES = _env_int("CACHE_TTL_MINUTES", 30)
CACHE_TTL_SECONDS = CACHE_TTL_MINUTES * 60
# 缓存过期后的"变更校验"：先只发 HEAD 比一下源站的 Content-Length/Last-Modified/ETag，
# 源文件没变就把产物续期，不再重转一遍。
# CAD 的 DWG→PDF 一次要 19~30 秒（Aspose 逐条线段描边），而图纸一周也不改一次，
# 过期就重转等于每隔半小时让第一个用户白等 20 秒。
CACHE_REVALIDATE = _env_bool("CACHE_REVALIDATE", True)
# 源文件根目录（网关只读挂载 /cephfs_data）。过期校验时直接 stat 本机文件，
# 不依赖业务 URL 的签名——预转缓存里存的是无签名地址，HEAD 必然 403。
# 业务 URL 的 path 参数是相对"受控文件/"的，所以根目录要指到那一层。
CEPH_ROOT = _env(
    "CEPH_ROOT",
    "/cephfs/fileManagement/files/示例事业部/文档中心/受控文件")
# 业务地址的第二种形态：`http://127.0.0.1:8090/files/<相对路径>`（直链，没有 ?path=）。
# 它同样落在同一个挂载上（/cephfs/fileManagement/files/…），所以也能本地 stat 做
# "源文件被覆盖后立刻重转"。之前只认 ?path=，于是这种形态（近 30 天 4377 次请求，
# 占业务流量的大头）每次打开都判断不了"源变没变"，只能等 30 分钟 TTL。
# 只对确认是这台文件服务器的 host 生效：别的系统也用 /files/ 前缀
# （127.0.0.1:9095 的发布库），映射过来只会 stat 不到，白跑一次系统调用。
CEPH_FILES_HOSTS = set(e.strip().lower() for e in _env(
    "CEPH_FILES_HOSTS", "127.0.0.1:8090").split(",") if e.strip())
CEPH_FILES_ROOT = _env("CEPH_FILES_ROOT", "/cephfs/fileManagement")
# 热门文件保活：把最近 N 天访问过的 CAD/大文件按周期重新校验并续期，
# 让"过期重转"永远发生在后台，而不是用户点开的那一下。
STRICT_SIGN = _env_bool("STRICT_SIGN", False)     # 是否强制校验 HMAC 签名
ADMIN_TOKEN = _env("ADMIN_TOKEN", "change-me-admin-token")  # 管理接口(建账号/看统计)令牌
# 管理员登录会话有效期：默认 3 天，过期必须重新输入 ADMIN_TOKEN
ADMIN_SESSION_TTL = _env_int("ADMIN_SESSION_TTL", 3 * 24 * 3600)
# 工号规则
EMP_NO_PATTERN = _env("EMP_NO_PATTERN", r"^[A-Za-z0-9_-]{3,32}$")
# 系统批量任务用的工号前缀（SYS_PLM、SYS_WMS…）。这类调用不代表某个自然人，
# 不该吃"单工号 20 QPS"的限制——否则一个系统的批处理会把该工号打满，
# 同一系统里真人打开预览也会被 429。改成只用 appId 的配额（默认 60 QPS）。
SYS_EMP_PREFIX = _env("SYS_EMP_PREFIX", "SYS").strip().upper()

# ---------------- 限流 ----------------
GLOBAL_QPS = _env_int("GLOBAL_QPS", 120)         # 全局每秒上限
DEFAULT_APP_QPS = _env_int("DEFAULT_APP_QPS", 60)
EMP_QPS = _env_int("EMP_QPS", 20)                # 单个工号每秒上限

# ---------------- 埋点 ----------------
COLLECT_QUEUE_SIZE = _env_int("COLLECT_QUEUE_SIZE", 20000)
COLLECT_BATCH = _env_int("COLLECT_BATCH", 300)
COLLECT_FLUSH_INTERVAL = _env_int("COLLECT_FLUSH_INTERVAL", 1)   # 秒
# 聚合表（stat_daily/weekly/monthly/file_daily/latency_hist/total）的刷新间隔。
#
# **看板不读这些表** —— stats_api 的每个接口都是实时查 access_log（2.8 万行，
# 40ms 内返回），聚合结果目前只有 tools/clean_metrics.py 在用。原来 60 秒刷一次，
# 一次 9 条 SQL、73ms，等于每天 1440 轮 / 1.3 万条 SQL 陪着业务抢 MySQL，没人读。
# 改成 30 分钟：需要历史聚合时数据仍然新鲜，负载降 30 倍。
# 设 0 = 完全关闭后台聚合（手动跑 tools/clean_metrics.py 仍可生成）。
AGGREGATE_INTERVAL = _env_int("AGGREGATE_INTERVAL", 1800)        # 秒

# 明细数据保留天数(access_log / audit_log)。审计与失败原因要能回溯半年,
# 超过这个天数由后台任务分批清理; 设 0 表示不清理(会无限增长)。
RETENTION_DAYS = _env_int("RETENTION_DAYS", 180)
# preview_task（转码任务表）的保留天数。它只是运行时状态，不是审计记录 ——
# 谁看了哪份文件在 access_log / audit_log 里。不清理会只增不减，
# 而 /health/detail 的「打不开的文件」要 GROUP BY 全表，越旧越慢。
PREVIEW_TASK_KEEP_DAYS = _env_int("PREVIEW_TASK_KEEP_DAYS", 90)
LOG_DETAIL = _env_bool("LOG_DETAIL", True)

# 后台任务账号：预转换(preconvert)、每日预热(prewarm)。
# 它们也走 /preview/url，所以会写进 access_log —— 但**不能算进用户指标**。
# 实测 30 天内 5882 条失败里有 88.3% 是这两个账号的 404（历史噪音），
# 混在一起会把看板成功率从真实的 96.2% 拉到 37%，等于在报警一个不存在的问题。
# 统计口径一律排除；排查后台任务请用"失败明细"按工号筛选。
SYSTEM_EMPS = tuple(e.strip() for e in _env(
    "SYSTEM_EMPS", "PRECONV,SYS_PREWARM,SYS_PREWARM_STEP").split(",") if e.strip())

# ---------------- 服务告警 ----------------
# 只报"容量/压力"维度的问题（限流风暴、排队超时、CPU/内存/磁盘、转换异常），
# 文件打不开这类"预览失败原因"走 access_log，不进告警表（见 alerts.py 文件头）。
ALERT_ENABLED = _env_bool("ALERT_ENABLED", True)
ALERT_INTERVAL = _env_int("ALERT_INTERVAL", 60)        # 评估周期（秒）
# 容器限额来自 docker-compose 的 cpus / mem_limit，改那边时这里要一起改
# （两边对不上的后果：阈值设得比限额小 → 天天误报；设得比限额大 → 永远不报，直到 OOM）
ALERT_CPU_CORES = _env_int("ALERT_CPU_CORES", 8)       # 达到 90% 即告警；compose cpus: "8"
# 必须跟 compose 的 mem_limit 挂钩。compose 里给的是 `${ALERT_MEM_GB:-13}`，
# 即限额 15g 的 87%；再乘下面的 90% 判据 → 用到限额的 78% 就告警，留得出余量。
# 原来这里默认 10，若是 compose 没配的场景会变成"用到 60% 就报 critical"，
# 而 11-04 那次 9.98GB 的 critical 更离谱 —— 那是把 page cache 也算进来了
# （见 alerts.mem_bytes 的注释），进程真实占用只有 1GB 量级。
ALERT_MEM_GB = _env_int("ALERT_MEM_GB", 13)            # 达到 90% 即告警；compose 默认 13（限额 15g）
ALERT_DISK_FREE_GB = _env_int("ALERT_DISK_FREE_GB", 20)    # 可用空间低于此值
ALERT_CACHE_GB = _env_int("ALERT_CACHE_GB", 160)       # 缓存目录超过此值
ALERT_QUEUE_MAX = _env_int("ALERT_QUEUE_MAX", 20)      # 排队个数阈值
ALERT_WAIT_MIN = _env_int("ALERT_WAIT_MIN", 5)         # 排队/加载超过多少分钟算超时
ALERT_RL_APPS = _env_int("ALERT_RL_APPS", 3)           # 同分钟多少个系统被限流算"多系统"
ALERT_RL_COUNT = _env_int("ALERT_RL_COUNT", 300)       # 单系统 5 分钟内被限流多少次算风暴

# 推送通道。告警只写进 alert_log 的话，没人盯看板就等于没有告警 ——
# 实测 11-04 内存涨到 9.98GB（上限 10GB）、11-06 有加载超时，
# 两条都是事后翻表才发现的。填了这个地址，触发/恢复会主动推出去。
#   ALERT_WEBHOOK     企业微信/钉钉群机器人地址，留空则不推送
#   ALERT_WEBHOOK_KIND  wecom（默认）/ dingtalk / raw（原样 POST JSON）
ALERT_WEBHOOK = _env("ALERT_WEBHOOK", "").strip()
ALERT_WEBHOOK_KIND = _env("ALERT_WEBHOOK_KIND", "wecom").strip().lower()
ALERT_NOTIFY_GAP = _env_int("ALERT_NOTIFY_GAP", 600)   # 同一告警的触发通知静默期（秒）

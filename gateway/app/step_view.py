# -*- coding: utf-8 -*-
"""STEP/IGES 的 3D 预览：服务端预解析成网格，浏览器只负责画。

**为什么不用引擎的 O3DV**

O3DV 是在浏览器里用 WASM 单线程解析 STEP：14MB 的装配体要 19.5 秒（且全程白屏，
用户只能看着"加载中"），47MB 的要 2~5 分钟。这段耗时与终端 CPU 直接相关，
换台老电脑更慢，且没法缓存（每次打开都要重新解析）。

**现在的方式**

    宿主机的 step2mesh.js（同一套 OCCT WASM）先解析一次 → 导出 4~5MB 的网格
      → 网关 gzip 后缓存（约 1.5MB）
      → 浏览器 fetch 一次、拼 BufferGeometry、three.js 直接画（1~2 秒）

代价是服务端要多花 20 秒/文件，但那是**离线**跑的：用户点开时只剩"下载 + 建几何"。
47MB 的装配体由 2~5 分钟降到 3~5 秒。

网格格式（tools/stepmesh/step2mesh.js 生成）：
    4B magic "S3DM" + 4B 小端 JSON 头长度 + JSON 头 + 二进制块
    JSON: {meshes:[{name,vertices,posOffset,posBytes,idxOffset,idxBytes,
                    nrmOffset,nrmBytes,color}]}
"""
import html
import json
import os

from . import sheet

def mesh_path(file_key: str) -> str:
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + ".stepmesh.gz")


def mesh_ready(file_key: str) -> bool:
    p = mesh_path(file_key)
    try:
        return os.path.getsize(p) > 1024
    except OSError:
        return False


WIRE_SUFFIX = ".stepwire.gz"


def wire_path(file_key: str) -> str:
    """线框数据（W3DM：magic + uint32 线段数 + float32 端点数组）。

    给两类模型用：① 没有实体面（只有曲线/边）；② 实体太多、OCCT 三角化
    几分钟都跑不完（实测 192MB / 281 个实体的装配体，pars 324 秒后仍是空）。
    直接从 STEP 文本里取 EDGE_CURVE 的两个端点：10.7 万条线段 2.4MB，
    浏览器画 LineSegments 毫无压力，而解析只要 2.3 秒。
    """
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + WIRE_SUFFIX)


def wire_ready(file_key: str) -> bool:
    try:
        return os.path.getsize(wire_path(file_key)) > 1024
    except OSError:
        return False


# 空模型标记：离线预解析跑完但没得到任何实体面（只有曲线/曲面/基准面），
# 由 tools/preconvert_step.py 写入。有标记时不再放行到引擎的 O3DV ——
# 那边会白解析几十秒，最后报 "The model doesn't contain any meshes."
EMPTY_SUFFIX = ".stepmesh.empty"


def empty_path(file_key: str) -> str:
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + EMPTY_SUFFIX)


def nomesh_path(file_key: str) -> str:
    """标记：转换跑完了，但没有实体面（只剩线框）。"""
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + ".stepmesh.nomesh")


def no_solid(file_key: str) -> bool:
    return os.path.exists(nomesh_path(file_key))


def empty_reason(file_key: str) -> str:
    """空模型的原因（空串表示没有这个标记）。"""
    try:
        with open(empty_path(file_key), encoding="utf-8") as f:
            return (f.read() or "没有可显示的实体面").strip()[:200]
    except OSError:
        return ""


def viewer_html(file_key: str, file_name: str, token_q: str, wm_url: str,
                beacon: str = "") -> str:
    """3D 查看器：单页、无框架，three.js 直接画预解析好的网格。"""
    title = html.escape(file_name or "3D 模型")
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<link rel="icon" href="data:,"><!-- 空 favicon：浏览器默认会请求 /favicon.ico，日志里一堆 404 -->
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%%;overflow:hidden;background:#f4f5f7;color:#222;
 font-family:-apple-system,"Microsoft YaHei","PingFang SC",sans-serif}
body{display:flex;flex-direction:column}
/* ---- 顶栏：三行。第 1 行零件名，第 2 行操作按钮，第 3 行信息与说明 ---- */
#top{flex:none;background:#fff;border-bottom:1px solid #e3e6ea;position:relative;z-index:20;
 box-shadow:0 1px 3px rgba(20,30,50,.05)}
#top .r1{display:flex;align-items:center;height:31px;padding:0 10px 0 12px}
#top .r1 .ttl{font-weight:600;font-size:13.5px;color:#22262b;flex:1 1 auto;min-width:0;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:104px;
 /* 右侧留 104px 给固定住的放大倍率，极长文件名也不会被它压住 */ }
#bar{display:flex;align-items:center;gap:5px;padding:4px 10px 5px;flex-wrap:wrap;
 background:#fafbfc;border-top:1px solid #f0f2f5;border-bottom:1px solid #f0f2f5}
#bar>*{flex:none}   /* 关键：不加这条，flex 会把按钮压窄、文字折成竖排 */
#bar button{padding:3px 7px;white-space:nowrap}
#bar .sep{width:1px;height:17px;background:#e1e5ea;margin:0 4px}
#top .r3{display:flex;align-items:center;gap:10px;height:23px;padding:0 12px}
#top .r3 #info{flex:1 1 auto;min-width:0;color:#123a7d;font-size:12px;font-weight:500;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#bar button{border:1px solid #cfd4da;background:#fff;color:#2f6feb;border-radius:5px;
 padding:3px 9px;font-size:12px;cursor:pointer}
#bar button:hover{border-color:#2f6feb;background:#f5f8ff}
#bar button.on{background:#2f6feb;color:#fff;border-color:#2f6feb}
#bar button:disabled{color:#b9bfc7;border-color:#e3e6ea;background:#fafbfc;cursor:default}
#bar .zg{display:flex;gap:0}
#bar .zg button{width:26px;padding:3px 0;text-align:center;font-size:13px;line-height:1.2}
#bar .zg button:first-child{border-radius:5px 0 0 5px}
#bar .zg button:last-child{border-radius:0 5px 5px 0;border-left:none}
#cv{display:block;width:100%%;flex:1 1 auto;min-height:0}
#ld{position:fixed;left:0;right:0;bottom:0;top:var(--th,100px);display:flex;flex-direction:column;gap:10px;
 align-items:center;justify-content:center;background:rgba(255,255,255,.86);
 z-index:15;font-size:13px;color:#5b636e}
#ld.hide{display:none}
#ldSteps{margin:14px auto 0;min-width:270px;text-align:left}
#ldSteps .st{display:flex;gap:9px;align-items:baseline;font-size:12px;color:#8a9099;
 padding:3px 0;line-height:1.5}
#ldSteps .st b{flex:none;width:74px;font-weight:600;color:#9aa1ab}
#ldSteps .st.run b{color:#2f6feb}
#ldSteps .st.run b::after{content:'…'}
#ldSteps .st.done b{color:#22a06b}
#ldSteps .st.done b::after{content:' ✓'}
#ldSteps .st i{font-style:normal;color:#8a9099}
#ldSteps .st.done i{color:#6b7280}
.spin{width:32px;height:32px;border:3px solid #e6e8eb;border-top-color:#4a7cf7;
 border-radius:50%%;animation:r .9s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
#hint{position:fixed;left:12px;bottom:12px;z-index:12;max-width:calc(100%% - 24px);
 padding:7px 11px;border-radius:6px;background:rgba(255,255,255,.93);
 border:1px solid #e3e6ea;box-shadow:0 2px 8px rgba(20,30,50,.08);
 font-size:11.5px;color:#5b636e;line-height:1.7;transition:opacity .45s;cursor:default}
#hint b{color:#2f6feb;font-weight:600}
#hint.hide{opacity:0;pointer-events:none}
#cv{cursor:grab}
#cv:focus{outline:none}   /* canvas 只为接键盘焦点，不要那圈虚线 */
#cv.drag{cursor:grabbing}
#gz{position:fixed;right:12px;bottom:12px;z-index:12;pointer-events:none}
#tree{position:fixed;left:12px;top:calc(var(--th,100px) + 8px);bottom:12px;width:186px;z-index:13;
 background:rgba(255,255,255,.95);border:1px solid #e3e6ea;border-radius:7px;
 box-shadow:0 2px 10px rgba(20,30,50,.09);display:none;flex-direction:column;overflow:hidden;
 font-size:11.5px;color:#3a3f47}
#tree.on{display:flex}
#tree .th{padding:6px 8px;border-bottom:1px solid #eef0f3;font-weight:600;
 display:flex;align-items:center;gap:6px;flex:none}
#tree .th span{font-weight:400;color:#8a9099;font-size:11px}
#tree .th input{flex:1;min-width:0;border:1px solid #d8dce2;border-radius:4px;
 padding:2px 5px;font-size:11px;outline:none}
#tree .th input:focus{border-color:#2f6feb}
#tlist{flex:1;overflow:auto;padding:3px 0}
#tlist label{display:flex;align-items:center;gap:6px;padding:2.5px 9px;cursor:pointer;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#tlist label:hover{background:#f2f6ff}
#tlist input{margin:0;flex:none}
#tlist .nm{overflow:hidden;text-overflow:ellipsis}
#tree .tf{display:flex;gap:6px;padding:6px 8px;border-top:1px solid #eef0f3;flex:none}
#tree .tf button{flex:1;border:1px solid #cfd4da;background:#fff;color:#2f6feb;
 border-radius:4px;padding:3px 0;font-size:11px;cursor:pointer}
#tree .tf button:hover{background:#f5f8ff;border-color:#2f6feb}
#mtip{position:fixed;left:50%%;bottom:14px;transform:translateX(-50%%);z-index:14;
 padding:5px 12px;border-radius:16px;background:rgba(47,111,235,.94);color:#fff;
 font-size:12px;display:none;pointer-events:none}
#mtip.on{display:block}
/* 顶部"下载 / 网速"提示：#dlTip。下载进度、构建、失败重试、测速结果都走这里。
   字号 16.8px = 24px 的 70%%（按用户要求缩小 30%%），圆角与内边距同步收一点。 */
#dlTip{position:fixed;left:50%%;top:calc(var(--th,100px) + 16px);transform:translateX(-50%%);
 z-index:22;display:none;pointer-events:none;max-width:calc(100%% - 32px);
 padding:7px 19px;border-radius:17px;background:rgba(47,111,235,.95);color:#fff;
 font-size:16.8px;font-weight:500;line-height:1.3;text-align:center;white-space:nowrap;
 box-shadow:0 5px 16px rgba(20,45,100,.26)}
#dlTip.on{display:block}
#dlTip a{color:#fff}
/* 键盘还没生效时的提示（iframe 嵌套、页面文档没有焦点）：点任意处即可 */
#keyTip{position:fixed;left:50%%;bottom:58px;transform:translateX(-50%%);z-index:14;
 display:none;pointer-events:none;padding:7px 16px;border-radius:16px;
 background:rgba(23,42,74,.90);color:#fff;font-size:12.5px;line-height:1.4;
 box-shadow:0 2px 10px rgba(10,20,40,.22)}
#keyTip.on{display:block}
#ver{color:#b6bcc4;font-size:10.5px;flex:none;user-select:none;cursor:default}
/* 放大倍率：固定在右上角，黄底黑字、字号加大，滚动/缩放时始终可见 */
#zoom{position:fixed;right:10px;top:4px;z-index:31;background:#ffd83d;color:#000;
 border:1px solid #e0b400;border-radius:6px;padding:1px 9px;line-height:1.4;
 font:700 15px/1.4 Consolas,Menlo,"Courier New",monospace;letter-spacing:.3px;
 box-shadow:0 1px 4px rgba(0,0,0,.14);cursor:default;user-select:none}
/* 进度条：放在顶栏下方居中（原来是贴底的，容易被底部提示条和操作面板挤住）。
   宽 516（原 258 的两倍）、条高 10（原 5 的两倍）、字 15px，外加白底圆角框，
   因为它浮在 3D 画面上，没有底框时深色模型会让文字看不清。 */
#prog{position:fixed;left:50%%;top:calc(var(--th,100px) + 14px);transform:translateX(-50%%);
 z-index:16;width:516px;max-width:calc(100%% - 24px);display:none;pointer-events:none;
 padding:11px 15px;border-radius:9px;background:rgba(255,255,255,.95);
 border:1px solid #e3e6ea;box-shadow:0 3px 12px rgba(20,30,50,.12);
 font-size:15px;font-weight:500;color:#3a3f47;text-align:center;line-height:1.35}
#prog.on{display:block}
#prog .bar{height:10px;background:#e6e8eb;border-radius:5px;overflow:hidden;margin-bottom:8px}
#prog .fill{height:100%%;width:0;background:#2f6feb;border-radius:5px;transition:width .4s}
#clipPanel,#expPanel{position:fixed;right:12px;top:calc(var(--th,100px) + 8px);z-index:13;width:196px;display:none;
 background:rgba(255,255,255,.96);border:1px solid #e3e6ea;border-radius:7px;
 box-shadow:0 2px 10px rgba(20,30,50,.09);padding:8px 10px;font-size:11.5px;color:#3a3f47}
#clipPanel.on,#expPanel.on{display:block}
#expPanel{top:auto;bottom:104px}
#clipPanel .pr,#expPanel .pr{display:flex;align-items:center;gap:6px;margin-bottom:6px}
#clipPanel .pr b,#expPanel .pr b{font-weight:600;margin-right:auto}
#clipPanel .pr button,#expPanel .pr button{border:1px solid #cfd4da;background:#fff;
 color:#3a3f47;border-radius:4px;padding:2px 7px;font-size:11px;cursor:pointer}
#clipPanel .pr button.on{background:#2f6feb;border-color:#2f6feb;color:#fff}
/* 视图面板：标准视角 + 拖拽反向 + 重定坐标系 */
#viewPanel{position:fixed;right:12px;top:calc(var(--th,100px) + 8px);z-index:13;width:214px;display:none;
 background:rgba(255,255,255,.97);border:1px solid #e3e6ea;border-radius:7px;
 box-shadow:0 2px 12px rgba(20,30,50,.12);padding:9px 10px;font-size:11.5px;color:#3a3f47}
#viewPanel.on{display:block}
#viewPanel .pr{display:flex;align-items:center;gap:6px;margin-bottom:7px}
#viewPanel .pr b{font-weight:600;margin-right:auto}
#viewPanel .pr button{border:1px solid #cfd4da;background:#fff;border-radius:4px;padding:2px 7px;
 font-size:11px;cursor:pointer;color:#3a3f47}
#viewPanel .vg{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-bottom:8px}
#viewPanel .vg button{border:1px solid #cfd4da;background:#fff;color:#2f6feb;border-radius:5px;
 padding:5px 0;font-size:11.5px;cursor:pointer}
#viewPanel .vg button:hover{border-color:#2f6feb;background:#f5f8ff}
#viewPanel .vg button.iso{grid-column:span 3}
#viewPanel label.ck{display:flex;align-items:center;gap:6px;padding:3px 0;cursor:pointer;font-size:11.5px}
#viewPanel .widebtn{width:100%%;margin-top:7px;border:1px solid #cfd4da;background:#fff;color:#2f6feb;
 border-radius:5px;padding:5px 0;font-size:11.5px;cursor:pointer}
#viewPanel .widebtn:hover{border-color:#2f6feb;background:#f5f8ff}
#viewPanel .hintline{color:#8a9099;font-size:10.5px;margin-top:7px;line-height:1.55}
#clipRange,#expRange{width:100%%;margin:0}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9;
 background-image:url("%(wm)s");background-repeat:repeat}
</style></head><body>
<div id="top">
  <div class="r1"><span class="ttl" title="%(title)s">%(title)s</span></div>
  <div id="bar">
    <span id="modes">
      <button data-m="solid" class="on">实体</button>
      <button data-m="edge">线框</button>
    </span>
    <span class="zg">
      <button id="zin" title="放大（滚轮 / + 键）">＋</button>
      <button id="zout" title="缩小（滚轮 / − 键）">－</button>
    </span>
    <button id="colBtn" class="on" title="原色（文件配色）/ 分色（按零件自动配色）/ 素色">原色</button>
    <span class="sep"></span>
    <span class="zg">
      <button id="spinL" title="向左水平旋转（Q 键）">⟲</button>
      <button id="spinR" title="向右水平旋转（E 键）">⟳</button>
    </span>
    <span class="zg">
      <button id="tiltU" title="向前翻转：视角抬高、看到顶面（W 键）">↑</button>
      <button id="tiltD" title="向后翻转：视角降低、看到底面（S 键）">↓</button>
    </span>
    <span class="zg">
      <button id="rollL" title="横向旋转：画面绕视线逆时针倾斜，用来摆正歪掉的图（A 键）">↺</button>
      <button id="rollR" title="横向旋转：画面绕视线顺时针倾斜（D 键）">↻</button>
    </span>
    <button id="viewBtn" title="标准视角切换、拖拽反向、重定坐标系">视图</button>
    <button id="reset" title="复位视角（双击画面 / 0 键）">复位视角</button>
    <span class="sep"></span>
    <button id="treeBtn" title="零部件显示/隐藏">部件</button>
    <button id="measBtn" title="测量距离（M 键）；按住 Alt 点面测面积；Esc 退出">测量</button>
    <button id="planeBtn" title="测两个平面之间的距离：点第一个面、再点第二个面（P 键，Esc 退出）">面距</button>
    <button id="clipBtn" title="剖切：沿 X/Y/Z 切开看内部">剖切</button>
    <button id="expBtn" title="爆炸视图：按零件散开">爆炸</button>
    <span class="sep"></span>
    <button id="netBtn" title="当前下载速度（点击重新测速）">⇅ --</button>
    <button id="help" title="操作提示">?</button>
  </div>
  <div class="r3">
    <span id="info">3D 模型</span>
    <span id="ver" title="页面版本。看不到这个数字说明浏览器拿的是旧缓存，按 Ctrl+F5 强刷">11-10s</span>
  </div>
</div>
<span id="zoom" title="相对初始视角的放大倍数">1.0×</span>
<canvas id="cv" tabindex="0" autofocus></canvas>
<canvas id="gz" width="76" height="76"></canvas>
<div id="tree">
  <div class="th">部件 <span id="tcnt"></span><input id="tfind" placeholder="搜索"></div>
  <div id="tlist"></div>
  <div class="tf"><button id="tall">全显</button><button id="tnone">全隐</button></div>
</div>
<div id="mtip">点击两个点测量距离（Esc 退出）</div>
<div id="dlTip"></div>
<div id="prog"><div class="bar"><div class="fill" id="progFill"></div></div><span id="progTxt"></span></div>
<div id="viewPanel">
  <div class="pr"><b>标准视角</b><button id="viewClose">关</button></div>
  <div class="vg">
    <button data-v="front">正视</button><button data-v="back">后视</button><button data-v="left">左视</button>
    <button data-v="right">右视</button><button data-v="top">俯视</button><button data-v="bottom">仰视</button>
    <button data-v="iso" class="iso">等轴测（默认四分之三视角）</button>
  </div>
  <label class="ck"><input type="checkbox" id="invDrag">拖拽反向（像抓住模型拖）</label>
  <button class="widebtn" id="rebaseBtn" title="把当前视角定成新的基准：模型按现在看到的样子摆正，之后所有旋转都以它为参考">以当前视角为基准（重定坐标系）</button>
  <div class="hintline">拖拽时按住 <b>X</b> / <b>Y</b> / <b>Z</b> 键，只绕该轴转，不会跑偏。</div>
</div>
<div id="clipPanel">
  <div class="pr"><b>剖切</b>
    <button data-ax="x">X</button><button data-ax="y" class="on">Y</button><button data-ax="z">Z</button>
    <button id="clipOff">关闭</button>
  </div>
  <input id="clipRange" type="range" min="0" max="100" value="50">
</div>
<div id="expPanel">
  <div class="pr"><b>爆炸</b><span id="expVal">0%%</span><button id="expOff">复位</button></div>
  <input id="expRange" type="range" min="0" max="100" value="0">
</div>
<div id="ld"><div class="spin"></div><div id="ldt">正在载入模型…</div><div id="ldSteps"></div></div>
<div id="hint">
  <b>左键拖</b> 任意角度旋转（上下可翻到底面） ·
  <b>⟲ ⟳</b> / <b>Q E</b> 左右转 · <b>↑ ↓</b> / <b>W S</b> 前后翻 ·
  <b>↺ ↻</b> / <b>A D</b> 横向旋转（画面原地倾斜，用来摆正歪掉的图） ·
  <b>右键拖</b> / <b>Shift+左键</b> 平移 · <b>滚轮</b> 或 <b>＋ －</b> 缩放 ·
  <b>测量</b>（M）点两点量距离、Alt 点面量面积 · <b>面距</b>（P）点两个面量平面距离 ·
  拖拽时按住 <b>X</b>/<b>Y</b>/<b>Z</b> 只绕该轴转 · <b>视图</b> 里有标准视角与拖拽反向 ·
  <b>双击</b> / <b>0</b> 复位
</div>
<div id="keyTip">点一下画面，键盘快捷键就能用了（Q E 左右转 · W S 前后翻 · A D 横滚 · 0 复位）</div>
<div id="wm"></div>
<script src="/three/three.min.js"></script>
<script src="/three/OrbitControls.js"></script>
<script>
(function(){
  var FK=%(fk)s, KKT=%(kkt)s, DATA='/stepdata/'+FK;
  /* 只读诊断出口：整个查看器是 IIFE，外部拿不到 meshes/renderer，
     现场排查"材质有没有共享、几何有没有泄漏"只能靠改代码。
     这里挂一个只读快照，控制台里 __kk.diag() 就能看到。 */
  window.__kk={ diag:function(){
    var mats={}, n=0;
    try{
      (meshes||[]).forEach(function(m){
        var u=m.obj&&m.obj.material&&m.obj.material.uuid;
        if(u){ mats[u]=(mats[u]||0)+1; n++; }
      });
    }catch(e){}
    var ri=(typeof renderer!=='undefined'&&renderer&&renderer.info)?renderer.info:null;
    return { parts:(typeof meshes!=='undefined'?meshes.length:0),
             uniqueMaterials:Object.keys(mats).length,
             geometries:ri?ri.memory.geometries:-1,
             drawCalls:ri?ri.render.calls:-1,
             triangles:ri?ri.render.triangles:-1,
             poolSize:(typeof matPoolN!=='undefined'?matPoolN:-1) };
  } };
  var cv=document.getElementById('cv'), ld=document.getElementById('ld'),
      ldt=document.getElementById('ldt'), info=document.getElementById('info');
  var renderer=null;
  try{
    renderer=new THREE.WebGLRenderer({canvas:cv,antialias:true});
  }catch(e){
    // WebGL 被禁用 / 显卡驱动异常：给一句人话，别让用户对着空白页猜
    document.getElementById('ldt').innerHTML =
      '浏览器没能创建 WebGL 上下文（显卡驱动或浏览器设置问题）。' +
      '<br><a href="javascript:location.reload()" style="color:#2f6feb">点这里重试</a>';
    throw e;
  }
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio||1));
  var scene=new THREE.Scene();
  scene.background=new THREE.Color(0xf4f5f7);
  var camera=new THREE.PerspectiveCamera(45,1,0.1,1e7);
  /* 光照按"看得清轮廓"配：环境光压到 0.45，靠三个方向光拉出层次，
     环境光给太多会一片惨白，薄壁件的边界线全糊掉。 */
  scene.add(new THREE.AmbientLight(0xffffff,0.45));
  var d1=new THREE.DirectionalLight(0xffffff,0.72); d1.position.set(0.8,1,0.9); scene.add(d1);
  var d2=new THREE.DirectionalLight(0xffffff,0.34); d2.position.set(-1,-0.8,-0.7); scene.add(d2);
  var d3=new THREE.DirectionalLight(0xffffff,0.22); d3.position.set(0,-1,0.4); scene.add(d3);
  /* 竖直轴固定取 Z。three.js 默认 up=(0,1,0)，但 STEP/FreeCAD 这类 CAD 文件
     是 Z 朝上：拿 Y 当上，左键左右拖出来的是"翻滚"（模型绕前后轴翻），
     不是原地转圈，俯仰也会歪 —— 实测同一份标定图，Z 朝上时设备是立在底板上，
     Y 朝上时整个躺倒。这里按 Z；若以后遇到 Y 朝上的模型，再加逐文件判断。 */
  var UP=new THREE.Vector3(0,0,1);
  camera.up.copy(UP);

  var group=new THREE.Group(); scene.add(group);
  var ctl=null, meshes=[], model=null, fitDist=1;

  /* 顶栏是三行、按钮还会按窗口宽度换行，高度不是固定值。把实测高度写进 --th，
     加载遮罩 / 部件树 / 剖切面板都按它下移，窗口再窄也不会压住按钮。 */
  function syncTopH(){
    var t=document.getElementById('top');
    if(t) document.documentElement.style.setProperty('--th', t.offsetHeight+'px');
  }
  syncTopH();
  function resize(){
    syncTopH();
    var w=cv.clientWidth||cv.parentElement.clientWidth, h=cv.clientHeight||(window.innerHeight-100);
    renderer.setSize(w,h,false); camera.aspect=w/Math.max(1,h); camera.updateProjectionMatrix();
  }
  /* 取景用"主体簇"而不是整份包围盒。
     FreeCAD/STEP 文档里常有几个零件被摆在很远的地方（比如 1800mm 外的小片），
     按整体包围盒算，主体会缩成画面中心一小块，什么都看不清。
     做法：按顶点数排序，取累计九成的那批零件的并集当主体。 */
  var cluC=new THREE.Vector3(), cluR=1, cluBox=new THREE.Box3();
  var wireFit=null;   // 线框模式的取景中心/半径（见 buildWire）
  function computeCluster(){
    /* 线框是一整块几何，按顶点数挑"主体"无从下手（只有一组）。若直接用包围盒，
       中心会算到几根长管围出的空当里 —— 表现就是复位后模型偏在一角、放大后
       整个跑出画面。所以 buildWire 预先算好质心与 85 分位半径，这里直接用。 */
    if(wireFit){
      cluC.copy(wireFit.c);
      cluR=wireFit.r || 1;
      cluBox.setFromCenterAndSize(cluC,
        new THREE.Vector3(cluR*2, cluR*2, cluR*2));
      return;
    }
    /* 按"顶点数占比"挑主体：装配体里真正占体积的几个大件顶点最多，
       散落在远处的小垫片只有一两千个点。取累计顶点九成的那批当主体，
       远处的小件不参与取景（它们仍在场景里，缩小就能看到）。 */
    var cs=[];
    group.children.forEach(function(o){
      if(!o.geometry.boundingBox) o.geometry.computeBoundingBox();
      if(!o.geometry.boundingBox) return;      /* 空几何：跳过，别让 clone 抛错 */
      /* 必须用 matrixWorld 变换：爆炸位移写在 obj.position 上、重定坐标系会转整个 group，
         只读 geometry.boundingBox 会把两者都漏掉（爆炸后复位视角框不住零件、
         重定基准后取景全偏）。 */
      var b=o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld);
      var pos=o.geometry.getAttribute('position');
      cs.push({b:b, n:(pos?pos.count:0)});
    });
    if(!cs.length) return;
    var total=0;
    cs.forEach(function(c){ total+=c.n; });
    cs.sort(function(x,y){ return y.n-x.n; });
    var acc=0, keep=[];
    for(var i=0;i<cs.length;i++){
      keep.push(cs[i].b);
      acc+=cs[i].n;
      if(total && acc>=total*0.92) break;
    }
    var box=new THREE.Box3();
    keep.forEach(function(b){ box.union(b); });
    box.getCenter(cluC);
    cluBox=box;
    cluR=box.getSize(new THREE.Vector3()).length()/2 || 1;
  }
  /* 视线方向：右上前方四分之三视角，归一化后按距离摆位 */
  var VIEW_DIR=new THREE.Vector3(0.50,-0.62,0.55).normalize();
  function fit(){
    if(!model) return;
    group.updateMatrixWorld(true);   /* computeCluster 要读 matrixWorld（爆炸位移 / 重定基准） */
    computeCluster();
    /* 距离按"包围盒 8 个角投影到相机平面"反算，不用"半径乘几倍"这种拍脑袋系数：
       细长件按包围球算会缩成画面中心一小条，按最大边长算又会顶出画面。
       对每个角 p（相对中心）：相机在 C+D*u，要让它落在视锥内必须
         D >= p·u + max(|p·right|/tan(水平半角), |p·up|/tan(垂直半角))
       取所有角的最大值就是最近能放到多近。 */
    var vHalf=Math.PI*45/360;
    var hHalf=Math.atan(Math.tan(vHalf)*Math.max(camera.aspect,0.2));
    var tH=Math.tan(hHalf), tV=Math.tan(vHalf);
    var right=new THREE.Vector3().crossVectors(VIEW_DIR, UP).normalize();
    var up=new THREE.Vector3().crossVectors(right, VIEW_DIR).normalize();
    var dist=0, p=new THREE.Vector3();
    for(var ci=0; ci<8; ci++){
      p.set(ci&1 ? cluBox.max.x : cluBox.min.x,
            ci&2 ? cluBox.max.y : cluBox.min.y,
            ci&4 ? cluBox.max.z : cluBox.min.z).sub(cluC);
      dist=Math.max(dist, p.dot(VIEW_DIR) +
        Math.max(Math.abs(p.dot(right))/tH, Math.abs(p.dot(up))/tV));
    }
    dist*=1.04;   // 视野留一点边，贴边看着压抑
    camera.position.copy(cluC).addScaledVector(VIEW_DIR, dist);
    camera.near=Math.max(dist/5000, cluR/2000); camera.far=dist+cluR*40;
    camera.updateProjectionMatrix();
    /* 控件只建一次。原来每次 fit 都 new 一个 OrbitControls：
       ① 新控件身上没有 change 监听 → 相机摆好了但画布不重绘，看着"复位没反应"；
       ② 旧控件的事件监听还挂在 canvas 上 → 之后一拖拽，它按旧的球坐标把相机拉回去。
       两个叠加就是"复位视角似乎没用"。 */
    var c=ensureCtl();
    c.target.copy(cluC);
    /* 最小距离决定"最多能放多大"。原来是 cluR*0.01（这份装配体约 27 单位），
       看一根几毫米的标定线还差得远；放到 cluR*0.002 后能贴到 5.5 单位，
       相对整机 5500 单位已是 1000 倍，细节够看了。 */
    c.minDistance=cluR*0.002; c.maxDistance=cluR*60;
    /* 自由旋转可以一路翻到上下颠倒，复位时把上方向也拉回正立 */
    camera.up.copy(UP);
    fitDist=dist;
    applyFrustum(dist);
    camera.lookAt(cluC);
    updateZoomBadge();
    renderer.render(scene,camera);
  }
  /* near/far 跟着当前距离走。near 原来按 cluR/2000 定（这份模型 1.4 单位），
     贴近看时会把眼前的线直接裁掉；改成按距离缩放，贴着看也不会切。 */
  function applyFrustum(d){
    camera.near=Math.max(d/500, 0.02);
    camera.far=d+cluR*3;
    camera.updateProjectionMatrix();
  }
  function currentDist(){
    return camera.position.distanceTo(ctl ? ctl.target : cluC);
  }
  function updateZoomBadge(){
    var el=document.getElementById('zoom');
    if(!el) return;
    var d=currentDist();
    el.textContent=(fitDist/Math.max(1e-6,d)).toFixed(fitDist/d>=10?0:1)+'×';
  }
  /* OrbitControls 只实例化一次；change 里重绘，滚轮/拖拽都有反馈 */
  function ensureCtl(){
    if(ctl) return ctl;
    ctl=new THREE.OrbitControls(camera, cv);
    if(ctl.addEventListener) ctl.addEventListener('change', function(){
      renderer.render(scene,camera);
      updateZoomBadge();
    });
    /* 方向键平移。keyPanSpeed 默认 7px/次，装配体尺度下几乎看不出移动，调到 18 */
    if(ctl.listenToKeyEvents){ ctl.listenToKeyEvents(window); }
    ctl.keyPanSpeed=18;
    ctl.enableZoom=false;      // 滚轮由上面的 zoomAt 接管（朝光标缩放 + 每次 25%%）
    /* 左键旋转交给下面的 orbitBy：OrbitControls 的极角只能在 0~PI 之间，
       转到正上方/正下方就锁死，翻不过去。右键平移、方向键仍由它负责。 */
    ctl.enableRotate=false;
    return ctl;
  }
  /* 缩放：光标处保持不动（"往哪看就往哪放大"），按钮则以外框中心为焦点。
     滚轮按 25%%/格，一屏 4 格就是 2.4 倍；原来 OrbitControls 每格只有 5%%，
     滚二十格才一倍，用户体感就是"放不大"。 */
  var ZMIN_R=0.002;

  /* 缩小朝 target（也就是画面中心）收放。
     曾试过"朝光标放大"（把 target 朝光标射线与平面的交点挪），但两种实现都翻车：
     ① 只按包围盒判断光标是否落在模型上 —— 包围盒里大片是空的，照样往虚无里扎；
     ② 回读光标像素判断有没有线 —— 这个环境（无头 Chrome + 多重采样）readPixels
        读回来全是 0，判断永远成立，结果更糟。
     现在的做法简单可靠：想放大某处，先用右键把它拖到画面中间，再滚轮放大。
     这样无论怎么滚，模型都不会被"放大到画面外"。 */
  function zoomAt(f){
    if(!model) return;
    var c=ensureCtl();
    var d=currentDist();
    var lo=Math.max(cluR*ZMIN_R, 1e-3), hi=cluR*60;
    var nd=Math.min(hi, Math.max(lo, d*f));
    if(Math.abs(nd-d)<1e-6) return;
    var dir=new THREE.Vector3().subVectors(camera.position, c.target);
    if(dir.lengthSq()<1e-12) return;
    dir.normalize();
    camera.position.copy(c.target).addScaledVector(dir, nd);
    applyFrustum(nd);
    camera.lookAt(c.target);
    renderer.render(scene,camera);
    updateZoomBadge();
  }
  function zoomBy(f){ zoomAt(f); }
  /* 绕竖直轴转。左键拖虽然也能转，但方向取决于当前俯仰角，想"看清楚侧面"
     往往要试几下；这两个按钮是固定步长 15°，按一下转一下，可预期。
     ---- 旋转圆心统一钉在主体几何中心 cluC ----
     取景、复位用的都是 cluC（主体簇包围盒中心）。旋转也绕它：
     右键平移改的只是"看向哪"，不该把"绕哪转"一起带走 ——
     以前绕 ctl.target，平移看过某个局部后 target 已经在模型外，
     再点 ⟲ ⟳ 整个模型就绕着画面外的一点甩出去，越放大越明显。
     现在相机位置和注视点一起绕 cluC 刚性旋转：视线方向不变，只是公转。 */
  function spin(deg){
    if(!model) return;
    var c=ensureCtl();
    var a=deg*Math.PI/180;
    var p=cluC;
    var off=new THREE.Vector3().subVectors(camera.position, p).applyAxisAngle(UP, a);
    var tOff=new THREE.Vector3().subVectors(c.target, p).applyAxisAngle(UP, a);
    camera.position.copy(p).add(off);
    c.target.copy(p).add(tOff);
    camera.up.applyAxisAngle(UP, a);   // 视角翻过时 up 也跟着，避免画面滚转
    camera.lookAt(c.target);
    applyFrustum(Math.max(currentDist(), off.length()));
    renderer.render(scene,camera);
    updateZoomBadge();
  }
  /* 前后翻转（俯仰）：绕相机右轴转，camera.up 跟着一起转，和左键上下拖是同一条路径，
     所以可以一路翻过去、不会被极点卡住。每次 15°，与 ⟲ ⟳ 的步长一致。 */
  function tilt(deg){ orbitBy(0, deg*Math.PI/180); }
  /* 横向旋转（横滚 roll）：绕"视线轴"转 camera.up。
     ⟲ ⟳ 和 ↑ ↓ 动的都是**相机位置**（绕模型转圈、抬高降低），画面里的上方向从没变过；
     模型被摆歪、或者一路翻到画面倒置，这两种操作都摆不正 —— 差的就是这一维。
     这里相机位置不动，只把 camera.up 绕 (target→camera) 旋转，画面在屏幕平面内打转。
     步长 15°，与另外两组一致；复位视角（双击 / 0）会把 up 拉回世界 Z，一并摆正。 */
  function rollView(deg){
    if(!model) return;
    var c=ensureCtl();
    var v=new THREE.Vector3().subVectors(camera.position, c.target);
    if(v.lengthSq()<1e-12) return;
    v.normalize();
    camera.up.applyAxisAngle(v, deg*Math.PI/180).normalize();
    camera.lookAt(c.target);
    if(c.update) c.update();     // 让控件的内部球坐标跟上新的 up，之后拖拽不跳
    renderer.render(scene,camera);
  }
  /* ---- 左键自由旋转：上下不设限，能一路翻过去 ----
     OrbitControls 的轨道模型把极角锁在 0~PI，转到正上方/正下方画面就不动了
     （实测继续拖两次 hash 不变），用户要"前后左右上下都能转 360 度"，只能自己接手：
       · 水平拖 → 绕世界竖直轴 UP 转，保持不翻滚（这条是之前修过的 bug，不能退回去）；
       · 垂直拖 → 绕相机当前的右轴转，并让 camera.up 一起转。这样视线永远不跟 up
         平行，极点不会退化，可以连续翻过去（翻过头画面是倒的，按"复位视角"回正）。
     控件那边只保留右键/Shift+左键平移和方向键。 */
  var dragRot={on:false,x:0,y:0,id:null};
  /* 绕任意世界轴、以主体几何中心为圆心刚性旋转（相机 + 注视点 + up 一起转）。
     给"按住 X/Y/Z 只绕该轴转"用，也方便以后加别的轴。 */
  function rotateAboutAxis(axis, ang){
    if(!model || !ang) return;
    var c=ensureCtl();
    var p=cluC;
    var off=new THREE.Vector3().subVectors(camera.position,p);
    var r=off.length(); if(r<1e-9) return;
    var tOff=new THREE.Vector3().subVectors(c.target,p);
    off.applyAxisAngle(axis,ang); tOff.applyAxisAngle(axis,ang);
    camera.up.applyAxisAngle(axis,ang);
    camera.position.copy(p).add(off);
    c.target.copy(p).add(tOff);
    camera.lookAt(c.target);
    applyFrustum(Math.max(currentDist(), r));
    renderer.render(scene,camera);
    updateZoomBadge();
  }
  /* 拖拽反向（记住用户选择） */
  var invertDrag=false;
  try{ invertDrag = localStorage.getItem('kk_inv_drag')==='1'; }catch(e){}
  /* 标记对象（点、线、数值标签）都挂在 group 上，跟着爆炸走；但重定坐标系之后
     group 自身有旋转，世界坐标直接塞进去会被二次变换 → 统一转成 group 局部坐标再加。
     爆改 group 变换后把 markInvDirty 置 true 让缓存失效。 */
  var _gInv=new THREE.Matrix4(), _gInvOk=false, markInvDirty=false;
  function localOf(p){
    if(!_gInvOk || markInvDirty){
      group.updateMatrixWorld(true);
      _gInv.copy(group.matrixWorld).invert();
      _gInvOk=true; markInvDirty=false;
    }
    return p.clone().applyMatrix4(_gInv);
  }
  /* 按住 X / Y / Z 键时，拖拽只绕该世界轴转：想看某个侧面时不会同时跑偏 */
  var lockAxis=null;
  var AXIS_X=new THREE.Vector3(1,0,0), AXIS_Y=new THREE.Vector3(0,1,0);
  function orbitBy(dAz,dEl){
    if(!model) return;
    if(lockAxis){
      var ax = lockAxis==='x' ? AXIS_X : (lockAxis==='y' ? AXIS_Y : UP);
      rotateAboutAxis(ax, (dAz||0) + (dEl||0));
      return;
    }
    var c=ensureCtl();
    var p=cluC;                                  /* 圆心：主体几何中心（同 spin） */
    var off=new THREE.Vector3().subVectors(camera.position,p);
    var r=off.length(); if(r<1e-9) return;
    var tOff=new THREE.Vector3().subVectors(c.target,p);
    camera.updateMatrix();
    var right=new THREE.Vector3().setFromMatrixColumn(camera.matrix,0).normalize();
    if(dAz){
      off.applyAxisAngle(UP,dAz); tOff.applyAxisAngle(UP,dAz); camera.up.applyAxisAngle(UP,dAz);
    }
    if(dEl){
      off.applyAxisAngle(right,dEl); tOff.applyAxisAngle(right,dEl); camera.up.applyAxisAngle(right,dEl);
    }
    camera.position.copy(p).add(off);
    c.target.copy(p).add(tOff);
    camera.lookAt(c.target);
    applyFrustum(Math.max(currentDist(), r));
    renderer.render(scene,camera);
    updateZoomBadge();
  }
  cv.addEventListener('pointerdown', function(e){
    if(e.button!==0 || !model) return;
    dragRot.on=true; dragRot.x=e.clientX; dragRot.y=e.clientY; dragRot.id=e.pointerId;
    if(cv.setPointerCapture){ try{ cv.setPointerCapture(e.pointerId); }catch(err){} }
  });
  cv.addEventListener('pointermove', function(e){
    if(!dragRot.on) return;
    var dx=e.clientX-dragRot.x, dy=e.clientY-dragRot.y;
    if(!dx && !dy) return;
    dragRot.x=e.clientX; dragRot.y=e.clientY;
    var k=2*Math.PI/Math.max(1,cv.clientHeight);
    /* 拖拽反向：默认是"相机绕模型转"（模型往拖的反方向走）；
       勾选后反过来，像用手抓住模型拖。 */
    if(invertDrag) orbitBy(dx*k, -dy*k);
    else orbitBy(-dx*k, dy*k);
  });
  function endRot(){
    if(!dragRot.on) return;
    dragRot.on=false;
    if(cv.releasePointerCapture && dragRot.id!=null){
      try{ cv.releasePointerCapture(dragRot.id); }catch(err){}
    }
    dragRot.id=null;
  }
  window.addEventListener('pointerup', endRot);
  window.addEventListener('pointercancel', endRot);
  /* 自己接管滚轮：OrbitControls 那套每格 5%%（滚二十格才一倍），太钝；
     这里 25%%/格，四格就是 2.4 倍。 */
  cv.addEventListener('wheel', function(e){
    if(!model) return;
    e.preventDefault();
    zoomAt(e.deltaY>0 ? 1.25 : 1/1.25);
  }, {passive:false});
  /* 线框模式：4 字节 magic W3DM + uint32 线段数 + float32 端点（每条线段 6 个数）。
     用于"没有实体面"或"实体太大、三角化跑不动"的模型。 */
  function buildWire(buf, dv){
    var n=dv.getUint32(4,true);
    var pos=new Float32Array(buf.slice(8, 8+n*6*4));
    var g=new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos,3));
    var mat=new THREE.LineBasicMaterial({color:0x1f4fbf});   // 加深一档：浅蓝 1px 线在白底上发虚
    var obj=new THREE.LineSegments(g, mat);
    group.add(obj);
    /* wire:true —— "实体/线框/分色"切换对它无效（它本来就是线），
       applyMats 见到这个标记直接跳过，别把线改成 Mesh 材质。
       不能靠 name==='线框' 判断：模型里真的叫这个名字的部件也会被误伤。 */
    meshes.push({obj:obj, name:'线框', wire:true});
    /* 取景用质心 + 85 分位点距，而不是包围盒：
       装配体里常有几根几米长的管子，包围盒中心落在它们围出的空处，
       按那个中心取景，模型会偏在一角，放大几次就跑出画面了。 */
    (function(){
      var cnt=pos.length/3;
      var step=Math.max(1, Math.floor(cnt/4000));   // 抽稀，几十万点也只要几毫秒
      var cx=0, cy=0, cz=0, n=0, i;
      for(i=0;i<cnt;i+=step){ cx+=pos[i*3]; cy+=pos[i*3+1]; cz+=pos[i*3+2]; n++; }
      if(!n) return;
      cx/=n; cy/=n; cz/=n;
      var ds=[];
      for(i=0;i<cnt;i+=step){
        var dx=pos[i*3]-cx, dy=pos[i*3+1]-cy, dz=pos[i*3+2]-cz;
        ds.push(Math.sqrt(dx*dx+dy*dy+dz*dz));
      }
      ds.sort(function(a,b){ return a-b; });
      var r=ds[Math.min(ds.length-1, Math.floor(ds.length*0.85))] || 1;
      wireFit={c:new THREE.Vector3(cx,cy,cz), r:r};
    })();
    /* 两种情况都会走到线框：① 大装配体实体网格还在后台转（先让用户看到结构）；
       ② 模型本身没有可用实体面。文案要同时覆盖，不能一上来就说"没有实体面"。 */
    info.textContent='线框 '+n.toLocaleString()+' 条线段 · 实体网格解析中，好了会自动切过来'
      +bboxText()+'（若一直是线框，说明该模型没有可用实体面）';
    // 线框模型没有面片，"实体/线框"切换对它无效，直接隐藏，避免点了没反应
    var md=document.getElementById('modes'); if(md) md.style.display='none';
    // 实体网格在后台转，转好后原地升级，不用用户刷新（见 upgradeToMesh）
    watchMesh();
  }
  /* 没有文件颜色的零件：按序号散列出色相，饱和度/亮度压低，接近工业配色 */
  var colorMode=0;      // 0=原色 1=分色 2=素色
  function autoColor(name){
    var m=/part([0-9]+)/.exec(name||'');   // 用 [0-9] 写法，避免正则转义在 Python 模板里报警
    var k=m? parseInt(m[1],10) : (name||'').length;
    var h=(k*0.6180339887)%%1;   // 模板里字面百分号要写两个
    return new THREE.Color().setHSL(h, 0.34, 0.70);
  }
  /* 按当前模式取材质，同色共享（见 matPool）。
     键里带上模式与色值：'s:0.72,0.72,0.77' / 'a:hue' / 'gray' / 'edge'。 */
  function matFor(m, mode){
    if(mode===3 || edgeMode) return matOf('edge', function(){
      return new THREE.MeshBasicMaterial({color:0x4a7cf7, wireframe:true});
    });
    if(mode===2) return matOf('gray', function(){
      return new THREE.MeshStandardMaterial({color:0xbcc6d2, metalness:0.10,
        roughness:0.62, flatShading:false, side:THREE.DoubleSide});
    });
    if(mode===1){
      var c=autoColor(m.name);
      var key='a:'+c.getHexString();
      return matOf(key, function(){
        return new THREE.MeshStandardMaterial({color:c, metalness:0.10,
          roughness:0.62, flatShading:false, side:THREE.DoubleSide});
      });
    }
    var key='s:'+(m.cFile? m.cFile.getHexString() : 'bcc6d2')+(m.hasColor?'c':'');
    return matOf(key, function(){
      var c2=m.cFile? m.cFile.clone() : new THREE.Color(0xbcc6d2);
      var mm=new THREE.MeshStandardMaterial({color:c2, metalness:0.10,
        roughness:0.62, flatShading:false, side:THREE.DoubleSide});
      if(m.hasColor && mm.color.convertSRGBToLinear) mm.color.convertSRGBToLinear();
      return mm;
    });
  }
  function applyMats(){
    var mode = edgeMode ? 3 : colorMode;
    meshes.forEach(function(m){
      if(!m.wire) m.obj.material = matFor(m, mode);
    });
  }
  /* 清空当前模型。线框转实体时会重建一次，6708 个部件的几何/材质不释放
     就是几十 MB 显存泄漏，所以逐项 dispose。
     注意：材质现在是**按颜色共享**的（见 matOf），同一个对象可能挂在几百个
     部件上，这里只能 dispose 几何；材质由 matPool 统一管理，重建时复用。 */
  function clearModel(){
    for(var i=group.children.length-1;i>=0;i--){
      var o=group.children[i];
      group.remove(o);
      if(o.geometry) o.geometry.dispose();
    }
    meshes.length=0;
    if(partGroups) partGroups.length=0;   // 部件分组下次 buildTree 时按新模型重建
    clearMarks();
  }
  /* 材质池：按"用途+颜色"共享。
     大装配体实测 9419 个部件，原来每个部件建 4 个材质（原色/分色/素色/线框）
     = 37676 个 MeshStandardMaterial，堆内存 341MB，首帧还要给每个材质走一遍
     状态准备。而实际上素色和线框色全模型只有一种，原色也常常只有几种。
     共享后材质数量从"部件数×4"降到"不同颜色数×3 + 2"，大模型省 300MB 级内存。 */
  var matPool={}, matPoolN=0;
  function matOf(key, maker){
    var m=matPool[key];
    if(!m){
      if(matPoolN>600){ return maker(); }   // 颜色异常多时退回各自新建，防止池子无限长
      m=maker(); matPool[key]=m; matPoolN++;
    }
    return m;
  }
  function fmtMm(v){
    return Math.abs(v)>=100? Math.round(v) : Math.round(v*10)/10;
  }
  function build(buf){
    clearModel();
    var dv=new DataView(buf);
    var magic=String.fromCharCode(dv.getUint8(0),dv.getUint8(1),dv.getUint8(2),dv.getUint8(3));
    if(magic==='W3DM'){ return buildWire(buf,dv); }
    if(magic!=='S3DM'){
      throw new Error('网格格式不对');
    }
    var n=dv.getUint32(4,true);
    var meta=JSON.parse(new TextDecoder('utf-8').decode(new Uint8Array(buf,8,n)));
    /* JSON 头长度不是 4 的倍数时，直接 new Float32Array(buf, offset) 会抛
       "start offset should be a multiple of 4"。每个块先 slice 出独立 buffer，
       拷贝量就是网格本身（几 MB，几毫秒），换来不用在服务端做对齐填充。 */
    var base=8+n, total=0, tris=0, colored=0;
    function f32(off, bytes){ return new Float32Array(buf.slice(base+off, base+off+bytes)); }
    function u32(off, bytes){ return new Uint32Array(buf.slice(base+off, base+off+bytes)); }
    function u16(off, bytes){ return new Uint16Array(buf.slice(base+off, base+off+bytes)); }
    function i8a(off, bytes){ return new Int8Array(buf.slice(base+off, base+off+bytes)); }
    /* 量化产物：坐标 int16（相对全局原点与比例）、法线 int8、索引 u16。
       服务端 meshpack.py 压完 gzip 从 35.7MB 降到 15.8MB（实测 X6M 台架）。
       老产物没有 posType/nrmType/idxType 字段，走 float32/u32 分支。 */
    var QORG=meta.org||[0,0,0], QSC=meta.scale||1;
    function dequantPos(off, bytes){
      var q=new Int16Array(buf.slice(base+off, base+off+bytes));
      var out=new Float32Array(q.length);
      for(var k=0;k<q.length;k+=3){
        out[k]  =(q[k]  +32768)/QSC+QORG[0];
        out[k+1]=(q[k+1]+32768)/QSC+QORG[1];
        out[k+2]=(q[k+2]+32768)/QSC+QORG[2];
      }
      return out;
    }
    function dequantNrm(off, bytes){
      var q=i8a(off, bytes), out=new Float32Array(q.length);
      for(var k=0;k<q.length;k+=3){
        var x=q[k]/127, y=q[k+1]/127, z=q[k+2]/127;
        var len=Math.sqrt(x*x+y*y+z*z)||1;      // 量化后不再严格是单位向量
        out[k]=x/len; out[k+1]=y/len; out[k+2]=z/len;
      }
      return out;
    }

    for(var i=0;i<meta.meshes.length;i++){
      var mi=meta.meshes[i];
      var pos=(mi.posType==='i16') ? dequantPos(mi.posOffset, mi.posBytes)
                                   : f32(mi.posOffset, mi.posBytes);
      var g=new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos,3));
      if(mi.idxBytes>0){
        var idx=(mi.idxType==='u16') ? u16(mi.idxOffset, mi.idxBytes)
                                     : u32(mi.idxOffset, mi.idxBytes);
        g.setIndex(new THREE.BufferAttribute(idx,1));
        tris+=idx.length/3;
      }
      if(mi.nrmBytes>0){
        var nr=(mi.nrmType==='i8') ? dequantNrm(mi.nrmOffset, mi.nrmBytes)
                                   : f32(mi.nrmOffset, mi.nrmBytes);
        g.setAttribute('normal', new THREE.BufferAttribute(nr,3));
      } else {
        g.computeVertexNormals();
      }
      /* 颜色：文件里带了 STYLED_ITEM 就用它；没带的大片灰件按零件序号取一个
         稳定的柔和色，否则整机一片灰、分不清哪块是哪块。 */
      /* 三种材质：文件原色 / 按零件自动分色 / 统一素色。
         很多 STEP 里所有零件本身就配成浅灰（0.72,0.72,0.77），只按原色看是一整坨，
         所以额外给一个"分色"档，按零件序号散列色相，便于分辨结构。 */
      /* 材质不在这里建。
         原来每个部件直接 new 4 个（原色/分色/素色/线框），9419 个部件就是
         37676 个 MeshStandardMaterial —— 实测堆内存 341MB，而其中三种模式
         用户可能一次都不切。现在只记下"这个部件该是什么颜色"，
         真正用到时再由 matFor() 按颜色从池子里取（同色共享）。 */
      var cFile = mi.color ? new THREE.Color(mi.color[0],mi.color[1],mi.color[2])
                           : new THREE.Color(0xbcc6d2);
      var obj=new THREE.Mesh(g, matFor({cFile:cFile, hasColor:!!mi.color,
                                        name:mi.name}, colorMode));
      group.add(obj);
      meshes.push({obj:obj, name:mi.name || ('part'+i),
                   cFile:cFile, hasColor:!!mi.color});
      if(mi.color) colored++;
      total+=mi.vertices;
    }
    /* 不少 SolidWorks/CATIA 导出的 STEP 根本没带颜色（实测 STYLED_ITEM=0），
       全模型一片浅灰、看不清哪块是哪块。这种情况自动切到"分色"，
       至少能分辨零件；用户点"原色"仍可退回灰白（那才是文件真实颜色）。 */
    if(!colored && colorMode===0){
      colorMode=1; applyMats();
      var _cb=document.getElementById('colBtn'); if(_cb) _cb.textContent='分色';
    }
    info.textContent='面片 '+tris.toLocaleString()+' · 顶点 '+total.toLocaleString()+
      ' · 部件 '+meta.meshes.length + bboxText();
  }
  /* 外廓尺寸（mm）：装配体最常被问的就是这个，直接读包围盒 */
  function bboxText(){
    var bb=new THREE.Box3().setFromObject(group);
    if(bb.isEmpty()) return '';
    var s=bb.getSize(new THREE.Vector3());
    return ' · 外廓 '+fmtMm(s.x)+'×'+fmtMm(s.y)+'×'+fmtMm(s.z)+' mm';
  }
  /* ---- 线框自动升级为实体 ----
     大装配体三角化要几分钟，页面先用线框顶上。这里每 4 秒问一次 /stepstate，
     网格好了就原地换成实体、保留当前视角，用户不用手动刷新。 */
  var meshPoll=0, upgrading=false;
  function watchMesh(){
    if(meshPoll) return;
    meshPoll=setInterval(function(){
      if(upgrading) return;
      fetch('/stepstate/'+FK+'?kkt='+encodeURIComponent(KKT), {cache:'no-store'})
        .then(function(r){ return r.json(); })
        .then(function(j){
          // 拆件拼装时显示"第几个零件"的进度条
          if(j.prog){
            var _q=String(j.prog).split('/');
            var _dn=parseInt(_q[0],10), _tt=parseInt(_q[1],10);
            if(_tt>0){
              var _pr=document.getElementById('prog');
              if(_pr && !_pr.classList.contains('on')) _pr.classList.add('on');
              var _fi=document.getElementById('progFill');
              var _tx=document.getElementById('progTxt');
              if(_fi) _fi.style.width=Math.min(100, _dn*100/_tt).toFixed(0)+'%%';
              /* 100%% 只代表"零件解析完"，后面还要合并、量化、gzip 写盘。
                 不说明的话用户看到 100%% 却迟迟不出图，会以为卡死了。 */
              if(_tx) _tx.textContent = (_dn>=_tt)
                ? '零件解析完成，正在生成网格文件…'
                : ('实体网格解析 '+_dn+' / '+_tt+' 个零件');
            }
          }
          if(j.nomesh){
            /* 转换跑完了但没有实体面：把"解析中"换成明确结论并停止轮询 */
            if(info){
              info.textContent = info.textContent.replace('实体网格解析中，好了会自动切过来',
                                                          '该文件没有实体面，仅显示线框');
            }
            var _pr3=document.getElementById('prog'); if(_pr3) _pr3.classList.remove('on');
            clearInterval(meshPoll); meshPoll=0;
            return;
          }
          if(j.state==='ready' && j.mode==='mesh'){
            clearInterval(meshPoll); meshPoll=0; upgradeToMesh();
          }
        }).catch(function(){});
    }, 4000);
  }
  /* 顶部"下载 / 网速"提示的操作口。用函数声明（会提升），
     这样上面 upgradeToMesh 里可以直接调用，不用管变量定义顺序。 */
  function showDl(html){
    var el=document.getElementById('dlTip');
    if(!el) return;
    if(html!=null) el.innerHTML=html;
    el.classList.add('on');
  }
  function hideDl(){
    var el=document.getElementById('dlTip');
    if(el) el.classList.remove('on');
  }
  var upgradeTry=0;
  window.__retryUpgrade=function(){
    upgradeTry=0;
    hideDl();
    upgradeToMesh();
  };
  function upgradeToMesh(){
    if(upgrading) return; upgrading=true;
    var tgt=(ctl?ctl.target:cluC).clone();
    var keep={pos:camera.position.clone(), up:camera.up.clone(), tgt:tgt, dist:currentDist()};
    /* 不盖全屏蒙层：那块半透明白布（#ld）在 build 大模型时要挂好几秒，
       用户看到的就是"中间一坨白色遮挡"，而且 build 一旦抛错它还留着。
       改成右下角的轻提示，失败也不会遮住画面。 */
    showDl('实体网格已就绪，正在切换…');
    var _t0=performance.now();
    fetchBuf(DATA, function(got, total, inst){
      showDl('正在下载实体网格 '+(got/1048576).toFixed(0)+'/'+
        (total/1048576).toFixed(0)+' MB · '+inst.toFixed(1)+' MB/s');
    })
      .then(function(buf){
        var _t1=performance.now();
        // 服务端可能还没换上新网格，拿到的仍是线框数据：直接放弃，等下一轮
        var magic=String.fromCharCode.apply(null, new Uint8Array(buf, 0, 4));
        if(magic==='W3DM'){
          // 还是线框：放开锁、重新开始轮询，等下一轮再试（不能就这一下放弃）
          upgrading=false; hideDl(); watchMesh(); return;
        }
        showDl('正在构建实体网格…');
        build(buf);
        console.log('UPGRADE 下载 ' + Math.round(_t1-_t0) + 'ms，构建 ' +
                    Math.round(performance.now()-_t1) + 'ms，' +
                    ((buf.byteLength/1048576).toFixed(1)) + 'MB');
        model=group;
        if(ctl) ctl.target.copy(keep.tgt);
        camera.position.copy(keep.pos);
        camera.up.copy(keep.up);
        camera.lookAt(keep.tgt);
        applyFrustum(keep.dist);
        var md=document.getElementById('modes'); if(md) md.style.display='';
        var _pr2=document.getElementById('prog'); if(_pr2) _pr2.classList.remove('on');
        upgradeTry=0;
        buildTree(); drawGizmo(); renderer.render(scene,camera);
      })
      .catch(function(e){
        /* 这里原来只打一条 console：网络抖一下（几十 MB 的网格很容易断）或者
           build 抛错，页面就永远停在"线框 + 进度 100%%"，用户完全不知道发生了什么。
           现在自动重试 3 次，仍失败就给出可点的重试入口。 */
        console.error('UPGRADE_FAIL', e);
        upgrading=false;
        upgradeTry++;
        if(upgradeTry<=3){
          showDl('实体网格加载中断，正在重试 ('+upgradeTry+'/3)…');
          setTimeout(function(){ upgradeToMesh(); }, 2000*upgradeTry);
        }else{
          showDl('实体网格加载失败。'+
            '<a href="javascript:__retryUpgrade()" style="color:#fff;text-decoration:underline">点这里重试</a>');
        }
      })
      .then(function(){
        upgrading=false;
        if(upgradeTry===0) hideDl();
      });
  }

  /* ---- 右下角坐标轴 ---- */
  var gz=document.getElementById('gz'), gctx=gz?gz.getContext('2d'):null;
  function drawGizmo(){
    if(!gctx) return;
    var w=gz.width, h=gz.height, cx=w/2, cy=h/2, r=23, i;
    gctx.clearRect(0,0,w,h);
    gctx.beginPath(); gctx.arc(cx,cy,r+9,0,Math.PI*2);
    gctx.fillStyle='rgba(255,255,255,.80)'; gctx.fill();
    camera.updateMatrix();
    var m=new THREE.Matrix4().extractRotation(camera.matrixWorldInverse);
    var ax=[[1,0,0,'#d9534f','X'],[0,1,0,'#3fa34d','Y'],[0,0,1,'#2f6feb','Z']];
    for(i=0;i<ax.length;i++){
      var a=ax[i], v=new THREE.Vector3(a[0],a[1],a[2]).applyMatrix4(m);
      var x=cx+v.x*r, y=cy-v.y*r;
      gctx.strokeStyle=a[3]; gctx.lineWidth=1.8; gctx.lineCap='round';
      gctx.beginPath(); gctx.moveTo(cx,cy); gctx.lineTo(x,y); gctx.stroke();
      gctx.fillStyle=a[3]; gctx.font='bold 9px sans-serif';
      gctx.fillText(a[4], cx+v.x*(r+7)-3, cy-v.y*(r+7)+3);
    }
  }

  /* ---- 部件树：显示/隐藏 ---- */
  var treeEl=document.getElementById('tree'), tlistEl=document.getElementById('tlist'),
      tfindEl=document.getElementById('tfind'), tcntEl=document.getElementById('tcnt');
  var TREE_MAX=400;   // 6708 个部件全渲染会卡死，只列前 400 组，其余用搜索
  var partGroups=[];  // [{name, objs:[]}] —— 同名的多个 mesh 合成一个"零部件"
  function rebuildGroups(){
    partGroups=[];
    var idx={};
    for(var i=0;i<meshes.length;i++){
      var nm=meshes[i].name || ('part'+i);
      if(!idx[nm]){ idx[nm]={name:nm, objs:[]}; partGroups.push(idx[nm]); }
      idx[nm].objs.push(meshes[i].obj);
    }
  }
  function groupVisible(g){
    for(var i=0;i<g.objs.length;i++) if(g.objs[i].visible){ return true; }
    return false;
  }
  function setGroupVisible(g, v){
    for(var i=0;i<g.objs.length;i++) g.objs[i].visible=v;
  }
  function escHtml(s){
    return String(s).replace(/[&<>"]/g, function(c){
      return c==='&' ? '&amp;' : (c==='<' ? '&lt;' : (c==='>' ? '&gt;' : '&quot;'));
    });
  }
  function buildTree(){
    if(!tlistEl || !meshes.length) return;
    if(!partGroups.length) rebuildGroups();
    var q=(tfindEl && tfindEl.value || '').trim().toLowerCase();
    var html=[], shown=0;
    for(var i=0;i<partGroups.length && shown<TREE_MAX;i++){
      var g=partGroups[i], nm=g.name;
      if(q && nm.toLowerCase().indexOf(q)<0) continue;
      shown++;
      var extra=g.objs.length>1 ? ' <span style="color:#a3a9b3">×'+g.objs.length+'</span>' : '';
      html.push('<label title="'+escHtml(nm)+'"><input type="checkbox" data-i="'+i+'"'
        +(groupVisible(g)?' checked':'')+'><span class="nm">'+escHtml(nm)+extra+'</span></label>');
    }
    tlistEl.innerHTML=html.join('');
    if(tcntEl) tcntEl.textContent=(q? shown+'/'+partGroups.length : partGroups.length);
  }
  function setAllVisible(v){
    for(var i=0;i<meshes.length;i++) meshes[i].obj.visible=v;
    buildTree(); renderer.render(scene,camera);
  }
  if(tlistEl){
    tlistEl.addEventListener('change', function(e){
      var t=e.target;
      if(!t || t.tagName!=='INPUT') return;
      var i=parseInt(t.getAttribute('data-i'),10);
      if(partGroups[i]) setGroupVisible(partGroups[i], !!t.checked);
      renderer.render(scene,camera);
    });
    if(tfindEl) tfindEl.addEventListener('input', buildTree);
    document.getElementById('tall').onclick=function(){ setAllVisible(true); };
    document.getElementById('tnone').onclick=function(){ setAllVisible(false); };
    document.getElementById('treeBtn').onclick=function(){
      treeEl.classList.toggle('on');
      if(treeEl.classList.contains('on')) buildTree();
    };
  }

  /* ---- 测量：点两个点出距离，同时当标注用 ---- */
  var measOn=false, measA=null, marks=[], ray=new THREE.Raycaster();
  var mtip=document.getElementById('mtip');
  function clearMarks(){
    while(marks.length){
      var o=marks.pop();
      group.remove(o);
      o.traverse(function(x){
        if(x.geometry) x.geometry.dispose();
        if(x.material){
          if(x.material.map) x.material.map.dispose();
          x.material.dispose();
        }
      });
    }
    measA=null; areaFaces=0; areaSum=0;
  }
  /* 测量数值标签。sizeAttenuation:false → 尺寸按屏幕比例定，不随模型大小和缩放变化。
     原来按 cluR 比例缩放：500mm 的大装配体上标签只有几个像素，"10 mm" 根本看不清；
     改成屏幕尺寸后，缩放到任何倍数都一样清楚。 */
  function labelSprite(text, pos){
    var c=document.createElement('canvas'); c.width=384; c.height=96;
    var x=c.getContext('2d');
    x.fillStyle='rgba(47,111,235,.95)'; x.fillRect(0,0,384,96);
    x.fillStyle='#fff'; x.font='bold 52px sans-serif';
    x.textAlign='center'; x.textBaseline='middle'; x.fillText(text,192,52);
    var tex=new THREE.CanvasTexture(c);
    var sp=new THREE.Sprite(new THREE.SpriteMaterial({map:tex, depthTest:false, transparent:true,
      sizeAttenuation:false}));
    sp.scale.set(0.26, 0.065, 1);   // NDC 单位：约占屏幕宽 13%%、高 3.2%%
    sp.position.copy(localOf(pos)); sp.renderOrder=999;
    return sp;
  }
  function dotAt(p, color){
    var d=new THREE.Mesh(new THREE.SphereGeometry(cluR*0.007,10,10),
      new THREE.MeshBasicMaterial({color:color, depthTest:false}));
    d.position.copy(localOf(p)); d.renderOrder=998;
    group.add(d); marks.push(d);
    return d;
  }
  function pickPoint(ev){
    var r=cv.getBoundingClientRect();
    if(!r.width || !r.height) return null;
    var ndc=new THREE.Vector2(((ev.clientX-r.left)/r.width)*2-1,
                              -((ev.clientY-r.top)/r.height)*2+1);
    ray.params.Line={threshold: Math.max(cluR*0.003, 1e-6)};
    ray.setFromCamera(ndc, camera);
    var hits=ray.intersectObjects(group.children, true);
    for(var i=0;i<hits.length;i++){
      var o=hits[i].object;
      // 隐藏的部件不参与拾取（否则点在空白处也能测到看不见的东西）
      var vis=true, p=o;
      while(p){ if(p.visible===false){ vis=false; break; } p=p.parent; }
      if(vis) return hits[i].point.clone();
    }
    return null;
  }
  /* 面积：戳到哪个三角面就把它的面积累加。法向由两条边叉乘得到，单位是模型单位（mm²）。 */
  var areaFaces=0, areaSum=0;
  function pickFace(ev){
    var r=cv.getBoundingClientRect();
    if(!r.width || !r.height) return null;
    var ndc=new THREE.Vector2(((ev.clientX-r.left)/r.width)*2-1,
                              -((ev.clientY-r.top)/r.height)*2+1);
    ray.setFromCamera(ndc, camera);
    var hits=ray.intersectObjects(group.children, true);
    for(var i=0;i<hits.length;i++){
      var h=hits[i], o=h.object;
      if(!o.isMesh || !h.face || !o.geometry || !o.geometry.attributes.position) continue;
      var pos=o.geometry.attributes.position, f=h.face;
      // face.a/b/c 已经是顶点下标（three 在求交时查过 index），不能再拿它去 index 里查一次
      function vert(k){
        return new THREE.Vector3().fromBufferAttribute(pos, k).applyMatrix4(o.matrixWorld);
      }
      var A=vert(f.a), B=vert(f.b), C=vert(f.c);
      var ar=B.clone().sub(A).cross(C.clone().sub(A)).length()/2;
      return {area:ar, point:h.point.clone()};
    }
    return null;
  }
  function onPick(ev){
    // Alt+左键：选面累计面积（Shift 被 OrbitControls 拿去平移了，所以用 Alt）
    if(ev.altKey){
      var f=pickFace(ev);
      if(!f) return;
      areaFaces++; areaSum+=f.area;
      dotAt(f.point, 0x00a86b);
      if(mtip) mtip.textContent='已选 '+areaFaces+' 个面 · 总面积 '+fmtMm(areaSum)+' mm²（Esc 清除）';
      renderer.render(scene,camera);
      return;
    }
    var p=pickPoint(ev);
    if(!p) return;
    if(!measA){
      measA=p; dotAt(p, 0xff8a00);
      if(mtip) mtip.textContent='已选起点，再点一个点量距离（Esc 退出）';
    } else {
      var d=measA.distanceTo(p);
      dotAt(p, 0xff8a00);
      var geo=new THREE.BufferGeometry().setFromPoints([localOf(measA), localOf(p)]);
      var line=new THREE.Line(geo, new THREE.LineBasicMaterial({color:0x2f6feb, depthTest:false}));
      line.renderOrder=997; group.add(line); marks.push(line);
      var mid=measA.clone().add(p).multiplyScalar(0.5);
      var lab=labelSprite(fmtMm(d)+' mm', mid); group.add(lab); marks.push(lab);
      measA=null;
      if(mtip) mtip.textContent='再点两个点继续测（Esc 退出并清除）';
    }
    renderer.render(scene,camera);
  }
  var downPt=null;
  cv.addEventListener('pointerdown', function(e){
    if(e.button===0) downPt={x:e.clientX, y:e.clientY};
  });
  window.addEventListener('pointerup', function(e){
    if((measOn || planeOn) && downPt && e.button===0){
      var dx=e.clientX-downPt.x, dy=e.clientY-downPt.y;
      // 位移小于 5px 才算点击，拖拽是旋转
      if(dx*dx+dy*dy<25){ if(planeOn) onPlanePick(e); else onPick(e); }
    }
    downPt=null;
  });

  /* ================= 面距：测两个平面之间的距离 =================
     点第一个面 → 记录它的平面（面上一 + 法向），整片共面三角形高亮；
     点第二个面 → 若两面平行，距离＝沿法向的投影差；不平行则同时给夹角，
     并按"第二个面到第一个面的垂距"出数，另外画一条垂线标出量的是哪段。 */
  var planeOn=false, planeP1=null, planeN1=null;
  function pickFaceFull(ev){
    var r=cv.getBoundingClientRect();
    if(!r.width || !r.height) return null;
    var ndc=new THREE.Vector2(((ev.clientX-r.left)/r.width)*2-1,
                              -((ev.clientY-r.top)/r.height)*2+1);
    ray.setFromCamera(ndc, camera);
    var hits=ray.intersectObjects(group.children, true);
    for(var i=0;i<hits.length;i++){
      var h=hits[i], o=h.object;
      if(!o.isMesh || !h.face || !o.geometry || !o.geometry.attributes.position) continue;
      var vis=true, q=o;
      while(q){ if(q.visible===false){ vis=false; break; } q=q.parent; }
      if(!vis) continue;                       // 隐藏的部件不参与拾取
      var pos=o.geometry.attributes.position, f=h.face;
      var A=new THREE.Vector3().fromBufferAttribute(pos,f.a).applyMatrix4(o.matrixWorld);
      var B=new THREE.Vector3().fromBufferAttribute(pos,f.b).applyMatrix4(o.matrixWorld);
      var C=new THREE.Vector3().fromBufferAttribute(pos,f.c).applyMatrix4(o.matrixWorld);
      var n=new THREE.Vector3().subVectors(B,A).cross(new THREE.Vector3().subVectors(C,A));
      if(n.lengthSq()<1e-18) continue;
      n.normalize();
      /* 法向朝观察者一侧：只影响箭头画出来的朝向，距离本身用绝对值 */
      if(n.dot(new THREE.Vector3().subVectors(camera.position, h.point))<0) n.negate();
      return {obj:o, point:h.point.clone(), normal:n, faceIndex:(h.faceIndex!=null?h.faceIndex:-1),
              tri:[A,B,C]};
    }
    return null;
  }
  /* 把同一零件里与命中三角共面的三角形整片染成半透明色 —— 只点一个小三角看不出选了什么，
     整块板亮起来才像"选中了一个平面"。 */
  function highlightPlane(obj, faceIndex, color){
    if(!obj || !obj.geometry || faceIndex<0) return;
    var geo=obj.geometry, pos=geo.attributes.position, idx=geo.index;
    var cnt=idx? Math.floor(idx.count/3) : Math.floor(pos.count/3);
    if(cnt<=0) return;
    var A=new THREE.Vector3(), B=new THREE.Vector3(), C=new THREE.Vector3(),
        n=new THREE.Vector3(), t1=new THREE.Vector3(), ctr=new THREE.Vector3();
    function vi(k, out){
      var gi = idx? idx.getX(k) : k;
      out.fromBufferAttribute(pos, gi);
    }
    vi(faceIndex*3, A); vi(faceIndex*3+1, B); vi(faceIndex*3+2, C);
    var n0=new THREE.Vector3().subVectors(B,A).cross(new THREE.Vector3().subVectors(C,A));
    if(n0.lengthSq()<1e-18) return;
    n0.normalize();
    var p0=A.clone();
    var cosTol=Math.cos(2*Math.PI/180);      // 2° 以内算共面
    var tol=Math.max(cluR*0.0015, 1e-4);     // 平面偏移容差
    var out=[], lim=Math.min(cnt, 400000);
    for(var t=0;t<lim;t++){
      var k=t*3;
      vi(k, A); vi(k+1, B); vi(k+2, C);
      n.subVectors(B,A).cross(t1.subVectors(C,A));
      if(n.lengthSq()<1e-18) continue;
      n.normalize();
      if(Math.abs(n.dot(n0))<cosTol) continue;
      ctr.copy(A).add(B).add(C).multiplyScalar(1/3);
      if(Math.abs(t1.subVectors(ctr,p0).dot(n0))>tol) continue;
      out.push(A.x,A.y,A.z, B.x,B.y,B.z, C.x,C.y,C.z);
    }
    if(!out.length) return;
    var g=new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(out,3));
    /* 顶点是 obj 的局部坐标；mesh 要挂到 group 上，所以先换成 group 的局部坐标
       （group 自身可能因为"重定坐标系"被旋转过）。 */
    var toG=new THREE.Matrix4().copy(group.matrixWorld).invert().multiply(obj.matrixWorld);
    g.applyMatrix4(toG);
    var m=new THREE.Mesh(g, new THREE.MeshBasicMaterial({color:color, transparent:true,
      opacity:0.45, side:THREE.DoubleSide, depthWrite:false}));
    m.renderOrder=994;
    group.add(m); marks.push(m);
  }
  function seg3(p, q, color){
    var geo=new THREE.BufferGeometry().setFromPoints([localOf(p), localOf(q)]);
    var line=new THREE.Line(geo, new THREE.LineBasicMaterial({color:color, depthTest:false}));
    line.renderOrder=997; group.add(line); marks.push(line);
  }
  function onPlanePick(ev){
    var f=pickFaceFull(ev);
    if(!f){                                   /* 点空了：说清楚，别让用户以为点了没反应 */
      if(mtip) mtip.textContent = planeP1
        ? '没点到面 —— 请点在模型表面上（Esc 退出）'
        : '请在模型表面上点一个平面（Esc 退出）';
      return;
    }
    /* 先记住"这是第几次点"，再决定要不要清标记 ——
       如果先清 planeP1 再判断，第二次点击会被当成第一次，永远出不了结果。 */
    var second=!!planeP1;
    /* 新一轮测量时才清画面：第二次点击要保留第一个面的高亮，两个面同时亮着才看得出
       量的是哪两个面；同时旧一轮的结果（垂线、标注）也不会越积越多。 */
    if(!second) clearMarks();
    highlightPlane(f.obj, f.faceIndex, second?0x00a86b:0xffb300);
    if(!second){
      planeP1=f.point.clone(); planeN1=f.normal.clone();
      if(mtip) mtip.textContent='已选第一个平面（黄），再点第二个平面…（Esc 退出）';
      renderer.render(scene,camera);
      return;
    }
    var p1=planeP1, n1=planeN1, p2=f.point, n2=f.normal;
    var v12=new THREE.Vector3().subVectors(p2,p1);
    var signed=v12.dot(n1);                  /* p2 到第一个平面的有符号垂距 */
    var dist=Math.abs(signed);
    var ang=Math.acos(Math.min(1, Math.abs(n1.dot(n2))))*180/Math.PI;
    var foot=p2.clone().addScaledVector(n1, -signed);
    seg3(p2, foot, 0x2f6feb);
    var mid=p2.clone().add(foot).multiplyScalar(0.5);
    var lab=labelSprite(fmtMm(dist)+' mm', mid);
    group.add(lab); marks.push(lab);
    var msg = dist<1e-6 ? '两面共面（距离 0 mm）' : ('平面距离 '+fmtMm(dist)+' mm');
    if(ang>1) msg+=' · 两面夹角 '+ang.toFixed(1)+'°（取第二面到第一面的垂距）';
    if(mtip) mtip.textContent=msg+'（Esc 清除）';
    showDl(msg);
    clearTimeout(planeTipTimer);
    planeTipTimer=setTimeout(hideDl, 7000);
    planeP1=null; planeN1=null;
    renderer.render(scene,camera);
  }
  var planeTipTimer=null;
  function setPlaneMode(on){
    planeOn=!!on;
    var pb=document.getElementById('planeBtn');
    if(pb) pb.classList.toggle('on', planeOn);
    if(planeOn && measOn){
      measOn=false;
      var mb=document.getElementById('measBtn'); if(mb) mb.classList.remove('on');
    }
    clearMarks(); planeP1=null; planeN1=null;
    if(mtip){
      mtip.classList.toggle('on', planeOn);
      if(planeOn) mtip.textContent='点击第一个平面（面片）…（Esc 退出）';
    }
    cv.style.cursor=planeOn?'crosshair':'';
    renderer.render(scene,camera);
  }
  if(mtip){
    document.getElementById('measBtn').onclick=function(){
      measOn=!measOn;
      if(measOn && planeOn) setPlaneMode(false);
      this.classList.toggle('on', measOn);
      mtip.classList.toggle('on', measOn);
      mtip.textContent='点两个点量距离 · 按住 Alt 点面量面积 · Esc 退出';
      measA=null;
      cv.style.cursor=measOn?'crosshair':'';
    };
    var planeBtnEl=document.getElementById('planeBtn');
    if(planeBtnEl) planeBtnEl.onclick=function(){ setPlaneMode(!planeOn); };
  }
  /* 网速自测：拉 8MB 不可压缩随机数据，算真实带宽 */
  var netBtn=document.getElementById('netBtn');
  if(netBtn){
    netBtn.onclick=function(){
      var self=this;
      if(self.disabled) return;
      self.disabled=true; self.textContent='⇅ …';
      var t0=performance.now();
      fetch('/api/v1/netprobe?size='+(8*1024*1024), {cache:'no-store'})
        .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.arrayBuffer(); })
        .then(function(b){
          var sec=(performance.now()-t0)/1000, mb=b.byteLength/1048576;
          var sp=mb/Math.max(sec,0.01);
          showDl('测速：下载 '+mb.toFixed(0)+' MB / '+sec.toFixed(2)+' 秒 = '+sp.toFixed(1)+' MB/s');
          setTimeout(hideDl, 8000);
          self.textContent='⇅ '+sp.toFixed(1)+'MB/s'; self.disabled=false;
          if(info) info.title = (info.textContent||'') + ' · 网速 '+sp.toFixed(1)+'MB/s';
        })
        .catch(function(e){
          self.textContent='⇅ 失败'; self.disabled=false;
          showDl('测速失败：'+e.message);
        });
    };
  }
  document.getElementById('reset').onclick=fit;

  /* ---- 剖切：three.js 全局 clippingPlanes ----
     用全局裁剪而不是给每个材质挂 clippingPlanes：1387 个部件 × 4 套材质要挂 5000 多次，
     而且切换颜色档时还得重挂。全局的只认一个数组，改完直接 render。 */
  var clipPlane=null, clipAxis='y';
  var clipPanel=document.getElementById('clipPanel'),
      clipRange=document.getElementById('clipRange');
  function modelBox(){
    var bb=new THREE.Box3().setFromObject(group);
    return bb.isEmpty()? null : bb;
  }
  function applyClip(){
    if(!clipPlane){ renderer.clippingPlanes=[]; renderer.render(scene,camera); return; }
    var bb=modelBox(); if(!bb) return;
    var t=parseInt(clipRange.value,10)/100;
    var lo=bb.min[clipAxis], hi=bb.max[clipAxis];
    var cut=lo+(hi-lo)*t;
    var n=new THREE.Vector3(0,0,0); n[clipAxis]=-1;   // 保留法向负侧
    clipPlane.set(n, cut);
    renderer.clippingPlanes=[clipPlane];
    renderer.render(scene,camera);
  }
  function closeClip(){
    if(clipPanel){ clipPanel.classList.remove('on'); }
    var b=document.getElementById('clipBtn'); if(b) b.classList.remove('on');
    clipPlane=null; applyClip();
  }
  if(clipPanel && clipRange){
    document.getElementById('clipBtn').onclick=function(){
      var on=!clipPanel.classList.contains('on');
      /* 三个面板共用右上角，开一个关其他 */
      if(on){ var vp=document.getElementById('viewPanel'); if(vp) vp.classList.remove('on');
              var vb=document.getElementById('viewBtn'); if(vb) vb.classList.remove('on');
              var ep2=document.getElementById('expPanel'); if(ep2) ep2.classList.remove('on'); }
      clipPanel.classList.toggle('on', on);
      this.classList.toggle('on', on);
      if(on){ if(!clipPlane) clipPlane=new THREE.Plane(new THREE.Vector3(0,-1,0), 0); applyClip(); }
      else closeClip();
    };
    clipRange.oninput=applyClip;
    var axs=clipPanel.querySelectorAll('button[data-ax]');
    for(var ai=0; ai<axs.length; ai++){
      axs[ai].onclick=function(){
        clipAxis=this.getAttribute('data-ax');
        for(var j=0;j<axs.length;j++) axs[j].classList.toggle('on', axs[j]===this);
        applyClip();
      };
    }
    document.getElementById('clipOff').onclick=closeClip;
  }

  /* ---- 爆炸视图：每个部件沿"中心→自身"方向外移 ----
     几何坐标是绝对的（obj.position 恒为 0），所以方向按各自包围盒中心算，
     位移量取到模型中心的距离 × 系数，0 就是原位。
     EXP_GAIN：滑块 100%% 时每个部件外移到"离中心 (1+GAIN) 倍"的位置。
     原来是 1 倍（满档只是把部件挪到两倍半径），装配体挤在一起看着不够开，
     用户要求再加一倍 → 2，满档就是 3 倍半径。 */
  var EXP_GAIN=2;
  var expDirs=[], expK=0;
  var expPanel=document.getElementById('expPanel'),
      expRange=document.getElementById('expRange'),
      expVal=document.getElementById('expVal');
  function prepExplode(force){
    if(expDirs.length && !force) return;
    expDirs=[];
    var bb=modelBox(); if(!bb) return;
    var ctr=bb.getCenter(new THREE.Vector3());
    for(var i=0;i<meshes.length;i++){
      var o=meshes[i].obj;
      if(!o.geometry || !o.geometry.attributes.position) continue;
      o.geometry.computeBoundingBox();
      var gb=o.geometry.boundingBox;
      if(!gb) continue;
      var c=gb.getCenter(new THREE.Vector3());
      var d=c.sub(ctr);
      var len=d.length();
      expDirs.push({obj:o, dir: len>1e-6 ? d.clone().multiplyScalar(1/len) : new THREE.Vector3(),
                    dist: len});
    }
  }
  function applyExplode(){
    prepExplode(false);
    for(var i=0;i<expDirs.length;i++){
      var e=expDirs[i];
      e.obj.position.copy(e.dir).multiplyScalar(e.dist * expK * EXP_GAIN);
    }
    if(expVal) expVal.textContent=Math.round(expK*100)+'%%';
    renderer.render(scene,camera);
  }
  /* 给自动化测试用：所有部件当前位移的最大值，爆炸复位后应为 0 */
  window.__stepDebug=function(){
    var c0=group.children[0];
    return { cam: camera.position.toArray(), near: camera.near, far: camera.far,
             up: camera.up.toArray(),   /* 横向旋转（roll）只改这个，排查时看它 */
             groupRot: [group.rotation.x, group.rotation.y, group.rotation.z]
                       .map(function(v){ return +v.toFixed(3); }),
             cluC: cluC.toArray(), cluR: cluR, children: group.children.length,
             wireFit: wireFit? {c: wireFit.c.toArray(), r: wireFit.r} : null,
             target: ctl? ctl.target.toArray() : null,
             child0: c0 ? { type:c0.type, visible:c0.visible,
                            count:(c0.geometry&&c0.geometry.attributes.position)?
                                  c0.geometry.attributes.position.count : -1,
                            mat:(c0.material&&c0.material.type)||'none',
                            drawRange:c0.geometry? c0.geometry.drawRange.count : -1 } : null,
             bg: scene.background? scene.background.getHexString() : 'none' };
  };
  window.__stepRender=function(){ renderer.render(scene,camera); return 'rendered'; };
  window.__stepMaxOffset=function(){
    var mx=0;
    for(var i=0;i<meshes.length;i++){
      var l=meshes[i].obj.position.length();
      if(l>mx) mx=l;
    }
    return mx;
  };
  if(expPanel && expRange){
    document.getElementById('expBtn').onclick=function(){
      var on=!expPanel.classList.contains('on');
      if(on){ var vp2=document.getElementById('viewPanel'); if(vp2) vp2.classList.remove('on');
              var vb2=document.getElementById('viewBtn'); if(vb2) vb2.classList.remove('on');
              var cp2=document.getElementById('clipPanel'); if(cp2) cp2.classList.remove('on'); }
      expPanel.classList.toggle('on', on);
      this.classList.toggle('on', on);
      if(on) prepExplode(true);
    };
    expRange.oninput=function(){ expK=parseInt(this.value,10)/100; applyExplode(); };
    document.getElementById('expOff').onclick=function(){
      expK=0; expRange.value=0; applyExplode();
    };
  }

  /* ================= 视图面板：标准视角 / 拖拽反向 / 重定坐标系 ================= */
  var STD_VIEWS={
    front:[0,-1,0], back:[0,1,0], left:[-1,0,0], right:[1,0,0],
    top:[0,0,1], bottom:[0,0,-1], iso:[0.50,-0.62,0.55]
  };
  function setStdView(name){
    var v=STD_VIEWS[name];
    if(!v || !model) return;
    var c=ensureCtl();
    var dir=new THREE.Vector3(v[0],v[1],v[2]).normalize();
    var d=Math.max(currentDist(), cluR*0.6);
    camera.position.copy(cluC).addScaledVector(dir, d);
    c.target.copy(cluC);
    /* 正对顶/底面时 up 不能再用 +Z：与视线平行，lookAt 会退化 */
    if(Math.abs(dir.dot(UP))>0.999) camera.up.set(0,1,0);
    else camera.up.copy(UP);
    camera.lookAt(cluC);
    applyFrustum(d);
    updateZoomBadge();
    renderer.render(scene,camera);
  }
  /* 以当前视角为基准重定坐标系：模型按"你现在看到的样子"摆正，
     之后 ⟲ ⟳ / ↑ ↓ / 标准视角 / 旋转圆心都以新基准为准。
     适合导入时本来就躺倒或歪着的模型 —— 不用一根一根量，看着顺眼就定下来。 */
  function rebaseView(){
    if(!model) return;
    var c=ensureCtl();
    clearMarks();                       /* 旧标记是按旧坐标算的，重定基准后必然错位 */
    var upV=camera.up.clone().normalize();
    var back=new THREE.Vector3().subVectors(camera.position, c.target).normalize(); // 中心→相机
    var nx=new THREE.Vector3().crossVectors(upV, back);
    if(nx.lengthSq()<1e-10) return;
    nx.normalize();
    var nz=new THREE.Vector3().crossVectors(back, nx).normalize();
    /* makeBasis 的列是新 X/Y/Z 在旧世界里的方向 → 它的逆就是"旧坐标转新坐标"。
       Y 轴必须取 -back：相机在"中心→相机"方向，标准前视要求相机站在 -Y 侧。
       取 +back 会让三个基向量构成左手系（行列式 -1），等于把模型镜像了。 */
    var m=new THREE.Matrix4().makeBasis(nx, back.clone().negate(), nz);
    group.applyMatrix4(m.clone().invert());
    group.updateMatrixWorld(true);
    markInvDirty=true;                  /* 标记坐标缓存失效 */
    computeCluster();
    var d=Math.max(currentDist(), cluR*0.6);
    camera.up.copy(UP);
    camera.position.copy(cluC).addScaledVector(new THREE.Vector3(0,-1,0), d);
    c.target.copy(cluC);
    camera.lookAt(cluC);
    applyFrustum(d);
    updateZoomBadge();
    renderer.render(scene,camera);
    showDl('已把当前视角设为基准（模型已摆正，旋转与标准视角都按新坐标）');
    clearTimeout(planeTipTimer);
    planeTipTimer=setTimeout(hideDl, 6000);
  }
  var viewPanel=document.getElementById('viewPanel'), viewBtn=document.getElementById('viewBtn');
  if(viewBtn && viewPanel){
    function closeViewPanel(){
      viewPanel.classList.remove('on');
      if(viewBtn) viewBtn.classList.remove('on');
    }
    viewBtn.onclick=function(){
      var on=!viewPanel.classList.contains('on');
      /* 三个面板共用右上角位置，开一个就关掉别的 */
      var cp=document.getElementById('clipPanel'), ep=document.getElementById('expPanel');
      if(on){ if(cp) cp.classList.remove('on'); if(ep) ep.classList.remove('on'); }
      viewPanel.classList.toggle('on', on);
      viewBtn.classList.toggle('on', on);
    };
    var vc=document.getElementById('viewClose'); if(vc) vc.onclick=closeViewPanel;
    Array.prototype.forEach.call(viewPanel.querySelectorAll('button[data-v]'), function(b){
      b.onclick=function(){ setStdView(b.getAttribute('data-v')); };
    });
    var inv=document.getElementById('invDrag');
    if(inv){
      inv.checked=!!invertDrag;
      inv.onchange=function(){
        invertDrag=!!inv.checked;
        try{ localStorage.setItem('kk_inv_drag', invertDrag?'1':'0'); }catch(e){}
      };
    }
    var rb=document.getElementById('rebaseBtn');
    if(rb) rb.onclick=function(){ rebaseView(); closeViewPanel(); };
  }
  document.getElementById('spinL').onclick=function(){ spin(15); };
  document.getElementById('spinR').onclick=function(){ spin(-15); };
  document.getElementById('tiltU').onclick=function(){ tilt(15); };
  document.getElementById('tiltD').onclick=function(){ tilt(-15); };
  document.getElementById('rollL').onclick=function(){ rollView(-15); };   /* 画面逆时针倾斜 */
  document.getElementById('rollR').onclick=function(){ rollView(15); };
  document.getElementById('zin').onclick=function(){ zoomBy(1/1.5); };
  document.getElementById('zout').onclick=function(){ zoomBy(1.5); };
  /* 方向键平移：OrbitControls 自带 keyPanSpeed，但默认不监听（listenToKeyEvents 要显式开） */
  var hint=document.getElementById('hint'), hTimer=null;
  function showHint(auto){
    hint.classList.remove('hide');
    clearTimeout(hTimer);
    /* 自动淡出，但点 ? 打开的不自动收（用户在等它看）。
       30 秒：原来 12 秒且从脚本执行就开始计时，那时还在加载、提示被遮罩盖着，
       等图出来往往已经淡出，用户根本没见过这条提示。 */
    if(auto) hTimer=setTimeout(function(){ hint.classList.add('hide'); }, 30000);
  }
  document.getElementById('help').onclick=function(){
    if(hint.classList.contains('hide')) showHint(false);
    else { hint.classList.add('hide'); clearTimeout(hTimer); }
  };
  hint.onclick=function(){ hint.classList.add('hide'); clearTimeout(hTimer); };
  /* 拖拽时把光标换成抓手，明确可以拖 */
  cv.addEventListener('pointerdown', function(){ cv.classList.add('drag'); });
  window.addEventListener('pointerup', function(){ cv.classList.remove('drag'); });
  /* 操作提示不在这里显示：此刻还在加载，#ld 遮罩把提示压住看不见。
     改到模型渲染完成时再弹（见下面 buildTree(); drawGizmo(); 之后）。 */

  /* 双击画面 = 复位；键盘 + - 0 也能用（大屏看图时手不用来回找按钮） */
  cv.addEventListener('dblclick', function(e){ e.preventDefault(); fit(); });
  /* ---- 键盘快捷键为什么"要先点一下页面才生效" ----
     预览页多半是嵌在业务系统 iframe 里的。浏览器按"焦点所在文档"派发键盘事件：
     iframe 的文档还没拿到焦点时，keydown 根本不会送到这个窗口，
     点一下页面（按钮/画布）焦点才过来，于是表现为"先点按钮，键盘才管用"。
     两件事一起做：① 载入完成就主动抢焦点；② 之后任何一次点击再补一次
     （用户点了画布就一定能用键盘）。canvas 加了 tabindex，才能被 focus。 */
  function grabKeys(){
    try{ window.focus(); }catch(e){}
    try{ cv.focus({preventScroll:true}); }catch(e){ try{ cv.focus(); }catch(e2){} }
  }
  /* 自动抢焦点会被浏览器拦（没有用户手势时不允许跨文档转移焦点），
     实测 iframe 里 window.focus() / autofocus 都无效。既然拦不住，就把它说清楚：
     页面没焦点时浮一条提示，点任意处即可；拿到过焦点后就不再出现。 */
  var keyTip=document.getElementById('keyTip'), keyTipDone=false;
  grabKeys();
  window.addEventListener('load', grabKeys);
  /* 任何一次点击都补一次焦点，同时把"点一下画面"的提示收掉 ——
     点击是用户手势，浏览器一定允许这一下把焦点给 iframe，不用等 hasFocus 回读。 */
  document.addEventListener('pointerdown', function(){
    grabKeys();
    keyTipDone=true;
    if(keyTip) keyTip.classList.remove('on');
  }, true);
  function syncKeyTip(){
    if(!keyTip || keyTipDone) return;
    var ok=true;
    try{ ok=document.hasFocus(); }catch(e){}
    if(ok){ keyTipDone=true; keyTip.classList.remove('on'); return; }
    keyTip.classList.add('on');
  }
  window.addEventListener('focus', syncKeyTip);
  window.addEventListener('blur', syncKeyTip);
  setTimeout(syncKeyTip, 1500);
  /* 挂在 document 的捕获阶段：内层元素 stopPropagation 也拦不住；
     另外跳过输入框 —— 以前在"部件"搜索框里打字，q/e/w/s 会被当成旋转键。 */
  document.addEventListener('keydown', function(e){
    if(e.ctrlKey || e.metaKey || e.altKey) return;
    var t=e.target || {}, tag=(t.tagName||'').toUpperCase();
    if(tag==='INPUT' || tag==='TEXTAREA' || tag==='SELECT' || t.isContentEditable) return;
    var k=e.key;
    if(k==='+' || k==='='){ zoomBy(1/1.5); e.preventDefault(); }
    else if(k==='-' || k==='_'){ zoomBy(1.5); e.preventDefault(); }
    else if(k==='0'){ fit(); e.preventDefault(); }
    else if(k==='q' || k==='Q'){ spin(15); e.preventDefault(); }
    else if(k==='e' || k==='E'){ spin(-15); e.preventDefault(); }
    else if(k==='w' || k==='W'){ tilt(15); e.preventDefault(); }
    else if(k==='s' || k==='S'){ tilt(-15); e.preventDefault(); }
    else if(k==='a' || k==='A'){ rollView(-15); e.preventDefault(); }
    else if(k==='d' || k==='D'){ rollView(15); e.preventDefault(); }
    else if(k==='m' || k==='M'){
      var mb=document.getElementById('measBtn'); if(mb) mb.click(); e.preventDefault();
    }
    else if(k==='p' || k==='P'){
      var pb=document.getElementById('planeBtn'); if(pb) pb.click(); e.preventDefault();
    }
    /* 按住 X / Y / Z 再拖：只绕这一根世界轴转（松手/松键即解除） */
    else if(k==='x' || k==='X'){ lockAxis='x'; e.preventDefault(); }
    else if(k==='y' || k==='Y'){ lockAxis='y'; e.preventDefault(); }
    else if(k==='z' || k==='Z'){ lockAxis='z'; e.preventDefault(); }
    else if(k==='Escape'){
      if(planeOn){ setPlaneMode(false); }
      if(measOn){
        clearMarks(); measOn=false;
        var mb2=document.getElementById('measBtn'); if(mb2) mb2.classList.remove('on');
        if(mtip) mtip.classList.remove('on');
        cv.style.cursor='';
        renderer.render(scene,camera);
      }
    }
  }, true);
  window.addEventListener('keyup', function(e){
    var k=(e.key||'').toLowerCase();
    if(k==='x' || k==='y' || k==='z') lockAxis=null;
  });
  window.addEventListener('blur', function(){ lockAxis=null; });   /* 切走窗口别把锁留着 */
  var edgeMode=false;
  Array.prototype.forEach.call(document.querySelectorAll('#bar button[data-m]'), function(b){
    b.onclick=function(){
      Array.prototype.forEach.call(document.querySelectorAll('#bar button[data-m]'),
        function(x){x.classList.remove('on')});
      b.classList.add('on');
      edgeMode = b.getAttribute('data-m')==='edge';
      applyMats();
      renderer.render(scene,camera);
    };
  });
  var COL_TXT=['原色','分色','素色'];
  document.getElementById('colBtn').onclick=function(){
    colorMode=(colorMode+1)%%3;
    this.textContent=COL_TXT[colorMode];
    this.classList.toggle('on', colorMode!==2);
    applyMats();
    renderer.render(scene,camera);
  };
  window.addEventListener('resize', function(){ resize(); renderer.render(scene,camera); });

  /* 加载阶段日志：把"下载/构建"分开显示，每步带耗时，用户知道卡在哪一步 */
  function stepLog(key, name, detail, state){
    var box=document.getElementById('ldSteps');
    if(!box) return;
    var el=document.getElementById('st_'+key);
    if(!el){
      el=document.createElement('div');
      el.id='st_'+key;
      box.appendChild(el);
    }
    el.className='st '+(state||'run');
    el.innerHTML='<b>'+name+'</b><i>'+(detail||'')+'</i>';
  }

  /* 带进度的下载：大网格几十 MB，加载过程中要能看到"下到哪了、多快"。
     cb(已下载字节, 总字节, 瞬时速度MB/s, 平均速度MB/s) */
  function fetchBuf(url, cb){
    /* 令牌走 X-Preview 头而不是 query：URL 保持稳定，浏览器才能命中缓存。
       网格有 30~90MB，每次重新下载是首屏慢的主要原因。
       服务端带 ETag，内容变了会拿到新文件，没变则 304（几百字节）。 */
    return fetch(url, {headers:{'X-Preview':KKT}}).then(function(r){
      if(!r.ok) throw new Error('HTTP '+r.status);
      var total=parseInt(r.headers.get('x-uncompressed-size')||'0', 10);
      if(!r.body || !r.body.getReader || !total) return r.arrayBuffer();
      var reader=r.body.getReader(), chunks=[], got=0;
      var t0=performance.now(), lastT=t0, lastB=0;
      function pump(){
        return reader.read().then(function(x){
          if(x.done) return;
          chunks.push(x.value); got+=x.value.length;
          var now=performance.now();
          if(cb && now-lastT>250){
            var dt=(now-lastT)/1000;
            cb(got, total, (got-lastB)/1048576/dt, got/1048576/((now-t0)/1000));
            lastT=now; lastB=got;
          }
          return pump();
        });
      }
      return pump().then(function(){
        var out=new Uint8Array(got), off=0;
        for(var i=0;i<chunks.length;i++){ out.set(chunks[i], off); off+=chunks[i].length; }
        return out.buffer;
      });
    });
  }

  var _dl0=performance.now(), netInfo='';
  stepLog('dl', '下载模型', '连接服务器…', 'run');
  ldt.textContent='正在载入模型…';
  fetchBuf(DATA, function(got, total, inst, avg){
    var _t=((performance.now()-_dl0)/1000).toFixed(1);
    stepLog('dl', '下载模型',
      (got/1048576).toFixed(0)+'/'+(total/1048576).toFixed(0)+' MB · '+
      inst.toFixed(1)+' MB/s · '+_t+'s', 'run');
  }).then(function(buf){
    var _sec=(performance.now()-_dl0)/1000, _mb=buf.byteLength/1048576;
    netInfo = _sec < 0.12 ? '缓存命中'
            : ('下载 '+_mb.toFixed(0)+'MB/'+_sec.toFixed(1)+'s='+(_mb/_sec).toFixed(0)+'MB/s');
    stepLog('dl', '下载模型',
      _mb.toFixed(0)+' MB · '+_sec.toFixed(1)+'s · '+
      (_mb/Math.max(_sec,0.01)).toFixed(0)+' MB/s', 'done');
    stepLog('bd', '构建场景', '解析几何数据…', 'run');
    ldt.textContent='正在构建场景…';
    var _b0=performance.now();
    build(buf);
    var _bs=((performance.now()-_b0)/1000).toFixed(2);
    stepLog('bd', '构建场景',
      ((typeof meshes!=='undefined'&&meshes.length)||1)+' 个部件 · '+_bs+'s', 'done');
    model=group;
    ld.classList.add('hide');
    // 取景/渲染出错时不能静默：ld 已经隐藏，用户只会看到一片空白
    /* 首次 render 要真的被计时。
       9419 个 geometry 在这一刻才上传 GPU 并编译着色器，实测能占掉 5~6 秒 ——
       比 build 本身（0.28s）大一个数量级。以前只记了 build，用户报"打开要十几秒"
       时一直查不到点子上（以为卡在下载或解析）。 */
    var _r0=performance.now();
    try {
      resize(); fit(); renderer.render(scene,camera);
    } catch(err) {
      console.error('FITFAIL', err);
      ldt.textContent='取景失败：'+(err && err.message ? err.message : err);
      ld.classList.remove('hide');
    }
    var _rs=((performance.now()-_r0)/1000).toFixed(2);
    stepLog('rd', '首帧渲染', 'GPU 上传 + 着色器编译 · '+_rs+'s',
            parseFloat(_rs)>2 ? 'slow' : 'done');
    if(window.__kk) window.__kk.phase={build:parseFloat(_bs), render:parseFloat(_rs),
      parts:(typeof meshes!=='undefined'?meshes.length:0),
      tris:(typeof tris!=='undefined'?tris:0)};
    if(netInfo){
      var _nb=document.getElementById('netBtn');
      var _m=/=([0-9.]+MB[/]s)$/.exec(netInfo);   /* 用 [/] 代替斜杠转义：模板里反斜杠会告警 */
      if(_nb && _m) _nb.textContent='⇅ '+_m[1];
      else if(_nb && netInfo==='缓存命中') _nb.textContent='⇅ 缓存';
      if(info) info.title = info.textContent + ' · ' + netInfo;
    }
    buildTree(); drawGizmo();
    syncKeyTip();   /* 模型就绪后补一次判断：页面没焦点就把"点一下画面"的提示浮出来 */
    showHint(true); /* 打开就默认显示操作提示（从这一刻开始计时，图出来就能看见） */
    /* 每帧都画。以前只在"相机变了"时才 render，省 GPU；但 WebGL 画布
       （preserveDrawingBuffer 默认 false）在 rAF 之外渲染时内容不保证保留，
       实测大模型（线框 29 万顶点）首屏会停在未清屏的黑帧，用户看到一片空白，
       手动再 render 一次才出来。改成 rAF 里逐帧渲染，代价可接受。 */
    (function loop(){
      requestAnimationFrame(loop);
      if(ctl && ctl.enableDamping) ctl.update();
      drawGizmo();
      renderer.render(scene, camera);
    })();
  }).catch(function(e){
    var msg=String((e && e.message) || e);
    /* 实测 three.js 在 WebGL 着色器编译失败时会去读一个为 null 的编译日志，
       报出 "Cannot read properties of null (reading 'trim')" —— 跟数据无关，
       刷新一次往往就好了。这种情况给重试入口，别甩英文异常给用户。 */
    if(/trim|webgl|context|shader/i.test(msg)){
      ldt.innerHTML='图形渲染初始化失败（显卡/WebGL 异常，偶发）。' +
        '<br><a href="javascript:location.reload()" style="color:#2f6feb">点这里重新载入</a>';
    } else {
      ldt.textContent='模型载入失败：'+msg;
    }
    console.error('LOADFAIL', e);
  });
})();
</script>
%(beacon)s
</body></html>""" % {"title": title, "wm": wm_url,
                        "fk": json.dumps(file_key), "kkt": json.dumps(token_q),
                        "beacon": beacon or ""}

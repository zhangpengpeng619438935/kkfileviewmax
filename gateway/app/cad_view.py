# -*- coding: utf-8 -*-
"""CAD（DWG/DXF）预览：服务端用 PDFium 出图，浏览器只显示位图。

**为什么要这么做**

Aspose 把 DWG 转成 PDF 时，一条线段就是一个独立的描边块。一份 7MB 的布局平面图
会产出 126,350 个 `q ... S Q` 块、444 万条线段、45MB 的 PDF。pdf.js 在单页里要
逐条描边这 444 万段，实测 **246 秒都没画完**（控制台无报错、画布空白、loading 一直转）。
同一份 PDF 交给 PDFium（C++，pypdfium2）渲染整页 6000px，只要 **0.77 秒**。

所以 CAD 不再走 pdf.js，改为：

    引擎转 PDF（Aspose，十几秒，无法避免）
      → 网关把 PDF 拉到本地缓存
      → 请求到达时用 PDFium 渲染成 PNG（首次 ~0.8s，之后走磁盘缓存）
      → 浏览器 <img> 直接显示

首屏是"一张图"，任何终端、任何图纸复杂度都能稳定出来；缩放换成更高档位的位图。
"""
import asyncio
import html
import io
import os
import re
import threading
import time

from . import config, sheet

log = None
try:                                     # 避免循环导入
    import logging
    log = logging.getLogger("kk.cad")
except Exception:                        # pragma: no cover
    pass

# 位图档位：前端按显示宽度挑最接近的一档，避免每次都渲染 8000px
LADDER = (1600, 2400, 3600, 5400, 8000)
MAX_WIDTH = 8000
PDF_URL_RE = re.compile(r"var\s+url\s*=\s*'([^']+?\.pdf)'", re.I)


def _enabled() -> bool:
    return bool(getattr(config, "CAD_RASTER", True))


def _pdfium():
    import pypdfium2
    return pypdfium2


def pdf_path(file_key: str) -> str:
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + ".cad.pdf")


def pdf_ready(file_key: str) -> bool:
    p = pdf_path(file_key)
    try:
        return os.path.getsize(p) > 1024
    except OSError:
        return False


def png_path(file_key: str, page: int, width: int) -> str:
    return os.path.join(sheet.SHEET_DIR,
                        "%s.cad_p%d_w%d.png" % (file_key or "", page, width))


def clear_pngs(file_key: str) -> int:
    """删掉该文件所有档位的位图缓存（图纸换了/过期时调用）。"""
    hit = 0
    prefix = (file_key or "") + ".cad_p"
    try:
        names = os.listdir(sheet.SHEET_DIR)
    except OSError:
        return 0
    for n in names:
        if n.startswith(prefix):
            try:
                os.remove(os.path.join(sheet.SHEET_DIR, n))
                hit += 1
            except OSError:
                pass
    return hit


def pdf_url_of_page(page_html: str) -> str:
    """从引擎的 CAD 页面里取出它引用的 PDF 地址（引擎自己拼的，最可靠）。"""
    m = PDF_URL_RE.search(page_html or "")
    return m.group(1) if m else ""


async def cache_pdf(pdf_url: str, file_key: str) -> bool:
    """把引擎的 PDF 拉到网关本地（内网直连，45MB 约 0.02s）。"""
    if not pdf_url:
        return False
    from . import engine
    client = await engine.http()
    dst = pdf_path(file_key)
    tmp = dst + ".part"
    try:
        async with client.stream("GET", pdf_url, timeout=300) as resp:
            if resp.status_code >= 400:
                raise RuntimeError("引擎 PDF HTTP %s" % resp.status_code)
            os.makedirs(sheet.SHEET_DIR, exist_ok=True)
            with open(tmp, "wb") as f:
                async for chunk in resp.aiter_raw():
                    f.write(chunk)
        size = os.path.getsize(tmp)
        if size < 1024:
            raise RuntimeError("引擎 PDF 为空(%d 字节)" % size)
        os.replace(tmp, dst)
        return True
    except Exception as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        if log:
            log.warning("CAD PDF 缓存失败(%s): %s", file_key, exc)
        return False


_PDF_LOCK = threading.Lock()


def _render_sync(path: str, width: int, page: int) -> bytes:
    """把 PDF 某页渲染成 PNG（同步、CPU 密集，调用方放进线程池）。

    PDFium 不是线程安全的：这里有锁串行化，避免并发打开同一份 PDF 时崩溃。
    """
    with _PDF_LOCK:
        return _render_sync_locked(path, width, page)


def _render_sync_locked(path: str, width: int, page: int) -> bytes:
    pdfium = _pdfium()
    doc = pdfium.PdfDocument(path)
    try:
        idx = max(0, min(page, len(doc) - 1))
        pg = doc[idx]
        w_pt, h_pt = pg.get_size()
        scale = float(width) / max(1.0, w_pt)
        bitmap = pg.render(scale=scale)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _count_pages(path: str) -> int:
    try:
        doc = _pdfium().PdfDocument(path)
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return 1


async def render_png(file_key: str, width: int, page: int = 0) -> bytes:
    """取（或生成）某页某宽度的 PNG。命中磁盘缓存直接读。"""
    width = max(600, min(int(width), MAX_WIDTH))
    page = max(0, int(page))
    out = png_path(file_key, page, width)
    if os.path.exists(out):
        try:
            with open(out, "rb") as f:
                return f.read()
        except OSError:
            pass
    src = pdf_path(file_key)
    data = await asyncio.get_event_loop().run_in_executor(
        None, _render_sync, src, width, page)
    tmp = out + ".part"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out)
    except OSError:
        pass
    return data


def pages_of(file_key: str) -> int:
    return _count_pages(pdf_path(file_key)) if pdf_ready(file_key) else 1


def pick_width(css_width: float, dpr: float = 1.0) -> int:
    want = css_width * max(1.0, dpr)
    for w in LADDER:
        if w >= want:
            return w
    return MAX_WIDTH


def viewer_html(file_key: str, file_name: str, token_q: str, wm_url: str,
                width: int = 1600, page: int = 0, pages: int = 1) -> str:
    """CAD 查看器：单张位图 + 缩放/平移 + 高清档位自动提升。"""
    title = html.escape(file_name or "图纸")
    base = "/cadpng/%s?kkt=%s" % (file_key, token_q)
    page_nav = ""
    if pages > 1:
        buttons = "".join(
            '<button class="pgn%s" data-p="%d">%d</button>'
            % (" on" if i == page else "", i, i + 1) for i in range(pages))
        page_nav = '<span class="pgs">%s</span>' % buttons
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%%;overflow:hidden;background:#f4f5f7;color:#222;
 font-family:-apple-system,"Microsoft YaHei","PingFang SC",sans-serif}
#bar{height:36px;display:flex;align-items:center;gap:8px;padding:0 10px;background:#fff;
 border-bottom:1px solid #e3e6ea;font-size:12.5px;color:#3a3f47;position:relative;z-index:20}
#bar .ttl{font-weight:600;max-width:44%%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#bar .sp{flex:1}
#bar button{border:1px solid #cfd4da;background:#fff;color:#2f6feb;border-radius:5px;
 padding:3px 9px;font-size:12px;cursor:pointer}
#bar button:hover{border-color:#2f6feb;background:#f5f8ff}
#bar button.on{background:#2f6feb;color:#fff;border-color:#2f6feb}
#bar .tip{color:#8a9099;font-size:11.5px}
#bar .pgs button{margin-left:2px}
#stage{position:absolute;top:36px;left:0;right:0;bottom:0;overflow:auto;cursor:grab;
 background:#e9ebef;padding:14px;display:flex;align-items:center}
#stage.drag{cursor:grabbing}
#wrap{position:relative;margin:auto;background:#fff;
 box-shadow:0 1px 5px rgba(0,0,0,.14);width:max-content}
#img{display:block;max-width:none;user-select:none;-webkit-user-drag:none}
#ld{position:absolute;inset:0;display:none;align-items:center;justify-content:center;
 background:rgba(255,255,255,.72);z-index:5;font-size:12.5px;color:#5b636e}
#ld.on{display:flex}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9;
 background-image:url("%(wm)s");background-repeat:repeat}
</style></head><body>
<div id="bar">
  <span class="ttl" title="%(title)s">%(title)s</span>
  <span class="tip">CAD 图纸 · 位图预览</span>
  %(pagenav)s
  <span class="sp"></span>
  <button data-z="fit" class="on">适应宽度</button>
  <button data-z="1">100%%</button>
  <button data-z="1.5">150%%</button>
  <button data-z="2">200%%</button>
  <button data-z="3">300%%</button>
</div>
<div id="stage"><div id="wrap"><img id="img" alt=""></div></div>
<div id="wm"></div>
<script>
(function(){
  var FK=%(fk)s, BASE=%(base)s, PAGE=%(page)d, LADDER=[1600,2400,3600,5400,8000];
  var img=document.getElementById('img'), stage=document.getElementById('stage'),
      wrap=document.getElementById('wrap'), ld=document.getElementById('ld'),
      curW=%(width)d, page=PAGE, zoom=0, fitW=0, natW=0;
  if(!document.getElementById('ld')){
    var d=document.createElement('div'); d.id='ld'; d.textContent='正在生成图纸…';
    wrap.appendChild(d); ld=d;
  }
  function url(w,p){ return BASE+'&w='+w+'&p='+p; }
  function hiResNeed(){
    if(!natW||!fitW) return curW;
    var shown=parseInt(img.style.width||'0',10)||natW;
    var need=shown*(window.devicePixelRatio||1);
    for(var i=0;i<LADDER.length;i++) if(LADDER[i]>=need) return LADDER[i];
    return LADDER[LADDER.length-1];
  }
  function load(w,p){
    if(w===curW && p===page && img.dataset.loaded==='1') return;
    curW=w; page=p; img.dataset.loaded='0';
    ld.classList.add('on'); ld.textContent='正在生成图纸…';
    var t=new Image();
    t.onload=function(){
      img.src=t.src; natW=t.naturalWidth; img.dataset.loaded='1';
      apply();
      ld.classList.remove('on');
      var want=hiResNeed();
      if(want>curW){ setTimeout(function(){ load(want,page); },60); }
    };
    t.onerror=function(){ ld.textContent='图纸渲染失败，请稍后重试'; };
    t.src=url(w,p);
  }
  function apply(){
    if(!natW) return;
    var w;
    if(zoom===0){ w=fitW||stage.clientWidth-28; }
    else { w=natW*zoom; }
    img.style.width=Math.round(w)+'px';
  }

  /* 滚轮缩放 + 左键拖拽。这个页面原来只能点 100%%/150%% 按钮，用户反馈"想用滚轮"。 */
  img.draggable=false;
  stage.addEventListener('wheel', function(e){
    e.preventDefault();
    if(!natW) return;
    var r=stage.getBoundingClientRect();
    var oldW=img.getBoundingClientRect().width||1;
    var base=(zoom===0? oldW/natW : zoom);
    var nz=Math.max(0.05, Math.min(8, base*(e.deltaY<0?1.15:1/1.15)));
    var mx=e.clientX-r.left+stage.scrollLeft, my=e.clientY-r.top+stage.scrollTop;
    zoom=nz; apply();
    var newW=img.getBoundingClientRect().width||1, k=newW/oldW;
    stage.scrollLeft=mx*k-(e.clientX-r.left);
    stage.scrollTop=my*k-(e.clientY-r.top);
  }, {passive:false});

  var _dn=false,_sx=0,_sy=0,_sl=0,_st=0;
  stage.addEventListener('mousedown', function(e){
    if(e.button!==0) return;
    _dn=true; _sx=e.clientX; _sy=e.clientY;
    _sl=stage.scrollLeft; _st=stage.scrollTop;
    stage.classList.add('drag'); e.preventDefault();
  });
  window.addEventListener('mousemove', function(e){
    if(!_dn) return;
    stage.scrollLeft=_sl-(e.clientX-_sx);
    stage.scrollTop=_st-(e.clientY-_sy);
  });
  window.addEventListener('mouseup', function(){ _dn=false; stage.classList.remove('drag'); });
  function setFit(){
    var dpr=window.devicePixelRatio||1;
    var css=Math.max(400, stage.clientWidth-28);
    var need=css*dpr, w=LADDER[0];
    for(var i=0;i<LADDER.length;i++){ if(LADDER[i]>=need){ w=LADDER[i]; break; } }
    if(w<curW && img.dataset.loaded==='1'){ w=curW; }   /* 不降质 */
    load(w,page);
  }
  /* 缩放 */
  Array.prototype.forEach.call(document.querySelectorAll('#bar button[data-z]'), function(b){
    b.onclick=function(){
      Array.prototype.forEach.call(document.querySelectorAll('#bar button[data-z]'),
        function(x){ x.classList.remove('on'); });
      b.classList.add('on');
      var z=b.getAttribute('data-z');
      if(z==='fit'){ zoom=0; apply(); return; }
      zoom=parseFloat(z);
      var dpr=window.devicePixelRatio||1;
      var need=natW*zoom*dpr, w=LADDER[0];
      for(var i=0;i<LADDER.length;i++){ if(LADDER[i]>=need){ w=LADDER[i]; break; } }
      if(w>curW) load(w,page); else apply();
    };
  });
  /* 页码 */
  Array.prototype.forEach.call(document.querySelectorAll('#bar .pgn'), function(b){
    b.onclick=function(){
      Array.prototype.forEach.call(document.querySelectorAll('#bar .pgn'),
        function(x){ x.classList.remove('on'); });
      b.classList.add('on');
      zoom=0; load(curW, parseInt(b.getAttribute('data-p'),10));
    };
  });
  /* 拖动平移 + 双击缩放 */
  var down=false,sx=0,sy=0,sl=0,st=0;
  stage.addEventListener('mousedown',function(e){
    down=true; sx=e.clientX; sy=e.clientY; sl=stage.scrollLeft; st=stage.scrollTop;
    stage.classList.add('drag'); e.preventDefault();
  });
  window.addEventListener('mousemove',function(e){
    if(!down) return;
    stage.scrollLeft=sl-(e.clientX-sx); stage.scrollTop=st-(e.clientY-sy);
  });
  window.addEventListener('mouseup',function(){ down=false; stage.classList.remove('drag'); });
  stage.addEventListener('dblclick',function(){
    zoom = zoom? 0 : 2;
    var btn=document.querySelector('#bar button[data-z="'+(zoom?'2':'fit')+'"]');
    if(btn) btn.click();
  });
  img.addEventListener('load', apply);
  window.addEventListener('resize', function(){ if(zoom===0) setFit(); });
  setFit();
})();
</script>
</body></html>""" % {"title": title, "wm": wm_url, "pagenav": page_nav,
                        "fk": '"%s"' % file_key, "base": '"%s"' % base,
                        "page": page, "width": width}

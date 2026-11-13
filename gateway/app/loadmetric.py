# -*- coding: utf-8 -*-
"""加载耗时（端到端）—— 看板上"加载耗时"这个指标的唯一口径来源。

**为什么要有这个模块**

以前看板里的"耗时"就是 `/api/v1/preview/url` 这一个 HTTP 请求的处理时间。
那个请求只做三件事：校验令牌、查一下缓存标记、把预览地址拼出来，实测
0.2~0.4 秒。可用户真正等的是"转码 + 下载 + 首屏渲染"：

    DWG 7.4MB（冷启动）  申请 0.26s + 转码 19.5s + 出图 0.8s ≈ 20 秒
    47MB STEP 装配体     申请 0.25s + 下载 1.7s + WASM 解析 100s+

也就是说旧口径把用户等待的 98% 漏掉了，看板写 0.4 秒、用户体感 20 秒，
两边说的根本不是一件事。

**现在的口径**

    preview_url    → 把"申请时刻"写进 Redis: kk:ld:<requestId>（30 分钟）
    预览页可见     → 页面 sendBeacon 报 /api/v1/preview/loaded
    网关           → 用 上报时刻 - 申请时刻 回填这条记录的 cost_ms

页面侧并不猜时间：脚本轮询"首屏是否真有内容"（大图 naturalWidth / canvas
上真的画出了东西），确认可见才报，所以报上来的就是用户看到东西的时刻。

审计与明细各一条，都用同一个 requestId 关联；落库是异步的（可能比上报还晚），
所以上报时既要尝试 UPDATE 已有行，也要把结果留在 kk:e2e:<rid> 里，
由 collector 落库时覆盖，两条路都堵上才不会漏。
"""
import json
import logging
import time

from . import db

log = logging.getLogger("kk.loadmetric")

REQ_PREFIX = "kk:ld:"         # 申请登记（hash）
E2E_PREFIX = "kk:e2e:"        # 已算出的端到端耗时（string, 毫秒）
DONE_PREFIX = "kk:ldone:"     # 幂等标记：一次预览只记第一次"可见"
REQ_TTL = 1800                # 30 分钟，比预览令牌(10 分钟)长，够覆盖慢文件
MAX_ELAPSED_MS = 30 * 60 * 1000


def now_ms() -> int:
    return int(time.time() * 1000)


async def mark_request(rid: str, file_key: str = "", emp_no: str = "",
                       app_id: str = "", file_name: str = "",
                       file_url: str = "", cache_hit: bool = False) -> None:
    """登记一次预览申请。失败不影响主流程（统计不能拖垮预览）。"""
    if not rid:
        return
    try:
        r = await db.get_redis()
        key = REQ_PREFIX + rid
        await r.hset(key, mapping={
            "t0": now_ms(), "fk": file_key or "", "emp": emp_no or "",
            "app": app_id or "", "nm": (file_name or "")[:200],
            "hit": "1" if cache_hit else "0",
        })
        await r.expire(key, REQ_TTL)
    except Exception as exc:      # pragma: no cover
        log.debug("加载计时登记失败: %s", exc)


async def _write_back(rid: str, elapsed: int) -> None:
    """回填到已有记录。行还没落库时返回 0，由 collector 兜底。"""
    try:
        await db.execute(
            "UPDATE audit_log SET cost_ms=%s WHERE request_id=%s", (elapsed, rid))
        # e2e=1 是给统计用的：看板的"加载耗时"只认这一批记录。
        # 没回填成功的历史数据（只有接口耗时的 0.4 秒）不参与平均，免得把数字拉花。
        await db.execute(
            "UPDATE access_log SET cost_ms=%s, e2e=1 WHERE request_id=%s"
            " AND action='preview'", (elapsed, rid))
    except Exception as exc:      # pragma: no cover
        log.debug("加载耗时回填失败: %s", exc)


async def _write_back_background(rid: str, elapsed: int) -> None:
    """页面切过后台的样本：写耗时但不置 e2e=1，不参与统计与告警。"""
    try:
        await db.execute(
            "UPDATE audit_log SET cost_ms=%s WHERE request_id=%s", (elapsed, rid))
        await db.execute(
            "UPDATE access_log SET cost_ms=%s WHERE request_id=%s AND action='preview'",
            (elapsed, rid))
    except Exception as exc:      # pragma: no cover
        log.debug("后台样本回填失败: %s", exc)


TIMEOUT_PREFIX = "kk:lto:"       # 超时计数（按天），给看板单独展示


async def _write_back_timeout(rid: str, elapsed: int) -> None:
    """探针跑满了最长等待仍未看到内容：记耗时，但**不置 e2e=1**。

    为什么不能当加载耗时：探针的上限就是 MAX(60/120 秒)，到点它只能报"超时"，
    不是说"这个文件加载用了 120 秒"。最初这种样本照常写 e2e=1，实测 57 条
    全部挤在 115~125 秒之间，把 p99 从 60 秒硬拉到 130 秒 —— 分位数被一个人为
    常量顶住了，看板上那个"最慢 130 秒"其实是个探针参数，不是真实数据。

    但也不能当没发生：用户确实盯着白屏等满了两分钟。所以单独计数，
    看板"加载超时"那一栏要能看见它。
    """
    try:
        await db.execute(
            "UPDATE audit_log SET cost_ms=%s WHERE request_id=%s", (elapsed, rid))
        await db.execute(
            "UPDATE access_log SET cost_ms=%s WHERE request_id=%s AND action='preview'",
            (elapsed, rid))
        r = await db.get_redis()
        await r.incr(TIMEOUT_PREFIX + time.strftime("%Y-%m-%d"))
        await r.expire(TIMEOUT_PREFIX + time.strftime("%Y-%m-%d"), 86400 * 40)
    except Exception as exc:      # pragma: no cover
        log.debug("超时样本回填失败: %s", exc)


async def timeout_count(day: str = "") -> int:
    """某天探针超时的次数（默认今天）。"""
    day = day or time.strftime("%Y-%m-%d")
    try:
        r = await db.get_redis()
        return int(await r.get(TIMEOUT_PREFIX + day) or 0)
    except Exception:
        return 0


async def report(rid: str, phase: str = "visible", bg: bool = False) -> dict:
    """结算一次端到端耗时。只认第一次上报，重复上报直接返回已算结果。

    bg=True 表示"等待期间页面被切到后台/最小化过"：浏览器会把 setInterval 节流到
    每分钟一次，探测"内容可见"的轮询因此被推迟，报上来的不是真实加载耗时
    （实测同一个人工手册 html：正常 662ms，切后台那次 285 秒）。这类样本仍然记
    cost_ms 供排查，但**不打 e2e 标记**，不进"加载耗时"统计，也不会撞上
    load_timeout 的 5 分钟告警线。
    """
    if not rid:
        return {"ok": False, "reason": "no-rid"}
    try:
        r = await db.get_redis()
    except Exception:
        return {"ok": False, "reason": "no-redis"}
    try:
        first = await r.set(DONE_PREFIX + rid, "1", nx=True, ex=REQ_TTL)
        if not first:
            v = await r.get(E2E_PREFIX + rid)
            return {"ok": True, "elapsedMs": int(v) if v else None, "duplicate": True}
        m = await r.hgetall(REQ_PREFIX + rid) or {}
        t0 = int(m.get("t0") or 0)
        if not t0:
            # 申请登记已过期（比如用户把页面挂了两小时再刷新）→ 不给结论，
            # 免得把一个巨大的假数字写进统计。
            return {"ok": False, "reason": "expired"}
        elapsed = now_ms() - t0
        if elapsed < 0 or elapsed > MAX_ELAPSED_MS:
            return {"ok": False, "reason": "out-of-range"}
        if bg:
            await _write_back_background(rid, elapsed)
            await r.set(E2E_PREFIX + rid, elapsed, ex=REQ_TTL)
            return {"ok": True, "elapsedMs": elapsed, "background": True,
                    "fileKey": m.get("fk", ""), "cacheHit": m.get("hit") == "1"}
        if phase == "timeout":
            # 探针等到上限也没看到内容：不是"加载用了 120 秒"，见 _write_back_timeout。
            # **不写 E2E_PREFIX**：collector 落库时会读它并置 e2e=1，
            # 写进去就等于又把超时混进加载耗时统计。
            await _write_back_timeout(rid, elapsed)
            return {"ok": True, "elapsedMs": elapsed, "timeout": True,
                    "fileKey": m.get("fk", ""), "cacheHit": m.get("hit") == "1"}
        await r.set(E2E_PREFIX + rid, elapsed, ex=REQ_TTL)
        await _write_back(rid, elapsed)
        return {"ok": True, "elapsedMs": elapsed, "phase": phase,
                "fileKey": m.get("fk", ""), "cacheHit": m.get("hit") == "1"}
    except Exception as exc:      # pragma: no cover
        log.warning("加载耗时结算失败(%s): %s", rid, exc)
        return {"ok": False, "reason": str(exc)[:80]}


async def pending_e2e(request_ids) -> dict:
    """批量取已算出的耗时（collector 落库前调用）。"""
    ids = [i for i in request_ids if i]
    if not ids:
        return {}
    try:
        r = await db.get_redis()
        vals = await r.mget([E2E_PREFIX + i for i in ids])
        return {i: int(v) for i, v in zip(ids, vals) if v}
    except Exception:
        return {}




# 页面侧的"看到了吗"探针。用 __X__ 占位而不是 %-格式化：
# 模板里写 JS 时一个裸 % 就会让整页 500，这个坑已经踩过一次。
_BEACON = """<script>(function(){
  var KT=__KKT__, MAX=__MAX__, T0=Date.now(), DONE=false, HID=0;
  var URL='/api/v1/preview/loaded';
  /* 画布真的画了东西吗：缩到 16x16 采样，颜色种类 > 3 才算 */
  function painted(c){
    try{
      var t=document.createElement('canvas'); t.width=16; t.height=16;
      var x=t.getContext('2d'); x.drawImage(c,0,0,16,16);
      var d=x.getImageData(0,0,16,16).data, s={}, n=0;
      for(var i=0;i<d.length;i+=4){
        var k=(d[i]>>4)+'-'+(d[i+1]>>4)+'-'+(d[i+2]>>4);
        if(!s[k]){ s[k]=1; n++; }
      }
      return n>3;
    }catch(e){ return true; }
  }
  function ready(){
    var im=document.images, i;
    for(i=0;i<im.length;i++){
      if(im[i].naturalWidth>200 && im[i].naturalHeight>200 && im[i].complete) return 'img';
    }
    /* 纯文本页（txt/json/源码/压缩包内文本）主体就是一个 <pre>：
       没有 img/canvas/embed，前面几个判据全不命中，以前只能等到超时。
       判"有内容且已经排出版面"（offsetHeight>0）即可，不是"元素存在"就算。 */
    var pre=document.querySelector('pre');
    if(pre && (pre.textContent||'').length>0 && pre.offsetHeight>0) return 'text';
    /* 3D 视图：网格态写"面片/顶点"，**线框态写的是"线框 N 条线段"**。
       线框是"实体网格还在后台三角化时先给用户看结构"的形态，画面确实已经出来了，
       可它不含"面片/顶点"这几个字，canvas 判据也过不了（白底 + 1px 细线，
       16x16 采样颜色种类不到 4 种）。于是线框页永远探测不到"可见"，
       一路等到超时 —— 实测 STEP 的 120 秒假样本里有相当一部分是这种。 */
    var bt=document.body ? (document.body.innerText||'') : '';
    if(/Triangles|Vertices|三角面|顶点|面片/i.test(bt)) return 'mesh';
    /* 用 [ ] 代替转义写法：这段是 Python 三引号模板，反斜杠会触发 SyntaxWarning */
    if(/线框[ 0-9,]+条线段/.test(bt)) return 'wire';
    var cs=document.querySelectorAll('canvas');
    for(i=0;i<cs.length;i++){
      if(cs[i].width>100 && cs[i].height>100 && painted(cs[i])) return 'canvas';
    }
    var objs=document.querySelectorAll('object,embed');
    for(i=0;i<objs.length;i++){ if(objs[i].offsetWidth>300) return 'embed'; }
    return '';
  }
  /* 等待期间被切到后台/最小化：定时器会被浏览器节流到每分钟一次，
     这时报上来的"可见时刻"晚得离谱，标记出来让网关别计入加载耗时 */
  document.addEventListener('visibilitychange', function(){
    if(document.hidden && !HID) HID=Date.now();
  });
  function send(ph){
    if(DONE) return; DONE=true;
    var body=JSON.stringify({kkt:KT, phase:ph, ms:Date.now()-T0, bg:HID?1:0});
    try{
      if(navigator.sendBeacon){
        if(navigator.sendBeacon(URL, new Blob([body],{type:'application/json'}))) return;
      }
    }catch(e){}
    try{
      fetch(URL,{method:'POST',body:body,keepalive:true,
                 headers:{'Content-Type':'application/json'}});
    }catch(e){}
  }
  var iv=setInterval(function(){
    var ph=ready();
    if(ph){ clearInterval(iv); send(ph); return; }
    if(Date.now()-T0>MAX){ clearInterval(iv); send('timeout'); }
  },250);
  window.addEventListener('load',function(){
    setTimeout(function(){ if(!DONE){ var ph=ready(); if(ph) send(ph); } },800);
  });
})();</script>"""

_MARK = "preview/loaded"


def beacon(kkt: str, max_ms: int = 120000) -> str:
    """给已经是完整页面、但页面体不受我们控制的地方（如自研 3D 查看器）插上报。"""
    if not kkt:
        return ""
    return (_BEACON.replace("__KKT__", json.dumps(kkt))
                   .replace("__MAX__", str(int(max_ms))))


def inject(html: str, kkt: str, max_ms: int = 120000) -> str:
    """把上报脚本塞进代理回来的 HTML（引擎页/查看器页），已塞过就不重复塞。"""
    if not kkt or _MARK in html:
        return html
    frag = beacon(kkt, max_ms)
    if "</body>" in html:
        return html.replace("</body>", frag + "</body>", 1)
    return html + frag

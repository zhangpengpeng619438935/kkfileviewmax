/**
 * Univer 预览页：xlsx / xls 走 Sheets，ppt / pptx 走 Slides。
 *
 * 数据由网关准备（`GET /api/v1/univer/data?kkt=...`）：
 *   { kind: "sheets", fileName, data: IWorkbookData }
 *   { kind: "slides", fileName, data: ISlideData }
 *
 * 为什么 PPT 用"每页一张位图"：Univer 1.1.0 的形状渲染在 @univerjs-pro/slides
 * （需要商业授权），免费的 @univerjs/slides 只有数据模型与画布；
 * 用 LibreOffice + PDFium 把每页渲染成图，再放进 Slides 的页面里，
 * 版式与导出的 PDF 完全一致，翻页/缩略图/演示模式仍然走 Univer。
 */
import { createUniver, defaultTheme, LocaleType, mergeLocales, UniverInstanceType } from '@univerjs/presets';
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core';
import sheetsZhCN from '@univerjs/preset-sheets-core/locales/zh-CN';
// Univer 的界面样式不会自动注入，必须显式 import，否则工具栏全是裸文本
import '@univerjs/design/lib/index.css';
import '@univerjs/ui/lib/index.css';
import '@univerjs/docs-ui/lib/index.css';
import '@univerjs/sheets-ui/lib/index.css';
import '@univerjs/sheets-formula-ui/lib/index.css';
import '@univerjs/sheets-numfmt-ui/lib/index.css';
import '@univerjs/slides-ui/lib/index.css';
// Univer 的界面样式不会自动注入，必须显式 import，否则工具栏全是裸文本
import '@univerjs/design/lib/index.css';
import '@univerjs/ui/lib/index.css';
import '@univerjs/docs-ui/lib/index.css';
import '@univerjs/sheets-ui/lib/index.css';
import '@univerjs/sheets-formula-ui/lib/index.css';
import '@univerjs/sheets-numfmt-ui/lib/index.css';
import '@univerjs/slides-ui/lib/index.css';

import { UniverRenderEnginePlugin } from '@univerjs/engine-render';
import { UniverUIPlugin, IUIPartsService, ISidebarService } from '@univerjs/ui';
import uiZhCN from '@univerjs/ui/locale/zh-CN';
import { UniverDocsPlugin } from '@univerjs/docs';
import { UniverDocsUIPlugin } from '@univerjs/docs-ui';
import docsZhCN from '@univerjs/docs-ui/locale/zh-CN';
import { UniverSlidesPlugin } from '@univerjs/slides';
import { UniverSlidesUIPlugin, CanvasView } from '@univerjs/slides-ui';
import slidesZhCN from '@univerjs/slides-ui/locale/zh-CN';

const qs = new URLSearchParams(location.search);
const kkt = qs.get('kkt') || '';
const API = '/api/v1/univer/data';
const mask = () => document.getElementById('mask');
const tip = (t) => {
  const el = document.getElementById('tip');
  if (el) el.textContent = t;
};

function fail(msg) {
  tip('预览加载失败：' + msg);
  mask()?.classList.remove('on');
  console.error('[univer-preview]', msg);
}

/* ------------------------------------------------------------------ 表格 */
function mountSheets(data, fileName) {
  const { univerAPI } = createUniver({
    locale: LocaleType.ZH_CN,
    locales: { [LocaleType.ZH_CN]: mergeLocales(sheetsZhCN) },
    theme: defaultTheme,
    presets: [UniverSheetsCorePreset({ container: 'app', ribbonType: 'simple' })],
  });
  data.id = data.id || 'wb_' + Math.random().toString(36).slice(2, 10);
  data.name = data.name || fileName || '工作簿';
  const wb = univerAPI.createWorkbook(data);
  // 只读：把当前工作簿设为不可编辑（单元格仍可选中、复制）
  try {
    univerAPI.getActiveWorkbook()?.setEditable?.(false);
  } catch (e) { /* 老版本没有该方法，忽略 */ }
  try {
    wb?.getActiveSheet?.()?.getRange?.(0, 0, 1, 1)?.activate?.();
  } catch (e) { /* 定位失败不影响预览 */ }
  return univerAPI;
}

/* ------------------------------------------------------------------ 幻灯片 */
/**
 * 关掉左侧"页面列表"栏。
 *
 * Univer 的幻灯片画布宽度是按「窗口宽 - 256px 侧栏」算的，页面却按整个窗口宽
 * 居中——侧栏一开，页面右边缘就被画布硬裁掉一截（实测 1500 宽窗口裁 66px，
 * 第一页右下角的图注正好在裁切线外）。
 * 关掉侧栏后画布与窗口等宽，页面居中不再溢出；顺带把 256px 还给正文。
 */
function hideSlideSidebar(univer) {
  try {
    const inj = univer.__getInjector();
    inj.get(IUIPartsService)?.setUIVisible('left-sidebar', false);
    inj.get(ISidebarService)?.close();
    // UI 的尺寸计算是订阅式的，尺寸变了要有人喊一声
    window.dispatchEvent(new Event('resize'));
    return true;
  } catch (e) {
    console.warn('[univer] 隐藏页面列表失败', e);
    return false;
  }
}

/**
 * 幻灯片翻页条。
 *
 * 关掉左侧页面列表后，Univer 自己就没有翻页入口了（滚轮只会滚动画布，
 * 不会换页）。这里补一条右下角悬浮控件：上一页/下一页/页码，
 * 并接上滚轮与键盘——这也是用户在 PDF 查看器里习惯的操作方式。
 */
function mountSlideNav(univer, univerAPI, unit, order, unitId) {
  if (!order || order.length < 1) return;
  let idx = 0;
  let canvasView = null;
  try {
    canvasView = univer.__getInjector().get(CanvasView);
  } catch (e) {
    console.warn('[univer] 取 CanvasView 失败，改用命令翻页', e);
  }

  const bar = document.createElement('div');
  bar.id = 'kk-nav';
  bar.innerHTML = '<button type="button" data-d="-1" title="上一页">‹</button>'
    + '<span id="kk-page">1 / ' + order.length + '</span>'
    + '<button type="button" data-d="1" title="下一页">›</button>';
  document.body.appendChild(bar);
  bar.classList.add('on');
  const label = bar.querySelector('#kk-page');

  const go = (i) => {
    const next = Math.max(0, Math.min(order.length - 1, i));
    if (next === idx) return;
    idx = next;
    label.textContent = (idx + 1) + ' / ' + order.length;
    try {
      // CanvasView.activePage 是 Univer 幻灯片真正"切到某页"的入口
      // （model.setActivePage 只改数据，不重绘）
      if (canvasView) canvasView.activePage(order[idx], unitId);
      else univerAPI.executeCommand('slide.operation.activate-slide',
        { unitId, id: order[idx] });
    } catch (e) {
      console.warn('[univer] 翻页失败', e);
    }
  };

  bar.addEventListener('click', (e) => {
    const d = e.target && e.target.dataset && e.target.dataset.d;
    if (d) go(idx + Number(d));
  });

  window.addEventListener('keydown', (e) => {
    if (e.target && /input|textarea/i.test(e.target.tagName || '')) return;
    const k = e.key;
    if (k === 'ArrowRight' || k === 'ArrowDown' || k === 'PageDown' || k === ' ') {
      e.preventDefault(); go(idx + 1);
    } else if (k === 'ArrowLeft' || k === 'ArrowUp' || k === 'PageUp') {
      e.preventDefault(); go(idx - 1);
    } else if (k === 'Home') {
      e.preventDefault(); go(0);
    } else if (k === 'End') {
      e.preventDefault(); go(order.length - 1);
    }
  });

  // 滚轮：整页式翻页（画布本身只会在页内轻微滚动，那样用户以为卡住了）
  let acc = 0;
  let lock = 0;
  window.addEventListener('wheel', (e) => {
    if (Math.abs(e.deltaY) < 2) return;
    e.preventDefault();
    e.stopPropagation();
    const now = Date.now();
    if (now < lock) return;
    acc += e.deltaY;
    if (Math.abs(acc) < 60) return;
    go(idx + (acc > 0 ? 1 : -1));
    acc = 0;
    lock = now + 260;                 // 防止一次滚轮连翻好几页
  }, { passive: false, capture: true });
}

function mountSlides(data, fileName) {
  const { univer, univerAPI } = createUniver({
    locale: LocaleType.ZH_CN,
    locales: {
      [LocaleType.ZH_CN]: mergeLocales(uiZhCN, docsZhCN, slidesZhCN),
    },
    theme: defaultTheme,
    presets: [],
    plugins: [
      [UniverRenderEnginePlugin],
      // 预览是只读的：撤销/字体/颜色那一排按钮用不上，还会占掉顶部 40px。
      // 隐藏后画布从 0 开始，页面能占满整个高度，「上面少一块」也不会再出现。
      [UniverUIPlugin, { container: 'app', ribbonType: 'simple',
                         header: false, toolbar: false }],
      [UniverDocsPlugin],
      [UniverDocsUIPlugin],
      [UniverSlidesPlugin],
      [UniverSlidesUIPlugin],
    ],
  });
  data.id = data.id || 'sl_' + Math.random().toString(36).slice(2, 10);
  data.title = data.title || fileName || '演示文稿';
  hideSlideSidebar(univer);
  const unit = univer.createUnit(UniverInstanceType.UNIVER_SLIDE, data);
  // createUnit 之后 UI 才真正挂载，再关一次确保生效
  setTimeout(() => hideSlideSidebar(univer), 0);
  setTimeout(() => {
    const order = (data.body && data.body.pageOrder) || [];
    mountSlideNav(univer, univerAPI, unit, order, data.id);
  }, 0);
  return univer;
}

/* ------------------------------------------------------------------ 入口 */
const viewSize = () => ({
  vw: Math.max(320, document.documentElement.clientWidth || window.innerWidth || 0),
  vh: Math.max(240, document.documentElement.clientHeight || window.innerHeight || 0),
});

async function loadData() {
  if (qs.get('demo') === 'slides') return demoSlides();
  if (qs.get('demo') === 'sheets') return demoSheets();
  // 幻灯片要把页面尺寸算成"正好铺满可视区"，所以把容器尺寸带给服务端；
  // 表格的快照与窗口无关，多传两个参数不影响。
  const { vw, vh } = viewSize();
  const r = await fetch(`${API}?kkt=${encodeURIComponent(kkt)}&vw=${vw}&vh=${vh}`,
    { cache: 'no-store' });
  // 409 = 缓存已过期（源文件被改过）。重载一次让网关重新转换，
  // 不重载的话用户只能看到"请重新打开文件"这种无解提示。
  if (r.status === 409) {
    const key = 'kk_reload_409';
    if (!sessionStorage.getItem(key)) {
      sessionStorage.setItem(key, '1');
      location.reload();
      return new Promise(() => {});
    }
    throw new Error('文件已更新，请重新打开预览');
  }
  const body = await r.json().catch(() => null);
  if (!r.ok || !body) {
    const msg = (body && (body.detail || body.msg)) || ('HTTP ' + r.status);
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg).slice(0, 160));
  }
  // 网关统一返回 { code, msg, data }，这里拆包
  if (typeof body.code === 'number' && body.code !== 0) {
    throw new Error(body.msg || '接口返回失败');
  }
  return body.data && body.data.kind ? body.data : body;
}

/** 窗口尺寸明显变化时重排一次：页面像素尺寸是加载时按容器算好的。 */
function watchResize() {
  let t = 0;
  const base = viewSize();
  window.addEventListener('resize', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      const now = viewSize();
      if (Math.abs(now.vw - base.vw) > 120 || Math.abs(now.vh - base.vh) > 120) {
        location.reload();
      }
    }, 600);
  });
}

/**
 * 大 PPT 两阶段：先只转前 N 页（十秒内可看），完整版在后台继续转。
 * 页面拿到 partial 就每 4 秒问一次，转完自动重载换成完整版——
 * 否则用户翻到第 N 页会以为文件被截断了。
 */
function watchPartial() {
  const fk = (location.pathname.match(/\/univer\/([0-9a-f]+)/) || [])[1] || '';
  if (!fk) return;
  let n = 0;
  const timer = setInterval(async () => {
    if (++n > 150) { clearInterval(timer); return; }
    try {
      const r = await fetch(`/api/v1/preview/partial?f=${fk}&kkt=${encodeURIComponent(kkt)}`,
        { cache: 'no-store' });
      const d = await r.json();
      if (!d || !d.partial) { clearInterval(timer); location.reload(); }
    } catch (e) { /* 网络抖动忽略，下一轮再问 */ }
  }, 4000);
}

(async () => {
  try {
    if (!kkt && !qs.get('demo')) throw new Error('缺少预览令牌（kkt）');
    tip('正在加载文档…');
    const payload = await loadData();
    // 大表格/超时：网关让我们改用轻量 HTML 视图（openpyxl 按整行建对象太慢）
    if (payload.kind === 'redirect' && payload.url) {
      location.replace(payload.url);
      return;
    }
    const kind = payload.kind === 'slides' ? 'slides' : 'sheets';
    tip(kind === 'slides' ? '正在准备幻灯片…' : '正在准备表格…');
    if (payload.fileName) document.title = payload.fileName;
    if (kind === 'slides') {
      mountSlides(payload.data, payload.fileName);
      watchResize();
      if (payload.partial) watchPartial();
    } else {
      mountSheets(payload.data, payload.fileName);
    }
    mask()?.classList.remove('on');
    sessionStorage.removeItem('kk_reload_409');
  } catch (e) {
    fail(e.message || String(e));
  }
})();

/* ------------------------------------------------------------ 本地自检数据 */
function demoSheets() {
  const rows = ['项目', '数量', '单价', '金额'];
  const cellData = { 0: {} };
  rows.forEach((t, c) => { cellData[0][c] = { v: t, s: 'hdr' }; });
  for (let r = 1; r <= 12; r++) {
    cellData[r] = {
      0: { v: '物料 ' + r },
      1: { v: r * 3, t: 2 },
      2: { v: 12.5 + r, t: 2 },
      3: { f: `=B${r + 1}*C${r + 1}`, v: (r * 3) * (12.5 + r), t: 2 },
    };
  }
  return Promise.resolve({
    kind: 'sheets',
    fileName: '自检表格.xlsx',
    data: {
      sheetOrder: ['s1'],
      styles: {
        hdr: { bl: 1, cl: { rgb: 'rgb(255,255,255)' }, bg: { rgb: 'rgb(47,111,235)' }, ht: 2, vt: 2 },
      },
      sheets: {
        s1: {
          id: 's1', name: 'Sheet1', rowCount: 60, columnCount: 12,
          defaultRowHeight: 24, defaultColumnWidth: 90,
          cellData,
          columnData: { 0: { w: 120 }, 1: { w: 80 }, 2: { w: 90 }, 3: { w: 110 } },
        },
      },
    },
  });
}

function demoSlides() {
  const w = 960, h = 540;
  const pages = {};
  const order = [];
  for (let i = 0; i < 3; i++) {
    const id = 'p' + i;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">`
      + `<rect width="100%" height="100%" fill="#${['ffffff', 'f7f9fc', 'eef4ff'][i]}"/>`
      + `<text x="60" y="200" font-size="56" fill="#1f2d3d">第 ${i + 1} 页</text>`
      + `<rect x="60" y="260" width="${300 + i * 80}" height="24" fill="#2f6feb"/></svg>`;
    pages[id] = {
      id, pageType: 0, zIndex: i, title: '第' + (i + 1) + '页', description: '',
      pageBackgroundFill: { rgb: 'rgb(255,255,255)' },
      pageElements: {
        ['e' + i]: {
          id: 'e' + i, zIndex: 0, left: 0, top: 0, width: w, height: h,
          title: '', description: '', type: 1,
          image: { imageProperties: { contentUrl: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg) } },
        },
      },
    };
    order.push(id);
  }
  return Promise.resolve({
    kind: 'slides',
    fileName: '自检演示.pptx',
    data: { id: 'sl_demo', title: '自检演示', pageSize: { width: w, height: h }, body: { pages, pageOrder: order } },
  });
}

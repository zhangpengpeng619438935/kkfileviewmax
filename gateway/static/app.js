const KEY_RANGE = 'kk_range', KEY_ADMIN = 'kk_admin', KEY_ADMIN_EXP = 'kk_admin_exp';
// 看板对内部开放：不登录就是"访客"，只读全站统计。
// 只有管理接入系统账号需要登录，会话由后端签发，默认 3 天过期。
let adminToken = localStorage.getItem(KEY_ADMIN) || '';
let adminExpire = parseInt(localStorage.getItem(KEY_ADMIN_EXP) || '0', 10);
// 全局统计范围：hour / day / week / month / total，看板所有页面共用
let globalRange = localStorage.getItem(KEY_RANGE) || 'day';
// 审计日志的额外筛选（由图表点击带过来）
let auditFilter = { result: '', orderBy: '', scope: false };
let page = 1, pageSize = 20, auditTotal = 0;
const RANGE_TEXT = { hour: '近 24 小时', day: '近 30 天', week: '近 12 周',
  month: '近 12 个月', total: '近 10 年' };
const charts = {};

function $(id) { return document.getElementById(id); }

/** 顶栏身份：访客 / 管理员（剩 N 天） */
function updateWhoami() {
  const el = $('whoami');
  if (!el) return;
  if (!adminToken) {
    el.textContent = '访客（只读）';
  } else {
    const left = adminExpire ? adminExpire * 1000 - Date.now() : 0;
    let txt = '管理员';
    if (left > 0) {
      const d = Math.ceil(left / 86400000);
      const h = Math.ceil((left % 86400000) / 3600000);
      txt += d > 0 ? '（剩 ' + d + ' 天）' : '（剩 ' + Math.max(h, 1) + ' 小时）';
    }
    el.textContent = txt;
  }
  const out = $('btnAdminOut');
  if (out) out.classList.toggle('hidden', !adminToken);
}

/** 清掉本地管理会话（过期或主动退出） */
function clearAdmin() {
  adminToken = '';
  adminExpire = 0;
  localStorage.removeItem(KEY_ADMIN);
  localStorage.removeItem(KEY_ADMIN_EXP);
  updateWhoami();
}

async function api(path, params) {
  const url = new URL(path, location.origin);
  const query = Object.assign({}, params || {});
  // 所有统计接口统一带上全局时间范围（显式传了 range 的以显式为准）
  if (path.indexOf('/api/v1/stats/') === 0 && query.range === undefined) {
    query.range = globalRange;
  }
  Object.entries(query).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  });
  // 不带任何身份 = 访客（后端按全站只读处理）；登录了管理员才带会话票据
  const headers = {};
  if (adminToken) headers['X-Admin-Token'] = adminToken;
  const res = await fetch(url, { headers });
  const json = await res.json().catch(function () { return {}; });
  if (res.status === 401 || res.status === 403) {
    if (adminToken) {
      clearAdmin();
      setAdminState();
      throw new Error('管理登录已过期（有效期 3 天），请重新登录');
    }
    throw new Error(json.msg || '没有权限查看该数据');
  }
  if (json.code !== 0) throw new Error(json.msg || '接口异常');
  return json.data;
}

function chart(id) {
  if (!charts[id]) charts[id] = echarts.init($(id));
  return charts[id];
}

// 没有任何数据时别画空坐标轴（只剩一条基线 + 图例，看着像坏掉），给一行灰字；
// 同时把 320px 的画布压到 150px，否则两栏面板里各挂一大块空白更难看。
function emptyChart(id, text) {
  const ch = chart(id);
  $(id).style.height = '150px';
  ch.resize();
  ch.clear();
  ch.setOption({
    title: { text: text, left: 'center', top: 'middle',
      textStyle: { color: '#9aa0a6', fontSize: 12.5, fontWeight: 'normal' } }
  });
  return ch;
}

// 有数据时恢复默认高度（上一轮可能是空态）
function fillChart(id) {
  const el = $(id);
  if (el.style.height) { el.style.height = ''; }
  const ch = chart(id);
  ch.resize();
  return ch;
}

function card(label, value, extra, cls, act) {
  return '<div class="card' + (act ? ' clickable' : '') + '"' +
    (act ? ' data-act="' + act + '"' : '') + '>' +
    '<div class="label">' + label + '</div>' +
    '<div class="value ' + (cls || '') + '">' + value + '</div>' +
    '<div class="extra">' + (extra || '') + '</div></div>';
}

/** 卡片点了就跳到审计日志对应数据 */
function bindCardClicks(root) {
  if (!root) return;
  root.querySelectorAll('[data-act]').forEach(function (el) {
    el.onclick = function () {
      var a = el.dataset.act;
      if (a === 'max') gotoAudit({ scope: true, orderBy: 'cost_desc', hint: '按耗时倒序 · 第一行即最慢的一次' });
      else if (a === 'min') gotoAudit({ scope: true, orderBy: 'cost_asc', hint: '按耗时正序 · 第一行即最快的一次' });
      else if (a === 'fail') gotoAudit({ scope: true, result: 'fail', hint: '仅看失败记录' });
    };
  });
}

function table(el, cols, rows, limit) {
  // limit: 单元格最多显示多少字（0 = 不截断，用于"部署信息"这种整段说明）
  if (!rows.length) { el.innerHTML = '<tr><td class="empty">暂无数据</td></tr>'; return; }
  let html = '<thead><tr>' + cols.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
  rows.forEach(r => {
    // 长文本（"涉及系统"里一串系统名）会把列撑爆、溢出到面板外，
    // 这里给完整值挂 title，视觉上由 CSS 截断成省略号。
    // 注意：部分列的调用方本来就传了 HTML（如 "<span class=state>(kk-demo)</span>"），
    // 一律 esc 会把它显示成字面标签，所以只转义纯文本。
    html += '<tr>' + r.map(v => {
      const s = (v === null || v === undefined ? '' : String(v));
      const isHtml = /<[a-zA-Z][^>]*>/.test(s);
      const plain = isHtml ? s.replace(/<[^>]+>/g, '') : s;
      const t = plain.length > 14 ? ' title="' + esc(plain) + '"' : '';
      // CSS 的 max-width 在 table-layout:auto 下对 td 不可靠，长文本仍会把表格
      // 撑出面板；这里按字数截断，全文留在 title 里
      const cap = (limit === undefined ? 30 : limit);
      const shown = (!isHtml && cap > 0 && plain.length > cap)
        ? plain.slice(0, cap) + '…' : s;
      return '<td' + t + '>' + (isHtml ? s : esc(shown)) + '</td>';
    }).join('') + '</tr>';
  });
  el.innerHTML = html + '</tbody>';
}

async function loadSummary() {
  const d = await api('/api/v1/stats/summary');
  const rate = d.total ? Math.round(d.success / d.total * 1000) / 10 : 0;
  $('summaryCards').innerHTML =
    card('访问量', d.total ?? 0,
      (d.rangeLabel || '') + ' · 成功率 ' + rate + '%') +
    card('平均加载耗时', d.e2e_cnt ? (d.avg_cost ?? 0) + ' ms' : '—',
      d.e2e_cnt ? ('申请→内容可见 · 样本 ' + d.e2e_cnt + ' 次')
                : '暂无端到端样本（预览页可见时才有数据）',
      d.avg_cost > 1000 ? 'warn' : 'ok') +
    card('最长加载', d.e2e_cnt ? (d.max_cost ?? 0) + ' ms' : '—', '点击看最慢的一次', 'warn', 'max') +
    card('最快加载', d.e2e_cnt ? (d.min_cost ?? 0) + ' ms' : '—', '点击看最快的一次', 'ok', 'min') +
    card('访问人数', d.uniq_emp ?? 0, '独立工号数') +
    card('涉及文件', d.uniq_file ?? 0, '独立文件数') +
    card('失败次数', d.fail ?? 0, '点击看失败明细', (d.fail ? 'warn' : 'ok'), 'fail') +
    /* 加载超时：探针等满上限仍没看到内容。这类样本不计入加载耗时
       （120 秒是探针参数，不是真实耗时），但用户确实白等了两分钟，必须单列。 */
    card('加载超时', d.timeout_cnt ?? 0,
      d.timeout_cnt ? '打开后 60/120 秒内没看到内容' : '今日暂无超时',
      (d.timeout_cnt ? 'warn' : 'ok'));
  bindCardClicks($('summaryCards'));
}

async function loadTrend() {
  // 粒度由全局统计范围决定（hour→按小时，day→按天，week→按周，month/total→按月）
  const rows = await api('/api/v1/stats/trend');
  chart('chartTrend').setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['访问量', '平均加载耗时'], bottom: 0, itemGap: 14 },
    // top 给双轴名称与曲线尖峰留空间：smooth 的贝塞尔会在峰值处过冲，
    // 原来 top:20 时尖峰被画出绘图区、看着像被截断
    grid: { left: 50, right: 60, top: 34, bottom: 46 },
    xAxis: { type: 'category', data: rows.map(r => r.label) },
    yAxis: [
      { type: 'value', name: '次数', nameGap: 12 },
      { type: 'value', name: 'ms', position: 'right', nameGap: 12,
        scale: true, max: v => Math.ceil(v.max * 1.15) }
    ],
    series: [
      { name: '访问量', type: 'bar', data: rows.map(r => r.total), itemStyle: { color: '#2f6fed' } },
      { name: '平均加载耗时', type: 'line', yAxisIndex: 1, smooth: 0.2,
        symbolSize: 5, data: rows.map(r => r.avg_cost), itemStyle: { color: '#f0a020' } }
    ]
  }, true);
}

async function loadApps() {
  const rows = await api('/api/v1/stats/apps', { top: 10 });
  const ch = chart('chartApps');
  ch.setOption({
    tooltip: { trigger: 'item', formatter: '{b}<br/>{c} 次（{d}%）' },
    // 外标签在 7 个系统时会互相压字（实测左上角三行叠在一起），改由底部图例承载
    legend: { bottom: 0, type: 'scroll', itemWidth: 10, itemHeight: 8,
      textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['42%', '70%'], label: { show: false },
      labelLine: { show: false },
      data: rows.map(r => ({
        // 图例只显示中文名：拼上 "(app_id)" 后 7 个系统一行放不下，
        // 末尾会被截成 "ecn 变..."，反而认不出是谁
        name: r.app_name || r.app_id,
        value: r.total,
        appId: r.app_id          // 点击时用来跳转该系统的调用记录
      }))
    }]
  }, true);
  // 点某个系统 → 直接看它的调用明细（审计日志）
  ch.off('click');
  ch.on('click', function (p) {
    var id = p.data && (p.data.appId || (p.data.name || '').replace(/^.*\(([^)]+)\)$/, '$1'));
    if (id) gotoAudit({ appId: id, scope: true, hint: '该系统在当前统计范围内的调用记录' });
  });
}

/** 跳到审计日志并按条件过滤。opts: {appId, result, orderBy, hint, scope} */
function gotoAudit(opts) {
  if (typeof opts === 'string') opts = { appId: opts };   // 兼容旧调用（只传 appId）
  opts = opts || {};
  $('fApp').value = opts.appId || '';
  $('fEmp').value = '';
  $('fKeyword').value = '';
  $('fFrom').value = '';
  $('fTo').value = '';
  // scope=true 表示"跟随看板顶部的统计范围"，点图表进来必须带上，
  // 否则卡片上写 7 次失败，日志里却是历史全部失败，对不上号。
  auditFilter = { result: opts.result || '', orderBy: opts.orderBy || '',
                  scope: !!opts.scope };
  auditFilter.hint = opts.hint || '';
  document.querySelector('.tab[data-view="audit"]').click();
  page = 1;
  loadAudit().catch(function (e) {
    $('lastUpdate').textContent = '查询失败: ' + e.message;
  });
}

/** 失败原因明细（跟随全局统计范围；范围=总计时最多回溯 180 天） */
async function showFailReasons() {
  $('failRange').textContent = '加载中…';
  const rows = await api('/api/v1/stats/errors', { top: 50 });
  const failTotal = rows.reduce((s, r) => s + (r.cnt || 0), 0);
  $('failRange').textContent = RANGE_TEXT[globalRange] + ' · ' +
    rows.length + ' 类 / 共 ' + failTotal + ' 次';
  table($('tableFails'), ['状态码', '失败原因', '次数', '影响工号', '涉及系统', '最后发生'],
    rows.map(r => [r.status_code, r.error_msg || '(无描述)', r.cnt,
      r.uniq_emp, r.apps || '', r.last_at]));
}

/** 全局范围 → 分布图的聚合粒度（十年不可能按小时画，会变成 8 万个点） */
const RANGE_GRAN = { hour: 'hour', day: 'day', week: 'week', month: 'month', total: 'month' };
const GRAN_TEXT = { hour: '按小时', day: '按天', week: '按周', month: '按月' };

/** 分布图的横轴标签：小时只留 HH:MM，日期只留 MM-DD，其余原样 */
function trendLabel(label, gran) {
  const s = String(label || '');
  if (gran === 'hour') return s.slice(11, 16);
  if (gran === 'day') return s.slice(5);
  return s;
}

async function loadHour() {
  const gran = RANGE_GRAN[globalRange] || 'day';
  // 分布图跟随全局范围：近24小时→按小时，近30天→按天，近12周→按周，按月/总计→按月
  const rows = await api('/api/v1/stats/trend', { granularity: gran });
  $('trafficTitle').textContent = '访问分布 · ' + RANGE_TEXT[globalRange];
  $('trafficScope').textContent = GRAN_TEXT[gran] + ' · 共 ' +
    rows.reduce((s, r) => s + (r.total || 0), 0) + ' 次';
  chart('chartHour').setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: rows.map(r => trendLabel(r.label, gran)) },
    yAxis: { type: 'value' },
    series: [{ type: 'line', smooth: true, areaStyle: {}, data: rows.map(r => r.total),
      itemStyle: { color: '#2f6fed' } }]
  }, true);

  // 成功/失败取自 summary（与总览卡片同一个口径、同一次查询），
  // 之前是从"24 小时趋势"里累加的，所以切范围时这个饼图纹丝不动。
  const s = await api('/api/v1/stats/summary');
  const ok = s.success || 0, fail = s.fail || 0;
  $('statusScope').textContent = RANGE_TEXT[globalRange] + ' · 点击扇区看明细';
  const st = chart('chartStatus');
  st.setOption({
    tooltip: { trigger: 'item' }, legend: { bottom: 0 },
    series: [{ type: 'pie', radius: '65%', data: [
      { name: '成功', value: ok, itemStyle: { color: '#18a058' } },
      { name: '失败', value: fail, itemStyle: { color: '#d03050' } }] }]
  }, true);
  // 点扇区 → 审计日志，只保留该范围、该结果，并把命中的行全部标黄
  st.off('click');
  st.on('click', function (p) {
    gotoAudit(p.name === '失败'
      ? { scope: true, result: 'fail', hint: '仅看失败记录' }
      : { scope: true, result: 'success', hint: '仅看成功记录' });
  });
}

async function loadEmp() {
  const rows = await api('/api/v1/stats/employees', { top: 20 });
  table($('tableEmp'),
    ['工号', '姓名', '部门', '来源系统', '访问次数', '涉及文件', '平均加载耗时ms', '最后访问'],
    rows.map(r => [r.emp_no, r.emp_name, r.dept,
      appLabel(r.app_name, r.app_id),
      r.cnt, r.files, r.avg_cost, r.last_at]));
}

/** 系统名称 + appId（没有名称就只显示 id） */
function appLabel(name, id) {
  if (!name) return id || '';
  if (!id || name === id) return name;
  return name + ' <span class="state">(' + id + ')</span>';
}

/** 文件名在库里可能存的是 URL 编码串（"01%C2%A0%EF%BC%9A..."），显示前解一次 */
function fileName(s) {
  if (!s) return '';
  try { return decodeURIComponent(s); } catch (e) { return s; }
}

async function loadFiles() {
  // 历史数据里有一条 file_key 为空的聚合行（早期没记 fileKey），
  // 文件名是空的，留在表里就是一行空白，直接剔掉
  const rows = (await api('/api/v1/stats/files', { top: 20 }))
    .filter(r => r.file_name);
  const cost = v => (v === null || v === undefined ? '—' : v);
  table($('tableFiles'),
    ['文件名', '类型', '访问系统', '浏览次数', '访问人数', '平均ms', '最大ms', '最小ms', '最后访问'],
    rows.map(r => [fileName(r.file_name), r.file_type, r.apps || '', r.view_cnt, r.uniq_emp,
      cost(r.avg_cost), cost(r.max_cost), cost(r.min_cost), r.last_view_at]));
  const top = rows.slice(0, 10);
  chart('chartFiles').setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 230, right: 40, top: 20, bottom: 30 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', axisLabel: { fontSize: 11 },
      data: top.map(r => {
        const n = fileName(r.file_name);
        return n.length > 26 ? n.slice(0, 26) + '…' : n;
      }).reverse() },
    series: [{ type: 'bar', data: top.map(r => r.view_cnt).reverse(),
      itemStyle: { color: '#2f6fed' } }]
  }, true);
}

async function loadLatency() {
  const d = await api('/api/v1/stats/latency');
  const s = d.summary || {}, p = d.percentile || {};
  const has = (s.cnt ?? 0) > 0;      // 只有"预览页可见已上报"的记录才参与，没样本就显示 —
  $('latencyCards').innerHTML =
    card('平均加载耗时', has ? (s.avg_cost ?? 0) + ' ms' : '—', '样本 ' + (s.cnt ?? 0) + ' 次') +
    card('最长加载', has ? (s.max_cost ?? 0) + ' ms' : '—', '点击看最慢的一次', 'warn', 'max') +
    card('最快加载', has ? (s.min_cost ?? 0) + ' ms' : '—', '点击看最快的一次', 'ok', 'min') +
    card('P50', has ? (p.p50 ?? 0) + ' ms' : '—', '中位加载耗时') +
    card('P95', has ? (p.p95 ?? 0) + ' ms' : '—', '95 分位', (p.p95 > 3000 ? 'err' : '')) +
    card('P99', has ? (p.p99 ?? 0) + ' ms' : '—', '99 分位', (p.p99 > 5000 ? 'err' : ''));
  bindCardClicks($('latencyCards'));
  const h = d.histogram || [];
  // 桶有 20 多个，每个都标 "1400ms+" 会斜着叠成一片：只标数字、隔一个显示，
  // 单位放到 tooltip 里
  const labels = h.map(b => b.lo);
  // 桶有 30 个左右，固定隔一个还是密成一团：按数量抽稀到最多 8 个刻度，
  // 单位写在轴名上（原来每个标签都带 "ms+"，斜 30° 后叠成一片灰边）
  const step = Math.max(1, Math.ceil(labels.length / 8));
  chart('chartHist').setOption({
    tooltip: { trigger: 'axis',
      formatter: p => '≥ ' + p[0].axisValue + ' ms<br/>' + p[0].value + ' 次' },
    grid: { left: 50, right: 20, top: 24, bottom: 58 },
    xAxis: { type: 'category', data: labels, name: '载入耗时(ms)', nameGap: 26,
      nameLocation: 'middle',
      axisLabel: { rotate: 30, interval: step - 1, fontSize: 10 } },
    yAxis: { type: 'value', name: '次' },
    series: [{ type: 'bar', data: h.map(b => b.cnt), itemStyle: { color: '#2f6fed' } }]
  }, true);
  const errs = await api('/api/v1/stats/errors', { top: 8 });
  // 错误文案很长（"400 文件地址不可访问: HTTP 404"），直接当饼图名会：
  // ① 外标签互相压字 ② 图例两行被截断。名称压成短词，原文放 tooltip。
  const short = m => {
    m = m || '';
    if (m.indexOf('加密') >= 0) return '文档加密';
    if (m.indexOf('不可访问') >= 0 || m.indexOf('404') >= 0) return '源文件不可达';
    if (m.indexOf('限流') >= 0) return '限流';
    if (m.indexOf('超时') >= 0) return '超时';
    if (m.indexOf('转换') >= 0 || m.indexOf('转码') >= 0) return '转换失败';
    return (m.split(/[:：]/)[0] || '其它').slice(0, 10);
  };
  chart('chartErrors').setOption({
    tooltip: { trigger: 'item', confine: true,
      formatter: p => p.name + '<br/>' + (p.data.full || '') + '<br/>'
        + p.value + ' 次（' + p.percent + '%）' },
    legend: { bottom: 0, type: 'scroll', itemWidth: 10, itemHeight: 8,
      textStyle: { fontSize: 11 } },
    series: [{ type: 'pie', radius: ['45%', '70%'],
      label: { show: false }, labelLine: { show: false },
      data: errs.map(e => ({
        name: e.status_code + ' ' + short(e.error_msg),
        value: e.cnt,
        full: e.error_msg || '' })) }]
  }, true);
}

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function loadAudit() {
  const scopeRange = auditFilter.scope ? globalRange : '';
  const d = await api('/api/v1/audit/logs', {
    empNo: $('fEmp').value, appId: $('fApp').value,
    keyword: $('fKeyword').value, from: $('fFrom').value, to: $('fTo').value,
    result: auditFilter.result, orderBy: auditFilter.orderBy, range: scopeRange,
    page, size: pageSize
  });
  auditTotal = d.total;
  renderAuditTable(d);
  // 顶部提示当前筛选，否则用户不知道为啥只剩这几条
  const bits = [];
  if (scopeRange) bits.push('统计范围 ' + RANGE_TEXT[scopeRange]);
  if ($('fApp').value) bits.push('系统 ' + $('fApp').value);
  if (auditFilter.result === 'fail') bits.push('仅失败');
  if (auditFilter.result === 'success') bits.push('仅成功');
  if (auditFilter.orderBy) bits.push(auditFilter.orderBy === 'cost_desc' ? '按耗时倒序' : '按耗时正序');
  if ($('fEmp').value) bits.push('工号含 ' + $('fEmp').value);
  if ($('fKeyword').value) bits.push('文件名含 ' + $('fKeyword').value);
  $('auditHint').innerHTML = bits.length
    ? '当前筛选：' + bits.join(' · ') + '　共 ' + d.total + ' 条'
      + (auditFilter.hint ? '　<span class="goldtip">' + esc(auditFilter.hint) + '</span>' : '')
      + (auditFilter.result === 'fail'
        ? '　<span class="state">失败明细取自访问明细（每次失败都有一条，不受审计去重影响）</span>'
        : '')
    : '';
  $('pageInfo').textContent = '第 ' + d.page + ' 页 / 共 ' + d.total + ' 条';
}

/** 审计表格：失败行整行标黄、结果格可点开详情弹窗 */
function renderAuditTable(d) {
  const el = $('tableAudit');
  if (!d.list.length) {
    el.innerHTML = '<tr><td class="empty">该条件下没有记录</td></tr>';
    return;
  }
  // 状态码只有访问明细才有（审计表不存）；没有这一列时不占位，避免整列"—"
  const showCode = d.source === 'access';
  const cols = ['时间', '工号', '姓名', '部门', '系统', '文件名', '结果']
    .concat(showCode ? ['状态码'] : []).concat(['IP', '加载耗时ms']);
  // 圈重点：按耗时排序时只标第一行（极值那一条）；按失败筛选时全标（就是卡片上那几条）
  const markAll = auditFilter.result === 'fail' && !auditFilter.orderBy;
  let html = '<thead><tr>' + cols.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
  d.list.forEach(function (r, i) {
    const fail = r.result !== 'success';
    const gold = auditFilter.orderBy ? i === 0 : markAll;
    const tip = fail ? '点击查看失败详情' : '';
    html += '<tr' + (gold ? ' class="gold"' : '')
      + (gold && auditFilter.orderBy
        ? ' title="' + (auditFilter.orderBy === 'cost_desc' ? '最慢的一次' : '最快的一次') + '"'
        : '') + '>'
      + '<td>' + esc(r.created_at) + '</td>'
      + '<td>' + esc(r.emp_no) + '</td>'
      + '<td>' + esc(r.emp_name) + '</td>'
      + '<td>' + esc(r.dept) + '</td>'
      + '<td>' + esc(r.app_id) + '</td>'
      + '<td title="' + esc(r.file_name) + '">' + esc((r.file_name || '').slice(0, 40)) + '</td>'
      + '<td>' + (fail
        ? '<span class="resTag fail" data-aid="' + r.id + '" data-src="' + (d.source || 'audit')
          + '" title="' + esc(tip) + '">失败</span>'
        : '<span class="resTag ok">成功</span>') + '</td>'
      + (showCode ? '<td>' + esc(r.status_code || (fail ? '—' : 200)) + '</td>' : '')
      + '<td>' + esc(r.client_ip) + '</td>'
      + '<td>' + esc(r.cost_ms) + '</td>'
      + '</tr>';
  });
  el.innerHTML = html + '</tbody>';
  el.querySelectorAll('.resTag.fail').forEach(function (t) {
    t.onclick = function () { openFailDetail(t.dataset.aid, t.dataset.src); };
  });
}

// ---------------- 服务告警（容量/压力维度，近 30 天） ----------------
let saPage = 1;
const saSize = 50;
let saTotal = 0;
let saKind = '';           // 点饼图扇区设置的类型筛选
let saKindsFilled = false;

const SA_COLOR = {
  rate_multi: '#f0a020', rate_storm: '#d9534f', queue_timeout: '#8a63d2',
  load_timeout: '#5b8ff9', cpu_high: '#e8684a', mem_high: '#b3261e',
  disk_low: '#c9a227', cache_big: '#8a9099', queue_backlog: '#e8873a',
  engine_error: '#bf1f3f'
};

function levelTag(lv) {
  const critical = lv === 'critical';
  const c = critical ? '#d9534f' : '#f0a020';
  return '<span class="resTag" style="color:' + c + ';border-color:' + c + '33;'
    + 'background:' + c + '14">' + (critical ? '严重' : '警告') + '</span>';
}

function durText(min) {
  if (min == null) return '-';
  if (min < 60) return min + ' 分钟';
  if (min < 1440) return (min / 60).toFixed(1) + ' 小时';
  return (min / 1440).toFixed(1) + ' 天';
}

async function loadServiceAlerts() {
  const d = await api('/api/v1/stats/alerts', {
    days: $('saDays').value, kind: saKind, level: $('saLevel').value,
    status: $('saStatus').value, keyword: $('saKeyword').value,
    page: saPage, size: saSize
  });
  saTotal = d.total;
  const s = d.summary || {};
  $('saCards').innerHTML =
    card('当前未恢复', s.open || 0, '条件仍在持续', (s.open ? 'warn' : 'ok')) +
    card('其中严重', s.openCritical || 0, '需要立即处理', (s.openCritical ? 'warn' : 'ok')) +
    card('近 ' + d.days + ' 天告警', s.total || 0, '今日新增 ' + (s.today || 0) + ' 条') +
    card('最长持续', durText(s.longestMin || 0), '单条告警从触发到现在');

  // 类型下拉：首次加载后用后端返回的类型填充，之后保持（避免每轮重建导致选择丢失）
  if (!saKindsFilled && d.byKind.length) {
    const sel = $('saKind');
    const cur = sel.value;
    d.byKind.forEach(function (r) {
      const o = document.createElement('option');
      o.value = r.kind; o.textContent = r.title || r.kind;
      sel.appendChild(o);
    });
    sel.value = cur;
    saKindsFilled = true;
  }

  if (!d.byDay.length) {
    emptyChart('chartSaDay', '近 ' + d.days + ' 天没有服务告警');
  } else {
    fillChart('chartSaDay').setOption({
      tooltip: { trigger: 'axis' },
      legend: { bottom: 0 },
      grid: { left: 48, right: 16, top: 16, bottom: 56 },
      xAxis: { type: 'category', data: d.byDay.map(r => r.d) },
      yAxis: { type: 'value', name: '条' },
      series: [
        { name: '严重', type: 'bar', stack: 'a',
          data: d.byDay.map(r => r.crit || 0), itemStyle: { color: '#d9534f' } },
        { name: '警告', type: 'bar', stack: 'a',
          data: d.byDay.map(r => (r.n || 0) - (r.crit || 0)), itemStyle: { color: '#f0a020' } }
      ]
    }, true);
  }

  if (!d.byKind.length) {
    emptyChart('chartSaKind', '暂无告警类型').off('click');
  } else {
    const ch = fillChart('chartSaKind');
    ch.setOption({
      tooltip: { trigger: 'item', formatter: '{b}：{c} 条（{d}%）' },
      legend: { bottom: 0, type: 'scroll' },
      series: [{
        type: 'pie', radius: ['40%', '66%'],
        label: { formatter: '{b}\n{c}' },
        data: d.byKind.map(r => ({ name: r.title || r.kind, value: r.n,
          itemStyle: { color: SA_COLOR[r.kind] || '#8a9099' } }))
      }]
    }, true);
    ch.off('click');
    ch.on('click', function (p) {
      // 饼图里显示的是中文名，筛选要用 kind，这里反查一次
      const hit = d.byKind.find(r => (r.title || r.kind) === p.name);
      saKind = (hit && saKind !== hit.kind) ? hit.kind : '';
      saPage = 1;
      $('saKind').value = saKind;
      loadServiceAlerts();
    });
  }

  renderSaTable(d);
  $('saPageInfo').textContent = '第 ' + d.page + ' 页 / 共 ' + d.total + ' 条';
  const bits = ['近 ' + d.days + ' 天'];
  if (saKind) bits.push('类型 ' + saKind + '（再点扇区可取消）');
  if ($('saLevel').value) bits.push('级别 ' + ($('saLevel').value === 'critical' ? '严重' : '警告'));
  if ($('saStatus').value) bits.push('状态 ' + ($('saStatus').value === 'open' ? '未恢复' : '已恢复'));
  if ($('saKeyword').value) bits.push('关键字 ' + $('saKeyword').value);
  $('saHint').textContent = '当前筛选：' + bits.join(' · ');
}

function renderSaTable(d) {
  const el = $('tableSa');
  if (!d.list.length) {
    el.innerHTML = '<tr><td class="empty">该条件下没有服务告警</td></tr>';
    return;
  }
  const cols = ['触发时间', '类型', '级别', '对象', '当前值', '峰值 / 阈值',
                '持续', '状态', '详情'];
  let html = '<thead><tr>' + cols.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
  d.list.forEach(function (r) {
    const peak = (r.peak != null && r.peak !== r.value ? '峰值 ' + r.peak + ' / ' : '')
      + '阈值 ' + r.threshold + (r.unit ? ' ' + r.unit : '');
    html += '<tr>'
      + '<td>' + esc(r.first_at) + '</td>'
      + '<td>' + esc(r.title) + '</td>'
      + '<td>' + levelTag(r.level) + '</td>'
      + '<td>' + esc(r.target) + '</td>'
      + '<td>' + esc(r.value) + ' <span class="state">' + esc(r.unit || '') + '</span></td>'
      + '<td class="state">' + esc(peak) + '</td>'
      + '<td>' + durText(r.durationMin) + '</td>'
      + '<td>' + (r.status === 'open'
        ? '<span class="resTag fail">未恢复</span>'
        : '<span class="resTag">已恢复</span>') + '</td>'
      + '<td title="' + esc(r.detail) + '">' + esc((r.detail || '').slice(0, 52)) + '</td>'
      + '</tr>';
  });
  el.innerHTML = html + '</tbody>';
}

// ---------------- 预览失败原因（近 180 天） ----------------
let alertPage = 1;
const alertSize = 50;
let alertTotal = 0;
let alertKind = '';        // 点饼图扇区设置的类型筛选

const KIND_COLOR = {
  '限流': '#f0a020', '加密文件': '#8a63d2', '源文件不可达': '#d9534f',
  '超时': '#e8873a', '服务异常': '#b3261e', '转换失败': '#c9a227', '其它': '#8a9099'
};

function kindTag(kind) {
  const c = KIND_COLOR[kind] || '#8a9099';
  return '<span class="resTag" style="color:' + c + ';border-color:' + c + '33;'
    + 'background:' + c + '14">' + esc(kind) + '</span>';
}

async function loadAlerts() {
  const d = await api('/api/v1/stats/failures', {
    days: $('alertDays').value, appId: $('aApp').value, empNo: $('aEmp').value,
    status: $('aStatus').value, keyword: $('aKeyword').value,
    kind: alertKind, page: alertPage, size: alertSize
  });
  alertTotal = d.total;
  $('alertCards').innerHTML =
    card('告警总数', d.summary.total, '近 ' + d.days + ' 天') +
    card('今日新增', d.summary.today, '今天 0 点起', d.summary.today ? 'warn' : 'ok') +
    card('涉及文件', d.summary.files, '个不同文件') +
    card('涉及范围', d.summary.apps + ' / ' + d.summary.emps, '个系统 · 个工号');

  if (!d.byDay.length) {
    emptyChart('chartAlertDay', '近 ' + d.days + ' 天没有预览失败');
  } else {
    fillChart('chartAlertDay').setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 48, right: 16, top: 16, bottom: 46 },
      xAxis: { type: 'category', data: d.byDay.map(r => r.d), axisLabel: { rotate: 40 } },
      yAxis: { type: 'value', name: '条' },
      series: [{ name: '失败', type: 'bar', data: d.byDay.map(r => r.n),
        itemStyle: { color: '#d9534f' } }]
    }, true);
  }

  if (!d.byKind.length) {
    emptyChart('chartAlertKind', '暂无失败类型').off('click');
  } else {
    const ch = fillChart('chartAlertKind');
    ch.setOption({
      tooltip: { trigger: 'item', formatter: '{b}：{c} 条（{d}%）' },
      legend: { bottom: 0 },
      series: [{
        type: 'pie', radius: ['42%', '68%'],
        label: { formatter: '{b}\n{c}' },
        data: d.byKind.map(r => ({ name: r.kind, value: r.n,
          itemStyle: { color: KIND_COLOR[r.kind] || '#8a9099' } }))
      }]
    }, true);
    ch.off('click');
    ch.on('click', function (p) {
      alertKind = (alertKind === p.name ? '' : p.name);
      alertPage = 1;
      loadAlerts();
    });
  }

  renderAlertTable(d);
  $('aPageInfo').textContent = '第 ' + d.page + ' 页 / 共 ' + d.total + ' 条';
  const bits = ['近 ' + d.days + ' 天'];
  if (alertKind) bits.push('类型 ' + alertKind + '（再点扇区可取消）');
  if ($('aApp').value) bits.push('系统 ' + $('aApp').value);
  if ($('aEmp').value) bits.push('工号含 ' + $('aEmp').value);
  if ($('aStatus').value) bits.push('状态码 ' + $('aStatus').value);
  if ($('aKeyword').value) bits.push('关键字 ' + $('aKeyword').value);
  $('alertHint').textContent = '当前筛选：' + bits.join(' · ');
}

function renderAlertTable(d) {
  const el = $('tableAlerts');
  if (!d.list.length) {
    el.innerHTML = '<tr><td class="empty">该条件下没有告警</td></tr>';
    return;
  }
  const cols = ['时间', '系统', '工号', '姓名', '文件名', '类型', '状态码', '原因', 'IP'];
  let html = '<thead><tr>' + cols.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
  d.list.forEach(function (r) {
    html += '<tr>'
      + '<td>' + esc(r.created_at) + '</td>'
      + '<td>' + esc(r.app_id) + '</td>'
      + '<td>' + esc(r.emp_no) + '</td>'
      + '<td>' + esc(r.emp_name) + '</td>'
      + '<td title="' + esc(r.file_name) + '">' + esc((r.file_name || '').slice(0, 34)) + '</td>'
      + '<td>' + kindTag(r.kind) + '</td>'
      + '<td>' + esc(r.status_code) + '</td>'
      + '<td title="' + esc(r.error_msg) + '">' + esc((r.error_msg || '').slice(0, 46)) + '</td>'
      + '<td>' + esc(r.client_ip) + '</td>'
      + '</tr>';
  });
  el.innerHTML = html + '</tbody>';
}

// ---------------- 失败详情弹窗 ----------------
async function openFailDetail(id, source) {
  const box = $('failModalBody');
  $('failModalTitle').textContent = '失败详情';
  box.innerHTML = '<p class="state" style="padding:12px 0">加载中…</p>';
  $('failModal').classList.remove('hidden');
  try {
    const d = await api('/api/v1/audit/detail', { id: id, source: source || 'audit' });
    renderFailDetail(d);
  } catch (e) {
    box.innerHTML = '<p class="msgbox" style="margin-top:12px">' + esc(e.message) + '</p>';
  }
}

function closeFailDetail() {
  $('failModal').classList.add('hidden');
}

function renderFailDetail(d) {
  const fail = !d.ok;
  const code = d.statusCode || (fail ? '失败' : 200);
  const msg = d.errorMsg || (fail
    ? '该记录没有留下错误描述（失败可能发生在网关转发之前）' : '调用成功，没有错误信息');
  let html = '<div class="msgbox' + (fail ? '' : ' ok') + '" style="margin-top:12px">'
    + '<span class="badgeCode' + (fail ? '' : ' ok') + '">' + esc(code) + '</span>'
    + esc(msg) + '</div>';
  const rows = [
    ['时间', esc(d.created_at)],
    ['工号', esc(d.emp_no) + (d.emp_name ? '　' + esc(d.emp_name) : '')
      + (d.dept ? '　<span class="state">' + esc(d.dept) + '</span>' : '')],
    ['来源系统', esc(d.app_id)],
    ['文件名', esc(d.file_name)],
    ['文件地址', d.fileUrl ? '<span class="mono">' + esc(d.fileUrl) + '</span>' : ''],
    ['动作', esc(d.action)],
    ['加载耗时', esc(d.cost_ms) + ' ms'
      + (d.upstreamMs ? '　<span class="state">引擎侧 ' + esc(d.upstreamMs) + ' ms</span>' : '')],
    ['缓存命中', d.cacheHit ? '是（直接返回已转好的结果）' : '否'],
    ['客户端 IP', esc(d.client_ip)],
    ['User-Agent', d.userAgent ? '<span class="mono">' + esc(d.userAgent) + '</span>' : ''],
    ['请求 ID', d.requestId ? '<span class="mono">' + esc(d.requestId) + '</span>' : ''],
  ].filter(function (r) { return r[1] !== ''; });
  html += '<table class="dl">' + rows.map(function (r) {
    return '<tr><th>' + r[0] + '</th><td>' + r[1] + '</td></tr>';
  }).join('') + '</table>';
  $('failModalBody').innerHTML = html;
  $('failModalTitle').textContent = fail ? '失败详情' : '调用详情';
}

// ---------------- 管理员: 接入系统账号管理 ----------------
let appRows = [];
let secretVisible = {};

async function apiAdmin(path, method, payload) {
  const res = await fetch(path, {
    method: method || 'GET',
    headers: { 'X-Admin-Token': adminToken, 'Content-Type': 'application/json' },
    body: payload ? JSON.stringify(payload) : undefined
  });
  const j = await res.json().catch(function () { return {}; });
  if (res.status === 401 || res.status === 403) {
    clearAdmin();
    setAdminState();
    throw new Error('管理登录已过期（有效期 3 天），请重新登录');
  }
  if (j.code !== 0) throw new Error(j.msg || '管理接口异常');
  return j.data;
}

function setAdminState() {
  const logged = !!adminToken;
  $('adminBody').classList.toggle('hidden', !logged);
  $('adminLoginBox').classList.toggle('hidden', logged);
  $('adminState').textContent = logged
    ? '已登录 · ' + (adminExpire
        ? new Date(adminExpire * 1000).toLocaleString('zh-CN') + ' 到期'
        : '3 天有效')
    : '未登录';
  updateWhoami();
}

/** 管理员登录：拿长期口令换 3 天会话票据，浏览器只存票据不存口令 */
async function adminLogin(val) {
  val = (val || '').trim();
  if (!val) { alert('请输入管理员令牌'); return; }
  try {
    const res = await fetch('/api/v1/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: val })
    });
    const j = await res.json().catch(function () { return {}; });
    if (j.code !== 0) throw new Error(j.msg || ('HTTP ' + res.status));
    adminToken = j.data.adminSession;
    adminExpire = j.data.expiresAt || 0;
    localStorage.setItem(KEY_ADMIN, adminToken);
    localStorage.setItem(KEY_ADMIN_EXP, String(adminExpire));
  } catch (e) {
    alert('登录失败：' + e.message);
    return;
  }
  $('adminToken').value = '';
  setAdminState();
  loadAppAccounts();
  refreshAll();
}

function adminLogout() {
  clearAdmin();
  setAdminState();
  $('lastUpdate').textContent = '已退出管理，当前为访客（只读）';
}

function maskSecret(s) {
  return s && s.length > 12 ? s.slice(0, 8) + '****' + s.slice(-4) : '****';
}

function copyText(t) {
  const ta = document.createElement('textarea');
  ta.value = t;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (e) { /* 忽略 */ }
  document.body.removeChild(ta);
}

async function loadAppAccounts() {
  try {
    appRows = await apiAdmin('/api/v1/admin/apps');
    renderApps();
  } catch (e) {
    $('tableApps').innerHTML = '<tr><td class="empty">' + e.message + '</td></tr>';
  }
}

function renderApps() {
  const rows = appRows.map(a => {
    const show = secretVisible[a.app_id];
    const secretCell = '<div class="secret"><code>' +
      (show ? a.app_secret : maskSecret(a.app_secret)) + '</code>' +
      '<button class="ghost" data-act="show" data-id="' + a.app_id + '">' +
      (show ? '隐藏' : '显示') + '</button>' +
      '<button class="ghost" data-act="copy" data-id="' + a.app_id + '">复制</button></div>';
    const ops = '<div class="op">' +
      '<button class="ghost" data-act="reset" data-id="' + a.app_id + '">重置密钥</button>' +
      '<button class="ghost" data-act="toggle" data-id="' + a.app_id + '">' +
      (a.enabled ? '禁用' : '启用') + '</button>' +
      '<button class="ghost" data-act="del" data-id="' + a.app_id + '">删除</button></div>';
    return [a.app_id, a.app_name || '', secretCell, a.qps_limit,
      a.ip_whitelist || '不限制',
      a.enabled ? '<span class="tag up">启用</span>' : '<span class="tag down">停用</span>',
      a.created_at, ops];
  });
  table($('tableApps'),
    ['系统标识', '系统名称', 'appSecret', 'QPS 限额', 'IP 白名单', '状态', '创建时间', '操作'], rows);
  $('tableApps').querySelectorAll('button[data-act]').forEach(b => {
    b.onclick = () => onAppAction(b.dataset.act, b.dataset.id);
  });
}

async function onAppAction(act, id) {
  try {
    if (act === 'show') {
      secretVisible[id] = !secretVisible[id];
      renderApps();
      return;
    }
    if (act === 'copy') {
      const row = appRows.find(x => x.app_id === id);
      copyText(row ? row.app_secret : '');
      return;
    }
    if (act === 'reset') {
      if (!confirm('重置后该系统的旧密钥立即失效, 需重新分发。确定重置 ' + id + ' 的密钥?')) return;
      const d = await apiAdmin('/api/v1/admin/app/' + id + '/reset-secret', 'POST');
      const row = appRows.find(x => x.app_id === id);
      if (row) { row.app_secret = d.appSecret; secretVisible[id] = true; }
      renderApps();
      return;
    }
    if (act === 'toggle') {
      const d = await apiAdmin('/api/v1/admin/app/' + id + '/toggle', 'POST');
      const row = appRows.find(x => x.app_id === id);
      if (row) row.enabled = d.enabled;
      renderApps();
      return;
    }
    if (act === 'del') {
      if (!confirm('删除后该系统无法调用预览服务(历史统计数据保留)。确定删除 ' + id + '?')) return;
      await apiAdmin('/api/v1/admin/app/' + id, 'DELETE');
      loadAppAccounts();
      return;
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function createApp() {
  const payload = {
    appId: $('fAppId').value.trim(),
    appName: $('fAppName').value.trim(),
    appSecret: $('fAppSecret').value.trim(),
    qpsLimit: parseInt($('fQps').value || '60', 10),
    ipWhitelist: $('fIp').value.trim()
  };
  if (!payload.appId) { alert('请填写系统标识 appId'); return; }
  try {
    const d = await apiAdmin('/api/v1/admin/app', 'POST', payload);
    $('createMsg').textContent = (d.action === 'created' ? '已创建, 密钥: ' : '已更新, 密钥: ') + d.appSecret;
    $('fAppSecret').value = d.appSecret;
    loadAppAccounts();
  } catch (e) {
    $('createMsg').textContent = '失败: ' + e.message;
  }
}

async function loadHealth() {
  const d = await api('/api/v1/health/detail');
  const svc = d.services || [];
  const bar = svc.map(s =>
    '<span class="hb-item"><span class="dot ' + s.status + '"></span>' +
    '<span class="hb-name">' + s.name + '</span>' +
    '<span class="hb-ms">' + (s.latencyMs ? s.latencyMs + 'ms' : '') + '</span></span>').join('');
  const sum = '<span class="hb-sum">' + d.upCount + '/' + d.total + ' 正常 · ' +
    (d.metrics ? d.metrics.checkedAt : '') + '</span>';
  $('healthBar').innerHTML = bar + sum;

  $('healthCards').innerHTML = svc.map(s =>
    card(s.name, '<span class="tag ' + s.status + '">' +
      (s.status === 'up' ? '正常' : '异常') + '</span>',
      s.detail || '', '')).join('');

  table($('tableHealth'), ['服务', '状态', '响应耗时', '说明'],
    svc.map(s => [s.name,
      '<span class="tag ' + s.status + '">' + (s.status === 'up' ? '正常' : '异常') + '</span>',
      (s.latencyMs || 0) + ' ms', s.detail || '']), 0);

  const m = d.metrics || {};
  $('healthMetrics').innerHTML =
    card('转码队列', m.convertingQueue ?? 0, '并发上限 ' + (m.maxConcurrentConvert ?? '-')) +
    card('已完成转码', m.readyTasks ?? 0, '累计 ready 任务 · 覆盖 ' + (m.readyFiles ?? 0) + ' 个文件') +
    // 只数 failed 记录会偏大（同文件失败两次算两条、且永远留着），
    // failedFiles 是"去重且至今没有任何成功产物"的，才是真打不开的。
    // 状态只看最近 24 小时有没有新增 —— 历史遗留一直标红，红久了就没人看了。
    card('打不开的文件', m.failedFiles ?? 0,
         '近 24 小时新增 ' + (m.failedRecent ?? 0),
         ((m.failedRecent ?? 0) > 0 ? 'warn' : 'ok')) +
    card('今日请求', m.todayRequests ?? 0, '明细表当日计数') +
    card('今日失败', m.todayErrors ?? 0, '成功=0 的请求数', (m.todayErrors > 0 ? 'warn' : 'ok'));

  table($('tableDeploy'), ['项', '值'], [
    ['服务地址', location.origin],
    ['预览引擎', '自研预览引擎（内核：kkFileView）· kkfileview:8012（仅容器内网，不对外暴露）'],
    ['统计库', 'MySQL 8 · 宿主 13306'],
    ['缓存', 'Redis 7 · 宿主 16379'],
    ['限流', '全局 120 QPS / 单系统 60 QPS / 单工号 20 QPS'],
    ['检查时间', m.checkedAt || '']
  ], 0);
}

async function refreshAll() {
  try {
    await loadHealth();
    await loadSummary();
    await loadTrend();
    await loadApps();
    await loadHour();
    await loadEmp();
    await loadFiles();
    await loadLatency();
    await loadAudit();
    await showFailReasons();
    // 告警/失败页只在可见时刷新：它们要跑一堆聚合查询，每分钟全量拉没必要
    if (!$('view-alerts').classList.contains('hidden')) await loadServiceAlerts();
    if (!$('view-failures').classList.contains('hidden')) await loadAlerts();
    $('lastUpdate').textContent = '更新于 ' + new Date().toLocaleTimeString();
  } catch (e) {
    $('lastUpdate').textContent = '刷新失败: ' + e.message;
  }
}

document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
    $('view-' + t.dataset.view).classList.remove('hidden');
    Object.values(charts).forEach(c => c.resize());
    if (t.dataset.view === 'apps' && adminToken) loadAppAccounts();
    if (t.dataset.view === 'alerts') loadServiceAlerts();
    if (t.dataset.view === 'failures') loadAlerts();
  };
});
$('btnRefresh').onclick = refreshAll;
$('btnHealthRefresh').onclick = loadHealth;
$('btnSearch').onclick = () => { page = 1; loadAudit(); };
$('btnFailModalClose').onclick = closeFailDetail;
$('failModalMask').onclick = closeFailDetail;
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') closeFailDetail();
});
$('prevPage').onclick = () => { if (page > 1) { page--; loadAudit(); } };
$('nextPage').onclick = () => { if (page * pageSize < auditTotal) { page++; loadAudit(); } };
$('btnExport').onclick = () => {
  const p = new URLSearchParams({ empNo: $('fEmp').value, appId: $('fApp').value,
    keyword: $('fKeyword').value, from: $('fFrom').value, to: $('fTo').value, export: 'csv' });
  const h = adminToken ? { 'X-Admin-Token': adminToken } : {};
  fetch('/api/v1/audit/logs?' + p, { headers: h })
    .then(r => {
      if (!r.ok) throw new Error('导出失败（HTTP ' + r.status + '）');
      return r.blob();
    })
    .then(b => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(b); a.download = 'audit.csv'; a.click();
    })
    .catch(e => alert(e.message));
};
$('btnSaSearch').onclick = () => { saPage = 1; loadServiceAlerts(); };
$('saDays').onchange = () => { saPage = 1; loadServiceAlerts(); };
$('saKind').onchange = () => { saKind = $('saKind').value; saPage = 1; loadServiceAlerts(); };
$('saLevel').onchange = () => { saPage = 1; loadServiceAlerts(); };
$('saStatus').onchange = () => { saPage = 1; loadServiceAlerts(); };
$('saPrev').onclick = () => { if (saPage > 1) { saPage--; loadServiceAlerts(); } };
$('saNext').onclick = () => { if (saPage * saSize < saTotal) { saPage++; loadServiceAlerts(); } };
$('btnSaClear').onclick = function () {
  $('saDays').value = '30'; $('saKind').value = ''; $('saLevel').value = '';
  $('saStatus').value = ''; $('saKeyword').value = '';
  saKind = ''; saPage = 1; loadServiceAlerts();
};
$('btnSaExport').onclick = function () {
  const p = new URLSearchParams({
    days: $('saDays').value, kind: saKind, level: $('saLevel').value,
    status: $('saStatus').value, keyword: $('saKeyword').value, export: 'csv'
  });
  fetch('/api/v1/stats/alerts?' + p)
    .then(r => { if (!r.ok) throw new Error('导出失败（HTTP ' + r.status + '）'); return r.blob(); })
    .then(b => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(b); a.download = 'service-alerts.csv'; a.click();
    })
    .catch(e => alert(e.message));
};
$('btnAlertSearch').onclick = () => { alertPage = 1; loadAlerts(); };
$('alertDays').onchange = () => { alertPage = 1; loadAlerts(); };
$('aPrev').onclick = () => { if (alertPage > 1) { alertPage--; loadAlerts(); } };
$('aNext').onclick = () => { if (alertPage * alertSize < alertTotal) { alertPage++; loadAlerts(); } };
$('btnAlertClear').onclick = function () {
  $('aApp').value = ''; $('aEmp').value = ''; $('aStatus').value = '';
  $('aKeyword').value = ''; alertKind = ''; alertPage = 1; loadAlerts();
};
$('btnAlertExport').onclick = function () {
  const p = new URLSearchParams({
    days: $('alertDays').value, appId: $('aApp').value, empNo: $('aEmp').value,
    status: $('aStatus').value, keyword: $('aKeyword').value,
    kind: alertKind, export: 'csv'
  });
  const h = adminToken ? { 'X-Admin-Token': adminToken } : {};
  fetch('/api/v1/stats/failures?' + p, { headers: h })
    .then(r => { if (!r.ok) throw new Error('导出失败（HTTP ' + r.status + '）'); return r.blob(); })
    .then(b => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(b); a.download = 'alerts.csv'; a.click();
    })
    .catch(e => alert(e.message));
};
$('btnAdminLogin').onclick = () => adminLogin($('adminToken').value);
$('btnAdminLogout').onclick = adminLogout;
$('btnFailToAudit').onclick = () => gotoAudit({ scope: true, result: 'fail', hint: '仅看失败记录' });
// 全局统计范围：切换后所有页面一起刷新
$('globalRange').value = globalRange;
$('globalRange').onchange = function () {
  globalRange = this.value;
  localStorage.setItem(KEY_RANGE, globalRange);
  refreshAll();
};
$('btnClearFilter').onclick = function () {
  $('fApp').value = '';
  $('fEmp').value = '';
  $('fKeyword').value = '';
  $('fFrom').value = '';
  $('fTo').value = '';
  auditFilter = { result: '', orderBy: '', scope: false };
  page = 1;
  loadAudit();
};
$('btnCreateApp').onclick = createApp;
$('btnReloadApps').onclick = loadAppAccounts;
$('envLine').textContent = location.host + ' · 与预览服务同一容器';
// 会话过期（或换机器）就地清掉，避免顶栏显示"管理员"却一直 403
if (adminToken && (!adminExpire || adminExpire * 1000 <= Date.now())) clearAdmin();
setAdminState();
$('btnAdminOut').onclick = adminLogout;
if (adminToken) loadAppAccounts();
refreshAll();
setInterval(() => {
  if (adminToken && adminExpire && adminExpire * 1000 <= Date.now()) {
    clearAdmin();
    setAdminState();
    $('lastUpdate').textContent = '管理登录已到期（3 天），请重新登录';
  }
  if ($('autoRefresh').checked) refreshAll();
}, 60000);

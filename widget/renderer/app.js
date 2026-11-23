/* HKU 邮件助手 —— 渲染逻辑 */

const CAT_STYLE = {
  deadline: { bg: '#fef3f2', fg: '#b42318', bd: '#fecdca' },
  academic: { bg: '#eff8ff', fg: '#175cd3', bd: '#b2ddff' },
  event:    { bg: '#f4f3ff', fg: '#6938ef', bd: '#d9d6fe' },
  it:       { bg: '#f2f4f7', fg: '#475467', bd: '#e4e7ec' },
  other:    { bg: '#f9fafb', fg: '#667085', bd: '#eaecf0' }
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

function scoreColor(score) {
  if (score >= 90) return '#d92d20';
  if (score >= 70) return '#dc6803';
  if (score >= 45) return '#175cd3';
  return '#98a2b3';
}

function catChip(key, label) {
  const s = CAT_STYLE[key] || CAT_STYLE.other;
  return `<span class="cat" style="background:${s.bg};color:${s.fg};border-color:${s.bd}">${esc(label)}</span>`;
}

function openUrl(url) {
  if (url) window.mailbot.openExternal(url);
}

/* ------------------------- 总览 ------------------------- */
function renderOverview(d) {
  const st = d.stats || {};
  $('ov-slot').textContent = (d.slot || '今日') + '汇总';
  $('ov-time').textContent = d.generated_at_label || '';
  $('ov-headline').textContent = d.headline_cn || '暂无摘要。';

  const cards = [
    { n: st.total || 0, label: '邮件总数', alert: false },
    { n: st.key || 0, label: '关键邮件', alert: (st.key || 0) > 0 },
    { n: st.unread || 0, label: '未读', alert: false },
    { n: st.deadline_count || 0, label: '含截止', alert: (st.urgent_key || 0) > 0 }
  ];
  $('ov-stats').innerHTML = cards
    .map((c) => `<div class="stat${c.alert ? ' alert' : ''}"><b>${c.n}</b><span>${c.label}</span></div>`)
    .join('');
}

/* ------------------------- 截止看板 ------------------------- */
function renderDeadlines(d) {
  const list = d.deadline_board || [];
  if (!list.length) { $('deadline-sec').classList.add('hidden'); return; }
  $('deadline-sec').classList.remove('hidden');

  $('deadline-list').innerHTML = list.map((x) => {
    let cls = 'dl-normal', txt = `${x.days_left} 天`;
    if (x.days_left <= 1) { cls = 'dl-urgent'; txt = x.days_left <= 0 ? '今天' : '明天'; }
    else if (x.days_left <= 3) cls = 'dl-urgent';
    else if (x.days_left <= 7) cls = 'dl-soon';
    return `<div class="dl-row ${cls}" data-url="${esc(x.web_link)}">
        <div class="dl-days" style="color:${x.category_color || '#475467'}">${esc(txt)}</div>
        <div class="dl-meta">
          <div class="dl-subj">${esc(x.subject)}</div>
          <div class="dl-when">截止 ${esc(x.date_label)} · ${esc(x.from_name)}</div>
        </div>
      </div>`;
  }).join('');
}

/* ------------------------- 关键邮件 ------------------------- */
function mailCard(m) {
  const urgent = m.deadline && m.deadline.days_left <= 3;
  const reasons = (m.reasons_cn || []).slice(0, 4)
    .map((r) => `<span class="tag reason">${esc(r)}</span>`).join('');
  const attach = m.has_attachments
    ? `<span class="tag">📎 ${esc((m.attachments || []).join('、') || '有附件')}</span>` : '';
  const dlTag = m.deadline
    ? `<span class="tag">⏰ ${esc(m.deadline.date)}${m.deadline.time ? ' ' + esc(m.deadline.time) : ''}（${m.deadline.days_left} 天）</span>` : '';
  const replyTag = m.needs_reply ? '<span class="tag">↩ 需回复</span>' : '';
  const dupTag = (m.dup_count && m.dup_count > 1)
    ? `<span class="tag" style="color:#6941c6;background:#f4f3ff;border-color:#d9d6fe">⇉ 合并 ${m.dup_count} 条同类提醒</span>` : '';
  const sumClass = (d) => '';
  const demoTag = m.is_demo ? '<span class="tag" style="color:#b54708;background:#fffaeb;border-color:#fedf89">示例数据</span>' : '';
  const openLabel = m.is_demo ? '打开邮箱' : (m.link_is_exact ? '打开邮件' : '搜索该邮件');

  return `<div class="mail${urgent ? ' urgent' : ''}" data-url="${esc(m.web_link)}">
    <div class="mail-top">
      ${catChip(m.category_key, m.category_label)}
      <span class="mail-from">${esc(m.from_name)}${m.is_read ? '' : ' · <b style="color:#d92d20">未读</b>'}</span>
      <span class="mail-time">${esc(m.date_label)}</span>
      <span class="score" style="background:${scoreColor(m.score)}">${m.score}</span>
    </div>
    <div class="mail-subj">${esc(m.subject)}</div>
    <div class="mail-sum">${esc(m.summary_cn)}</div>
    <div class="meta-row">${dlTag}${replyTag}${attach}${dupTag}${demoTag}${reasons}</div>
    <div class="mail-foot">
      <span class="action">建议：${esc(m.action_cn)}</span>
      <span class="btns">
        <button class="btn detail-btn">详情</button>
        <button class="btn primary open-btn">${openLabel}</button>
      </span>
    </div>
    <div class="mail-detail hidden" style="margin-top:8px;border-top:1px solid var(--line-soft);padding-top:7px">
      <div style="font-size:10.5px;color:var(--fg-2);line-height:1.6;white-space:pre-line">${esc(m.body_preview)}</div>
      ${(m.links && m.links.length) ? `<div style="margin-top:6px;font-size:10px;color:var(--blue);word-break:break-all">${m.links.map((l) => `<div class="lnk" data-url="${esc(l)}" style="cursor:pointer;text-decoration:underline">${esc(l)}</div>`).join('')}</div>` : ''}
      <div style="margin-top:6px;display:flex;gap:8px;font-size:10px">
        ${m.sender_link ? `<span class="lnk" data-url="${esc(m.sender_link)}" style="color:var(--hku);cursor:pointer;text-decoration:underline">查看该发件人的全部邮件</span>` : ''}
        <span class="lnk" data-url="${esc(m.inbox_link || 'https://outlook.office365.com/mail/inbox')}" style="color:var(--hku);cursor:pointer;text-decoration:underline">打开收件箱</span>
      </div>
      ${(m.keywords_hit && m.keywords_hit.length) ? `<div style="margin-top:6px;font-size:10px;color:var(--fg-3)">命中关键词：${esc(m.keywords_hit.join('、'))}</div>` : ''}
    </div>
  </div>`;
}

function renderKeys(d) {
  const list = d.key_emails || [];
  $('key-count').textContent = list.length ? `（${list.length}）` : '';
  if (!list.length) {
    $('key-list').innerHTML = `<div class="empty"><b>✓</b>没有需要立刻处理的邮件<br>今天的信箱很安静</div>`;
    return;
  }
  $('key-list').innerHTML = list.map(mailCard).join('');
}

/* ------------------------- 分类小结 ------------------------- */
function renderCats(d) {
  const list = d.category_summary_cn || [];
  if (!list.length) { $('cats-sec').classList.add('hidden'); return; }
  $('cats-sec').classList.remove('hidden');
  $('cats-list').innerHTML = list.map((c) => `
    <div class="cat-row">
      <div class="cat-bar" style="background:${c.color}"></div>
      <div class="cat-body">
        <div class="cat-head">
          <span class="cat-name">${esc(c.label)}</span>
          <span class="cat-num">${c.count} 封${c.key_count ? ` · 重点 ${c.key_count}` : ''}</span>
        </div>
        <div class="cat-text">${esc(c.summary_cn)}</div>
      </div>
    </div>`).join('');
}

/* ------------------------- 折叠列表 ------------------------- */
function miniRows(list) {
  return list.map((m) => `
    <div class="mini${m.is_read ? '' : ' unread'}" data-url="${esc(m.web_link)}">
      <span class="mini-dot" style="background:${m.category_color || '#98a2b3'}"></span>
      <span class="mini-subj">${esc(m.subject)}</span>
      <span class="mini-date">${esc(m.date_label)}</span>
    </div>`).join('');
}

function renderFolds(d) {
  const watch = d.watch_emails || [];
  const other = d.other_emails || [];

  if (watch.length) {
    $('watch-sec').classList.remove('hidden');
    $('watch-title').textContent = '值得关注';
    $('watch-n').textContent = `${watch.length} 封`;
    $('watch-list').innerHTML = watch.map((m) => `
      <div class="mini${m.is_read ? '' : ' unread'}" data-url="${esc(m.web_link)}">
        <span class="mini-dot" style="background:${m.category_color || '#98a2b3'}"></span>
        <span class="mini-subj">${esc(m.subject)}</span>
        <span class="mini-date">${esc(m.date_label)}</span>
      </div>`).join('');
  } else $('watch-sec').classList.add('hidden');

  if (other.length) {
    $('other-sec').classList.remove('hidden');
    $('other-title').textContent = '其他邮件';
    $('other-n').textContent = `${other.length} 封`;
    $('other-list').innerHTML = miniRows(other);
  } else $('other-sec').classList.add('hidden');
}

/* ------------------------- 主渲染 ------------------------- */
let lastHeight = 0;
function autoResize() {
  const card = $('card');
  const h = card.scrollHeight + 16;
  if (Math.abs(h - lastHeight) < 40) return;
  lastHeight = h;
  window.mailbot.resizeHeight(h);
}

function render(d) {
  if (!d) {
    $('hd-sub').textContent = '暂无数据';
    $('ov-headline').textContent = '还没有生成汇总数据。运行一次 daily_run.py 即可。';
    return;
  }
  $('hd-sub').textContent = `${d.generated_at_label || ''} · ${(d.source_mode === 'demo' ? '示例数据' : '港大邮箱')}`;
  $('ft-hint').textContent = d.ai && d.ai.enriched ? 'AI 中文摘要已更新' : '规则版摘要 · 自动刷新中';

  renderOverview(d);
  renderDeadlines(d);
  renderKeys(d);
  renderCats(d);
  renderFolds(d);

  setTimeout(autoResize, 60);
}

/* ------------------------- 事件 ------------------------- */
document.addEventListener('click', (e) => {
  // 折叠/收起
  if (e.target.closest('#btn-collapse') || e.target.closest('#pill')) {
    window.mailbot.toggleCollapse();
    return;
  }
  if (e.target.closest('#btn-close')) { window.mailbot.hide(); return; }
  if (e.target.closest('#btn-mail')) { openUrl('https://outlook.office365.com/mail/inbox'); return; }
  if (e.target.closest('#btn-refresh')) {
    window.mailbot.getDigest().then((d) => { render(d); window.mailbot.notifyTest(); });
    return;
  }
  if (e.target.closest('#btn-demo-test')) { window.mailbot.notifyTest(); return; }

  const detailBtn = e.target.closest('.detail-btn');
  if (detailBtn) {
    const box = detailBtn.closest('.mail').querySelector('.mail-detail');
    box.classList.toggle('hidden');
    detailBtn.textContent = box.classList.contains('hidden') ? '详情' : '收起';
    setTimeout(autoResize, 60);
    return;
  }

  const lnk = e.target.closest('.lnk');
  if (lnk) { e.stopPropagation(); openUrl(lnk.dataset.url); return; }

  const openBtn = e.target.closest('.open-btn');
  if (openBtn) {
    const mail = openBtn.closest('.mail');
    openUrl(mail.dataset.url);
    return;
  }

  const row = e.target.closest('[data-url]');
  if (row && !e.target.closest('button') && !e.target.closest('summary')) {
    openUrl(row.dataset.url);
  }
});

window.mailbot.onDigest((d) => render(d));
window.mailbot.onCollapsed((v) => {
  $('card').classList.toggle('hidden', v);
  $('pill').classList.toggle('hidden', !v);
});

// 启动
window.mailbot.getDigest().then((d) => {
  render(d);
  const k = d && d.stats ? d.stats.key || 0 : 0;
  $('pill-badge').textContent = k;
});
setInterval(() => {
  window.mailbot.getDigest().then((d) => {
    if (d && d.stats) $('pill-badge').textContent = d.stats.key || 0;
  });
}, 20000);

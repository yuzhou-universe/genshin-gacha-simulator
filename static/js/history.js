/* 历史记录页：筛选、分页、统计、CSV 导出。 */
(function () {
  'use strict';

  const esc = window.App.escapeHtml;

  const el = {
    pool: document.querySelector('[data-f-pool]'),
    star: document.querySelector('[data-f-star]'),
    up: document.querySelector('[data-f-up]'),
    from: document.querySelector('[data-f-from]'),
    to: document.querySelector('[data-f-to]'),

    rows: document.querySelector('[data-rows]'),
    total: document.querySelector('[data-total]'),
    empty: document.querySelector('[data-empty]'),
    pager: document.querySelector('[data-pager]'),
    pageInfo: document.querySelector('[data-page-info]'),

    progress: document.querySelector('[data-progress]'),
  };

  const state = { page: 1, pages: 1, pageSize: 20 };

  const POOL_TYPE_LABEL = {
    character: '角色活动祈愿',
    weapon: '武器活动祈愿',
    standard: '常驻祈愿',
    beginner: '新手祈愿',
  };

  function currentFilter() {
    const f = {};
    if (el.pool.value) f.pool_id = el.pool.value;
    if (el.star.value) f.star = el.star.value;
    if (el.up.value) f.is_up = el.up.value;
    if (el.from.value) f.date_from = el.from.value;
    if (el.to.value) f.date_to = el.to.value;
    return f;
  }

  function qs(extra) {
    const params = new URLSearchParams(Object.assign(currentFilter(), extra || {}));
    return params.toString();
  }

  /* ------------------------------------------------------------------
     统计
     ------------------------------------------------------------------ */
  function setStat(key, value) {
    const node = document.querySelector(`[data-s="${key}"]`);
    if (node) node.textContent = value;
  }

  function renderStats(stats) {
    setStat('total_pulls', stats.total_pulls);
    setStat('five_star_count', stats.five_star_count);
    setStat('four_star_count', stats.four_star_count);
    setStat('up_count', stats.up_count);
    setStat(
      'average_pulls_per_five_star',
      stats.average_pulls_per_five_star != null
        ? stats.average_pulls_per_five_star + ' 抽'
        : '—'
    );
    setStat('five_star_rate', (stats.five_star_rate * 100).toFixed(2) + '%');
    setStat(
      'extremes',
      stats.max_pulls_for_five_star != null
        ? `${stats.max_pulls_for_five_star} / ${stats.min_pulls_for_five_star}`
        : '—'
    );
    setStat('lost_5050_count', stats.lost_5050_count);

    renderProgress(stats.pool_progress || []);
    fillPoolFilter(stats.pool_progress || []);
  }

  function fillPoolFilter(progress) {
    if (el.pool.options.length > 1) return; // 只在首次填充
    const seen = new Set();
    progress.forEach((p) => {
      if (p.pool_id == null || seen.has(p.pool_id)) return;
      seen.add(p.pool_id);
      const opt = document.createElement('option');
      opt.value = String(p.pool_id);
      opt.textContent = p.pool_name;
      el.pool.appendChild(opt);
    });
  }

  function renderProgress(list) {
    if (!list.length) {
      el.progress.innerHTML = '<p class="empty">暂无数据</p>';
      return;
    }
    el.progress.innerHTML = list
      .map((p) => {
        const pct = Math.min(100, (p.pity_5 / p.hard_pity_5) * 100);
        const fillClass = p.guaranteed_5 ? ' progress-row__fill--guaranteed' : '';
        const notes = [];
        if (p.guaranteed_5) notes.push('<b>大保底</b>');
        if (p.epitomized_max_points) {
          notes.push(
            `定轨 ${p.charted_item_name ? esc(p.charted_item_name) : '未选择'} ` +
              `命定值 ${p.fate_points}/${p.epitomized_max_points}`
          );
        }
        return `<div class="progress-row">
          <div class="progress-row__name">
            ${esc(p.pool_name)}
            <small>${POOL_TYPE_LABEL[p.pool_type] || ''} · 累计 ${p.total_pulls} 抽</small>
          </div>
          <div>
            <div class="progress-row__track">
              <div class="progress-row__fill${fillClass}" style="width:${pct}%"></div>
            </div>
            ${notes.length ? `<div class="progress-row__meta" style="margin-top:5px">${notes.join(' · ')}</div>` : ''}
          </div>
          <div class="progress-row__meta">
            五星 <b>${p.pity_5}</b>/${p.hard_pity_5} · 四星 ${p.pity_4}/${p.hard_pity_4}
          </div>
        </div>`;
      })
      .join('');
  }

  /* ------------------------------------------------------------------
     记录表
     ------------------------------------------------------------------ */
  function starClass(star) {
    return star === 5 ? 'item-name--five' : star === 4 ? 'item-name--four' : 'item-name--three';
  }

  function renderRows(page) {
    if (!page.items.length) {
      el.rows.innerHTML = '';
      el.empty.hidden = false;
      el.pager.hidden = true;
      el.total.textContent = '';
      return;
    }
    el.empty.hidden = true;
    el.total.textContent = `共 ${page.total} 条`;

    el.rows.innerHTML = page.items
      .map((r) => {
        const thumb = r.item_image
          ? `<img class="thumb" src="${esc(r.item_image)}" alt="" loading="lazy">`
          : '';
        const time = new Date(r.created_at).toLocaleString('zh-CN', { hour12: false });
        return `<tr>
          <td class="cell-time">${esc(time)}</td>
          <td class="cell-pool">${esc(r.pool_name)}</td>
          <td>
            <span class="item-name ${starClass(r.star)} star-${r.star}">
              ${thumb}<span class="item-name__dot"></span>${esc(r.item_name)}
            </span>
          </td>
          <td class="ta-c">${'★'.repeat(r.star)}</td>
          <td class="ta-c">${r.is_up ? '<span class="badge badge--up">UP</span>' : '—'}</td>
          <td class="ta-c">${r.is_pity ? '<span class="badge badge--pity">保底</span>' : '—'}</td>
          <td class="ta-c">${r.star === 5 ? r.pulls_used : '—'}</td>
        </tr>`;
      })
      .join('');

    state.pages = page.pages;
    el.pager.hidden = page.pages <= 1;
    el.pageInfo.textContent = `第 ${page.page} / ${page.pages} 页`;
    document.querySelector('[data-page="prev"]').disabled = page.page <= 1;
    document.querySelector('[data-page="next"]').disabled = page.page >= page.pages;
  }

  /* ------------------------------------------------------------------
     数据加载
     ------------------------------------------------------------------ */
  async function reload(resetPage) {
    if (resetPage) state.page = 1;
    try {
      const [page, stats] = await Promise.all([
        window.App.request(`/api/history?${qs({ page: state.page, page_size: state.pageSize })}`),
        window.App.request(`/api/history/stats?${qs()}`),
      ]);
      renderRows(page);
      renderStats(stats);
    } catch (err) {
      window.App.toast(err.message, 'error');
    }
  }

  /* ------------------------------------------------------------------
     事件
     ------------------------------------------------------------------ */
  document.querySelector('[data-apply]').addEventListener('click', () => reload(true));
  document.querySelector('[data-reset]').addEventListener('click', () => {
    el.pool.value = '';
    el.star.value = '';
    el.up.value = '';
    el.from.value = '';
    el.to.value = '';
    reload(true);
  });

  // 下拉类筛选改动后立即生效；日期输入等用户点"筛选"再应用
  [el.pool, el.star, el.up].forEach((node) =>
    node.addEventListener('change', () => reload(true))
  );

  document.querySelector('[data-export]').addEventListener('click', () => {
    // 直接跳转，让浏览器按 Content-Disposition 下载
    window.location.href = `/api/history/export?${qs()}`;
  });

  document.querySelector('[data-pager]').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-page]');
    if (!btn || btn.disabled) return;
    if (btn.dataset.page === 'prev' && state.page > 1) state.page -= 1;
    if (btn.dataset.page === 'next' && state.page < state.pages) state.page += 1;
    reload(false);
  });

  reload(true);
})();

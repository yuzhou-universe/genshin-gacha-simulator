/* 管理后台：仪表盘、卡池管理、素材上传、全局配置、操作日志。 */
(function () {
  'use strict';

  const esc = window.App.escapeHtml;
  const req = window.App.request;
  const toast = window.App.toast;

  /* ------------------------------------------------------------------
     侧栏高亮
     ------------------------------------------------------------------ */
  (function highlightNav() {
    const path = window.location.pathname;
    document.querySelectorAll('[data-nav]').forEach((link) => {
      const target = link.dataset.nav;
      if (target === '/admin' ? path === '/admin' : path.startsWith(target)) {
        link.classList.add('is-active');
      }
    });
  })();

  function showError(node, message) {
    if (!node) return;
    node.textContent = message;
    node.hidden = false;
  }
  function clearError(node) {
    if (!node) return;
    node.hidden = true;
    node.textContent = '';
  }

  /* ==================================================================
     仪表盘
     ================================================================== */
  const statHost = document.querySelector('[data-stats]');
  if (statHost) {
    const load = async () => {
      try {
        const s = await req('/admin/stats');
        const set = (key, value) => {
          const node = document.querySelector(`[data-s="${key}"]`);
          if (node) node.textContent = value;
        };
        set('total_users', s.total_users);
        set('active_users', s.active_users);
        set('admin_count', s.admin_count);
        set('total_pulls', s.total_pulls);
        set('total_five_stars', s.total_five_stars);
        set('total_four_stars', s.total_four_stars);
        set('five_star_rate', (s.five_star_rate * 100).toFixed(3) + '%');
        set('pulls_today', s.pulls_today);
        set('pulls_last_7_days', s.pulls_last_7_days);

        document.querySelector('[data-breakdown]').innerHTML =
          s.pool_breakdown.length
            ? s.pool_breakdown
                .map(
                  (p) =>
                    `<tr><td>${esc(p.pool_name)}</td>
                     <td class="ta-r">${p.pulls}</td>
                     <td class="ta-r">${p.five_stars}</td></tr>`
                )
                .join('')
            : '<tr><td colspan="3" class="muted">暂无数据</td></tr>';

        document.querySelector('[data-top]').innerHTML = s.top_items.length
          ? s.top_items
              .map((i) => `<tr><td>${esc(i.name)}</td><td class="ta-r">${i.count}</td></tr>`)
              .join('')
          : '<tr><td colspan="2" class="muted">暂无数据</td></tr>';
      } catch (err) {
        toast(err.message, 'error');
      }
    };
    document.querySelector('[data-refresh]')?.addEventListener('click', load);
    load();
  }

  /* ==================================================================
     卡池列表
     ================================================================== */
  const poolTable = document.querySelector('[data-pool-table]');
  if (poolTable) {
    const render = async () => {
      try {
        const pools = await req('/admin/api/pools');
        poolTable.innerHTML = pools
          .map((p) => {
            const flags = [
              p.is_active
                ? '<span class="flag flag--on">已启用</span>'
                : '<span class="flag flag--off">已停用</span>',
              p.is_current ? '<span class="flag flag--current">开放中</span>' : '',
              p.is_main ? '<span class="flag flag--main">主推</span>' : '',
              p.epitomized_enabled ? '<span class="flag flag--epi">定轨</span>' : '',
            ].join('');

            return `<div class="pool-row${p.is_active ? '' : ' is-off'}">
              <img class="pool-row__thumb" src="${esc(p.background_image_url || '/static/img/banner-default.svg')}" alt="">
              <div>
                <div class="pool-row__name">${esc(p.name)}</div>
                <div class="pool-row__meta">
                  ${esc(p.key)} · ${p.hard_pity_5} 抽保底 ·
                  五星 ${(p.base_rate_5 * 100).toFixed(1)}%${p.soft_pity_enabled ? ` (${p.soft_pity_start} 起递增)` : ''} ·
                  ${p.has_5050 ? `UP ${(p.up_rate_5 * 100).toFixed(0)}%` : '无 UP'} ·
                  ${p.items.length} 件物品
                </div>
                <div class="pool-row__flags">${flags}</div>
              </div>
              <div class="pool-row__actions">
                <button type="button" class="btn btn--ghost btn--sm" data-toggle="${p.id}">
                  ${p.is_current ? '关闭' : '开放'}
                </button>
                <button type="button" class="btn btn--ghost btn--sm" data-main="${p.id}"
                        ${p.is_main || !p.is_current ? 'disabled' : ''}>设为主推</button>
                <a class="btn btn--ghost btn--sm" href="/admin/pools/${p.id}">编辑</a>
              </div>
            </div>`;
          })
          .join('');
      } catch (err) {
        toast(err.message, 'error');
      }
    };

    poolTable.addEventListener('click', async (e) => {
      const toggle = e.target.closest('[data-toggle]');
      const main = e.target.closest('[data-main]');
      try {
        if (toggle) {
          await req(`/admin/pools/${toggle.dataset.toggle}/set-current`, { method: 'POST' });
          toast('已更新开放状态', 'success');
          render();
        } else if (main) {
          await req(`/admin/pools/${main.dataset.main}/set-main`, { method: 'POST' });
          toast('已设为主推卡池', 'success');
          render();
        }
      } catch (err) {
        toast(err.message, 'error');
      }
    });

    // 新建卡池
    const modal = document.querySelector('[data-new-pool-modal]');
    document.querySelector('[data-new-pool]')?.addEventListener('click', () => {
      modal.hidden = false;
      clearError(modal.querySelector('[data-error]'));
    });
    modal?.addEventListener('click', (e) => {
      if (e.target.closest('[data-close-modal]')) modal.hidden = true;
    });

    document.querySelector('[data-new-pool-form]')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.target;
      const errorBox = form.querySelector('[data-error]');
      const data = Object.fromEntries(new FormData(form));
      await window.App.withBusy(form.querySelector('button[type=submit]'), async () => {
        try {
          const pool = await req('/admin/pools', { method: 'POST', body: data });
          toast('卡池已创建，正在前往编辑页', 'success');
          window.location.href = `/admin/pools/${pool.id}`;
        } catch (err) {
          showError(errorBox, err.message);
        }
      });
    });

    render();
  }

  /* ==================================================================
     卡池编辑
     ================================================================== */
  const poolForm = document.querySelector('[data-pool-form]');
  if (poolForm) {
    const poolId = poolForm.dataset.poolId;
    const rowsHost = poolForm.querySelector('[data-item-rows]');
    const summary = poolForm.querySelector('[data-item-summary]');
    const errorBox = poolForm.querySelector('[data-form-error]');
    let nextTempId = -1;

    const ITEM_TYPES = { character: '角色', weapon: '武器' };

    function itemRow(item) {
      const id = item.id ?? nextTempId--;
      return `<div class="item-row${item.is_up ? ' is-up' : ''}" data-item data-id="${id}">
        <input type="text" data-f="name" value="${esc(item.name)}" placeholder="名称" maxlength="128">
        <select data-f="item_type">
          ${Object.entries(ITEM_TYPES)
            .map(
              ([v, l]) =>
                `<option value="${v}"${item.item_type === v ? ' selected' : ''}>${l}</option>`
            )
            .join('')}
        </select>
        <select data-f="star">
          ${[3, 4, 5]
            .map((s) => `<option value="${s}"${item.star === s ? ' selected' : ''}>${s}★</option>`)
            .join('')}
        </select>
        <input type="checkbox" data-f="is_up" ${item.is_up ? 'checked' : ''} title="是否 UP">
        <input type="number" data-f="weight" value="${item.weight}" step="0.1" min="0" title="权重">
        <input type="text" data-f="image_url" value="${esc(item.image_url || '')}" placeholder="展示图 URL">
        <input type="checkbox" data-f="is_active" ${item.is_active ? 'checked' : ''} title="启用">
        <button type="button" data-remove-item title="删除">×</button>
      </div>`;
    }

    async function loadItems() {
      const pools = await req('/admin/api/pools');
      const pool = pools.find((p) => String(p.id) === String(poolId));
      if (!pool) return;
      rowsHost.innerHTML = pool.items.map(itemRow).join('');
      updateSummary();
    }

    function collectItems() {
      return [...rowsHost.querySelectorAll('[data-item]')].map((row) => {
        const get = (f) => row.querySelector(`[data-f="${f}"]`);
        const id = Number(row.dataset.id);
        return {
          id: id > 0 ? id : null,
          name: get('name').value.trim(),
          item_type: get('item_type').value,
          star: Number(get('star').value),
          is_up: get('is_up').checked,
          weight: Number(get('weight').value) || 0,
          image_url: get('image_url').value.trim(),
          icon_url: '',
          is_active: get('is_active').checked,
          sort_order: 0,
        };
      });
    }

    /** 实时提示配置问题，避免管理员保存后才发现 */
    function updateSummary() {
      const items = collectItems();
      const active = items.filter((i) => i.is_active);
      const five = active.filter((i) => i.star === 5);
      const up5 = five.filter((i) => i.is_up);
      const off5 = five.filter((i) => !i.is_up);
      const four = active.filter((i) => i.star === 4);
      const three = active.filter((i) => i.star === 3);

      const parts = [];
      const warn = (text) => parts.push(`<span class="bad">${text}</span>`);

      parts.push(`五星 <b>${five.length}</b>（UP ${up5.length} / 歪池 ${off5.length}）`);
      parts.push(`四星 <b>${four.length}</b>`);
      parts.push(`三星 <b>${three.length}</b>`);

      if (!three.length) warn('缺少三星物品：未中四/五星时无处可掉');
      if (!up5.length) warn('没有 UP 五星');
      if (!off5.length) warn('没有非 UP 五星作为歪池');

      summary.innerHTML = parts.join('');
    }

    rowsHost.addEventListener('input', updateSummary);
    rowsHost.addEventListener('change', updateSummary);
    rowsHost.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-remove-item]');
      if (!btn) return;
      btn.closest('[data-item]').remove();
      updateSummary();
    });

    poolForm.querySelector('[data-add-item]')?.addEventListener('click', () => {
      rowsHost.insertAdjacentHTML(
        'beforeend',
        itemRow({ name: '', item_type: 'character', star: 3, is_up: false, weight: 1, image_url: '', is_active: true })
      );
      updateSummary();
    });

    // ---- 软保底到达 100% 的抽数提示 ----
    function updatePityWarning() {
      const box = poolForm.querySelector('[data-pity-warning]');
      const num = (name) => Number(poolForm.querySelector(`[name="${name}"]`).value);
      const enabled = poolForm.querySelector('[name="soft_pity_enabled"]').checked;
      if (!enabled) {
        box.hidden = true;
        return;
      }
      const base = num('base_rate_5');
      const step = num('soft_pity_step');
      const start = num('soft_pity_start');
      const hard = num('hard_pity_5');

      if (step <= 0 || base <= 0) {
        box.hidden = true;
        return;
      }
      const cap = start + Math.ceil((1 - base) / step) - 1;
      if (cap < hard) {
        box.textContent =
          `按当前参数，五星概率会在第 ${cap} 抽到达 100%，早于硬保底 ${hard} 抽 —— ` +
          `这会让硬保底形同虚设。这是参数使然（武器池默认就是这样），不是错误，但请知悉。`;
        box.hidden = false;
      } else {
        box.hidden = true;
      }
    }
    poolForm.addEventListener('input', updatePityWarning);

    // ---- 保存 ----
    async function save() {
      clearError(errorBox);
      const form = poolForm;
      const payload = {};

      // 文本 / 数字 / 下拉
      form.querySelectorAll('input[name], select[name], textarea[name]').forEach((node) => {
        if (node.disabled) return;
        const name = node.name;
        let value;
        if (node.type === 'checkbox') value = node.checked;
        else if (node.type === 'number') {
          if (node.value === '') return; // 留空 = 不改
          value = Number(node.value);
        } else if (node.type === 'datetime-local') {
          if (!node.value) { payload[name] = null; return; }
          value = new Date(node.value).toISOString();
        } else if (name === 'cost_per_ten' || name === 'lifetime_pull_cap') {
          // 这两个留空表示"不限"，需要显式传 null 才能清掉
          payload[name] = node.value === '' ? null : Number(node.value);
          return;
        } else value = node.value;

        payload[name] = value;
      });

      payload.items = collectItems();

      try {
        await req(`/admin/pools/${poolId}`, { method: 'PUT', body: payload });
        toast('已保存', 'success');
        await loadItems();
        updatePityWarning();
      } catch (err) {
        showError(errorBox, err.message);
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
      }
    }

    poolForm.querySelectorAll('[data-save-pool]').forEach((btn) =>
      btn.addEventListener('click', () => window.App.withBusy(btn, save))
    );

    poolForm.querySelector('[data-delete-pool]')?.addEventListener('click', async (e) => {
      if (!window.confirm('确定删除这个卡池？已产生的抽卡历史会保留（记录里存有快照）。')) return;
      await window.App.withBusy(e.currentTarget, async () => {
        try {
          const res = await req(`/admin/pools/${poolId}`, { method: 'DELETE' });
          toast(`已删除，保留 ${res.existing_records} 条历史记录`, 'success');
          setTimeout(() => (window.location.href = '/admin/pools'), 700);
        } catch (err) {
          showError(errorBox, err.message);
        }
      });
    });

    loadItems().then(updatePityWarning);
  }

  /* ==================================================================
     素材管理
     ================================================================== */
  const uploadForm = document.querySelector('[data-upload-form]');
  if (uploadForm) {
    const result = document.querySelector('[data-upload-result]');

    uploadForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = uploadForm.querySelector('button[type=submit]');
      await window.App.withBusy(btn, async () => {
        try {
          const fd = new FormData(uploadForm);
          const data = await req('/admin/upload', { method: 'POST', body: fd });
          result.hidden = false;
          result.querySelector('[data-upload-url]').value = data.url;
          result.querySelector('[data-upload-preview]').src = data.url;
          toast('上传成功', 'success');
          uploadForm.reset();
          loadAssets();
        } catch (err) {
          toast(err.message, 'error', 4500);
        }
      });
    });

    result?.addEventListener('click', async (e) => {
      if (!e.target.closest('[data-copy-url]')) return;
      const input = result.querySelector('[data-upload-url]');
      try {
        await navigator.clipboard.writeText(input.value);
        toast('已复制到剪贴板', 'success');
      } catch (_) {
        input.select();
        toast('请手动复制', 'info');
      }
    });

    async function loadAssets() {
      const grid = document.querySelector('[data-asset-grid]');
      try {
        const assets = await req('/admin/api/assets');
        if (!assets.length) {
          grid.innerHTML = '<p class="empty">还没有上传任何素材</p>';
          return;
        }
        grid.innerHTML = assets
          .map(
            (a) => `<div class="asset-card">
              <img src="${esc(a.url)}" alt="" loading="lazy">
              <div class="asset-card__body">
                <div class="asset-card__name">${esc(a.name)}</div>
                <div class="asset-card__meta">${a.category} · ${(a.size / 1024).toFixed(0)} KB</div>
                ${
                  a.used_by.length
                    ? `<div class="asset-card__used">使用中：${esc(a.used_by.slice(0, 2).join('、'))}${
                        a.used_by.length > 2 ? ` 等 ${a.used_by.length} 处` : ''
                      }</div>`
                    : '<div class="asset-card__meta">未被引用</div>'
                }
              </div>
            </div>`
          )
          .join('');
      } catch (err) {
        toast(err.message, 'error');
      }
    }
    loadAssets();
  }

  /* ==================================================================
     全局配置
     ================================================================== */
  const settingsForm = document.querySelector('[data-save-settings]');
  if (settingsForm) {
    const errorBox = document.querySelector('[data-form-error]');
    const collect = () => {
      const num = (name) => Number(document.querySelector(`[name="${name}"]`).value) || 0;
      const templates = {};
      ['pull_comment', 'pool_intro', 'history_summary', 'character_line'].forEach((k) => {
        const node = document.querySelector(`[name="tpl_${k}"]`);
        if (node && node.value.trim()) templates[k] = node.value;
      });
      return {
        initial_primogems: num('initial_primogems'),
        initial_intertwined_fate: num('initial_intertwined_fate'),
        initial_acquaint_fate: num('initial_acquaint_fate'),
        animation: {
          enabled: document.querySelector('[name="anim_enabled"]').checked,
          reveal_delay_ms: num('reveal_delay_ms'),
          five_star_intro_ms: num('five_star_intro_ms'),
          skippable: document.querySelector('[name="anim_skippable"]').checked,
        },
        prompt_templates: templates,
      };
    };

    settingsForm.addEventListener('click', async (e) => {
      await window.App.withBusy(e.currentTarget, async () => {
        clearError(errorBox);
        try {
          await req('/admin/api/settings', { method: 'PUT', body: collect() });
          toast('配置已保存', 'success');
        } catch (err) {
          showError(errorBox, err.message);
        }
      });
    });
  }

  /* ==================================================================
     操作日志
     ================================================================== */
  const logRows = document.querySelector('[data-log-rows]');
  if (logRows) {
    let page = 1;
    let pages = 1;

    const ACTION_LABELS = {
      'pool.create': '新建卡池',
      'pool.update': '修改卡池',
      'pool.delete': '删除卡池',
      'pool.set_current': '切换开放',
      'pool.set_main': '设为主推',
      'item.create': '新增物品',
      'item.delete': '删除物品',
      'asset.upload': '上传素材',
      'settings.update': '修改配置',
    };

    function prettyDetail(raw) {
      if (!raw) return '—';
      try {
        const obj = JSON.parse(raw);
        if (typeof obj !== 'object' || obj === null) return esc(String(obj));
        return Object.entries(obj)
          .map(([k, v]) => {
            if (v && typeof v === 'object' && 'from' in v) {
              return `${esc(k)}: ${esc(String(v.from))} → ${esc(String(v.to))}`;
            }
            return `${esc(k)}: ${esc(typeof v === 'object' ? JSON.stringify(v) : String(v))}`;
          })
          .join('\n');
      } catch (_) {
        return esc(raw);
      }
    }

    async function loadLogs() {
      const action = document.querySelector('[data-log-action]').value;
      const params = new URLSearchParams({ page, page_size: 50 });
      if (action) params.set('action', action);
      try {
        const data = await req(`/admin/api/logs?${params}`);
        logRows.innerHTML = data.items.length
          ? data.items
              .map(
                (l) => `<tr>
                  <td>${esc(new Date(l.created_at).toLocaleString('zh-CN', { hour12: false }))}</td>
                  <td>${esc(l.admin_username)}</td>
                  <td>${esc(ACTION_LABELS[l.action] || l.action)}</td>
                  <td>${esc(l.target_type)}${l.target_id ? ' #' + esc(l.target_id) : ''}</td>
                  <td class="detail-cell">${prettyDetail(l.detail)}</td>
                  <td class="muted">${esc(l.ip || '—')}</td>
                </tr>`
              )
              .join('')
          : '';

        pages = data.pages;
        document.querySelector('[data-log-empty]').hidden = data.items.length > 0;
        document.querySelector('[data-log-pager]').hidden = data.pages <= 1;
        document.querySelector('[data-log-page-info]').textContent = `第 ${data.page} / ${data.pages} 页（共 ${data.total} 条）`;
        document.querySelector('[data-log-page="prev"]').disabled = data.page <= 1;
        document.querySelector('[data-log-page="next"]').disabled = data.page >= data.pages;
      } catch (err) {
        toast(err.message, 'error');
      }
    }

    document.querySelector('[data-log-action]').addEventListener('change', () => {
      page = 1;
      loadLogs();
    });
    document.querySelector('[data-reload-logs]').addEventListener('click', loadLogs);
    document.querySelector('[data-log-pager]').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-log-page]');
      if (!btn || btn.disabled) return;
      if (btn.dataset.logPage === 'prev' && page > 1) page -= 1;
      if (btn.dataset.logPage === 'next' && page < pages) page += 1;
      loadLogs();
    });

    loadLogs();
  }
})();

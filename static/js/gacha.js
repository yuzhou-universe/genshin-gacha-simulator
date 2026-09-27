/* 抽卡界面：底部大图 + 固定操作 UI + 固定抽卡演出。
 *
 * 演出流程对所有卡池完全一致，不随卡池切换而变化：
 *   星光跃迁 → 结果逐个翻出 → 若有五星，最后单独呈现 → AI 点评
 *
 * 抽卡接口立即返回结果，AI 点评是**另发的异步请求**。因此模型超时、
 * 报错、未配置，都只影响那句点评，抽卡本身已经完成。
 */
(function () {
  'use strict';

  const PLACEHOLDER_5STAR = '/static/img/placeholder-5star.svg';
  const DEFAULT_BG = '/static/img/banner-default.svg';

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const el = {
    art: document.querySelector('[data-pool-art]'),
    bg: document.querySelector('[data-pool-bg]'),
    banner: document.querySelector('[data-pool-banner]'),
    name: document.querySelector('[data-pool-name]'),
    desc: document.querySelector('[data-pool-desc]'),
    up: document.querySelector('[data-pool-up]'),
    upItems: document.querySelector('[data-pool-up-items]'),
    rates: document.querySelector('[data-pool-rates]'),
    epitomized: document.querySelector('[data-epitomized]'),
    epiPoints: document.querySelector('[data-epi-points]'),
    epiChoices: document.querySelector('[data-epi-choices]'),

    curPrimogems: document.querySelector('[data-cur-primogems]'),
    curIntertwined: document.querySelector('[data-cur-intertwined]'),
    curAcquaint: document.querySelector('[data-cur-acquaint]'),

    dockPool: document.querySelector('[data-dock-pool]'),
    dockPity: document.querySelector('[data-dock-pity]'),
    dockFill: document.querySelector('[data-dock-fill]'),
    dockNote: document.querySelector('[data-dock-note]'),
    dockCost: document.querySelector('[data-dock-cost]'),

    switcher: document.querySelector('[data-switcher]'),
    poolList: document.querySelector('[data-pool-list]'),

    wish: document.querySelector('[data-wish]'),
    warp: document.querySelector('[data-wish-warp]'),
    grid: document.querySelector('[data-wish-grid]'),
    five: document.querySelector('[data-wish-five]'),
    fiveImg: document.querySelector('[data-five-img]'),
    fiveName: document.querySelector('[data-five-name]'),
    fiveTag: document.querySelector('[data-five-tag]'),
    ai: document.querySelector('[data-wish-ai]'),
  };

  const state = {
    pools: [],
    current: null,
    pity: {},        // pool_id -> { pity_5, ... }
    busy: false,
    skip: false,
  };

  const esc = window.App.escapeHtml;
  const sleep = (ms) => new Promise((r) => setTimeout(r, state.skip ? 0 : ms));

  /* ------------------------------------------------------------------
     数据加载
     ------------------------------------------------------------------ */
  async function loadPools() {
    state.pools = await window.App.request('/api/pools');
    if (!state.pools.length) {
      el.name.textContent = '暂无开放中的卡池';
      el.desc.textContent = '请联系管理员在后台开启卡池。';
      return;
    }
    const main = state.pools.find((p) => p.is_main) || state.pools[0];
    await selectPool(main.id);
  }

  async function selectPool(poolId) {
    const pool = state.pools.find((p) => p.id === poolId);
    if (!pool) return;
    state.current = pool;
    try {
      localStorage.setItem('gacha:lastPool', String(poolId));
    } catch (_) { /* 隐私模式下忽略 */ }

    renderPool(pool);
    closeSwitcher();
    await refreshPity(pool.id);
  }

  async function refreshPity(poolId) {
    try {
      const data = await window.App.request('/api/pull/pity/' + poolId);
      state.pity[poolId] = data;
      renderPity();
    } catch (_) { /* 保底信息拿不到不影响抽卡 */ }
  }

  /* ------------------------------------------------------------------
     渲染
     ------------------------------------------------------------------ */
  function renderPool(pool) {
    el.name.textContent = pool.name;
    el.desc.textContent = pool.description || '';

    el.bg.src = pool.background_image_url || DEFAULT_BG;
    if (pool.banner_url) {
      el.banner.src = pool.banner_url;
      el.banner.hidden = false;
    } else {
      el.banner.hidden = true;
    }

    // UP 内容
    const ups = pool.up_items_5.concat(pool.up_items_4);
    el.up.hidden = ups.length === 0;
    el.upItems.innerHTML = ups
      .map(
        (i) =>
          `<span class="up-chip star-${i.star}">
             <span class="up-chip__star">${'★'.repeat(i.star)}</span>${esc(i.name)}
           </span>`
      )
      .join('');

    renderRates(pool);
    renderEpitomized(pool);
    renderDock(pool, state.pity[pool.id]);
  }

  function renderRates(p) {
    const lines = [];
    const pct = (v) => (v * 100).toFixed(1).replace(/\.0$/, '') + '%';

    lines.push(`五星基础概率 <b>${pct(p.base_rate_5)}</b>，最多 <b>${p.hard_pity_5}</b> 抽必出五星`);
    lines.push(`四星基础概率 <b>${pct(p.base_rate_4)}</b>，最多 <b>${p.hard_pity_4}</b> 抽必出四星或以上`);

    if (p.soft_pity_enabled) {
      lines.push(
        `第 <b>${p.soft_pity_start}</b> 抽起五星概率每抽提升 <b>${pct(p.soft_pity_step)}</b>` +
          (p.soft_pity_cap_pull
            ? `，第 <b>${p.soft_pity_cap_pull}</b> 抽到达 100%`
            : '')
      );
    }

    if (p.has_5050) {
      lines.push(
        `五星有 <b>${pct(p.up_rate_5)}</b> 概率为 UP 物品；` +
          `若未获得 UP，则下一个五星必定为 UP`
      );
    } else {
      lines.push('本卡池<b>无 UP</b>，所有五星均来自固定物品池');
    }

    if (p.epitomized_enabled) {
      lines.push(
        `神铸定轨：命定值满 <b>${p.epitomized_max_points}</b> 后，下一个五星必定是定轨武器`
      );
    }
    if (p.capturing_radiance_enabled) {
      lines.push('已开启<b>捕获明光</b>，连续歪三次后下一个五星必定为 UP');
    }
    if (p.soft_pity_cap_pull && p.soft_pity_cap_pull < p.hard_pity_5) {
      lines.push(
        `<b>提示：</b>本卡池五星概率在第 ${p.soft_pity_cap_pull} 抽即到达 100%，` +
          `早于硬保底 ${p.hard_pity_5} 抽`
      );
    }
    if (p.lifetime_pull_cap) {
      lines.push(`本卡池终身最多可抽 <b>${p.lifetime_pull_cap}</b> 抽`);
    }

    el.rates.innerHTML = lines.map((t) => `<div>· ${t}</div>`).join('');
  }

  function renderEpitomized(pool) {
    if (!pool.epitomized_enabled) {
      el.epitomized.hidden = true;
      return;
    }
    el.epitomized.hidden = false;
    const epi = state.pity[pool.id] && state.pity[pool.id].epitomized;
    const points = epi ? epi.fate_points : 0;
    const max = epi ? epi.max_points : pool.epitomized_max_points;
    const charted = epi ? epi.charted_item_id : null;

    el.epiPoints.innerHTML = `命定值 <b>${points}</b> / ${max}`;
    el.epiChoices.innerHTML = pool.up_items_5
      .map(
        (i) =>
          `<button type="button" class="epi-choice${i.id === charted ? ' is-active' : ''}"
                   data-epi="${i.id}">${esc(i.name)}</button>`
      )
      .join('');
  }

  function renderDock(pool, pity) {
    el.dockPool.textContent = pool.name;

    const cost = pool.cost_per_pull;
    const ten = pool.cost_per_ten != null ? pool.cost_per_ten : cost * 10;
    const label = { primogems: '原石', intertwined_fate: '纠缠之缘', acquaint_fate: '相遇之缘' }[
      pool.cost_currency
    ] || pool.cost_currency;
    el.dockCost.textContent = `单抽 ${cost} ${label} · 十连 ${ten} ${label}`;

    if (pity) {
      const p5 = pity.pity_5;
      const hard = pool.hard_pity_5;
      const pct = Math.min(100, (p5 / hard) * 100);
      el.dockFill.style.width = pct + '%';
      el.dockPity.textContent = `${p5} / ${hard}`;

      const notes = [];
      if (pity.guaranteed_5) {
        notes.push('<b style="color:var(--gold)">大保底</b>：下一个五星必定为 UP');
      }
      if (pity.epitomized && pity.epitomized.charted_item_id) {
        notes.push(
          `定轨「${esc(pity.epitomized.charted_item_name || '')}」` +
            `命定值 ${pity.epitomized.fate_points}/${pity.epitomized.max_points}`
        );
      }
      if (pool.lifetime_pull_cap) {
        notes.push(`剩余 ${pity.remaining_pulls != null ? pity.remaining_pulls : '—'} 抽`);
      }
      el.dockNote.innerHTML = notes.join(' · ');
    } else {
      el.dockFill.style.width = '0%';
      el.dockPity.textContent = '— / ' + pool.hard_pity_5;
      el.dockNote.textContent = '';
    }
  }

  function renderWallet(currency) {
    if (!currency) return;
    el.curPrimogems.textContent = currency.primogems;
    el.curIntertwined.textContent = currency.intertwined_fate;
    el.curAcquaint.textContent = currency.acquaint_fate;
  }

  /* ------------------------------------------------------------------
     抽卡
     ------------------------------------------------------------------ */
  async function doPull(count) {
    if (state.busy || !state.current) return;
    state.busy = true;
    state.skip = false;

    const buttons = document.querySelectorAll('[data-pull]');
    buttons.forEach((b) => (b.disabled = true));

    try {
      const endpoint = count === 10 ? '/api/pull/ten' : '/api/pull/single';
      const result = await window.App.request(endpoint, {
        method: 'POST',
        body: { pool_id: state.current.id },
      });

      // 抽卡已完成并落库。先更新数值，再放演出。
      renderWallet(result.currency);
      state.pity[state.current.id] = result.pity;
      if (result.epitomized) {
        state.pity[state.current.id].epitomized = result.epitomized;
      }
      renderDock(state.current, state.pity[state.current.id]);
      renderEpitomized(state.current);

      await playWish(result);
      loadAiComment(result);
    } catch (err) {
      window.App.toast(err.message, 'error', 4200);
    } finally {
      state.busy = false;
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  /* ------------------------------------------------------------------
     演出
     ------------------------------------------------------------------ */
  function starClass(star) {
    return 'star-' + star;
  }

  function cardHtml(item, hidden) {
    const img = item.image_url
      ? `<img class="wish-card__img" src="${esc(item.image_url)}" alt="">`
      : '';
    return `<div class="wish-card ${starClass(item.star)}" ${hidden ? 'hidden' : ''}>
      ${img}
      ${item.is_up ? '<span class="wish-card__up">UP</span>' : ''}
      <div class="wish-card__body">
        <span class="wish-card__name">${esc(item.name)}</span>
        <span class="wish-card__stars">${'★'.repeat(item.star)}</span>
      </div>
    </div>`;
  }

  async function playWish(result) {
    el.wish.hidden = false;
    el.five.hidden = true;
    el.ai.hidden = true;
    el.warp.hidden = false;
    el.grid.innerHTML = '';
    el.grid.dataset.count = String(result.count);

    // ---- 阶段一：星光跃迁 ----
    await sleep(reduceMotion ? 0 : 1050);
    el.warp.hidden = true;

    // ---- 阶段二：逐个翻出（五星最后单独呈现，所以先只放非五星） ----
    const normal = result.items.filter((i) => i.star < 5);
    const fiveStars = result.items.filter((i) => i.star === 5);
    el.grid.innerHTML = normal.map((i) => cardHtml(i, false)).join('');

    const cards = el.grid.querySelectorAll('.wish-card');
    for (let i = 0; i < cards.length; i++) {
      cards[i].style.animationDelay = '0ms';
      await sleep(reduceMotion ? 0 : 90);
    }
    await sleep(reduceMotion ? 0 : 260);

    // ---- 阶段三：五星单独呈现 ----
    if (fiveStars.length) {
      const five = fiveStars[fiveStars.length - 1];
      el.fiveImg.src = five.image_url || PLACEHOLDER_5STAR;
      el.fiveImg.onerror = () => {
        el.fiveImg.onerror = null;
        el.fiveImg.src = PLACEHOLDER_5STAR;
      };
      el.fiveName.textContent = five.name;
      el.fiveTag.textContent = five.is_up
        ? 'UP · 五星'
        : five.source === 'epitomized'
          ? '定轨 · 五星'
          : '五星';
      el.five.hidden = false;
      el.five.style.animation = 'none';
      void el.five.offsetWidth;
      el.five.style.animation = '';
    }
  }

  async function loadAiComment(result) {
    const five = result.items.find((i) => i.star === 5);
    try {
      const data = await window.App.request('/api/ai/comment', {
        method: 'POST',
        body: {
          pool_id: result.pool_id,
          pool_name: result.pool_name,
          batch_id: result.batch_id,
          items: result.items.map((i) => ({
            name: i.name,
            star: i.star,
            is_up: i.is_up,
            is_pity: i.is_pity,
          })),
          pity_5: result.pity.pity_5,
        },
      });
      if (data && data.comment) {
        el.ai.textContent = data.comment;
        el.ai.hidden = false;
      }
    } catch (_) {
      // AI 不可用时静默跳过 —— 本地模板文案已在服务端兜底，
      // 但即便这里完全失败，抽卡结果与演出也已经完整呈现。
    }
    if (five) el.ai.dataset.hasFive = '1';
  }

  function closeWish() {
    state.skip = true;
    el.wish.hidden = true;
    el.five.hidden = true;
    el.ai.hidden = true;
    el.grid.innerHTML = '';
  }

  /* ------------------------------------------------------------------
     抽屉
     ------------------------------------------------------------------ */
  function openSwitcher() {
    el.poolList.innerHTML = state.pools
      .map(
        (p) => `<button type="button" class="pool-card${
          state.current && p.id === state.current.id ? ' is-active' : ''
        }" data-pool="${p.id}">
          ${p.icon_url ? `<img class="pool-card__icon" src="${esc(p.icon_url)}" alt="">` : '<span class="pool-card__icon"></span>'}
          <span class="pool-card__body">
            <span class="pool-card__name">${esc(p.name)}${
              p.is_main ? '<span class="pool-card__flag">主推</span>' : ''
            }</span>
            <span class="pool-card__meta">${p.hard_pity_5} 抽保底${
              p.has_5050 ? ' · UP ' + Math.round(p.up_rate_5 * 100) + '%' : ' · 无 UP'
            }</span>
          </span>
        </button>`
      )
      .join('');
    el.switcher.hidden = false;
  }

  function closeSwitcher() {
    if (el.switcher) el.switcher.hidden = true;
  }

  /* ------------------------------------------------------------------
     事件绑定
     ------------------------------------------------------------------ */
  document.addEventListener('click', async (e) => {
    const pullBtn = e.target.closest('[data-pull]');
    if (pullBtn) {
      e.preventDefault();
      await doPull(Number(pullBtn.dataset.pull));
      return;
    }

    if (e.target.closest('[data-open-switcher]')) {
      e.preventDefault();
      openSwitcher();
      return;
    }
    if (e.target.closest('[data-close-switcher]')) {
      e.preventDefault();
      closeSwitcher();
      return;
    }

    const poolBtn = e.target.closest('[data-pool]');
    if (poolBtn) {
      e.preventDefault();
      await selectPool(Number(poolBtn.dataset.pool));
      return;
    }

    const epiBtn = e.target.closest('[data-epi]');
    if (epiBtn) {
      e.preventDefault();
      await setEpitomized(Number(epiBtn.dataset.epi));
      return;
    }

    if (e.target.closest('[data-wish-skip]')) {
      e.preventDefault();
      closeWish();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!el.wish.hidden) closeWish();
      else closeSwitcher();
    }
  });

  async function setEpitomized(itemId) {
    const pool = state.current;
    if (!pool) return;
    // 再次点击已选中的武器 = 取消定轨
    const epi = state.pity[pool.id] && state.pity[pool.id].epitomized;
    const isActive = epi && epi.charted_item_id === itemId;
    try {
      const data = await window.App.request('/api/pull/epitomized', {
        method: 'POST',
        body: { pool_id: pool.id, item_id: isActive ? null : itemId },
      });
      if (!state.pity[pool.id]) state.pity[pool.id] = {};
      state.pity[pool.id].epitomized = data;
      renderEpitomized(pool);
      renderDock(pool, state.pity[pool.id]);
      window.App.toast(isActive ? '已取消定轨' : '已定轨：' + (data.charted_item_name || ''), 'success');
    } catch (err) {
      window.App.toast(err.message, 'error');
    }
  }

  /* ------------------------------------------------------------------
     启动
     ------------------------------------------------------------------ */
  (async function init() {
    try {
      // 货币余额已由服务端渲染进页面，这里只需加载卡池
      await loadPools();
    } catch (err) {
      window.App.toast(err.message, 'error', 5000);
    }
  })();
})();

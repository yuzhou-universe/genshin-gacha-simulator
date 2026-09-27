/* 抽卡界面（对齐原神实机）
 *
 * 结构：顶部页签切换卡池 / 居中横向海报卡 / 底栏祈愿按钮
 * 演出：星光跃迁 → 结果逐个翻出 → 若有五星，最后单独呈现 → AI 点评
 *       所有卡池共用同一套，不随卡池切换而变化
 *
 * 抽卡接口立即返回结果，AI 点评是**另发的异步请求**。因此模型超时、报错、
 * 未配置，都只影响那句点评，抽卡本身已经完成。
 */
(function () {
  'use strict';

  const PLACEHOLDER_5STAR = '/static/img/placeholder-5star.svg';
  const DEFAULT_BG = '/static/img/banner-default.svg';

  const POOL_TYPE_LABEL = {
    character: '角色活动祈愿',
    weapon: '武器活动祈愿',
    standard: '常驻祈愿',
    beginner: '新手祈愿',
  };

  const CURRENCY = {
    primogems: { label: '原石', icon: 'gem' },
    intertwined_fate: { label: '纠缠之缘', icon: 'intertwined' },
    acquaint_fate: { label: '相遇之缘', icon: 'acquaint' },
  };

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const qs = (sel) => document.querySelector(sel);
  const esc = window.App.escapeHtml;

  const el = {
    banner: qs('[data-banner]'),
    poolTabs: qs('[data-pool-tabs]'),
    bg: qs('[data-pool-bg]'),

    poolType: qs('[data-pool-type]'),
    poolName: qs('[data-pool-name]'),
    poolLead: qs('[data-pool-lead]'),
    poolHighlight: qs('[data-pool-highlight]'),
    poolDesc: qs('[data-pool-desc]'),
    poolTimer: qs('[data-pool-timer]'),
    poolArtImg: qs('[data-pool-art-img]'),
    plate: qs('[data-plate]'),
    plateName: qs('[data-plate-name]'),
    plateStars: qs('[data-plate-stars]'),
    upFlag: qs('[data-up-flag]'),
    poolDetail: qs('[data-pool-detail]'),

    curPrimogems: qs('[data-cur-primogems]'),
    curContext: qs('[data-cur-context]'),
    curContextIcon: qs('[data-cur-context-icon]'),
    curContextValue: qs('[data-cur-context-value]'),
    curOther: qs('[data-cur-other]'),
    curOtherIcon: qs('[data-cur-other-icon]'),
    curOtherValue: qs('[data-cur-other-value]'),

    walletSource: qs('[data-wallet]'),
    topup: qs('[data-topup]'),
    topupToggle: qs('[data-topup-toggle]'),

    epitomized: qs('[data-epitomized]'),
    epiDial: qs('[data-epi-dial]'),
    epiPoints: qs('[data-epi-points]'),
    epiName: qs('[data-epi-name]'),
    epiChoices: qs('[data-epi-choices]'),
    epiPicker: qs('[data-epi-picker]'),

    detail: qs('[data-detail]'),

    wish: qs('[data-wish]'),
    warp: qs('[data-wish-warp]'),
    grid: qs('[data-wish-grid]'),
    five: qs('[data-wish-five]'),
    fiveImg: qs('[data-five-img]'),
    fiveName: qs('[data-five-name]'),
    fiveTag: qs('[data-five-tag]'),
    ai: qs('[data-wish-ai]'),
  };

  const state = {
    pools: [],
    current: null,
    pity: {},
    wallet: { primogems: 0, intertwined_fate: 0, acquaint_fate: 0 },
    busy: false,
    skip: false,
    timer: null,
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, state.skip ? 0 : ms));

  /* ==================================================================
     数据
     ================================================================== */
  async function loadPools() {
    state.pools = await window.App.request('/api/pools');
    if (!state.pools.length) {
      el.poolName.textContent = '暂无开放中的卡池';
      el.poolDesc.textContent = '请联系管理员在后台开启卡池。';
      return;
    }
    renderTabs();
    // 优先沿用上次选择，其次主推，最后第一个
    let last = null;
    try {
      last = Number(localStorage.getItem('gacha:lastPool'));
    } catch (_) { /* 隐私模式下忽略 */ }
    const pool =
      state.pools.find((p) => p.id === last) ||
      state.pools.find((p) => p.is_main) ||
      state.pools[0];
    await selectPool(pool.id);
  }

  async function selectPool(poolId) {
    const pool = state.pools.find((p) => p.id === poolId);
    if (!pool) return;
    state.current = pool;
    try {
      localStorage.setItem('gacha:lastPool', String(poolId));
    } catch (_) { /* 忽略 */ }

    renderTabs();
    renderBanner(pool);
    renderWallet();
    await refreshPity(pool.id);
  }

  async function refreshPity(poolId) {
    try {
      const data = await window.App.request('/api/pull/pity/' + poolId);
      state.pity[poolId] = data;
      renderEpitomized();
    } catch (_) { /* 保底信息拿不到不影响抽卡 */ }
  }

  /* ==================================================================
     顶栏：卡池页签
     ================================================================== */
  function renderTabs() {
    el.poolTabs.innerHTML = state.pools
      .map((p) => {
        const active = state.current && p.id === state.current.id;
        const icon = p.icon_url
          ? `<img class="pool-tab__img" src="${esc(p.icon_url)}" alt="">`
          : `<span class="pool-tab__fallback">${esc(p.name.slice(0, 1))}</span>`;
        return `<button type="button" class="pool-tab pool-theme--${esc(p.pool_type)}${
          active ? ' is-active' : ''
        }" data-pool="${p.id}" title="${esc(p.name)}"
          style="--accent:${
            { character: '#2fa89f', weapon: '#e0863a', standard: '#8375d4', beginner: '#4f9e6a' }[
              p.pool_type
            ] || '#d9a441'
          }">
          ${icon}
          <span class="pool-tab__name">${esc(p.name)}</span>
        </button>`;
      })
      .join('');
  }

  /* ==================================================================
     海报卡
     ================================================================== */
  function renderBanner(pool) {
    el.banner.className = 'banner pool-theme--' + pool.pool_type;

    el.poolType.textContent = POOL_TYPE_LABEL[pool.pool_type] || '祈愿';
    el.poolName.textContent = pool.name;
    el.poolDesc.textContent = pool.description || '';

    const hasUp = pool.up_items_5.length > 0;
    el.poolLead.hidden = !hasUp;
    el.poolHighlight.textContent = '每十次祈愿必出四星或以上物品。';

    // 背景图：管理员为卡池设置的整屏背景，未设置则用内置晴空
    el.bg.src = pool.background_image_url || DEFAULT_BG;

    // 立绘与名牌：有 UP 用 UP，没有（常驻/新手）用池中的五星
    const featured = (pool.featured_items_5 || [])[0];
    if (featured) {
      el.poolArtImg.src = featured.image_url || PLACEHOLDER_5STAR;
      el.poolArtImg.alt = featured.name;
      el.poolArtImg.onerror = () => {
        el.poolArtImg.onerror = null;
        el.poolArtImg.src = PLACEHOLDER_5STAR;
      };
      el.poolArtImg.hidden = false;
      el.plateName.textContent = featured.name;
      el.plateStars.textContent = '★'.repeat(featured.star);
      el.plate.hidden = false;
      el.upFlag.hidden = !featured.is_up;
    } else {
      el.poolArtImg.hidden = true;
      el.plate.hidden = true;
      el.upFlag.hidden = true;
    }

    renderTimer(pool);
    renderDetail(pool);
  }

  /** 剩余时间倒计时。每分钟刷新一次就够了 —— 秒级跳动反而干扰阅读。 */
  function renderTimer(pool) {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    if (!pool.close_at) {
      // 没设结束时间就是长期开放。这里不整块隐藏：区块留在原位，
      // 海报卡左下才不会空出一大块。
      el.poolTimer.innerHTML = '<b>长期开放</b>';
      el.poolTimer.hidden = false;
      return;
    }

    const end = new Date(pool.close_at).getTime();
    const tick = () => {
      const ms = end - Date.now();
      if (ms <= 0) {
        el.poolTimer.innerHTML = '<b>已结束</b>';
        return;
      }
      const d = Math.floor(ms / 86400000);
      const h = Math.floor(ms / 3600000) % 24;
      const m = Math.floor(ms / 60000) % 60;
      el.poolTimer.innerHTML = `<b>${d}天${h}时${m}分</b>`;
    };

    tick();
    el.poolTimer.hidden = false;
    state.timer = setInterval(tick, 30000);
  }

  /** 详情弹窗内容：概率说明 + 展示物品。复用同一份数据，不额外请求。 */
  function renderDetail(pool) {
    const pct = (v) => (v * 100).toFixed(1).replace(/\.0$/, '') + '%';
    const lines = [];

    lines.push(
      `五星基础概率 <b>${pct(pool.base_rate_5)}</b>，最多 <b>${pool.hard_pity_5}</b> 抽必出五星`
    );
    lines.push(
      `四星基础概率 <b>${pct(pool.base_rate_4)}</b>，最多 <b>${pool.hard_pity_4}</b> 抽必出四星或以上`
    );
    if (pool.soft_pity_enabled) {
      lines.push(
        `第 <b>${pool.soft_pity_start}</b> 抽起五星概率每抽提升 <b>${pct(pool.soft_pity_step)}</b>` +
          (pool.soft_pity_cap_pull ? `，第 <b>${pool.soft_pity_cap_pull}</b> 抽到达 100%` : '')
      );
    }
    if (pool.has_5050) {
      lines.push(
        `五星有 <b>${pct(pool.up_rate_5)}</b> 概率为 UP 物品；若未获得 UP，则下一个五星必定为 UP`
      );
    } else {
      lines.push('本卡池<b>无 UP</b>，所有五星均来自固定物品池');
    }
    if (pool.epitomized_enabled) {
      lines.push(
        `神铸定轨：命定值满 <b>${pool.epitomized_max_points}</b> 后，下一个五星必定是定轨武器`
      );
    }
    if (pool.capturing_radiance_enabled) {
      lines.push('已开启<b>捕获明光</b>，连续歪三次后下一个五星必定为 UP');
    }
    if (pool.soft_pity_cap_pull && pool.soft_pity_cap_pull < pool.hard_pity_5) {
      lines.push(
        `本卡池五星概率在第 ${pool.soft_pity_cap_pull} 抽即到达 100%，早于硬保底 ${pool.hard_pity_5} 抽`
      );
    }
    if (pool.lifetime_pull_cap) {
      lines.push(`本卡池终身最多可抽 <b>${pool.lifetime_pull_cap}</b> 抽`);
    }

    const chips = (items) =>
      items
        .map(
          (i) => `<span class="detail-chip star-${i.star}">
            ${'★'.repeat(i.star)} ${esc(i.name)}
          </span>`
        )
        .join('');

    const up5 = pool.up_items_5 || [];
    const up4 = pool.up_items_4 || [];

    el.poolDetail.innerHTML = `
      <div class="detail-block">
        <div class="detail-block__title">概率说明</div>
        ${lines.map((t) => `<div class="detail-line">· ${t}</div>`).join('')}
      </div>
      ${
        up5.length
          ? `<div class="detail-block">
               <div class="detail-block__title">五星 UP</div>
               <div class="detail-items">${chips(up5)}</div>
             </div>`
          : ''
      }
      ${
        up4.length
          ? `<div class="detail-block">
               <div class="detail-block__title">四星 UP</div>
               <div class="detail-items">${chips(up4)}</div>
             </div>`
          : ''
      }
      ${
        !up5.length
          ? `<div class="detail-block">
               <div class="detail-block__title">五星物品</div>
               <div class="detail-items">${chips(pool.featured_items_5 || [])}</div>
             </div>`
          : ''
      }`;
  }

  /* ==================================================================
     货币
     ================================================================== */
  function otherFate(costCurrency) {
    return costCurrency === 'intertwined_fate' ? 'acquaint_fate' : 'intertwined_fate';
  }

  function renderWallet() {
    const w = state.wallet;
    el.curPrimogems.textContent = w.primogems;

    // 顶栏右侧放"本卡池消耗的那种缘"，底栏左侧放另一种 —— 与实机一致
    const cost = (state.current && state.current.cost_currency) || 'intertwined_fate';
    applyCurrencySlot('curContext', cost, w[cost]);
    const other = otherFate(cost);
    applyCurrencySlot('curOther', other, w[other]);

    // 祈愿按钮上的消耗图标跟着卡池走
    document.querySelectorAll('[data-wish-icon-1], [data-wish-icon-10]').forEach((node) => {
      node.className = 'cur__icon cur__icon--' + CURRENCY[cost].icon;
    });

    // 十连消耗按卡池定价（新手池八折）
    const ten =
      state.current && state.current.cost_per_ten != null
        ? state.current.cost_per_ten
        : (state.current ? state.current.cost_per_pull * 10 : 10);
    const costLabel = qs('[data-wish-cost-10]');
    if (costLabel) costLabel.textContent = '× ' + ten;

    syncTopupState();
  }

  function applyCurrencySlot(kind, currency, value) {
    const key = kind === 'curContext' ? 'Context' : 'Other';
    const iconNode = el['cur' + key + 'Icon'];
    const valueNode = el['cur' + key + 'Value'];
    const box = el[kind];
    if (!iconNode || !valueNode) return;
    iconNode.className = 'cur__icon cur__icon--' + CURRENCY[currency].icon;
    valueNode.textContent = value;
    if (box) box.title = CURRENCY[currency].label;
  }

  /** 顶到上限时禁用「+」，避免用户点了却什么都没发生。 */
  function syncTopupState() {
    if (!el.topupToggle || !el.topup) return;
    const cap = Number(el.topup.dataset.cap) || 0;
    const reached = cap > 0 && state.wallet.primogems >= cap;

    el.topupToggle.disabled = reached;
    el.topupToggle.title = reached ? '已达上限' : '增加原石';
    el.topup
      .querySelectorAll('[data-topup-amount]')
      .forEach((btn) => (btn.disabled = reached));

    if (reached) closeTopup();
  }

  async function addPrimogems(amount, button) {
    await window.App.withBusy(button, async () => {
      try {
        const data = await window.App.request('/api/currency/topup', {
          method: 'POST',
          body: { amount },
        });
        state.wallet.primogems = data.primogems;
        renderWallet();

        el.curPrimogems.classList.remove('is-bumped');
        void el.curPrimogems.offsetWidth; // 重启动画
        el.curPrimogems.classList.add('is-bumped');

        if (data.added === 0) {
          window.App.toast(`原石已达上限 ${data.cap.toLocaleString()}`, 'info');
        } else if (data.capped) {
          // 被截断是因为上限，如实告知实际加了多少，而不是假装加满
          window.App.toast(
            `已达上限，本次只增加 ${data.added.toLocaleString()}（当前 ${data.primogems.toLocaleString()}）`,
            'info',
            3600
          );
        } else {
          window.App.toast(`原石 +${data.added.toLocaleString()}`, 'success');
        }
      } catch (err) {
        window.App.toast(err.message, 'error');
      }
    });
  }

  function openTopup() {
    el.topup.hidden = false;
    el.topupToggle.setAttribute('aria-expanded', 'true');
  }
  function closeTopup() {
    if (!el.topup) return;
    el.topup.hidden = true;
    el.topupToggle.setAttribute('aria-expanded', 'false');
  }

  /* ==================================================================
     定轨
     ================================================================== */
  function renderEpitomized() {
    const pool = state.current;
    if (!pool || !pool.epitomized_enabled) {
      el.epitomized.hidden = true;
      return;
    }
    const epi = (state.pity[pool.id] && state.pity[pool.id].epitomized) || null;
    const points = epi ? epi.fate_points : 0;
    const max = epi ? epi.max_points : pool.epitomized_max_points;
    const charted = epi ? epi.charted_item_id : null;

    el.epitomized.hidden = false;
    el.epiPoints.textContent = `${points}/${max}`;
    el.epiDial.classList.toggle('is-full', max > 0 && points >= max);
    el.epiName.textContent = (epi && epi.charted_item_name) || '未选择';

    el.epiChoices.innerHTML = pool.up_items_5
      .map(
        (i) => `<button type="button" class="epi-choice${i.id === charted ? ' is-active' : ''}"
            data-epi="${i.id}">
          <img class="epi-choice__img" src="${esc(i.image_url || PLACEHOLDER_5STAR)}" alt="">
          <span>
            <span class="epi-choice__name">${esc(i.name)}</span><br>
            <span class="epi-choice__hint">★★★★★</span>
          </span>
          ${i.id === charted ? '<span class="epi-choice__flag">已定轨</span>' : ''}
        </button>`
      )
      .join('');
  }

  async function setEpitomized(itemId) {
    const pool = state.current;
    if (!pool) return;
    const epi = (state.pity[pool.id] && state.pity[pool.id].epitomized) || null;
    // 再次点击已选中的武器 = 取消定轨
    const isActive = epi && epi.charted_item_id === itemId;
    try {
      const data = await window.App.request('/api/pull/epitomized', {
        method: 'POST',
        body: { pool_id: pool.id, item_id: isActive ? null : itemId },
      });
      if (!state.pity[pool.id]) state.pity[pool.id] = {};
      state.pity[pool.id].epitomized = data;
      renderEpitomized();
      closeEpiPicker();
      window.App.toast(
        isActive ? '已取消定轨' : '已定轨：' + (data.charted_item_name || ''),
        'success'
      );
    } catch (err) {
      window.App.toast(err.message, 'error');
    }
  }

  function openEpiPicker() {
    if (el.epiPicker) el.epiPicker.hidden = false;
  }
  function closeEpiPicker() {
    if (el.epiPicker) el.epiPicker.hidden = true;
  }

  function openDetail() {
    if (el.detail) el.detail.hidden = false;
  }
  function closeDetail() {
    if (el.detail) el.detail.hidden = true;
  }

  /* ==================================================================
     抽卡
     ================================================================== */
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
      state.wallet = result.currency;
      state.pity[result.pool_id] = result.pity;
      if (result.epitomized) {
        state.pity[result.pool_id].epitomized = result.epitomized;
      }
      renderWallet();
      renderEpitomized();

      await playWish(result);
      loadAiComment(result);
    } catch (err) {
      window.App.toast(err.message, 'error', 4200);
    } finally {
      state.busy = false;
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  /* ==================================================================
     演出
     ================================================================== */
  function cardHtml(item) {
    const img = item.image_url
      ? `<img class="wish-card__img" src="${esc(item.image_url)}" alt="">`
      : '';
    return `<div class="wish-card star-${item.star}">
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

    // ---- 阶段二：先放非五星，五星留到最后单独呈现 ----
    const normal = result.items.filter((i) => i.star < 5);
    const fiveStars = result.items.filter((i) => i.star === 5);
    el.grid.innerHTML = normal.map(cardHtml).join('');

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
    try {
      // 只回传游戏内公开信息：卡池 id、抽数、物品名称与星级、保底进度。
      // 不含用户名、用户 id、会话等任何身份字段。
      const data = await window.App.request('/api/ai/comment', {
        method: 'POST',
        body: {
          pool_id: result.pool_id,
          count: result.count,
          until_hard_pity: result.pity.until_hard_pity_5,
          guaranteed: result.pity.guaranteed_5,
          items: result.items.map((i) => ({
            name: i.name,
            star: i.star,
            is_up: i.is_up,
            is_pity: i.is_pity,
          })),
        },
      });
      if (data && data.comment) {
        el.ai.textContent = data.comment;
        el.ai.hidden = false;
      }
    } catch (_) {
      // AI 不可用时静默跳过 —— 抽卡结果与演出已经完整呈现
    }
  }

  function closeWish() {
    state.skip = true;
    el.wish.hidden = true;
    el.five.hidden = true;
    el.ai.hidden = true;
    el.grid.innerHTML = '';
  }

  /* ==================================================================
     事件
     ================================================================== */
  document.addEventListener('click', async (e) => {
    const pullBtn = e.target.closest('[data-pull]');
    if (pullBtn) {
      e.preventDefault();
      await doPull(Number(pullBtn.dataset.pull));
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

    const topupAmount = e.target.closest('[data-topup-amount]');
    if (topupAmount) {
      e.preventDefault();
      await addPrimogems(Number(topupAmount.dataset.topupAmount), topupAmount);
      return;
    }

    if (e.target.closest('[data-topup-toggle]')) {
      e.preventDefault();
      el.topup.hidden ? openTopup() : closeTopup();
      return;
    }
    if (e.target.closest('[data-epi-dial]')) {
      e.preventDefault();
      openEpiPicker();
      return;
    }
    if (e.target.closest('[data-open-detail]')) {
      e.preventDefault();
      openDetail();
      return;
    }
    if (e.target.closest('[data-close-detail]')) {
      e.preventDefault();
      closeDetail();
      return;
    }
    if (e.target.closest('[data-close-epi]')) {
      e.preventDefault();
      closeEpiPicker();
      return;
    }
    if (e.target.closest('[data-wish-skip]')) {
      e.preventDefault();
      closeWish();
      return;
    }

    // 点面板外部收起
    if (el.topup && !el.topup.hidden && !e.target.closest('[data-topup]')) closeTopup();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (el.wish && !el.wish.hidden) closeWish();
    else if (el.detail && !el.detail.hidden) closeDetail();
    else if (el.epiPicker && !el.epiPicker.hidden) closeEpiPicker();
    else if (el.topup && !el.topup.hidden) closeTopup();
  });

  /* ==================================================================
     启动
     ================================================================== */
  (async function init() {
    // 货币真值由服务端渲染，先从 DOM 读进来（避免额外请求），再交给 renderWallet 分发到两个位置
    const src = el.walletSource;
    if (src) {
      state.wallet = {
        primogems: Number(src.dataset.primogems) || 0,
        intertwined_fate: Number(src.dataset.intertwined) || 0,
        acquaint_fate: Number(src.dataset.acquaint) || 0,
      };
    }
    try {
      await loadPools();
      renderWallet();
    } catch (err) {
      window.App.toast(err.message, 'error', 5000);
    }
  })();
})();

/* 全局工具：吐司提示、带 CSRF 语义的 fetch 封装、退出登录。 */
(function () {
  'use strict';

  const host = document.getElementById('toast-host');

  /** 弹出一条提示。type: 'info' | 'success' | 'error' */
  function toast(message, type = 'info', timeout = 3200) {
    if (!host) return;
    const el = document.createElement('div');
    el.className = 'toast' + (type === 'info' ? '' : ' toast--' + type);
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .25s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 260);
    }, timeout);
  }

  /**
   * fetch 封装：自动带 Cookie、解析 JSON、把后端的中文错误消息抛出来。
   * 所有失败路径都以 Error(detail) 的形式冒泡，调用方只需 try/catch。
   */
  async function request(url, options = {}) {
    const opts = Object.assign({ credentials: 'same-origin' }, options);
    // 后端据此返回 JSON 错误而不是 HTML 错误页
    opts.headers = Object.assign({ 'X-Requested-With': 'XMLHttpRequest' }, opts.headers);
    if (opts.body instanceof FormData) {
      // 交给浏览器自己设置 multipart 边界
    } else if (opts.body && typeof opts.body === 'object') {
      opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers);
      opts.body = JSON.stringify(opts.body);
    }

    const res = await fetch(url, opts);

    // 页面请求被重定向到登录页时，fetch 会跟随并返回 HTML
    const ctype = res.headers.get('content-type') || '';
    if (!ctype.includes('application/json')) {
      if (res.redirected && res.url.includes('/login')) {
        window.location.href = '/login';
        throw new Error('登录状态已失效，请重新登录');
      }
      throw new Error('服务器返回了非预期的响应');
    }

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || data.message || '请求失败 (' + res.status + ')');
    }
    return data;
  }

  /** 退出登录 */
  async function logout() {
    try {
      const data = await request('/auth/logout', { method: 'POST' });
      window.location.href = (data && data.redirect_to) || '/login';
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-logout]');
    if (btn) {
      e.preventDefault();
      logout();
    }
  });

  /** 转义 HTML，所有来自数据库的文本都要经过它再插入 DOM */
  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  /** 请求期间禁用按钮，避免重复提交（例如十连被点两次） */
  async function withBusy(button, fn) {
    if (!button || button.disabled) return;
    const original = button.innerHTML;
    button.disabled = true;
    try {
      return await fn();
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  window.App = { toast, request, logout, escapeHtml, withBusy };
})();

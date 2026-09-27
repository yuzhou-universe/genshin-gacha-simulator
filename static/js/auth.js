/* 登录 / 注册表单提交。表单在不带 JS 时也能原生提交（后端同时接受表单编码）。 */
(function () {
  'use strict';

  const form = document.querySelector('[data-auth-form]');
  if (!form) return;

  const errorBox = form.querySelector('[data-error]');
  const submitBtn = form.querySelector('button[type="submit"]');

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    if (!errorBox) return;
    errorBox.hidden = true;
    errorBox.textContent = '';
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearError();

    const endpoint = form.dataset.authForm;
    const body = new FormData(form);

    // 前端先行校验，省一次往返；后端仍会独立复核
    const username = (body.get('username') || '').toString().trim();
    const password = (body.get('password') || '').toString();

    if (username.length < 3 || username.length > 20) {
      showError('用户名需为 3-20 位');
      return;
    }
    if (password.length < 8) {
      showError('密码至少 8 位');
      return;
    }

    await window.App.withBusy(submitBtn, async () => {
      try {
        const data = await window.App.request(endpoint, { method: 'POST', body });
        window.location.href = data.redirect_to || '/';
      } catch (err) {
        showError(err.message);
      }
    });
  });
})();

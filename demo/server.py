"""Synthetic demo target; local runs use loopback and Docker may bind its isolated service."""

from __future__ import annotations

import argparse
import json
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

MAX_BODY_BYTES = 64 * 1024
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-pass"


@dataclass
class DemoState:
    lock: Any = field(default_factory=threading.RLock, repr=False)
    tokens: dict[str, float] = field(default_factory=dict, repr=False)
    resources: dict[int, dict[str, Any]] = field(default_factory=dict)
    users: dict[int, dict[str, Any]] = field(
        default_factory=lambda: {
            1: {"id": 1, "username": "demo", "display_name": "演示用户"},
            2: {"id": 2, "username": "buyer", "display_name": "采购员"},
        }
    )
    products: dict[int, dict[str, Any]] = field(
        default_factory=lambda: {
            1: {"id": 1, "name": "自动化测试键盘", "price_cents": 19900, "stock": 20},
            2: {"id": 2, "name": "质量保障鼠标", "price_cents": 9900, "stock": 30},
            3: {"id": 3, "name": "持续交付显示器", "price_cents": 129900, "stock": 10},
        }
    )
    orders: dict[int, dict[str, Any]] = field(default_factory=dict)
    next_id: int = 1
    next_order_id: int = 1
    changed_locator: bool = False
    login_count: int = 0
    flaky_attempts: dict[str, int] = field(default_factory=dict)


SITE_STYLE = """
<style>
:root {
  color-scheme: dark;
  --ink: #edf5ff;
  --muted: #9bacbf;
  --panel: rgba(14, 25, 42, 0.88);
  --panel-strong: #111f34;
  --line: rgba(148, 177, 211, 0.16);
  --cyan: #35d9e6;
  --cyan-strong: #12b8c8;
  --lime: #b7f36b;
  --danger: #ff7f89;
  --warning: #ffcb66;
  --shadow: 0 24px 80px rgba(0, 0, 0, 0.34);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", "Microsoft YaHei", sans-serif;
}
* { box-sizing: border-box; }
html { min-width: 320px; background: #07111f; }
body {
  min-height: 100vh;
  margin: 0;
  color: var(--ink);
  background:
    radial-gradient(circle at 12% 0%, rgba(53, 217, 230, 0.13), transparent 30rem),
    radial-gradient(circle at 92% 14%, rgba(183, 243, 107, 0.08), transparent 26rem),
    linear-gradient(145deg, #07111f 0%, #0a1627 48%, #081320 100%);
}
body::before {
  position: fixed;
  inset: 0;
  z-index: -1;
  content: "";
  opacity: 0.18;
  background-image:
    linear-gradient(rgba(130, 164, 198, 0.12) 1px, transparent 1px),
    linear-gradient(90deg, rgba(130, 164, 198, 0.12) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: linear-gradient(to bottom, black, transparent 82%);
}
button, input { font: inherit; }
button, a { -webkit-tap-highlight-color: transparent; }
button:focus-visible, input:focus-visible, a:focus-visible {
  outline: 3px solid rgba(53, 217, 230, 0.32);
  outline-offset: 2px;
}
button { cursor: pointer; }
button:disabled { cursor: wait; opacity: 0.65; }
.brand {
  display: inline-flex;
  align-items: center;
  gap: 0.75rem;
  color: var(--ink);
  font-size: 0.88rem;
  font-weight: 750;
  letter-spacing: 0.03em;
}
.brand-mark {
  display: grid;
  width: 2.2rem;
  height: 2.2rem;
  place-items: center;
  border: 1px solid rgba(53, 217, 230, 0.5);
  border-radius: 0.7rem;
  color: #07111f;
  background: linear-gradient(135deg, var(--cyan), var(--lime));
  box-shadow: 0 0 28px rgba(53, 217, 230, 0.18);
  font-size: 0.72rem;
  font-weight: 900;
}
.eyebrow {
  margin: 0 0 0.7rem;
  color: var(--cyan);
  font-size: 0.75rem;
  font-weight: 800;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}
.muted { color: var(--muted); }
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2rem;
  padding: 0.35rem 0.75rem;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: #c9d6e4;
  background: rgba(9, 19, 33, 0.62);
  font-size: 0.78rem;
}
.status-chip::before {
  width: 0.48rem;
  height: 0.48rem;
  border-radius: 50%;
  background: var(--lime);
  box-shadow: 0 0 12px rgba(183, 243, 107, 0.8);
  content: "";
}
.panel {
  border: 1px solid var(--line);
  border-radius: 1.35rem;
  background: var(--panel);
  box-shadow: var(--shadow);
  backdrop-filter: blur(18px);
}
.field {
  display: grid;
  gap: 0.48rem;
}
.field-label {
  color: #c7d4e4;
  font-size: 0.86rem;
  font-weight: 700;
}
input {
  width: 100%;
  min-height: 3rem;
  border: 1px solid rgba(150, 178, 210, 0.22);
  border-radius: 0.82rem;
  padding: 0 0.95rem;
  color: var(--ink);
  background: rgba(5, 13, 24, 0.78);
  transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
}
input::placeholder { color: #65758a; }
input:hover { border-color: rgba(53, 217, 230, 0.42); }
input:focus {
  border-color: var(--cyan);
  outline: none;
  background: rgba(7, 17, 31, 0.98);
  box-shadow: 0 0 0 4px rgba(53, 217, 230, 0.1);
}
.primary-button, .secondary-button, .ghost-button, .danger-button {
  min-height: 2.8rem;
  border-radius: 0.82rem;
  padding: 0.65rem 1rem;
  font-weight: 780;
  transition: transform 150ms ease, border-color 150ms ease, background 150ms ease;
}
.primary-button {
  border: 0;
  color: #06131e;
  background: linear-gradient(135deg, var(--cyan), #7be5cd 54%, var(--lime));
  box-shadow: 0 10px 28px rgba(53, 217, 230, 0.18);
}
.primary-button:hover { transform: translateY(-1px); }
.secondary-button {
  border: 1px solid rgba(53, 217, 230, 0.32);
  color: var(--cyan);
  background: rgba(53, 217, 230, 0.08);
}
.secondary-button:hover { background: rgba(53, 217, 230, 0.14); }
.ghost-button {
  border: 1px solid var(--line);
  color: #d0dbea;
  background: rgba(255, 255, 255, 0.035);
}
.ghost-button:hover { border-color: rgba(53, 217, 230, 0.32); }
.danger-button {
  min-height: 2.25rem;
  border: 1px solid rgba(255, 127, 137, 0.25);
  color: #ffadb4;
  background: rgba(255, 127, 137, 0.08);
  font-size: 0.78rem;
}
.danger-button:hover { background: rgba(255, 127, 137, 0.14); }
.empty-state {
  margin: 0;
  padding: 1.25rem;
  border: 1px dashed rgba(147, 177, 210, 0.18);
  border-radius: 0.9rem;
  color: #7f91a7;
  text-align: center;
  font-size: 0.86rem;
}
.is-hidden { display: none !important; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
</style>
"""


def login_html(changed: bool) -> str:
    button_id = "signin-confirm" if changed else "login-btn"
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="AI 原生智能测试编排平台的本机合成验收目标">
<title>V1 验收 · 登录</title>
SITE_STYLE
<style>
.auth-page {
  display: grid;
  min-height: 100vh;
  place-items: center;
  padding: 2rem;
}
.auth-shell {
  width: min(68rem, 100%);
}
.auth-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.2rem;
}
.auth-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(22rem, 0.85fr);
  overflow: hidden;
}
.auth-intro {
  position: relative;
  display: flex;
  min-height: 34rem;
  flex-direction: column;
  justify-content: space-between;
  padding: clamp(2rem, 5vw, 4.5rem);
  border-right: 1px solid var(--line);
  background:
    linear-gradient(150deg, rgba(53, 217, 230, 0.08), transparent 48%),
    rgba(8, 18, 32, 0.45);
}
.auth-intro::after {
  position: absolute;
  right: -4rem;
  bottom: -6rem;
  width: 18rem;
  height: 18rem;
  border: 1px solid rgba(53, 217, 230, 0.18);
  border-radius: 50%;
  content: "";
  box-shadow:
    0 0 0 2rem rgba(53, 217, 230, 0.025),
    0 0 0 5rem rgba(53, 217, 230, 0.018);
}
.auth-intro h1 {
  max-width: 9ch;
  margin: 0;
  font-size: clamp(2.65rem, 6vw, 4.8rem);
  line-height: 0.98;
  letter-spacing: -0.055em;
}
.auth-intro h1 span { color: var(--cyan); }
.auth-intro-copy {
  max-width: 34rem;
  margin: 1.5rem 0 0;
  color: #aab9ca;
  font-size: 1rem;
  line-height: 1.75;
}
.capability-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem;
  position: relative;
  z-index: 1;
}
.capability-row span {
  padding: 0.55rem 0.75rem;
  border: 1px solid var(--line);
  border-radius: 0.7rem;
  color: #b9c7d7;
  background: rgba(7, 16, 29, 0.66);
  font-size: 0.78rem;
}
.auth-form-wrap {
  display: grid;
  align-content: center;
  padding: clamp(2rem, 5vw, 4rem);
  background: rgba(13, 24, 40, 0.8);
}
.auth-form-wrap h2 {
  margin: 0;
  font-size: 1.75rem;
  letter-spacing: -0.035em;
}
.auth-form-wrap > p {
  margin: 0.7rem 0 1.8rem;
  color: var(--muted);
  line-height: 1.65;
}
#login-form { display: grid; gap: 1rem; }
#login-form .primary-button { margin-top: 0.4rem; }
.credential-note {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 1.15rem;
  padding: 0.8rem 0.9rem;
  border: 1px solid var(--line);
  border-radius: 0.8rem;
  color: var(--muted);
  background: rgba(5, 13, 24, 0.5);
  font-size: 0.8rem;
}
.credential-note code {
  color: var(--lime);
  font-family: "SFMono-Regular", Consolas, monospace;
}
#message {
  min-height: 1.5rem;
  margin: 0.85rem 0 0;
  color: var(--danger);
  font-size: 0.86rem;
  font-weight: 700;
}
.locator-note {
  margin: 0.9rem 0 0;
  color: #6f8198;
  font-size: 0.74rem;
}
@media (max-width: 800px) {
  .auth-page { padding: 1rem; }
  .auth-grid { grid-template-columns: 1fr; }
  .auth-intro { min-height: 22rem; border-right: 0; border-bottom: 1px solid var(--line); }
  .auth-intro h1 { max-width: 11ch; }
}
@media (max-width: 480px) {
  .auth-topbar .status-chip { display: none; }
  .auth-intro, .auth-form-wrap { padding: 1.5rem; }
  .auth-intro { min-height: 20rem; }
  .credential-note { align-items: flex-start; flex-direction: column; gap: 0.35rem; }
}
</style>
</head>
<body class="auth-page">
<main class="auth-shell">
  <header class="auth-topbar">
    <div class="brand"><span class="brand-mark" aria-hidden="true">AT</span>V1 验收目标</div>
    <span class="status-chip">LOCAL · 8765</span>
  </header>
  <section class="auth-grid panel">
    <div class="auth-intro">
      <div>
        <p class="eyebrow">Deterministic test target</p>
        <h1>让每条链路<span>看得见</span></h1>
        <p class="auth-intro-copy">
          本机合成环境用于验证 API、Web、Session、Evidence 与 Locator 自愈。
          所有数据仅存在于当前进程，关闭服务即清空。
        </p>
      </div>
      <div class="capability-row" aria-label="演示能力">
        <span>14 API operations</span>
        <span>Web recording</span>
        <span>Failure injection</span>
      </div>
    </div>
    <div class="auth-form-wrap">
      <p class="eyebrow">Secure sandbox access</p>
      <h2>进入演示系统</h2>
      <p>使用公开的合成账号登录，不要在此输入任何真实凭据。</p>
      <form id="login-form">
        <label class="field">
          <span class="field-label">用户名</span>
          <input name="username" aria-label="用户名" autocomplete="username"
            data-testid="username" placeholder="请输入演示用户名">
        </label>
        <label class="field">
          <span class="field-label">密码</span>
          <input name="password" aria-label="密码" type="password"
            autocomplete="current-password" data-testid="password"
            placeholder="请输入演示密码">
        </label>
        <button class="primary-button" type="submit" aria-label="登录"
          id="BUTTON_ID" data-testid="BUTTON_ID">登录</button>
      </form>
      <p role="alert" aria-live="polite" id="message"></p>
      <div class="credential-note">
        <span>公开测试账号</span>
        <code>demo / demo-pass</code>
      </div>
      <p class="locator-note">登录按钮保留可控 Locator，用于复现定位失败和人工修复。</p>
    </div>
  </section>
</main>
<script>
const loginForm = document.getElementById('login-form');
const loginButton = loginForm.querySelector('button[type="submit"]');
const loginMessage = document.getElementById('message');
loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  loginMessage.textContent = '';
  loginButton.disabled = true;
  loginButton.textContent = '正在验证…';
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        username: loginForm.elements.username.value,
        password: loginForm.elements.password.value
      })
    });
    if (response.ok) {
      loginButton.textContent = '登录成功';
      location.href = '/app';
      return;
    }
    loginMessage.textContent = '用户名或密码错误，请使用演示账号重试。';
  } catch (_) {
    loginMessage.textContent = '演示服务暂时不可用，请确认本机进程仍在运行。';
  } finally {
    loginButton.disabled = false;
    if (location.pathname !== '/app') loginButton.textContent = '登录';
  }
});
</script>
</body>
</html>""".replace("SITE_STYLE", SITE_STYLE).replace("BUTTON_ID", button_id)


APP_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="AI 原生智能测试编排平台的本机资源与订单验收工作台">
<title>V1 验收 · 资源工作台</title>
SITE_STYLE
<style>
.app-page { min-height: 100vh; }
.app-header {
  position: sticky;
  top: 0;
  z-index: 10;
  border-bottom: 1px solid var(--line);
  background: rgba(7, 17, 31, 0.82);
  backdrop-filter: blur(18px);
}
.app-header-inner {
  display: flex;
  width: min(88rem, calc(100% - 2rem));
  min-height: 4.5rem;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin: 0 auto;
}
.header-actions { display: flex; align-items: center; gap: 0.7rem; }
.header-actions .ghost-button { min-height: 2.35rem; padding: 0.45rem 0.8rem; }
.workspace {
  width: min(88rem, calc(100% - 2rem));
  margin: 0 auto;
  padding: 2.2rem 0 4rem;
}
.workspace-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 2rem;
  margin-bottom: 1.4rem;
}
.workspace-hero h1 {
  margin: 0;
  font-size: clamp(2.25rem, 4vw, 4rem);
  line-height: 1;
  letter-spacing: -0.055em;
}
.workspace-hero-copy {
  max-width: 42rem;
  margin: 1rem 0 0;
  color: var(--muted);
  font-size: 1rem;
  line-height: 1.65;
}
.welcome-state {
  display: grid;
  min-width: 12rem;
  gap: 0.25rem;
  padding: 1rem 1.1rem;
  border: 1px solid rgba(183, 243, 107, 0.2);
  border-radius: 1rem;
  background: rgba(183, 243, 107, 0.055);
}
.welcome-state span { color: var(--muted); font-size: 0.74rem; text-transform: uppercase; }
.welcome-state p { margin: 0; color: var(--lime); font-weight: 800; }
.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.85rem;
  margin-bottom: 1rem;
}
.metric-card {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 1rem;
  padding: 1.05rem 1.2rem;
  border: 1px solid var(--line);
  border-radius: 1rem;
  background: rgba(13, 25, 42, 0.65);
}
.metric-card span { color: var(--muted); font-size: 0.78rem; }
.metric-card strong { font-size: 1.7rem; letter-spacing: -0.04em; }
.metric-code {
  color: rgba(53, 217, 230, 0.65);
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 0.72rem;
}
.status-banner {
  display: none;
  margin: 0 0 1rem;
  padding: 0.85rem 1rem;
  border: 1px solid rgba(53, 217, 230, 0.2);
  border-radius: 0.9rem;
  color: #bceef2;
  background: rgba(53, 217, 230, 0.07);
  font-size: 0.88rem;
  font-weight: 700;
}
.status-banner.is-visible { display: block; }
.status-banner[data-tone="error"] {
  border-color: rgba(255, 127, 137, 0.25);
  color: #ffc1c6;
  background: rgba(255, 127, 137, 0.08);
}
.workspace-grid {
  display: grid;
  grid-template-columns: minmax(18rem, 0.8fr) minmax(22rem, 1.2fr) minmax(20rem, 1fr);
  gap: 1rem;
  align-items: start;
}
.workspace-panel { overflow: hidden; }
.panel-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.2rem 1.25rem 1rem;
  border-bottom: 1px solid var(--line);
}
.panel-heading h2 { margin: 0; font-size: 1.05rem; letter-spacing: -0.02em; }
.panel-heading p { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.78rem; line-height: 1.5; }
.panel-index {
  color: rgba(53, 217, 230, 0.7);
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 0.72rem;
}
.panel-body { padding: 1.2rem 1.25rem 1.3rem; }
.inline-form { display: grid; gap: 0.8rem; }
.inline-form.compact { grid-template-columns: minmax(0, 1fr) auto; align-items: end; }
.resource-list, .product-list, .order-list {
  display: grid;
  gap: 0.65rem;
  margin: 1rem 0 0;
  padding: 0;
  list-style: none;
}
.resource-list li, .order-list li, .product-list li {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 0.8rem;
  padding: 0.8rem 0.85rem;
  border: 1px solid var(--line);
  border-radius: 0.85rem;
  background: rgba(5, 13, 24, 0.45);
}
.row-primary {
  display: grid;
  min-width: 0;
  gap: 0.2rem;
}
.row-primary strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.88rem;
}
.row-primary small { color: var(--muted); font-size: 0.72rem; }
.row-badge {
  flex: 0 0 auto;
  padding: 0.35rem 0.55rem;
  border-radius: 999px;
  color: var(--lime);
  background: rgba(183, 243, 107, 0.08);
  font-size: 0.72rem;
  font-weight: 800;
}
.commerce-form {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.8rem;
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--line);
}
.commerce-form .primary-button { grid-column: 1 / -1; }
.download-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 1rem;
  padding: 1rem;
  border: 1px solid rgba(53, 217, 230, 0.18);
  border-radius: 0.9rem;
  background: linear-gradient(135deg, rgba(53, 217, 230, 0.08), rgba(183, 243, 107, 0.04));
}
.download-card div { display: grid; gap: 0.2rem; }
.download-card strong { font-size: 0.86rem; }
.download-card small { color: var(--muted); font-size: 0.72rem; }
.download-card a {
  flex: 0 0 auto;
  color: var(--cyan);
  font-size: 0.8rem;
  font-weight: 800;
  text-decoration: none;
}
@media (max-width: 1080px) {
  .workspace-grid { grid-template-columns: 1fr 1fr; }
  .workspace-panel:last-child { grid-column: 1 / -1; }
}
@media (max-width: 720px) {
  .workspace-hero { grid-template-columns: 1fr; }
  .welcome-state { min-width: 0; }
  .metrics, .workspace-grid { grid-template-columns: 1fr; }
  .workspace-panel:last-child { grid-column: auto; }
}
@media (max-width: 480px) {
  .app-header-inner, .workspace { width: min(100% - 1rem, 88rem); }
  .header-actions .status-chip { display: none; }
  .workspace { padding-top: 1.4rem; }
  .inline-form.compact, .commerce-form { grid-template-columns: 1fr; }
  .commerce-form .primary-button { grid-column: auto; }
}
</style>
</head>
<body class="app-page">
<header class="app-header">
  <div class="app-header-inner">
    <div class="brand"><span class="brand-mark" aria-hidden="true">AT</span>V1 验收工作台</div>
    <div class="header-actions">
      <span class="status-chip">合成环境在线</span>
      <button class="ghost-button" id="logout" aria-label="退出登录">退出登录</button>
    </div>
  </div>
</header>
<main class="workspace">
  <section class="workspace-hero">
    <div>
      <p class="eyebrow">Controlled test workspace</p>
      <h1>资源工作台</h1>
      <p class="workspace-hero-copy">
        在可重复的本机环境中创建资源、查询商品、验证订单与精确清理。
        这里的每次操作都适合被录制、断言和审计。
      </p>
    </div>
    <div class="welcome-state">
      <span>Session status</span>
      <p data-testid="welcome">登录成功</p>
    </div>
  </section>

  <section class="metrics" aria-label="工作台摘要">
    <article class="metric-card"><div><span>当前资源</span><strong id="resource-count">0</strong></div><code class="metric-code">RESOURCE</code></article>
    <article class="metric-card"><div><span>活动订单</span><strong id="order-count">0</strong></div><code class="metric-code">ORDER</code></article>
    <article class="metric-card"><div><span>商品库存</span><strong id="stock-count">—</strong></div><code class="metric-code">STOCK</code></article>
  </section>

  <p role="status" aria-live="polite" id="status" class="status-banner"></p>

  <div class="workspace-grid">
    <section class="workspace-panel panel">
      <header class="panel-heading">
        <div><h2>临时资源</h2><p>创建后可按精确 ID 清理</p></div>
        <span class="panel-index">01</span>
      </header>
      <div class="panel-body">
        <form id="resource-form" class="inline-form">
          <label class="field">
            <span class="field-label">资源名称</span>
            <input name="name" aria-label="资源名称" data-testid="resource-name"
              maxlength="120" placeholder="例如：回归测试资源">
          </label>
          <button class="primary-button" type="submit" aria-label="创建资源"
            data-testid="create-resource">创建资源</button>
        </form>
        <ul id="resources" class="resource-list" data-testid="resources"></ul>
        <p id="resources-empty" class="empty-state">暂无资源，创建一条开始测试</p>
      </div>
    </section>

    <section class="workspace-panel panel">
      <header class="panel-heading">
        <div><h2>商品目录</h2><p>搜索固定合成商品并观察库存</p></div>
        <span class="panel-index">02</span>
      </header>
      <div class="panel-body">
        <div class="inline-form compact">
          <label class="field">
            <span class="field-label">商品查询</span>
            <input id="product-query" aria-label="商品查询" placeholder="输入名称关键词">
          </label>
          <button class="secondary-button" id="search-products" type="button">查询商品</button>
        </div>
        <ul id="products" class="product-list" data-testid="products"></ul>
        <p id="products-empty" class="empty-state is-hidden">没有匹配的商品</p>
        <div class="download-card">
          <div><strong>下载断言样例</strong><small>固定 CSV · 适合验证下载动作</small></div>
          <a href="/api/download" download="demo.csv">下载示例</a>
        </div>
      </div>
    </section>

    <section class="workspace-panel panel">
      <header class="panel-heading">
        <div><h2>订单验证</h2><p>创建订单会扣减库存，删除后恢复</p></div>
        <span class="panel-index">03</span>
      </header>
      <div class="panel-body">
        <form id="order-form" class="commerce-form">
          <label class="field">
            <span class="field-label">商品 ID</span>
            <input name="product_id" type="number" min="1" aria-label="订单商品 ID" value="1">
          </label>
          <label class="field">
            <span class="field-label">数量</span>
            <input name="quantity" type="number" min="1" max="20" aria-label="订单数量" value="1">
          </label>
          <button class="primary-button" type="submit" aria-label="创建订单">创建订单</button>
        </form>
        <ul id="orders" class="order-list" data-testid="orders"></ul>
        <p id="orders-empty" class="empty-state">暂无订单</p>
      </div>
    </section>
  </div>
</main>
<script>
const statusBanner = document.getElementById('status');

function setStatus(message, tone) {
  statusBanner.textContent = message;
  statusBanner.dataset.tone = tone || 'success';
  statusBanner.classList.toggle('is-visible', Boolean(message));
}

function setBusy(button, busy, idleLabel, busyLabel) {
  button.disabled = busy;
  button.textContent = busy ? busyLabel : idleLabel;
}

function showEmpty(id, isEmpty) {
  document.getElementById(id).classList.toggle('is-hidden', !isEmpty);
}

async function refresh() {
  const response = await fetch('/api/resources');
  if (response.status === 401) {
    location.href = '/login';
    return;
  }
  const result = await response.json();
  const list = document.getElementById('resources');
  list.replaceChildren();
  result.items.forEach(item => {
    const row = document.createElement('li');
    row.dataset.resourceId = item.id;
    const content = document.createElement('div');
    content.className = 'row-primary';
    const label = document.createElement('strong');
    label.textContent = item.name;
    const meta = document.createElement('small');
    meta.textContent = 'Resource ID · ' + item.id;
    content.append(label, meta);
    const button = document.createElement('button');
    button.className = 'danger-button';
    button.textContent = '删除';
    button.setAttribute('aria-label', '删除资源 ' + item.id);
    button.onclick = async () => {
      setBusy(button, true, '删除', '清理中…');
      const deleted = await fetch('/api/resources/' + item.id, {method: 'DELETE'});
      setStatus(deleted.ok ? '资源 #' + item.id + ' 已精确清理' : '资源清理失败', deleted.ok ? 'success' : 'error');
      await refresh();
    };
    row.append(content, button);
    list.append(row);
  });
  document.getElementById('resource-count').textContent = String(result.items.length);
  showEmpty('resources-empty', result.items.length === 0);
}

async function loadProducts() {
  const query = document.getElementById('product-query').value;
  const response = await fetch('/api/products?q=' + encodeURIComponent(query));
  if (response.status === 401) {
    location.href = '/login';
    return;
  }
  const result = await response.json();
  const list = document.getElementById('products');
  list.replaceChildren();
  let totalStock = 0;
  result.items.forEach(item => {
    totalStock += item.stock;
    const row = document.createElement('li');
    const content = document.createElement('div');
    content.className = 'row-primary';
    const name = document.createElement('strong');
    name.textContent = item.name;
    const meta = document.createElement('small');
    meta.textContent = 'ID ' + item.id + ' · ¥' + (item.price_cents / 100).toFixed(2);
    content.append(name, meta);
    const stock = document.createElement('span');
    stock.className = 'row-badge';
    stock.textContent = '库存 ' + item.stock;
    row.append(content, stock);
    list.append(row);
  });
  document.getElementById('stock-count').textContent = String(totalStock);
  showEmpty('products-empty', result.items.length === 0);
}

async function loadOrders() {
  const response = await fetch('/api/orders');
  if (response.status === 401) {
    location.href = '/login';
    return;
  }
  const result = await response.json();
  const list = document.getElementById('orders');
  list.replaceChildren();
  result.items.forEach(item => {
    const row = document.createElement('li');
    row.dataset.orderId = item.id;
    const content = document.createElement('div');
    content.className = 'row-primary';
    const label = document.createElement('strong');
    label.textContent = item.product_name + ' × ' + item.quantity;
    const meta = document.createElement('small');
    meta.textContent = '订单 #' + item.id + ' · ¥' + (item.total_cents / 100).toFixed(2);
    content.append(label, meta);
    const button = document.createElement('button');
    button.className = 'danger-button';
    button.textContent = '删除订单';
    button.setAttribute('aria-label', '删除订单 ' + item.id);
    button.onclick = async () => {
      setBusy(button, true, '删除订单', '清理中…');
      const deleted = await fetch('/api/orders/' + item.id, {method: 'DELETE'});
      setStatus(deleted.ok ? '订单 #' + item.id + ' 已删除，库存已恢复' : '订单删除失败', deleted.ok ? 'success' : 'error');
      await Promise.all([loadOrders(), loadProducts()]);
    };
    row.append(content, button);
    list.append(row);
  });
  document.getElementById('order-count').textContent = String(result.items.length);
  showEmpty('orders-empty', result.items.length === 0);
}

document.getElementById('resource-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  setBusy(button, true, '创建资源', '创建中…');
  try {
    const response = await fetch('/api/resources', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: event.target.elements.name.value})
    });
    setStatus(response.ok ? '资源已创建并进入当前会话' : '创建失败，请检查资源名称', response.ok ? 'success' : 'error');
    if (response.ok) event.target.elements.name.value = '';
    await refresh();
  } finally {
    setBusy(button, false, '创建资源', '创建中…');
  }
});

document.getElementById('search-products').onclick = async event => {
  setBusy(event.currentTarget, true, '查询商品', '查询中…');
  try {
    await loadProducts();
  } finally {
    setBusy(event.currentTarget, false, '查询商品', '查询中…');
  }
};

document.getElementById('product-query').addEventListener('keydown', event => {
  if (event.key === 'Enter') document.getElementById('search-products').click();
});

document.getElementById('order-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  setBusy(button, true, '创建订单', '创建中…');
  try {
    const response = await fetch('/api/orders', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        product_id: Number(event.target.elements.product_id.value),
        quantity: Number(event.target.elements.quantity.value)
      })
    });
    setStatus(response.ok ? '订单已创建，库存已同步扣减' : '订单创建失败，请检查商品与库存', response.ok ? 'success' : 'error');
    await Promise.all([loadOrders(), loadProducts()]);
  } finally {
    setBusy(button, false, '创建订单', '创建中…');
  }
});

document.getElementById('logout').onclick = async event => {
  setBusy(event.currentTarget, true, '退出登录', '退出中…');
  await fetch('/api/logout', {method: 'POST'});
  location.href = '/login';
};

Promise.all([refresh(), loadProducts(), loadOrders()]).catch(() => {
  setStatus('工作台数据加载失败，请刷新页面重试', 'error');
});
</script>
</body>
</html>""".replace("SITE_STYLE", SITE_STYLE)


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0, host: str = "127.0.0.1") -> None:
        self.state = DemoState()
        super().__init__((host, port), DemoHandler)


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoServer
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        """Never log targets, query strings, cookies or request bodies."""

    def _reply(
        self,
        status: int,
        payload: Any,
        *,
        content_type: str = "application/json; charset=utf-8",
        cookie: str | None = None,
    ) -> None:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if content_type.startswith("application/json")
            else str(payload).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        # Slow-response timeout tests intentionally close the client connection.
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _body(self) -> dict[str, Any] | None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= MAX_BODY_BYTES:
                raise ValueError
            value = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(value, dict):
                raise TypeError
            return value
        except (TypeError, ValueError, UnicodeDecodeError):
            self._reply(400, {"error": "INVALID_BODY"})
            return None

    def _session_token(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        cookie: SimpleCookie[str] = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        item = cookie.get("demo_session")
        return item.value if item else ""

    def _authorized(self) -> bool:
        with self.server.state.lock:
            return (
                self.server.state.tokens.get(self._session_token(), 0)
                > time.monotonic()
            )

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        state = self.server.state
        if path == "/health":
            self._reply(200, {"status": "ok", "service": "v1-demo"})
        elif path in {"/", "/login"}:
            with state.lock:
                html = login_html(state.changed_locator)
            self._reply(200, html, content_type="text/html; charset=utf-8")
        elif path == "/app":
            if not self._authorized():
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
            else:
                self._reply(200, APP_HTML, content_type="text/html; charset=utf-8")
        elif path == "/control/state":
            with state.lock:
                self._reply(
                    200,
                    {
                        "resource_count": len(state.resources),
                        "order_count": len(state.orders),
                        "login_count": state.login_count,
                        "changed_locator": state.changed_locator,
                    },
                )
        elif path == "/api/slow":
            try:
                delay = int(parse_qs(parsed.query).get("delay_ms", ["500"])[0])
                if not 0 <= delay <= 30_000:
                    raise ValueError
            except ValueError:
                self._reply(400, {"error": "INVALID_DELAY"})
                return
            time.sleep(delay / 1000)
            self._reply(200, {"completed": True})
        elif path == "/api/flaky":
            key = parse_qs(parsed.query).get("key", ["default"])[0][:64]
            with state.lock:
                attempt = state.flaky_attempts.get(key, 0) + 1
                state.flaky_attempts[key] = attempt
            self._reply(503 if attempt == 1 else 200, {"attempt": attempt})
        elif path.startswith("/api/"):
            if not self._authorized():
                self._reply(401, {"error": "UNAUTHORIZED"})
            elif path == "/api/resources":
                with state.lock:
                    self._reply(200, {"items": list(state.resources.values())})
            elif path == "/api/users":
                query = parse_qs(parsed.query).get("q", [""])[0].strip().lower()[:120]
                with state.lock:
                    items = [
                        dict(item)
                        for item in state.users.values()
                        if not query
                        or query in item["username"].lower()
                        or query in item["display_name"].lower()
                    ]
                self._reply(200, {"items": items})
            elif path == "/api/products":
                query = parse_qs(parsed.query).get("q", [""])[0].strip().lower()[:120]
                with state.lock:
                    items = [
                        dict(item)
                        for item in state.products.values()
                        if not query or query in item["name"].lower()
                    ]
                self._reply(200, {"items": items})
            elif path == "/api/orders":
                query = parse_qs(parsed.query).get("q", [""])[0].strip().lower()[:120]
                with state.lock:
                    items = [
                        dict(item)
                        for item in state.orders.values()
                        if not query
                        or query in str(item["id"])
                        or query in item["product_name"].lower()
                    ]
                self._reply(200, {"items": items})
            elif path == "/api/download":
                self._reply(
                    200, "name,value\ndemo,1\n", content_type="text/csv; charset=utf-8"
                )
            else:
                self._reply(404, {"error": "NOT_FOUND"})
        else:
            self._reply(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:
        body = self._body()
        if body is None:
            return
        path = urlsplit(self.path).path
        state = self.server.state
        if path == "/api/login":
            if (
                body.get("username") != DEMO_USERNAME
                or body.get("password") != DEMO_PASSWORD
            ):
                self._reply(401, {"error": "INVALID_CREDENTIALS"})
                return
            token = secrets.token_urlsafe(24)
            with state.lock:
                state.tokens[token] = time.monotonic() + 1200
                state.login_count += 1
            self._reply(
                200,
                {"data": {"token": token, "username": DEMO_USERNAME}},
                cookie=f"demo_session={token}; Path=/; HttpOnly; SameSite=Strict",
            )
        elif path == "/api/logout":
            with state.lock:
                state.tokens.pop(self._session_token(), None)
            self._reply(
                200,
                {"logged_out": True},
                cookie="demo_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0",
            )
        elif path == "/control/locator":
            if type(body.get("changed")) is not bool:
                self._reply(400, {"error": "INVALID_LOCATOR_MODE"})
                return
            with state.lock:
                state.changed_locator = body["changed"]
            self._reply(200, {"changed": body["changed"]})
        elif path == "/control/expire-sessions":
            with state.lock:
                state.tokens.clear()
            self._reply(200, {"expired": True})
        elif path == "/api/resources":
            if not self._authorized():
                self._reply(401, {"error": "UNAUTHORIZED"})
                return
            name = body.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
                self._reply(400, {"error": "INVALID_NAME"})
                return
            with state.lock:
                item = {"id": state.next_id, "name": name.strip()}
                state.resources[state.next_id] = item
                state.next_id += 1
            self._reply(201, {"data": item})
        elif path == "/api/orders":
            if not self._authorized():
                self._reply(401, {"error": "UNAUTHORIZED"})
                return
            product_id = body.get("product_id")
            quantity = body.get("quantity")
            if (
                isinstance(product_id, bool)
                or not isinstance(product_id, int)
                or isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or not 1 <= quantity <= 20
            ):
                self._reply(400, {"error": "INVALID_ORDER"})
                return
            with state.lock:
                product = state.products.get(product_id)
                if product is None:
                    self._reply(404, {"error": "PRODUCT_NOT_FOUND"})
                    return
                if product["stock"] < quantity:
                    self._reply(409, {"error": "INSUFFICIENT_STOCK"})
                    return
                order = {
                    "id": state.next_order_id,
                    "user_id": 1,
                    "product_id": product_id,
                    "product_name": product["name"],
                    "quantity": quantity,
                    "total_cents": product["price_cents"] * quantity,
                    "status": "CREATED",
                }
                state.orders[state.next_order_id] = order
                state.next_order_id += 1
                product["stock"] -= quantity
            self._reply(201, {"data": order})
        else:
            self._reply(404, {"error": "NOT_FOUND"})

    def do_DELETE(self) -> None:
        path = urlsplit(self.path).path
        if not path.startswith(("/api/resources/", "/api/orders/")):
            self._reply(404, {"error": "NOT_FOUND"})
            return
        if not self._authorized():
            self._reply(401, {"error": "UNAUTHORIZED"})
            return
        try:
            target_id = int(path.rsplit("/", 1)[-1])
        except ValueError:
            self._reply(404, {"error": "NOT_FOUND"})
            return
        with self.server.state.lock:
            if path.startswith("/api/orders/"):
                item = self.server.state.orders.pop(target_id, None)
                if item is not None:
                    product = self.server.state.products.get(item["product_id"])
                    if product is not None:
                        product["stock"] += item["quantity"]
            else:
                item = self.server.state.resources.pop(target_id, None)
        self._reply(200 if item else 404, {"deleted": item is not None})


@contextmanager
def running_demo() -> Iterator[str]:
    """Start an isolated loopback instance on an OS-assigned port for tests."""
    server = DemoServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        choices=("127.0.0.1", "0.0.0.0"),
        default="127.0.0.1",
        help="listen address; 0.0.0.0 is intended only for the isolated Docker service",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    server = DemoServer(args.port, args.host)
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(f"V1 demo: http://{display_host}:{server.server_port}/login", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

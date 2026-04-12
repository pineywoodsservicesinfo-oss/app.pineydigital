"""
ui_helpers.py — UI Helper Functions for Consistent UX
Piney Digital Outreach System

Toast notifications, error handling, and UI utilities.
"""


def get_nav(page='overview'):
    """Generate consistent navigation sidebar."""
    return f"""
<div class="sidebar">
  <div class="logo">
    <h1>🌲 Piney Digital</h1>
    <p>Outreach Dashboard</p>
  </div>
  <nav class="nav">
    <a href="/" class="{'active' if page=='overview' else ''}">
      <div class="dot"></div>Overview</a>
    <a href="/leads" class="{'active' if page=='leads' else ''}">
      <div class="dot"></div>Leads</a>
    <a href="/loyalty" class="{'active' if page=='loyalty' else ''}">
      <div class="dot"></div>Loyalty</a>
    <a href="/log" class="{'active' if page=='log' else ''}">
      <div class="dot"></div>Outreach log</a>
    <a href="/send" class="{'active' if page=='send' else ''}">
      <div class="dot"></div>Send now</a>
  </nav>
  <div class="sidebar-footer">joel@pineydigital.com &nbsp;·&nbsp; <a href="/logout" style="color:#64748b">Sign out</a></div>
</div>
"""


TOAST_CSS = """
<style>
.toast-container{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px}
.toast{background:#1e293b;border-left:4px solid;padding:12px 20px;border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,0.3);animation:slideIn .3s ease-out;min-width:280px;max-width:400px}
.toast-success{border-left-color:#166534}
.toast-error{border-left-color:#7f1d1d}
.toast-info{border-left-color:#1e3a5f}
.toast-title{font-weight:600;font-size:13px;margin-bottom:4px}
.toast-message{font-size:12px;color:#94a3b8}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
</style>
"""


def toast_html(message, toast_type='success', title=None):
    """Generate toast notification HTML."""
    titles = {
        'success': 'Success',
        'error': 'Error',
        'info': 'Info'
    }
    return f"""
<div class="toast toast-{toast_type}">
  <div class="toast-title">{title or titles.get(toast_type, 'Notification')}</div>
  <div class="toast-message">{message}</div>
</div>
"""


def loading_spinner():
    """Generate loading spinner HTML."""
    return """
<div class="loading-spinner" style="display:flex;align-items:center;justify-content:center;padding:40px">
  <div style="width:32px;height:32px;border:3px solid #334155;border-top-color:#166534;border-radius:50%;animation:spin 1s linear infinite"></div>
</div>
<style>@keyframes spin{to{transform:rotate(360deg)}}</style>
"""


def empty_state(icon, title, description, action_text=None, action_url=None):
    """Generate empty state component."""
    action_html = ""
    if action_text and action_url:
        action_html = f'<a href="{action_url}" class="btn btn-blue" style="margin-top:16px;display:inline-block">{action_text}</a>'
    
    return f"""
<div style="text-align:center;padding:48px 24px;color:#64748b">
  <div style="font-size:48px;margin-bottom:16px">{icon}</div>
  <h3 style="font-size:16px;color:#94a3b8;margin-bottom:8px">{title}</h3>
  <p style="font-size:13px;line-height:1.5">{description}</p>
  {action_html}
</div>
"""


def confirm_dialog(message, confirm_text="Confirm", cancel_text="Cancel"):
    """Generate JavaScript confirm dialog."""
    return f"""
<script>
if (!confirm('{message}')) {{
  event.preventDefault();
}}
</script>
"""

"""
email_sender.py — Email sending module
Piney Digital Outreach System

Sends emails via Resend API (preferred) or SMTP fallback for:
  - 2FA verification codes
  - Email verification
  - Password reset
  - Notifications

Usage:
  from modules.email_sender import send_email, send_2fa_code
"""

import os
import json
import logging
from typing import Tuple
import requests

logger = logging.getLogger(__name__)

# Email Configuration (set in .env)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", "joel@pineydigital.com")
FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Piney Digital")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "joel@pineydigital.com")

# SMTP fallback (if Resend not configured)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


def is_email_configured() -> bool:
    """Check if email is configured (Resend or SMTP)."""
    return bool(RESEND_API_KEY) or bool(SMTP_USER and SMTP_PASSWORD)


def send_email(to_email: str, subject: str, body: str, html_body: str = None) -> Tuple[bool, str]:
    """
    Send an email via Resend API (preferred) or SMTP fallback.

    Args:
        to_email: Recipient email address
        subject: Email subject line
        body: Plain text body
        html_body: Optional HTML body

    Returns:
        (success, message)
    """
    # Try Resend first
    if RESEND_API_KEY:
        return send_email_resend(to_email, subject, body, html_body)

    # Fallback to SMTP
    if SMTP_USER and SMTP_PASSWORD:
        return send_email_smtp(to_email, subject, body, html_body)

    logger.warning(f"Email not configured. Email to {to_email} not sent.")
    return False, "Email not configured"


def send_email_resend(to_email: str, subject: str, body: str, html_body: str = None) -> Tuple[bool, str]:
    """Send email via Resend API."""
    try:
        data = {
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
        if html_body:
            data["html"] = html_body

        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json=data,
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            logger.info(f"Email sent via Resend to {to_email}: {result.get('id', 'unknown')}")
            return True, "Email sent"
        else:
            error_msg = response.text
            logger.error(f"Resend API error: {response.status_code} - {error_msg}")
            return False, f"Resend error: {response.status_code} - {error_msg}"

    except Exception as e:
        logger.error(f"Failed to send email via Resend to {to_email}: {e}")
        return False, str(e)


def send_email_smtp(to_email: str, subject: str, body: str, html_body: str = None) -> Tuple[bool, str]:
    """Send email via SMTP (fallback)."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"] = to_email

        # Add plain text part
        msg.attach(MIMEText(body, "plain"))

        # Add HTML part if provided
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

        # Send via SMTP
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())

        logger.info(f"Email sent via SMTP to {to_email}: {subject}")
        return True, "Email sent"

    except Exception as e:
        logger.error(f"Failed to send email via SMTP to {to_email}: {e}")
        return False, str(e)


def send_2fa_code(email: str, code: str) -> Tuple[bool, str]:
    """
    Send 2FA verification code via email.

    Args:
        email: Admin email address
        code: 6-digit verification code

    Returns:
        (success, message)
    """
    subject = "Your Piney Digital Login Code"

    body = f"""Your verification code is: {code}

This code will expire in 5 minutes.

If you didn't request this code, please ignore this email.

- Piney Digital Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; padding: 40px; }}
    .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 12px; padding: 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    .logo {{ font-size: 24px; margin-bottom: 24px; }}
    .code {{ font-size: 36px; font-weight: bold; color: #22c55e; letter-spacing: 8px; padding: 20px; background: #f0fdf4; border-radius: 8px; text-align: center; margin: 24px 0; }}
    .footer {{ font-size: 12px; color: #666; margin-top: 24px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">Piney Digital</div>
    <p>Hi there,</p>
    <p>Here's your verification code for the admin dashboard:</p>
    <div class="code">{code}</div>
    <p>This code will expire in <strong>5 minutes</strong>.</p>
    <p>If you didn't request this code, you can safely ignore this email.</p>
    <div class="footer">
      <p>- The Piney Digital Team</p>
      <p>pineydigital.com</p>
    </div>
  </div>
</body>
</html>
"""

    return send_email(email, subject, body, html_body)


def send_welcome_email(email: str, name: str, verify_link: str = None) -> Tuple[bool, str]:
    """Send welcome email to new business users."""
    subject = "Welcome to Piney Digital!"

    body = f"""Hi {name},

Welcome to Piney Digital! Your account has been created.

You can now:
- Set up your business profile
- Configure your loyalty program
- Start accepting customers

{f"Verify your email: {verify_link}" if verify_link else ""}

If you have any questions, reply to this email.

- Piney Digital Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; padding: 40px; }}
    .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 12px; padding: 40px; }}
    .logo {{ font-size: 24px; margin-bottom: 24px; }}
    .btn {{ display: inline-block; background: #22c55e; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; margin: 16px 0; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">Piney Digital</div>
    <h2>Welcome, {name}!</h2>
    <p>Your account has been created. You can now:</p>
    <ul>
      <li>Set up your business profile</li>
      <li>Configure your loyalty program</li>
      <li>Start accepting customers</li>
    </ul>
    {"<a href='" + verify_link + "' class='btn'>Verify Email</a>" if verify_link else ""}
    <p style="color:#666;font-size:12px;margin-top:24px;">- Piney Digital Team</p>
  </div>
</body>
</html>
"""

    return send_email(email, subject, body, html_body)


def send_password_reset(email: str, reset_link: str) -> Tuple[bool, str]:
    """Send password reset link."""
    subject = "Reset Your Piney Digital Password"

    body = f"""Hi,

You requested a password reset. Click the link below:

{reset_link}

This link expires in 24 hours.

If you didn't request this, ignore this email.

- Piney Digital Team
"""

    return send_email(email, subject, body)


def send_daily_call_summary(stats: dict, hot_leads: list = None) -> Tuple[bool, str]:
    """
    Send daily call summary email.

    Args:
        stats: Dict with call stats (called, interested, voicemail, etc.)
        hot_leads: List of hot lead dicts with business_name, city, phone

    Returns:
        (success, message)
    """
    subject = f"Daily Call Summary - {stats.get('called', 0)} Calls Made"

    # Build plain text body
    body = f"""Daily AI Calling Summary

Calls Made: {stats.get('called', 0)}
Hot Leads: {stats.get('interested', 0)}
Voicemails: {stats.get('voicemail', 0)}
Transferred: {stats.get('transferred', 0)}
No Answer: {stats.get('no_answer', 0)}
Declined: {stats.get('declined', 0)}
Ready to Call: {stats.get('queued', 0) + stats.get('new', 0)}

"""

    if hot_leads:
        body += "Hot Leads:\n"
        for lead in hot_leads:
            body += f"  - {lead.get('business_name', 'Unknown')} ({lead.get('city', 'Unknown')})\n"

    body += "\nView details: https://app.pineydigital.com/call\n\n- Piney Digital"

    # Build HTML body
    hot_leads_html = ""
    if hot_leads:
        hot_leads_html = """
        <div style="background:#f0fdf4;border-radius:8px;padding:16px;margin-top:16px;">
          <h3 style="color:#166534;margin:0 0 12px;">🔥 Hot Leads</h3>
          <ul style="margin:0;padding-left:20px;">
        """
        for lead in hot_leads:
            hot_leads_html += f"<li>{lead.get('business_name', 'Unknown')} ({lead.get('city', 'Unknown')})</li>"
        hot_leads_html += "</ul></div>"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; padding: 40px; }}
        .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 12px; padding: 40px; }}
        .stats {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }}
        .stat {{ background: #f8fafc; border-radius: 8px; padding: 12px; text-align: center; }}
        .stat-val {{ font-size: 24px; font-weight: bold; color: #1e40af; }}
        .stat-label {{ font-size: 12px; color: #64748b; }}
        .green {{ color: #22c55e; }}
        .btn {{ display: inline-block; background: #22c55e; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; margin-top: 20px; }}
      </style>
    </head>
    <body>
      <div class="container">
        <h2 style="margin:0 0 24px;">📊 Daily Call Summary</h2>
        <div class="stats">
          <div class="stat">
            <div class="stat-val">{stats.get('called', 0)}</div>
            <div class="stat-label">Calls Made</div>
          </div>
          <div class="stat">
            <div class="stat-val green">{stats.get('interested', 0)}</div>
            <div class="stat-label">Hot Leads</div>
          </div>
          <div class="stat">
            <div class="stat-val">{stats.get('voicemail', 0)}</div>
            <div class="stat-label">Voicemails</div>
          </div>
          <div class="stat">
            <div class="stat-val">{stats.get('no_answer', 0)}</div>
            <div class="stat-label">No Answer</div>
          </div>
        </div>
        {hot_leads_html}
        <a href="https://app.pineydigital.com/call" class="btn">View Dashboard</a>
      </div>
    </body>
    </html>
    """

    return send_email(ADMIN_EMAIL, subject, body, html_body)
"""The account-invite email: a plain-text body plus an HTML version with a button.

Pure functions (no database, no SMTP) so the wording can be tested and previewed.
The HTML uses a table layout with inline styles only -- the lowest common
denominator that Outlook, Gmail and Apple Mail all render the same way -- and
the button is an ordinary link inside a coloured table cell, which also works in
Outlook's Word-based renderer.
"""

from html import escape
from pathlib import Path

ACCENT = "#e8710a"
HEADER_BG = "#11151c"  # the app's dark surface: the logo wordmark is white
LOGO_CID = "pyxie-logo"
LOGO_PATH = Path(__file__).parent / "assets" / "pyxie-email-logo.png"  # 360 px wide, shown at 180 px for sharp high-DPI screens


def logo_bytes() -> bytes:
    return LOGO_PATH.read_bytes()


def invite_email_text(link: str, ttl_days: int) -> str:
    return (
        "You've been invited to PyXie Manager. Set up your account:\n\n"
        f"{link}\n\n"
        f"This link expires in {ttl_days} days and works once."
    )


def invite_email_html(link: str, ttl_days: int) -> str:
    href = escape(link, quote=True)
    shown = escape(link)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>You've been invited to PyXie</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">Set up your PyXie Manager account. The link works once and expires in {ttl_days} days.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f3f4f6;">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="520" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:520px;background:#ffffff;border-radius:8px;border:1px solid #e5e7eb;">
    <tr><td bgcolor="{HEADER_BG}" style="background:{HEADER_BG};padding:22px 32px;border-radius:8px 8px 0 0;border-bottom:3px solid {ACCENT};font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:20px;font-weight:700;color:#ffffff;">
      <img src="cid:{LOGO_CID}" width="180" height="46" alt="PyXie Proxmox Operations" style="display:block;border:0;outline:none;text-decoration:none;width:180px;height:auto;color:#ffffff;font-size:20px;">
    </td></tr>
    <tr><td style="padding:28px 32px 8px 32px;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#111827;">
      <p style="margin:0 0 12px 0;font-size:18px;font-weight:600;">You've been invited to PyXie</p>
      <p style="margin:0;font-size:15px;line-height:22px;color:#374151;">Someone has set up an account for you on PyXie Manager. Choose a password to finish setting it up.</p>
    </td></tr>
    <tr><td align="left" style="padding:20px 32px 8px 32px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td align="center" bgcolor="{ACCENT}" style="border-radius:6px;background:{ACCENT};">
          <a href="{href}" target="_blank" style="display:inline-block;padding:13px 28px;font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:16px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:6px;">Set up your account</a>
        </td>
      </tr></table>
    </td></tr>
    <tr><td style="padding:16px 32px 28px 32px;font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:13px;line-height:20px;color:#6b7280;">
      This link expires in {ttl_days} days and works once.<br>
      If the button doesn't work, copy this address into your browser:<br>
      <a href="{href}" style="color:{ACCENT};word-break:break-all;">{shown}</a>
    </td></tr>
  </table>
  <p style="margin:16px 0 0 0;font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:12px;color:#9ca3af;">If you weren't expecting this, you can ignore this email.</p>
</td></tr>
</table>
</body>
</html>
"""

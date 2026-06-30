"""
Professional email template for overdue reminders.
(Admin-only system – debtors have no dashboard access)
"""

from datetime import datetime
from typing import Dict, Any, Optional


# ============================================================
# COLORS
# ============================================================

COLORS = {
    'primary': '#0e9d7c',
    'primaryDark': '#0a7a62',
    'success': '#10b981',
    'danger': '#ef4444',
    'warning': '#f59e0b',
    'text': '#1e293b',
    'textSecondary': '#64748b',
    'textLight': '#94a3b8',
    'border': '#e5e7eb',
    'bgLight': '#f8fafc',
    'bgCard': '#ffffff',
    'footerBg': '#f1f5f9',
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def format_currency(amount: float) -> str:
    """
    Format currency amount with PHP peso sign.

    Args:
        amount: The amount to format

    Returns:
        str: Formatted currency string (e.g., ₱1,234.56)
    """
    if amount is None:
        amount = 0
    return f"₱{amount:,.2f}"


def _base_layout(content: str, title: str, options: Optional[Dict[str, Any]] = None) -> str:
    """
    Base HTML layout for all email templates.

    Args:
        content: The HTML content to embed
        title: The email title
        options: Additional options (company_name, branch_address, etc.)

    Returns:
        str: Complete HTML email
    """
    if options is None:
        options = {}

    company_name = options.get('company_name', 'Collectly')
    branch_address = options.get('branch_address', 'Manila, Philippines')
    contact_email = options.get('contact_email', 'support@collectly.ph')
    contact_phone = options.get('contact_phone', '+63 (2) 8123-4567')

    today = datetime.now().strftime('%B %d, %Y')
    year = datetime.now().year

    return f"""
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
      background-color: {COLORS['bgLight']};
      color: {COLORS['text']};
      -webkit-font-smoothing: antialiased;
    }}
    table {{ border-collapse: collapse; mso-table-lspace: 0; mso-table-rspace: 0; }}
    td {{ padding: 0; }}
    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
    .card {{
      background: {COLORS['bgCard']};
      border-radius: 16px;
      padding: 40px 32px;
      box-shadow: 0 4px 24px rgba(0, 0, 0, 0.06);
      border: 1px solid {COLORS['border']};
    }}
    .header {{
      text-align: center;
      padding-bottom: 24px;
      border-bottom: 2px solid {COLORS['border']};
      margin-bottom: 24px;
    }}
    .header-logo {{
      font-size: 28px;
      font-weight: 700;
      color: {COLORS['primary']};
      letter-spacing: -0.5px;
    }}
    .header-logo span {{ color: {COLORS['text']}; }}
    .header-sub {{
      font-size: 14px;
      color: {COLORS['textSecondary']};
      margin-top: 4px;
    }}
    .status-badge {{
      display: inline-block;
      padding: 8px 20px;
      border-radius: 100px;
      font-size: 14px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin: 12px 0 8px;
    }}
    .badge-overdue {{
      background: #fee2e2;
      color: #991b1b;
    }}
    .greeting {{
      font-size: 20px;
      font-weight: 600;
      margin-bottom: 8px;
      color: {COLORS['text']};
    }}
    .message-body {{
      font-size: 15px;
      line-height: 1.7;
      color: {COLORS['textSecondary']};
    }}
    .message-body p {{ margin: 0 0 12px 0; }}
    .details-grid {{
      display: table;
      width: 100%;
      margin: 20px 0;
      background: {COLORS['bgLight']};
      border-radius: 12px;
      padding: 16px 20px;
      border: 1px solid {COLORS['border']};
    }}
    .detail-row {{ display: table-row; }}
    .detail-label {{
      display: table-cell;
      font-size: 13px;
      color: {COLORS['textSecondary']};
      padding: 6px 12px 6px 0;
      font-weight: 500;
      white-space: nowrap;
    }}
    .detail-value {{
      display: table-cell;
      font-size: 14px;
      font-weight: 600;
      color: {COLORS['text']};
      padding: 6px 0;
      text-align: right;
    }}
    .divider {{
      border: none;
      border-top: 1px solid {COLORS['border']};
      margin: 20px 0;
    }}
    .footer {{
      margin-top: 24px;
      padding-top: 20px;
      border-top: 1px solid {COLORS['border']};
      font-size: 13px;
      color: {COLORS['textLight']};
      text-align: center;
      line-height: 1.6;
    }}
    .footer a {{ color: {COLORS['primary']}; text-decoration: none; }}
    .footer a:hover {{ text-decoration: underline; }}
    .fine-print {{
      font-size: 11px;
      color: {COLORS['textLight']};
      margin-top: 12px;
      border-top: 1px solid {COLORS['border']};
      padding-top: 12px;
    }}
    @media screen and (max-width: 480px) {{
      .container {{ padding: 12px; }}
      .card {{ padding: 24px 16px; }}
      .details-grid {{ padding: 12px 14px; }}
      .detail-label, .detail-value {{ display: block; text-align: left; padding: 4px 0; }}
      .detail-row {{ display: block; }}
      .greeting {{ font-size: 18px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <div class="header">
        <div class="header-logo">{company_name} <span>Lending</span></div>
        <div class="header-sub">Debt management · {today}</div>
      </div>
      {content}
      <div class="footer">
        <p>
          {company_name} · {branch_address}<br>
          <a href="mailto:{contact_email}">{contact_email}</a> · 
          {contact_phone}
        </p>
        <p class="fine-print">
          This is an automated message. Please do not reply to this email.<br>
          © {year} {company_name} · All rights reserved.
        </p>
      </div>
    </div>
  </div>
</body>
</html>
    """


# ============================================================
# EMAIL TEMPLATES
# ============================================================

def generate_overdue_reminder_email(data: Dict[str, Any]) -> str:
    """
    Generate Overdue Reminder Email.

    Args:
        data: {
            'debtor_name': str,
            'debt_id': int,
            'debt_name': str,
            'original_amount': float,
            'paid_amount': float,
            'remaining_balance': float,
            'due_date': str or datetime,
            'days_overdue': int,
            'penalty_note': str or None,
            'company_name': str,
            'branch_address': str,
            'contact_email': str,
            'contact_phone': str,
        }

    Returns:
        str: Complete HTML email
    """
    debtor_name = data.get('debtor_name', 'Borrower')
    debt_id = data.get('debt_id', 'N/A')
    debt_name = data.get('debt_name', 'N/A')
    original_amount = data.get('original_amount', 0)
    paid_amount = data.get('paid_amount', 0)
    remaining_balance = data.get('remaining_balance', 0)
    due_date = data.get('due_date')
    days_overdue = data.get('days_overdue', 0)
    penalty_note = data.get('penalty_note')

    # Format due date
    if due_date:
        if isinstance(due_date, str):
            try:
                due_date = datetime.fromisoformat(due_date.replace('Z', '+00:00')).strftime('%B %d, %Y')
            except ValueError:
                due_date = due_date
        else:
            due_date = due_date.strftime('%B %d, %Y')
    else:
        due_date = 'N/A'

    penalty_html = ""
    if penalty_note:
        penalty_html = f"""
        <div style="background: {COLORS['bgLight']}; border-radius: 12px; padding: 12px 16px; border-left: 4px solid {COLORS['danger']}; margin: 16px 0;">
          <p style="margin: 0; font-size: 14px; color: {COLORS['textSecondary']};">
            <strong>Note:</strong> {penalty_note}
          </p>
        </div>
        """

    content = f"""
    <div style="text-align: center;">
      <div class="status-badge badge-overdue">⏰ Overdue Reminder</div>
      <div class="greeting">Dear {debtor_name},</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        Your debt payment is now <strong>{days_overdue} day(s) overdue</strong>. Please settle the outstanding balance at your earliest convenience to avoid further penalties.
      </p>
    </div>

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Debt ID</span>
        <span class="detail-value">#{debt_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Debt Name</span>
        <span class="detail-value">{debt_name}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Original Amount</span>
        <span class="detail-value">{format_currency(original_amount)}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Amount Paid</span>
        <span class="detail-value">{format_currency(paid_amount)}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Remaining Balance</span>
        <span class="detail-value" style="color: {COLORS['danger']};">{format_currency(remaining_balance)}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Due Date</span>
        <span class="detail-value" style="color: {COLORS['warning']};">{due_date}</span>
      </div>
    </div>

    {penalty_html}

    <hr class="divider">

    <div class="message-body">
      <p><strong>What you need to do:</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">⚠️ Settle the remaining balance as soon as possible</li>
        <li style="margin-bottom: 6px;">📞 If you have questions or need payment arrangements, contact us immediately</li>
        <li style="margin-bottom: 6px;">💰 Avoid additional penalties by paying before the next reminder</li>
      </ul>
      <p style="margin-top: 12px; font-size: 14px; background: {COLORS['bgLight']}; padding: 12px 16px; border-radius: 8px; border-left: 4px solid {COLORS['warning']};">
        📞 <strong>Need assistance?</strong> Call us at <a href="tel:{data.get('contact_phone', '+63 (2) 8123-4567')}" style="color: {COLORS['primary']};">{data.get('contact_phone', '+63 (2) 8123-4567')}</a> or email <a href="mailto:{data.get('contact_email', 'support@collectly.ph')}" style="color: {COLORS['primary']};">{data.get('contact_email', 'support@collectly.ph')}</a>.
      </p>
    </div>
    """

    return _base_layout(content, 'Overdue Reminder – Payment Required', data)
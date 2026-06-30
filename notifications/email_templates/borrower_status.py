"""
Professional email templates for borrower status changes.
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
    'info': '#3b82f6',
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
    .badge-success {{ background: #d1fae5; color: #065f46; }}
    .badge-danger {{ background: #fee2e2; color: #991b1b; }}
    .badge-info {{ background: #dbeafe; color: #1e40af; }}
    .badge-warning {{ background: #fef3c7; color: #92400e; }}
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
        <div class="header-sub">Borrower management · {today}</div>
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
          © {datetime.now().year} {company_name} · All rights reserved.
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

def generate_activated_email(data: Dict[str, Any]) -> str:
    """
    Generate email for BORROWER ACTIVATION.

    Args:
        data: {
            'borrower_id': int,
            'borrower_name': str,
            'borrower_email': str or None,
            'borrower_contact': str or None,
            'company_name': str,
            'branch_address': str,
            'contact_email': str,
            'contact_phone': str,
        }

    Returns:
        str: Complete HTML email
    """
    borrower_id = data.get('borrower_id', 'N/A')
    borrower_name = data.get('borrower_name', 'Borrower')
    borrower_email = data.get('borrower_email', '—')
    borrower_contact = data.get('borrower_contact', '—')
    today = datetime.now().strftime('%B %d, %Y')

    content = f"""
    <div style="text-align: center;">
      <div class="status-badge badge-success">✓ Account Reactivated</div>
      <div class="greeting">Welcome back, {borrower_name}! 🙌</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        Your account has been <strong>reactivated</strong>. You may now apply for new loans and access your account.
      </p>
    </div>

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Borrower ID</span>
        <span class="detail-value">#{borrower_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Name</span>
        <span class="detail-value">{borrower_name}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Email</span>
        <span class="detail-value">{borrower_email}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Contact</span>
        <span class="detail-value">{borrower_contact}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Reactivated On</span>
        <span class="detail-value">{today}</span>
      </div>
    </div>

    <hr class="divider">

    <div class="message-body">
      <p><strong>What you can do now:</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">✓ Apply for a new loan</li>
        <li style="margin-bottom: 6px;">✓ View your account status</li>
        <li style="margin-bottom: 6px;">✓ Update your contact information</li>
      </ul>
      <p style="margin-top: 12px; font-size: 14px; background: {COLORS['bgLight']}; padding: 12px 16px; border-radius: 8px; border-left: 4px solid {COLORS['success']};">
        💡 <strong>Tip:</strong> Keep your contact details up‑to‑date to receive important notifications.
      </p>
    </div>
    """

    return _base_layout(content, 'Account Reactivated', data)


def generate_deactivated_email(data: Dict[str, Any]) -> str:
    """
    Generate email for BORROWER DEACTIVATION.

    Args:
        data: {
            'borrower_id': int,
            'borrower_name': str,
            'borrower_email': str or None,
            'borrower_contact': str or None,
            'active_debt_count': int,
            'company_name': str,
            'branch_address': str,
            'contact_email': str,
            'contact_phone': str,
        }

    Returns:
        str: Complete HTML email
    """
    borrower_id = data.get('borrower_id', 'N/A')
    borrower_name = data.get('borrower_name', 'Borrower')
    borrower_email = data.get('borrower_email', '—')
    borrower_contact = data.get('borrower_contact', '—')
    active_debt_count = data.get('active_debt_count', 0)

    content = f"""
    <div style="text-align: center;">
      <div class="status-badge badge-danger">✗ Account Deactivated</div>
      <div class="greeting">Dear {borrower_name},</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        Your account has been <strong>deactivated</strong>. All active debts have been marked as <strong>defaulted</strong>.
      </p>
    </div>

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Borrower ID</span>
        <span class="detail-value">#{borrower_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Name</span>
        <span class="detail-value">{borrower_name}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Email</span>
        <span class="detail-value">{borrower_email}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Contact</span>
        <span class="detail-value">{borrower_contact}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Active Debts Affected</span>
        <span class="detail-value" style="color: {COLORS['danger']};">{active_debt_count}</span>
      </div>
    </div>

    <hr class="divider">

    <div class="message-body">
      <p><strong>Important Information:</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">⚠️ All active debts have been set to defaulted status</li>
        <li style="margin-bottom: 6px;">⚖️ Legal action may be initiated for outstanding balances</li>
        <li style="margin-bottom: 6px;">📞 Contact us immediately to resolve this matter</li>
      </ul>
      <p style="margin-top: 12px; font-size: 14px; background: {COLORS['bgLight']}; padding: 12px 16px; border-radius: 8px; border-left: 4px solid {COLORS['danger']};">
        📞 <strong>Urgent:</strong> Call us at <a href="tel:{data.get('contact_phone', '+63 (2) 8123-4567')}" style="color: {COLORS['primary']};">{data.get('contact_phone', '+63 (2) 8123-4567')}</a> to discuss your account.
      </p>
    </div>
    """

    return _base_layout(content, 'Account Deactivated', data)


def generate_merged_email(data: Dict[str, Any]) -> Dict[str, str]:
    """
    Generate email for BORROWER MERGE.

    Returns two emails: one for the source borrower and one for the target borrower.

    Args:
        data: {
            'source_borrower_id': int,
            'source_borrower_name': str,
            'target_borrower_id': int,
            'target_borrower_name': str,
            'debts_transferred': int,
            'company_name': str,
            'branch_address': str,
            'contact_email': str,
            'contact_phone': str,
        }

    Returns:
        dict: {
            'source': str (HTML email for source borrower),
            'target': str (HTML email for target borrower),
        }
    """
    source_id = data.get('source_borrower_id', 'N/A')
    source_name = data.get('source_borrower_name', 'Source Borrower')
    target_id = data.get('target_borrower_id', 'N/A')
    target_name = data.get('target_borrower_name', 'Target Borrower')
    debts_transferred = data.get('debts_transferred', 0)
    today = datetime.now().strftime('%B %d, %Y')

    # Source borrower content (the one being merged in)
    source_content = f"""
    <div style="text-align: center;">
      <div class="status-badge badge-info">↔ Account Merged</div>
      <div class="greeting">Dear {source_name},</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        Your account has been <strong>merged</strong> into <strong>{target_name}</strong>. You will no longer have access to this account.
      </p>
    </div>

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Source Account ID</span>
        <span class="detail-value">#{source_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Target Account ID</span>
        <span class="detail-value">#{target_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Target Name</span>
        <span class="detail-value">{target_name}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Debts Transferred</span>
        <span class="detail-value">{debts_transferred}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Merge Date</span>
        <span class="detail-value">{today}</span>
      </div>
    </div>

    <hr class="divider">

    <div class="message-body">
      <p><strong>Next steps:</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">✓ Use <strong>{target_name}</strong> for all future transactions</li>
        <li style="margin-bottom: 6px;">✓ All your debts and payment history have been transferred</li>
        <li style="margin-bottom: 6px;">📞 Contact us if you have questions about the merge</li>
      </ul>
    </div>
    """

    source_email = _base_layout(source_content, 'Account Merge – Your Account Merged', data)

    # Target borrower content (the one receiving the merged data)
    target_content = f"""
    <div style="text-align: center;">
      <div class="status-badge badge-info">↔ Account Merge Completed</div>
      <div class="greeting">Dear {target_name},</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        <strong>{source_name}</strong> has been merged into your account. All their debts and transactions are now under your name.
      </p>
    </div>

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Your Account ID</span>
        <span class="detail-value">#{target_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Merged From</span>
        <span class="detail-value">{source_name} (ID: #{source_id})</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Additional Debts Added</span>
        <span class="detail-value">{debts_transferred}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Merge Date</span>
        <span class="detail-value">{today}</span>
      </div>
    </div>

    <hr class="divider">

    <div class="message-body">
      <p><strong>What you need to know:</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">✓ All debts from {source_name} are now your responsibility</li>
        <li style="margin-bottom: 6px;">✓ Make sure to review your updated debt list</li>
        <li style="margin-bottom: 6px;">📞 Contact us for any clarification</li>
      </ul>
    </div>
    """

    target_email = _base_layout(target_content, 'Account Merge – New Debts Added', data)

    return {
        'source': source_email,
        'target': target_email,
    }
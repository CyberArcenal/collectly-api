"""
Professional email templates for loan application status changes.
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
    .status-approved {{ background: #d1fae5; color: #065f46; }}
    .status-rejected {{ background: #fee2e2; color: #991b1b; }}
    .status-pending {{ background: #fef3c7; color: #92400e; }}
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
    .cta-button {{
      display: inline-block;
      background-color: {COLORS['primary']};
      color: #ffffff;
      padding: 12px 32px;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 600;
      text-decoration: none;
      margin: 8px 0;
    }}
    .cta-button:hover {{ background-color: {COLORS['primaryDark']}; }}
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
        <div class="header-sub">Smart loan management · {today}</div>
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

def generate_approved_email(data: Dict[str, Any]) -> str:
    """
    Generate email for APPROVED loan application.

    Args:
        data: {
            'applicant_name': str,
            'application_id': int,
            'debt_id': int,
            'purpose': str,
            'amount': float,
            'interest_rate': float,
            'interest_period': str,  # 'per_annum' or 'per_month'
            'due_date': str or datetime,
            'company_name': str,
            'branch_address': str,
            'contact_email': str,
            'contact_phone': str,
        }

    Returns:
        str: Complete HTML email
    """
    applicant_name = data.get('applicant_name', 'Borrower')
    application_id = data.get('application_id') or data.get('debt_id') or 'N/A'
    purpose = data.get('purpose', 'General loan')
    amount = data.get('amount', 0)
    interest_rate = data.get('interest_rate', 0)
    interest_period = data.get('interest_period', 'per_annum')
    due_date = data.get('due_date')

    period_label = 'per month' if interest_period == 'per_month' else 'per annum'

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

    content = f"""
    <div style="text-align: center;">
      <div class="status-badge status-approved">✓ Approved</div>
      <div class="greeting">Congratulations, {applicant_name}! 🎉</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        Your loan application has been <strong>approved</strong>. Your funds are now available.
      </p>
    </div>

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Loan ID</span>
        <span class="detail-value">#{application_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Purpose</span>
        <span class="detail-value">{purpose}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Principal Amount</span>
        <span class="detail-value" style="color: {COLORS['success']}; font-size: 18px;">{format_currency(amount)}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Interest Rate</span>
        <span class="detail-value">{interest_rate}% {period_label}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Proposed Due Date</span>
        <span class="detail-value" style="color: {COLORS['warning']};">{due_date}</span>
      </div>
    </div>

    <hr class="divider">

    <div class="message-body">
      <p><strong>What happens next?</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">✓ Funds have been credited to your account</li>
        <li style="margin-bottom: 6px;">✓ A loan agreement document will be sent separately (by email or courier)</li>
        <li style="margin-bottom: 6px;">📄 The agreement will specify the full payment terms, including the due date and any installment arrangements</li>
      </ul>
      <p style="margin-top: 12px; font-size: 14px; background: {COLORS['bgLight']}; padding: 12px 16px; border-radius: 8px; border-left: 4px solid {COLORS['primary']};">
        📞 <strong>For inquiries</strong> about the schedule, please contact our loan officer at <a href="mailto:{data.get('contact_email', 'support@collectly.ph')}" style="color: {COLORS['primary']};">{data.get('contact_email', 'support@collectly.ph')}</a>.
      </p>
    </div>
    """

    return _base_layout(content, 'Loan Approved', data)


def generate_rejected_email(data: Dict[str, Any]) -> str:
    """
    Generate email for REJECTED loan application.

    Args:
        data: {
            'applicant_name': str,
            'application_id': int,
            'amount': float,
            'purpose': str,
            'rejection_reason': str or None,
            'company_name': str,
            'branch_address': str,
            'contact_email': str,
            'contact_phone': str,
        }

    Returns:
        str: Complete HTML email
    """
    applicant_name = data.get('applicant_name', 'Borrower')
    application_id = data.get('application_id', 'N/A')
    amount = data.get('amount', 0)
    purpose = data.get('purpose', 'General loan')
    rejection_reason = data.get('rejection_reason')

    rejection_html = ""
    if rejection_reason:
        rejection_html = f"""
        <div style="background: {COLORS['bgLight']}; border-radius: 12px; padding: 16px 20px; border-left: 4px solid {COLORS['danger']}; margin: 16px 0;">
          <p style="margin: 0; font-size: 14px; color: {COLORS['textSecondary']};">
            <strong>Reason for decline:</strong><br>
            {rejection_reason}
          </p>
        </div>
        """

    content = f"""
    <div style="text-align: center;">
      <div class="status-badge status-rejected">✗ Not Approved</div>
      <div class="greeting">Dear {applicant_name},</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        We regret to inform you that your loan application could not be approved at this time.
      </p>
    </div>

    {rejection_html}

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Application ID</span>
        <span class="detail-value">#{application_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Amount Requested</span>
        <span class="detail-value">{format_currency(amount)}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Purpose</span>
        <span class="detail-value">{purpose}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Date Applied</span>
        <span class="detail-value">{datetime.now().strftime('%B %d, %Y')}</span>
      </div>
    </div>

    <hr class="divider">

    <div class="message-body">
      <p><strong>What can you do next?</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">✓ Improve your credit score by paying existing loans on time</li>
        <li style="margin-bottom: 6px;">✓ Reduce your debt-to-income ratio</li>
        <li style="margin-bottom: 6px;">✓ Re-apply after 30 days with updated financial documents</li>
      </ul>
      <p style="margin-top: 12px; font-size: 14px; background: {COLORS['bgLight']}; padding: 12px 16px; border-radius: 8px; border-left: 4px solid {COLORS['warning']};">
        📞 <strong>Need help?</strong> Contact our loan officer at <a href="mailto:{data.get('contact_email', 'support@collectly.ph')}" style="color: {COLORS['primary']};">{data.get('contact_email', 'support@collectly.ph')}</a>.
      </p>
    </div>
    """

    return _base_layout(content, 'Loan Update', data)


def generate_submitted_email(data: Dict[str, Any]) -> str:
    """
    Generate email for SUBMITTED loan application (confirmation).

    Args:
        data: {
            'applicant_name': str,
            'application_id': int,
            'purpose': str,
            'amount': float,
            'company_name': str,
            'branch_address': str,
            'contact_email': str,
            'contact_phone': str,
        }

    Returns:
        str: Complete HTML email
    """
    applicant_name = data.get('applicant_name', 'Borrower')
    application_id = data.get('application_id', 'N/A')
    purpose = data.get('purpose', 'General loan')
    amount = data.get('amount', 0)

    now = datetime.now()
    submitted_date = now.strftime('%B %d, %Y')
    submitted_time = now.strftime('%I:%M %p')

    content = f"""
    <div style="text-align: center;">
      <div class="status-badge status-pending">⏳ Under Review</div>
      <div class="greeting">Thank You, {applicant_name}! 🙏</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        Your loan application has been <strong>received</strong> and is currently under review.
      </p>
    </div>

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Application ID</span>
        <span class="detail-value">#{application_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Purpose</span>
        <span class="detail-value">{purpose}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Amount Requested</span>
        <span class="detail-value" style="color: {COLORS['primary']}; font-size: 18px;">{format_currency(amount)}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Status</span>
        <span class="detail-value" style="color: {COLORS['warning']};">Pending review</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Submitted On</span>
        <span class="detail-value">{submitted_date} at {submitted_time}</span>
      </div>
    </div>

    <hr class="divider">

    <div class="message-body">
      <p><strong>What happens next?</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">⏳ Our team will review your application within 1-3 business days</li>
        <li style="margin-bottom: 6px;">📧 You will receive an email notification of the decision</li>
        <li style="margin-bottom: 6px;">📞 If we need additional information, we will contact you directly</li>
      </ul>
      <p style="margin-top: 12px; font-size: 14px; background: {COLORS['bgLight']}; padding: 12px 16px; border-radius: 8px; border-left: 4px solid {COLORS['primary']};">
        💡 <strong>Tip:</strong> Ensure your contact details are up‑to‑date to avoid delays. You can reach us at <a href="mailto:{data.get('contact_email', 'support@collectly.ph')}" style="color: {COLORS['primary']};">{data.get('contact_email', 'support@collectly.ph')}</a>.
      </p>
    </div>
    """

    return _base_layout(content, 'Loan Application Received', data)
  
  
def generate_pending_reminder_email(data: Dict[str, Any]) -> str:
    """
    Generate reminder email for pending loan applications (admin reminder to review).

    Args:
        data: {
            'applicant_name': str,
            'application_id': int,
            'purpose': str,
            'amount': float,
            'days_waiting': int,  # number of days since submission
            'company_name': str,
            'branch_address': str,
            'contact_email': str,
            'contact_phone': str,
        }

    Returns:
        str: Complete HTML email
    """
    applicant_name = data.get('applicant_name', 'Borrower')
    application_id = data.get('application_id', 'N/A')
    purpose = data.get('purpose', 'General loan')
    amount = data.get('amount', 0)
    days_waiting = data.get('days_waiting', 0)

    # Determine urgency tone based on waiting days
    if days_waiting >= 7:
        urgency_note = "⚠️ This application has been pending for over a week. Please prioritize."
    elif days_waiting >= 3:
        urgency_note = "⏳ This application is approaching the 5‑day review target."
    else:
        urgency_note = "🕒 This application is within the normal review window."

    content = f"""
    <div style="text-align: center;">
      <div class="status-badge status-pending">⏳ Pending Review</div>
      <div class="greeting">Reminder: Pending Loan Application</div>
      <p class="message-body" style="font-size: 15px; color: {COLORS['textSecondary']}; margin-bottom: 12px;">
        The following loan application has been waiting for review for <strong>{days_waiting} days</strong>.
      </p>
      <p style="font-size: 14px; color: {COLORS['warning']}; margin-top: -6px;">
        {urgency_note}
      </p>
    </div>

    <div class="details-grid">
      <div class="detail-row">
        <span class="detail-label">Application ID</span>
        <span class="detail-value">#{application_id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Applicant</span>
        <span class="detail-value">{applicant_name}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Purpose</span>
        <span class="detail-value">{purpose}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Amount Requested</span>
        <span class="detail-value" style="color: {COLORS['primary']}; font-size: 18px;">{format_currency(amount)}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Days Waiting</span>
        <span class="detail-value" style="color: {COLORS['warning']};">{days_waiting} days</span>
      </div>
    </div>

    <hr class="divider">

    <div class="message-body">
      <p><strong>Suggested Actions:</strong></p>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; list-style: none;">
        <li style="margin-bottom: 6px;">📋 Review the application details</li>
        <li style="margin-bottom: 6px;">✅ Make a decision (approve/reject) or request additional documents</li>
        <li style="margin-bottom: 6px;">📧 Notify the applicant of the outcome</li>
      </ul>
      <p style="margin-top: 12px; font-size: 14px; background: {COLORS['bgLight']}; padding: 12px 16px; border-radius: 8px; border-left: 4px solid {COLORS['warning']};">
        📞 <strong>Need assistance?</strong> Contact support at <a href="mailto:{data.get('contact_email', 'support@collectly.ph')}" style="color: {COLORS['primary']};">{data.get('contact_email', 'support@collectly.ph')}</a>.
      </p>
    </div>
    """

    return _base_layout(content, 'Pending Loan Reminder', data)
  
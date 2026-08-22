import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------
# Environment Variables & Secrets
# ---------------------------------------------------------
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")


def send_email_alert(setup):
    """Dispatches a formatted HTML test alert to Gmail recipients."""
    print("--- Configuration Diagnostic ---")
    print(f"EMAIL_SENDER: {'Set (' + EMAIL_SENDER + ')' if EMAIL_SENDER else 'MISSING'}")
    print(f"EMAIL_RECEIVER: {'Set (' + EMAIL_RECEIVER + ')' if EMAIL_RECEIVER else 'MISSING'}")
    print(f"EMAIL_APP_PASSWORD: {'Set (Hidden)' if EMAIL_APP_PASSWORD else 'MISSING'}")
    print("--------------------------------")

    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECEIVER):
        print("ERROR: One or more required email environment variables are missing.")
        return

    # Clean and parse recipient addresses
    recipients = [e.strip() for e in EMAIL_RECEIVER.split(",") if e.strip()]
    if not recipients:
        print("ERROR: No valid recipient email addresses found.")
        return

    subject = f"🎯 Test Pipeline Alert: {setup['ticker']} - {setup['setup_type']} ({setup['direction']})"
    
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #2e7d32;">
          {setup['ticker']} Setup: {setup['setup_type']} ({setup['direction']})
        </h2>
        <p style="font-weight: bold; color: #0066cc;">
          ✅ Your GitHub Actions to Gmail alert pipeline is successfully connected!
        </p>
        <table style="width: 100%; max-width: 550px; border-collapse: collapse; margin-bottom: 15px;">
          <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px; font-weight: bold;">Key Level / Node:</td>
            <td style="padding: 8px;">${setup['key_level']}</td>
          </tr>
          <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px; font-weight: bold;">Trigger Price (+1.15 ATR):</td>
            <td style="padding: 8px;">${setup['trigger_price']}</td>
          </tr>
          <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px; font-weight: bold;">Invalidation:</td>
            <td style="padding: 8px;">${setup['invalidation_price']}</td>
          </tr>
          <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px; font-weight: bold;">Volatility Context:</td>
            <td style="padding: 8px;">{setup['vol_rank']}% ({setup['vol_regime']})</td>
          </tr>
        </table>
        <h3 style="margin-bottom: 6px;">Execution Thesis:</h3>
        <p style="background: #f9f9f9; padding: 12px; border-left: 4px solid #0066cc; border-radius: 4px;">
          {setup['reasoning']}
        </p>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Market Scanner Bot <{EMAIL_SENDER}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_content, "html"))

    try:
        print("Connecting to Gmail SMTP server (smtp.gmail.com:465)...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(EMAIL_SENDER, recipients, msg.as_string())
        print(f"SUCCESS: Test email successfully delivered to: {', '.join(recipients)}")
    except Exception as e:
        print(f"FAILED: SMTP connection or authentication error: {e}")


def main():
    print("Initiating test alert execution...")
    dummy_setup = {
        "ticker": "TEST-NVDA",
        "direction": "Long",
        "setup_type": "Support Reclaim",
        "key_level": 124.50,
        "trigger_price": 127.80,
        "invalidation_price": 122.20,
        "vol_rank": 58.4,
        "vol_regime": "High Vol (Mean Reversion Favored)",
        "reasoning": "This is a diagnostic notification to confirm your GitHub Actions cron runner and Gmail SMTP integration are working properly."
    }
    send_email_alert(dummy_setup)


if __name__ == "__main__":
    main()

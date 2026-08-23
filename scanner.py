def send_digest_email(top_setups):
    """Sends a consolidated Top 20 opportunities digest via BCC with VOLA branding."""
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECEIVER):
        print("Email configuration incomplete. Skipping email.")
        return

    recipients = [clean_env(e) for e in EMAIL_RECEIVER.split(",") if clean_env(e)]
    if not recipients:
        print("No valid recipient addresses found.")
        return

    subject = f"VOLA Update: Top {len(top_setups)} Market Setups & Volatility Report"
    
    rows_html = ""
    for idx, s in enumerate(top_setups, 1):
        color = "#2e7d32" if "Long" in s.get('direction', '') else "#c62828"
        rows_html += f"""
        <tr style="border-bottom: 1px solid #e0e0e0;">
          <td style="padding: 8px; font-weight: bold;">#{idx} {s['ticker']}</td>
          <td style="padding: 8px; color: {color}; font-weight: bold;">{s['direction']} ({s['setup_type']})</td>
          <td style="padding: 8px;">${s['key_level']}</td>
          <td style="padding: 8px;">${s['trigger_price']}</td>
          <td style="padding: 8px;">${s['invalidation_price']}</td>
          <td style="padding: 8px; font-weight: bold;">{s.get('risk_reward', '2.5:1')}</td>
          <td style="padding: 8px;">{s['vol_rank']}%</td>
          <td style="padding: 8px; font-size: 13px; color: #555;">{s['reasoning']}</td>
        </tr>
        """

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #222; max-width: 950px; margin: auto;">
        <div style="background-color: #0f172a; padding: 16px 20px; border-radius: 6px 6px 0 0;">
          <h2 style="color: #38bdf8; margin: 0; font-size: 20px;">
            ⚡ VOLA Market Intelligence Digest
          </h2>
          <p style="color: #94a3b8; margin: 4px 0 0 0; font-size: 13px;">
            Top {len(top_setups)} Opportunities (Price Action Structure, ATR Displacement & Volatility Rank)
          </p>
        </div>
        <div style="border: 1px solid #e2e8f0; border-top: none; padding: 15px; border-radius: 0 0 6px 6px;">
          <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 14px;">
            <thead>
              <tr style="background-color: #f8fafc; border-bottom: 2px solid #e2e8f0;">
                <th style="padding: 8px;">Rank / Ticker</th>
                <th style="padding: 8px;">Bias</th>
                <th style="padding: 8px;">Key Level</th>
                <th style="padding: 8px;">Trigger (+1.15 ATR)</th>
                <th style="padding: 8px;">Invalidation</th>
                <th style="padding: 8px;">Est. R:R</th>
                <th style="padding: 8px;">IV Rank</th>
                <th style="padding: 8px;">Thesis</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
        </div>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    # Changes sender display name to VOLA Update
    msg["From"] = f"VOLA Update <{EMAIL_SENDER}>"
    # Hides individual emails from recipients
    msg["To"] = "VOLA Subscribers <undisclosed-recipients:;>"
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            # Passing 'recipients' list to sendmail delivers the email to each inbox via BCC
            server.sendmail(EMAIL_SENDER, recipients, msg.as_string())
        print(f"VOLA Update successfully dispatched via BCC to {len(recipients)} recipients.")
    except Exception as e:
        print(f"Failed to send VOLA email: {e}")

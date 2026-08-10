#!/usr/bin/env python3
"""Send a report email via Gmail SMTP (app-password auth).

Used by the /nightly-loose-ends skill to deliver the overnight autopilot
report to the user before 7am. Dependency-free (stdlib only) so it runs
under any Python on this box.

Config is read from the first .env found among:
    <repo>/Governance/.env   (canonical)
    <bin>/../.env            (Order Samurai/.env)
    ./.env                   (cwd)

Required keys:
    GMAIL_APP_PASSWORD   16-char Google app password (NOT the account password)
    REPORT_EMAIL_FROM    sending Gmail address; also the SMTP login user
Optional keys:
    REPORT_EMAIL_TO      recipient (defaults to REPORT_EMAIL_FROM)

Usage:
    python send_report_email.py --subject "..." --body-file report.md
    echo "body" | python send_report_email.py --subject "..."

Exit codes: 0 sent, 2 missing credential/config, 3 send failure.
"""
import argparse
import html as _html
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Windows console can be cp1252; force UTF-8 so non-ASCII report text survives.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT = 30  # seconds — never wait forever on a remote call


def _candidate_env_paths():
    here = Path(__file__).resolve()
    return [
        here.parent.parent.parent / ".env",   # Governance/.env
        here.parent.parent / ".env",           # Order Samurai/.env
        Path.cwd() / ".env",
    ]


def load_env():
    cfg = {}
    for p in _candidate_env_paths():
        if not p.is_file():
            continue
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            cfg.setdefault(key, val)  # first .env wins
        break  # only the first existing .env
    # process env overrides file
    for k in ("GMAIL_APP_PASSWORD", "REPORT_EMAIL_FROM", "REPORT_EMAIL_TO"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def build_message(subject, body, sender, recipient):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(body, "plain", "utf-8"))
    html_body = (
        "<html><body style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "font-size:14px;line-height:1.5;color:#1a1a1a\">"
        "<pre style=\"white-space:pre-wrap;word-wrap:break-word;font-family:inherit\">"
        + _html.escape(body)
        + "</pre></body></html>"
    )
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def main():
    ap = argparse.ArgumentParser(description="Send a report email via Gmail SMTP.")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body-file", help="path to report body (UTF-8). If omitted, reads stdin.")
    ap.add_argument("--to", help="override recipient")
    args = ap.parse_args()

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8", errors="replace")
    else:
        body = sys.stdin.read()
    if not body.strip():
        print("[send_report_email] empty body — refusing to send.", file=sys.stderr)
        return 2

    cfg = load_env()
    password = cfg.get("GMAIL_APP_PASSWORD", "")
    sender = cfg.get("REPORT_EMAIL_FROM", "")
    recipient = args.to or cfg.get("REPORT_EMAIL_TO") or sender

    if not password or password.upper().startswith("REPLACE") or "<" in password:
        print(
            "[send_report_email] GMAIL_APP_PASSWORD not set in Governance/.env.\n"
            "  Create one at https://myaccount.google.com/apppasswords and add:\n"
            "    GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx\n"
            "  The report was NOT emailed (it is still saved to the state file).",
            file=sys.stderr,
        )
        return 2
    if not sender:
        print("[send_report_email] REPORT_EMAIL_FROM not set in Governance/.env.", file=sys.stderr)
        return 2

    msg = build_message(args.subject, body, sender, recipient)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
            server.login(sender, password)
            server.sendmail(sender, [recipient], msg.as_string())
    except Exception as exc:  # noqa: BLE001 — report any send failure, don't crash the night
        print(f"[send_report_email] SEND FAILED: {exc}", file=sys.stderr)
        return 3

    print(f"[send_report_email] sent '{args.subject}' to {recipient}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
SMTP email sender — replaces the original win32com Outlook integration.
Supports STARTTLS (port 587) and SSL (port 465).
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


@dataclass
class SMTPConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_address: str = ""
    use_tls: bool = True        # STARTTLS on port 587
    use_ssl: bool = False       # SSL on port 465


def is_configured(cfg: SMTPConfig) -> bool:
    return bool(cfg.host and cfg.username and cfg.password and cfg.from_address)


def test_connection(cfg: SMTPConfig) -> tuple[bool, str]:
    """Returns (success, message)."""
    try:
        if cfg.use_ssl:
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=10) as server:
                server.login(cfg.username, cfg.password)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=10) as server:
                if cfg.use_tls:
                    server.starttls()
                server.login(cfg.username, cfg.password)
        return True, "Connection successful."
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed — check username/password."
    except smtplib.SMTPConnectError as e:
        return False, f"Could not connect to {cfg.host}:{cfg.port} — {e}"
    except Exception as e:
        return False, str(e)


def _send(cfg: SMTPConfig, to_addresses: list[str], subject: str, body: str) -> str:
    """Sends a plain-text email. Returns a status string."""
    if not to_addresses:
        return "skipped (no recipients)"
    msg = MIMEMultipart()
    msg["From"] = cfg.from_address
    msg["To"] = ", ".join(to_addresses)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if cfg.use_ssl:
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=15) as server:
                server.login(cfg.username, cfg.password)
                server.sendmail(cfg.from_address, to_addresses, msg.as_string())
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=15) as server:
                if cfg.use_tls:
                    server.starttls()
                server.login(cfg.username, cfg.password)
                server.sendmail(cfg.from_address, to_addresses, msg.as_string())
        return f"sent to {', '.join(to_addresses)}"
    except Exception as e:
        return f"FAILED: {e}"


def _format_rows(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        lines.append(
            f"  Art: {r.get('Artikelnummer','')} | "
            f"REF: {r.get('Artikelnummer (Lieferant)','')} | "
            f"Menge: {r.get('Menge Gebinde','')} {r.get('Gebinde','')} | "
            f"Beleg: {r.get('Belegnummer','')} | "
            f"Lieferant: {r.get('Lieferant','')}"
        )
    return "\n".join(lines)


def _resolve_recipients(
    rows: list[dict],
    fixed: list[str],
    employee_mapping: dict[str, str],
) -> list[str]:
    """Merges fixed recipients with employee-derived ones (deduped)."""
    recipients = set(fixed)
    for row in rows:
        sach = row.get("Sachbearbeiter", "")
        email = employee_mapping.get(sach, "")
        if email:
            recipients.add(email)
    return [r for r in recipients if r]


def send_all_alerts(
    cfg: SMTPConfig,
    result,                          # ProcessingResult
    float_recipients: list[str],
    direct_recipients: list[str],
    info_recipients: list[str],
    employee_mapping: dict[str, str],
) -> list[str]:
    """
    Sends alert emails for each flagged category.
    Returns a list of human-readable status lines.
    """
    if not is_configured(cfg):
        return ["Email not configured — skipped."]

    statuses: list[str] = []

    # Float quantity alerts
    if result.floats:
        recipients = _resolve_recipients(result.floats, float_recipients, employee_mapping)
        subject = f"MobyDik | Float-Mengen gebucht | {len(result.floats)} Position(en)"
        body = (
            "Hallo,\n\n"
            "bei folgenden Positionen wurde eine Gleitkommazahl als Menge gebucht:\n\n"
            + _format_rows(result.floats)
            + "\n\nBitte prüfen."
        )
        status = _send(cfg, recipients, subject, body)
        statuses.append(f"Float alert: {status}")

    # Direct delivery alerts
    if result.directs:
        recipients = _resolve_recipients(result.directs, direct_recipients, employee_mapping)
        subject = f"MobyDik | Direktlieferungen | {len(result.directs)} Position(en)"
        body = (
            "Hallo,\n\n"
            "folgende Positionen wurden als Direktlieferung gebucht (#DL / #Direkt):\n\n"
            + _format_rows(result.directs)
            + "\n\nBitte prüfen."
        )
        status = _send(cfg, recipients, subject, body)
        statuses.append(f"Direct delivery alert: {status}")

    # Info article alerts
    if result.info_hits:
        recipients = _resolve_recipients(result.info_hits, info_recipients, employee_mapping)
        subject = f"MobyDik | Info-Artikel gebucht | {len(result.info_hits)} Position(en)"
        body = (
            "Hallo,\n\n"
            "folgende Info-Artikel wurden gebucht:\n\n"
            + _format_rows(result.info_hits)
            + "\n\nBitte prüfen."
        )
        status = _send(cfg, recipients, subject, body)
        statuses.append(f"Info article alert: {status}")

    if not statuses:
        statuses.append("No alerts triggered.")

    return statuses

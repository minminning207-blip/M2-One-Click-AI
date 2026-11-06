"""
mailer.py - email ပို့ခြင်း (SMTP) ။ SMTP_HOST မထည့်ထားလျှင် dev mode: terminal ထဲ + data/mail_outbox.log ထဲ ရေးသည်။
"""
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

from app.core import config


def configured() -> bool:
    return bool(config.SMTP_HOST)


def send(to: str, subject: str, text: str, html: str = "") -> bool:
    """ပို့နိုင်လျှင် True ။ SMTP မထည့်ရသေးလျှင် dev mode (log) ဖြင့် True ။ ပို့မရလျှင် False။"""
    if not configured():
        print(f"\n[MAIL - dev mode] To: {to}\nSubject: {subject}\n{text}\n", flush=True)
        try:
            Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
            with open(Path(config.DATA_DIR) / "mail_outbox.log", "a", encoding="utf-8") as f:
                f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} To: {to}\nSubject: {subject}\n{text}\n\n")
        except OSError:
            pass
        return True
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        if config.SMTP_SECURITY == "ssl":
            smtp = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        with smtp:
            if config.SMTP_SECURITY == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[MAIL] ပို့မရပါ: {config.scrub(e)}", flush=True)
        return False

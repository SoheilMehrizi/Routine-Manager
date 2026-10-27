import unittest
from email.message import EmailMessage
from unittest.mock import patch

from app.email_service import (MailConfigError, MailDeliveryError, agenda_email_html,
                               send_agenda_email, smtp_config_status)


class EmailServiceTests(unittest.TestCase):
    def setUp(self):
        self.env = {"SMTP_HOST": "smtp.example.test", "SMTP_PORT": "587",
                    "SMTP_USER": "planner@example.test", "SMTP_PASSWORD": "secret",
                    "SMTP_FROM": "planner@example.test"}

    def test_missing_or_partial_smtp_configuration_is_explained(self):
        ok, message = smtp_config_status({})
        self.assertFalse(ok)
        self.assertIn("SMTP_HOST", message)
        partial = dict(self.env, SMTP_PASSWORD="")
        ok, message = smtp_config_status(partial)
        self.assertFalse(ok)
        self.assertIn("both SMTP_USER and SMTP_PASSWORD", message)

    def test_html_escapes_agenda_content_and_supports_rtl(self):
        body = "Good morning — 2026-10-27\n🕖 Asia/Tehran\n\nTasks\n• <script>alert(1)</script>"
        out = agenda_email_html("2026-10-27", body, "fa", test=True)
        self.assertIn('dir="rtl"', out)
        self.assertIn("TEST EMAIL", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>alert", out)

    @patch("app.email_service.smtplib.SMTP")
    def test_sends_multipart_message_over_starttls(self, smtp_class):
        smtp = smtp_class.return_value.__enter__.return_value
        send_agenda_email("user@example.test", "2026-10-27", "Morning\n\nTasks\n• Plan", env=self.env)
        smtp.starttls.assert_called_once()
        msg = smtp.send_message.call_args.args[0]
        self.assertEqual(msg["To"], "user@example.test")
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        self.assertEqual([part.get_content_type() for part in msg.iter_parts()], ["text/plain", "text/html"])

    @patch("app.email_service.smtplib.SMTP_SSL")
    def test_uses_implicit_tls_on_port_465(self, smtp_class):
        env = dict(self.env, SMTP_PORT="465")
        send_agenda_email("user@example.test", "2026-10-27", "Morning", env=env)
        smtp_class.assert_called_once()
        smtp_class.return_value.__enter__.return_value.send_message.assert_called_once()

    @patch("app.email_service.smtplib.SMTP")
    def test_authentication_failure_is_actionable_and_redacted(self, smtp_class):
        smtp = smtp_class.return_value.__enter__.return_value
        from smtplib import SMTPAuthenticationError
        smtp.login.side_effect = SMTPAuthenticationError(535, b"bad credentials")
        with self.assertRaisesRegex(MailDeliveryError, "SMTP authentication failed") as raised:
            send_agenda_email("user@example.test", "2026-10-27", "Morning", env=self.env)
        self.assertNotIn("secret", str(raised.exception))

    def test_bad_recipient_is_rejected_before_sending(self):
        with self.assertRaisesRegex(MailDeliveryError, "account email address is invalid"):
            send_agenda_email("not-an-email", "2026-10-27", "Morning", env=self.env)


if __name__ == "__main__":
    unittest.main()

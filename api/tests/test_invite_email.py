import email
from email import policy
from html.parser import HTMLParser

from pyxie_core.invite_email import invite_email_html, invite_email_text

LINK = "https://pyxie.example.com/accept-invite?token=abc123&x=1"


def test_text_keeps_the_original_wording():
    t = invite_email_text(LINK, 7)
    assert t.startswith("You've been invited to PyXie Manager. Set up your account:")
    assert LINK in t and "expires in 7 days and works once" in t


def test_html_has_a_button_linking_to_the_invite_and_escapes_it():
    h = invite_email_html(LINK, 7)
    assert "Set up your account</a>" in h
    assert 'href="https://pyxie.example.com/accept-invite?token=abc123&amp;x=1"' in h
    assert "expires in 7 days" in h


def test_html_is_well_formed_enough_to_parse_and_has_the_link_twice():
    class P(HTMLParser):
        hrefs = []
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.hrefs.append(dict(attrs)["href"])
    p = P(); p.feed(invite_email_html(LINK, 7))
    assert p.hrefs == [LINK, LINK]  # button + the copy-paste fallback


def test_a_hostile_link_cannot_inject_markup():
    h = invite_email_html('https://x/"><script>alert(1)</script>', 7)
    assert "<script>" not in h


def test_multipart_message_carries_both_parts():
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(invite_email_text(LINK, 7), "plain"))
    msg.attach(MIMEText(invite_email_html(LINK, 7), "html"))
    parsed = email.message_from_string(msg.as_string(), policy=policy.default)
    kinds = [p.get_content_type() for p in parsed.iter_parts()]
    assert kinds == ["text/plain", "text/html"]

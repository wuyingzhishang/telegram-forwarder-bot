from forwarder.links import parse_message_link


def test_parse_public_and_private_telegram_message_links():
    assert parse_message_link("https://t.me/NewsRoom/123?single=1") == ("@NewsRoom", 123)
    assert parse_message_link("https://t.me/c/43210/987/654") == ("-10043210", 654)
    assert parse_message_link("https://t.me/+invitecode") is None
    assert parse_message_link("https://example.com/NewsRoom/123") is None

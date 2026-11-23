from datetime import datetime, timedelta

from services.refunds import refund_amount, refund_percent

NOW = datetime(2026, 11, 25, 10, 0)


def test_full_refund_over_24h():
    assert refund_percent(NOW + timedelta(hours=25), NOW) == 100
    assert refund_amount(60000, NOW + timedelta(hours=48), NOW) == (100, 60000)


def test_half_refund_2_to_24h():
    assert refund_percent(NOW + timedelta(hours=23), NOW) == 50
    assert refund_percent(NOW + timedelta(hours=2), NOW) == 50
    assert refund_amount(60000, NOW + timedelta(hours=5), NOW) == (50, 30000)


def test_no_refund_under_2h():
    assert refund_percent(NOW + timedelta(hours=1), NOW) == 0
    assert refund_amount(60000, NOW + timedelta(minutes=30), NOW) == (0, 0)
    assert refund_percent(NOW - timedelta(hours=1), NOW) == 0

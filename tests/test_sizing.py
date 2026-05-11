from src.sizing import target_shares, target_value, target_weight


def test_target_shares_rounds_down_by_default():
    assert target_shares(10000, 3) == 3


def test_target_shares_can_allow_fractional():
    assert round(target_shares(10000, 3, allow_fractional=True), 4) == 3.3333


def test_target_value_and_weight():
    assert target_value(2, 50) == 100
    assert target_weight(100, 10000) == 0.01

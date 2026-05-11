from src.sizing import risk_parity_targets, target_shares, target_value, target_weight


def test_target_shares_rounds_down_by_default():
    assert target_shares(10000, 3) == 3


def test_target_shares_can_allow_fractional():
    assert round(target_shares(10000, 3, allow_fractional=True), 4) == 3.3333


def test_target_value_and_weight():
    assert target_value(2, 50) == 100
    assert target_weight(100, 10000) == 0.01


def test_risk_parity_targets_spend_available_cash_with_whole_shares():
    targets = risk_parity_targets(
        10000,
        [
            ("AAA", 100, 5),
            ("BBB", 50, 2.5),
        ],
    )

    assert [target.shares for target in targets] == [50, 100]
    assert sum(target.value for target in targets) == 10000
    assert round(targets[0].atr_risk, 2) == round(targets[1].atr_risk, 2)

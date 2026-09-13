from common.urban_vpn import (
    DIRECT_CODE,
    FREE_URBAN_VPN_LOCATIONS,
    normalize_vpn_order,
    vpn_location_for_attempt,
)


def test_normalize_vpn_order_keeps_only_supported_unique_codes():
    assert normalize_vpn_order(["de", "DE", "bad", "us"]) == [
        DIRECT_CODE, "DE", "US"
    ]


def test_vpn_location_attempt_stays_at_last_configured_route():
    order = ["DE", DIRECT_CODE, "GB"]
    assert vpn_location_for_attempt(order, 0) == "DE"
    assert vpn_location_for_attempt(order, 1) == DIRECT_CODE
    assert vpn_location_for_attempt(order, 99) == "GB"


def test_free_location_list_contains_no_direct_or_duplicate_codes():
    codes = [code for code, _ in FREE_URBAN_VPN_LOCATIONS]
    assert DIRECT_CODE not in codes
    assert len(codes) == len(set(codes))
    assert {"DE", "US", "GB", "VN"}.issubset(codes)

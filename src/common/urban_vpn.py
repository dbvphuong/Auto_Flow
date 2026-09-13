"""Urban VPN locations exposed by the free browser extension."""

DIRECT_CODE = "DIRECT"

# Urban VPN's official free-location page currently lists these browser VPN
# locations. Codes are used in saved settings; labels are only presentation.
FREE_URBAN_VPN_LOCATIONS = (
    ("DZ", "Algeria"), ("AR", "Argentina"), ("AU", "Australia"),
    ("AT", "Austria"), ("BY", "Belarus"), ("BE", "Belgium"),
    ("BO", "Bolivia"), ("BR", "Brazil"), ("BG", "Bulgaria"),
    ("CA", "Canada"), ("CL", "Chile"), ("CN", "China"),
    ("CO", "Colombia"), ("CR", "Costa Rica"), ("HR", "Croatia"),
    ("CY", "Cyprus"), ("CZ", "Czech Republic"), ("DK", "Denmark"),
    ("EC", "Ecuador"), ("EG", "Egypt"), ("EE", "Estonia"),
    ("FI", "Finland"), ("FR", "France"), ("DE", "Germany"),
    ("GR", "Greece"), ("GT", "Guatemala"), ("HN", "Honduras"),
    ("HK", "Hong Kong"), ("HU", "Hungary"), ("IS", "Iceland"),
    ("IN", "India"), ("ID", "Indonesia"), ("IE", "Ireland"),
    ("IL", "Israel"), ("IT", "Italy"), ("JP", "Japan"),
    ("JO", "Jordan"), ("KZ", "Kazakhstan"), ("KG", "Kyrgyzstan"),
    ("LV", "Latvia"), ("LT", "Lithuania"), ("LU", "Luxembourg"),
    ("MY", "Malaysia"), ("MT", "Malta"), ("MX", "Mexico"),
    ("MN", "Mongolia"), ("MA", "Morocco"), ("NL", "Netherlands"),
    ("NZ", "New Zealand"), ("NI", "Nicaragua"), ("NO", "Norway"),
    ("PK", "Pakistan"), ("PA", "Panama"), ("PY", "Paraguay"),
    ("PE", "Peru"), ("PH", "Philippines"), ("PL", "Poland"),
    ("PT", "Portugal"), ("PR", "Puerto Rico"), ("QA", "Qatar"),
    ("RO", "Romania"), ("RU", "Russia"), ("SA", "Saudi Arabia"),
    ("RS", "Serbia"), ("SG", "Singapore"), ("SK", "Slovakia"),
    ("SI", "Slovenia"), ("ZA", "South Africa"), ("KR", "South Korea"),
    ("ES", "Spain"), ("SE", "Sweden"), ("CH", "Switzerland"),
    ("TW", "Taiwan"), ("TH", "Thailand"), ("TR", "Turkey"),
    ("UA", "Ukraine"), ("AE", "United Arab Emirates"),
    ("GB", "United Kingdom"), ("US", "United States"),
    ("UY", "Uruguay"), ("VE", "Venezuela"), ("VN", "Vietnam"),
)

URBAN_VPN_LABELS = {
    DIRECT_CODE: "IP gốc (mạng thường)",
    **dict(FREE_URBAN_VPN_LOCATIONS),
}


def normalize_vpn_order(value):
    """Return a unique, supported order that always contains original IP."""
    raw = value if isinstance(value, list) else []
    result = []
    for item in raw:
        code = str(item).strip().upper()
        if code in URBAN_VPN_LABELS and code not in result:
            result.append(code)
    if DIRECT_CODE not in result:
        result.insert(0, DIRECT_CODE)
    return result or [DIRECT_CODE]


def vpn_location_for_attempt(order, attempt):
    normalized = normalize_vpn_order(order)
    index = min(max(0, int(attempt or 0)), len(normalized) - 1)
    return normalized[index]

from app.pii import mask_pii_in_text, verhoeff_check_digit, verhoeff_is_valid


def test_verhoeff_matches_known_example():
    # 236 -> 3 is the textbook Verhoeff example.
    assert verhoeff_check_digit("236") == 3
    assert verhoeff_is_valid("2363")
    assert not verhoeff_is_valid("2364")
    # Long inputs reach every row of the tables. Generate and validate must agree.
    for payload in ["50950406940", "99999999999", "20000000001"]:
        assert verhoeff_is_valid(payload + str(verhoeff_check_digit(payload)))


def test_mask_pii_in_text_hides_identifiers_but_keeps_other_numbers():
    payload = "50950406940"
    aadhaar = payload + str(verhoeff_check_digit(payload))
    spaced_aadhaar = f"{aadhaar[:4]} {aadhaar[4:8]} {aadhaar[8:]}"
    not_an_aadhaar = payload + str((verhoeff_check_digit(payload) + 1) % 10)

    text = (
        f"Aadhaar {spaced_aadhaar}, PAN ABCPE1234F, call +91-9876543210 or 8123456789, "
        f"mail priya.s@gmail.com. Order {not_an_aadhaar} total 34900."
    )
    masked = mask_pii_in_text(text)

    for secret in [spaced_aadhaar, "ABCPE1234F", "9876543210", "8123456789", "priya.s@gmail.com"]:
        assert secret not in masked
    assert "XXXX" + spaced_aadhaar[-4:] in masked
    # A 12-digit number that fails the checksum is an order id, not an Aadhaar number.
    assert not_an_aadhaar in masked
    assert "34900" in masked

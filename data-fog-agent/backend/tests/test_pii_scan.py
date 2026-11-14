from sqlalchemy import select

from app.db import system_session
from app.models import Alert, Asset, AssetColumn
from app.pii import column_pii_types, verhoeff_check_digit
from app.pii_scan import scan_asset


def scan_everything() -> dict[tuple[str, str], set[str]]:
    """Scans every asset. Returns {(asset path, column): kinds} for every tagged column."""
    with system_session() as db:
        asset_ids = db.scalars(select(Asset.id)).all()
    for asset_id in asset_ids:
        scan_asset(asset_id)
    with system_session() as db:
        rows = db.execute(
            select(Asset.path, AssetColumn.name, AssetColumn.pii_type)
            .join(Asset, Asset.id == AssetColumn.asset_id)
            .where(AssetColumn.pii_type.is_not(None))
        ).all()
    return {(path, column): set(kinds.split(",")) for path, column, kinds in rows}


def catalogue_path(ground_truth_asset: str) -> str:
    # Ground truth names files "drive:hr/x.xlsx"; the files connector stores "hr/x.xlsx".
    return ground_truth_asset.removeprefix("drive:")


def test_scan_finds_every_pii_column_in_the_demo(demo, synced_demo):
    _, ground_truth = demo
    tagged = scan_everything()

    expected = {
        (catalogue_path(entry["asset"]), entry["column"]): set(entry["pii_types"])
        for entry in ground_truth["pii_columns"]
    }
    for key, kinds in expected.items():
        assert key in tagged, f"{key} was not tagged"
        found = tagged[key]
        # Identifiers come from exact rules, so they must match exactly. The small spaCy model
        # mixes up Indian names and cities, so for those either tag protects the column.
        assert kinds - {"PERSON", "LOCATION"} <= found, (key, found)
        if kinds & {"PERSON", "LOCATION"}:
            assert found & {"PERSON", "LOCATION"}, (key, found)
    # Over-tagging would mask useful columns like order status or amounts.
    assert set(tagged) == set(expected)


def test_new_pii_raises_one_alert_without_the_values(synced_demo):
    scan_everything()
    with system_session() as db:
        alerts = db.execute(
            select(Asset.path, Alert.message).join(Asset, Asset.id == Alert.asset_id)
        ).all()
    messages = dict(alerts)
    assert len(alerts) == len(messages)  # one alert per asset
    assert "notes (AADHAAR, PHONE)" in messages["support_db.public.tickets"]
    assert "sales_db.public.orders" not in messages
    for message in messages.values():
        assert "@" not in message and "XXXX" not in message

    # Nothing new on a second scan, so no new alerts.
    scan_everything()
    with system_session() as db:
        assert len(db.scalars(select(Alert)).all()) == len(alerts)


def test_free_text_counts_a_single_match_but_other_columns_need_a_fifth():
    payload = "50950406940"
    aadhaar = payload + str(verhoeff_check_digit(payload))
    notes = ["Refund done."] * 99 + [f"Customer shared Aadhaar {aadhaar}"]
    assert column_pii_types("notes", "text", notes) == {"AADHAAR"}
    # The same one-in-a-hundred in a code column is noise.
    assert column_pii_types("reference", "bigint", [aadhaar] + ["1001"] * 99) == set()
    assert column_pii_types("contact", "bigint", ["9876543210"] * 30 + ["1"] * 70) == {"PHONE"}


def test_columns_without_personal_data_stay_untagged():
    assert column_pii_types("status", "text", ["completed", "cancelled", "returned"] * 20) == set()
    assert column_pii_types("store_code", "text", ["STORE-PUN-01", "ONLINE"] * 20) == set()
    assert column_pii_types("notes", "text", []) == set()

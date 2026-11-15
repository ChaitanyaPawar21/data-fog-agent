import hashlib
import shutil

import pandas as pd
import pytest
from sqlalchemy import select

from app import merger
from app.db import system_session
from app.main import app
from app.models import Asset, AssetPermission, AuditLog, Group, Org, Proposal, User
from app.routers.sources import get_job_queue
from app.sync import create_source, sync_source
from tests.conftest import log_in


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append((name, *args))
        return object()


@pytest.fixture
def job_queue():
    queue = FakeJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    yield queue
    app.dependency_overrides.clear()


@pytest.fixture
def drive_copy(demo, demo_org, tmp_path):
    """A copy of the demo drive as its own source. One export sits in finance/, which only
    finance can see, while sales/ is shared with sales and finance. Returns (folder, source id)."""
    output_dir, _ = demo
    drive = tmp_path / "drive"
    shutil.copytree(output_dir / "drive", drive)  # copy2: modified times are kept
    shutil.move(drive / "sales" / "orders_export_jan (1).csv", drive / "finance")
    with system_session() as db:
        org_id = db.scalar(select(Org.id))
        source = create_source(
            db,
            org_id,
            "files",
            "drive copy",
            {
                "path": str(drive),
                "folder_groups": {"sales": ["sales", "finance"], "finance": ["finance"]},
            },
        )
        db.commit()
        source_id = source.id
    sync_source(source_id)
    return drive, source_id


def proposals_by_files() -> dict[frozenset, Proposal]:
    with system_session() as db:
        proposals = db.scalars(select(Proposal).where(Proposal.type == "merge")).all()
    return {frozenset(f["path"].split("/")[-1] for f in p.payload["files"]): p for p in proposals}


def groups_of(path: str) -> list[str]:
    with system_session() as db:
        asset = db.scalar(select(Asset).where(Asset.path == path, Asset.archived.is_(False)))
        return sorted(
            db.scalars(
                select(Group.name)
                .join(AssetPermission, AssetPermission.group_id == Group.id)
                .where(AssetPermission.asset_id == asset.id)
            )
        )


def approve_as_admin(client, proposal_id: int):
    admin = log_in(client, "admin@chaiwala.co")
    response = client.post(f"/api/proposals/{proposal_id}/approve", headers=admin)
    assert response.status_code == 200
    return response.json()


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_finds_the_demo_copies_with_the_right_numbers(demo, synced_demo):
    _, ground_truth = demo
    assert len(merger.find_merge_candidates()) == 2
    found = proposals_by_files()

    for expected in [g for g in ground_truth["duplicate_groups"] if g["kind"] == "file_merge"]:
        payload = found[frozenset(expected["files"])].payload
        assert payload["key_column"] == expected["key_column"]
        assert payload["total_rows"] == expected["total_rows"]
        assert payload["exact_duplicate_rows"] == expected["exact_duplicate_rows"]
        assert payload["merged_rows"] == expected["merged_rows"]
        assert [c["key"] for c in payload["conflicts"]] == expected["conflicting_keys"]
        # Oldest first, and the newest file holding a conflicting row wins it.
        assert [f["path"].split("/")[-1] for f in payload["files"]] == expected["files"] or (
            not expected["conflicting_keys"]
        )
        for conflict in payload["conflicts"]:
            assert conflict["winner"] == conflict["versions"][-1]["file"]

    # Nothing else looks like a copy, and the same files are never proposed twice.
    assert merger.find_merge_candidates() == []


def test_merge_writes_the_result_archives_originals_and_narrows_groups(
    demo, drive_copy, client, job_queue
):
    _, ground_truth = demo
    drive, source_id = drive_copy
    merger.find_merge_candidates()
    found = proposals_by_files()
    reports = found[frozenset(ground_truth["duplicate_groups"][0]["files"])]
    exports = found[frozenset(ground_truth["duplicate_groups"][1]["files"])]

    for proposal, expected in [
        (reports, ground_truth["duplicate_groups"][0]),
        (exports, ground_truth["duplicate_groups"][1]),
    ]:
        approve_as_admin(client, proposal.id)
        assert ("execute_merge_job", proposal.id) in job_queue.jobs
        merger.execute_merge(proposal.id)

        with system_session() as db:
            done = db.get(Proposal, proposal.id)
            assert done.status == "done", done.payload.get("error")
            payload = done.payload
        output = drive / payload["output_path"]
        merged = pd.read_csv(output) if output.suffix == ".csv" else pd.read_excel(output)
        assert len(merged) == expected["merged_rows"]
        assert (
            round(float(merged[expected["amount_column"]].sum()), 2)
            == (expected["merged_amount_sum"])
        )
        for moved in payload["moves"]:
            assert not (drive / moved["from"]).exists()
            assert "_archive" in moved["to"]
        with system_session() as db:
            originals = [db.get(Asset, f["asset_id"]) for f in payload["files"]]
            assert all(asset.archived for asset in originals)
            actions = set(
                db.scalars(
                    select(AuditLog.action).where(AuditLog.target == f"proposal:{proposal.id}")
                )
            )
        assert {"approve_proposal", "merge_file_written", "merge_originals_archived"} <= actions
        assert "merge_done" in actions

    # Reports were all in sales/ (sales + finance). One export was in finance/ (finance only),
    # so only finance may see the merged export, and a later sync doesn't widen it.
    with system_session() as db:
        reports_out = db.get(Proposal, reports.id).payload["output_path"]
        exports_out = db.get(Proposal, exports.id).payload["output_path"]
    assert groups_of(reports_out) == ["finance", "sales"]
    assert groups_of(exports_out) == ["finance"]
    sync_source(source_id)
    assert groups_of(exports_out) == ["finance"]


def test_undo_restores_the_originals_byte_for_byte(demo, drive_copy, client, job_queue):
    drive, _ = drive_copy
    merger.find_merge_candidates()
    proposal = next(
        p for files, p in proposals_by_files().items() if "sales_report_final.xlsx" in files
    )
    originals = {f["path"]: sha(drive / f["path"]) for f in proposal.payload["files"]}
    approve_as_admin(client, proposal.id)
    merger.execute_merge(proposal.id)

    admin = log_in(client, "admin@chaiwala.co")
    response = client.post(f"/api/proposals/{proposal.id}/undo", headers=admin)
    assert response.status_code == 202
    with system_session() as db:
        admin_id = db.scalar(select(User.id).where(User.email == "admin@chaiwala.co"))
    merger.undo_merge(proposal.id, admin_id)

    for path, digest in originals.items():
        assert sha(drive / path) == digest
    with system_session() as db:
        undone = db.get(Proposal, proposal.id)
        assert undone.status == "undone"
        # Same asset rows as before, live again; the merged file's asset is archived.
        for file in undone.payload["files"]:
            assert not db.get(Asset, file["asset_id"]).archived
        merged_asset = db.get(Asset, undone.payload["output_asset_id"])
        assert merged_asset.archived
    assert not (drive / undone.payload["output_path"]).exists()
    assert (drive / "sales" / "_archive" / undone.payload["output_name"]).exists()


def test_a_file_changed_after_the_proposal_is_not_merged(drive_copy, client, job_queue):
    drive, source_id = drive_copy
    merger.find_merge_candidates()
    proposal = next(
        p for files, p in proposals_by_files().items() if "sales_report_final.xlsx" in files
    )
    report = drive / "sales" / "sales_report_final.xlsx"
    pd.concat([pd.read_excel(report)] * 2).to_excel(report, index=False)
    sync_source(source_id)

    approve_as_admin(client, proposal.id)
    merger.execute_merge(proposal.id)
    with system_session() as db:
        failed = db.get(Proposal, proposal.id)
    assert failed.status == "failed"
    assert "changed since the merge was proposed" in failed.payload["error"]
    assert report.exists()
    assert not (drive / "sales" / failed.payload["output_name"]).exists()


def test_merge_previews_are_for_reviewers_only(synced_demo, client, job_queue):
    merger.find_merge_candidates()
    admin = log_in(client, "admin@chaiwala.co")
    listed = client.get("/api/proposals", params={"type": "merge"}, headers=admin).json()
    assert len(listed) == 2
    preview = listed[0]["merge"]
    assert {"files", "key_column", "total_rows", "conflicts", "merged_rows"} <= set(preview)

    # Priya can see the sales files but doesn't own them.
    priya = log_in(client, "priya.sharma@chaiwala.co")
    assert client.get("/api/proposals", params={"type": "merge"}, headers=priya).json() == []
    response = client.post(f"/api/proposals/{listed[0]['id']}/approve", headers=priya)
    assert response.status_code == 404


def test_copies_whose_headers_differ_only_in_case_are_merged(demo_org, tmp_path, client, job_queue):
    folder = tmp_path / "drive" / "sales"
    folder.mkdir(parents=True)
    old = pd.DataFrame({"Order_ID": [1, 2], "Amount": [10, 20]})
    new = pd.DataFrame({"order_id": [2, 3], "amount": [25, 30]})
    old.to_csv(folder / "export.csv", index=False)
    new.to_csv(folder / "export (1).csv", index=False)
    with system_session() as db:
        source = create_source(
            db,
            db.scalar(select(Org.id)),
            "files",
            "case drive",
            {"path": str(tmp_path / "drive"), "folder_groups": {"sales": ["sales"]}},
        )
        db.commit()
        source_id = source.id
    sync_source(source_id)

    (proposal_id,) = merger.find_merge_candidates()
    approve_as_admin(client, proposal_id)
    merger.execute_merge(proposal_id)

    with system_session() as db:
        done = db.get(Proposal, proposal_id)
    assert done.status == "done", done.payload.get("error")
    merged = pd.read_csv(tmp_path / "drive" / done.payload["output_path"])
    assert len(merged.columns) == 2
    assert sorted(merged.iloc[:, 0]) == [1, 2, 3]

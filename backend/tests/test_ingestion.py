import asyncio

from sqlalchemy import func, select

from app.db.database import AsyncSessionLocal
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.services.connectors import ConnectorError
from app.services.connectors.sandbox import sandbox_onedrive
from app.services.indexing import index_file
from app.services.jobs import drain_jobs
from tests.conftest import user_id_for


async def _ingest(client, headers, items):
    res = await client.post("/api/ingestion/start", headers=headers, json={"items": items})
    assert res.status_code == 202, res.text
    await drain_jobs()
    return (await client.get(f"/api/ingestion/{res.json()['job_id']}", headers=headers)).json()


async def _docs(client, headers):
    return {d["filename"]: d for d in (await client.get("/api/documents", headers=headers)).json()["documents"]}


async def test_folder_selection_is_expanded_and_every_file_indexed(client, auth_headers):
    job = await _ingest(client, auth_headers, [{"id": "folder_hr", "drive_type": "onedrive", "is_folder": True},
                                              {"id": "file_fin_budget", "drive_type": "onedrive"}])
    assert job["status"] == "COMPLETED", job
    docs = await _docs(client, auth_headers)
    assert set(docs) == {"Leave Policy 2026.pdf", "Employee Handbook.docx", "Budget 2026.xlsx"}
    assert all(d["status"] == "INDEXED" and d["chunk_count"] > 0 for d in docs.values())
    assert docs["Leave Policy 2026.pdf"]["folder_path"] == "/HR"
    assert docs["Budget 2026.xlsx"]["modified_date"] is not None


async def test_google_workspace_files_are_exported_and_indexed(client, auth_headers):
    job = await _ingest(client, auth_headers, [{"id": "root", "drive_type": "google_drive", "is_folder": True}])
    assert job["status"] == "COMPLETED", job
    docs = await _docs(client, auth_headers)
    assert {d["file_type"] for d in docs.values()} == {"docx", "xlsx", "pptx", "pdf"}


async def test_unsupported_files_are_recorded_with_a_reason(client, auth_headers):
    sandbox_onedrive.set_content("file_fin_expense", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    job = await _ingest(client, auth_headers, [{"id": "file_fin_expense", "drive_type": "onedrive"}])
    assert job["status"] == "COMPLETED"
    doc = (await _docs(client, auth_headers))["Expense Policy.txt"]
    assert doc["status"] == "UNSUPPORTED" and "Legacy" in doc["error_message"]


async def test_reingesting_unchanged_files_does_not_reembed(client, auth_headers):
    item = [{"id": "file_hr_handbook", "drive_type": "onedrive"}]
    await _ingest(client, auth_headers, item)
    job = await _ingest(client, auth_headers, item)
    assert job["result"]["summary"] == {"unchanged": 1}


async def test_concurrent_indexing_of_the_same_file_is_safe(client, auth_headers):
    user_id = await user_id_for(auth_headers, client)
    item = await sandbox_onedrive.get_file("", "file_hr_leave")
    data = await sandbox_onedrive.download("", item)
    results = await asyncio.gather(*[index_file(user_id, item, data) for _ in range(4)])
    assert all(r.status in ("INDEXED", "UNCHANGED") for r in results)
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(func.count(Document.id)))).scalar_one() == 1
        doc = (await s.execute(select(Document))).scalar_one()
        chunks = (await s.execute(select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == doc.id))).scalar_one()
        assert chunks == doc.chunk_count


async def test_sync_reindexes_changes_and_purges_deleted_files(client, auth_headers):
    await _ingest(client, auth_headers, [{"id": "root", "drive_type": "onedrive", "is_folder": True}])
    sandbox_onedrive.set_content("file_fin_expense", b"Meals are now reimbursed up to $90 per day.")
    sandbox_onedrive.remove("file_welcome")
    assert (await client.post("/api/sync/start", headers=auth_headers)).status_code == 202
    await drain_jobs()
    summary = (await client.get("/api/sync/status", headers=auth_headers)).json()["last_sync"]
    assert summary["status"] == "COMPLETED", summary
    counts = summary["result"]["summary"]
    assert counts["indexed"] == 1 and counts["deleted"] == 1 and counts["unchanged"] == 4
    docs = await _docs(client, auth_headers)
    assert "Company Overview.txt" not in docs
    res = await client.post("/api/chat/stream", headers=auth_headers, json={"query": "meal reimbursement per day"})
    assert "$90" in res.text


async def test_sync_never_deletes_when_the_drive_is_unreachable(client, auth_headers, monkeypatch):
    await _ingest(client, auth_headers, [{"id": "folder_finance", "drive_type": "onedrive", "is_folder": True}])

    async def down(token):
        raise ConnectorError("OneDrive is unreachable")

    monkeypatch.setattr(sandbox_onedrive, "get_root", down)
    await client.post("/api/sync/start", headers=auth_headers)
    await drain_jobs()
    docs = await _docs(client, auth_headers)
    assert len(docs) == 2 and all(d["status"] == "INDEXED" for d in docs.values())


async def test_sync_requests_are_deduplicated_per_user(client, auth_headers):
    first = (await client.post("/api/sync/start", headers=auth_headers)).json()
    second = (await client.post("/api/sync/start", headers=auth_headers)).json()
    assert first["job_id"] == second["job_id"]


async def test_delete_document_removes_its_chunks(client, auth_headers):
    await _ingest(client, auth_headers, [{"id": "file_hr_leave", "drive_type": "onedrive"}])
    doc = (await _docs(client, auth_headers))["Leave Policy 2026.pdf"]
    assert (await client.delete(f"/api/documents/{doc['id']}", headers=auth_headers)).status_code == 200
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(func.count(DocumentChunk.id)))).scalar_one() == 0


async def _sync(client, headers):
    assert (await client.post("/api/sync/start", headers=headers)).status_code == 202
    await drain_jobs()
    return (await client.get("/api/sync/status", headers=headers)).json()["last_sync"]


async def test_sync_indexes_files_added_to_a_tracked_folder(client, auth_headers):
    await _ingest(client, auth_headers, [{"id": "folder_finance", "drive_type": "onedrive", "is_folder": True}])
    folders = (await client.get("/api/tracked-folders", headers=auth_headers)).json()["folders"]
    assert [(f["name"], f["path"]) for f in folders] == [("Finance", "/Finance")]

    sandbox_onedrive.add_file("file_fin_travel", "Travel Policy.txt", "folder_finance", b"Train travel is preferred under 500 km.")
    summary = await _sync(client, auth_headers)
    assert summary["status"] == "COMPLETED", summary
    assert summary["result"]["summary"] == {"unchanged": 2, "indexed": 1}
    docs = await _docs(client, auth_headers)
    assert docs["Travel Policy.txt"]["status"] == "INDEXED" and docs["Travel Policy.txt"]["folder_path"] == "/Finance"


async def test_removed_documents_stay_out_until_selected_again(client, auth_headers):
    await _ingest(client, auth_headers, [{"id": "folder_hr", "drive_type": "onedrive", "is_folder": True}])
    doc = (await _docs(client, auth_headers))["Employee Handbook.docx"]
    assert (await client.delete(f"/api/documents/{doc['id']}", headers=auth_headers)).status_code == 200
    await _sync(client, auth_headers)
    assert "Employee Handbook.docx" not in await _docs(client, auth_headers)

    await _ingest(client, auth_headers, [{"id": "file_hr_handbook", "drive_type": "onedrive"}])
    assert (await _docs(client, auth_headers))["Employee Handbook.docx"]["status"] == "INDEXED"


async def test_untracked_folders_are_no_longer_scanned(client, auth_headers):
    await _ingest(client, auth_headers, [{"id": "folder_finance", "drive_type": "onedrive", "is_folder": True}])
    folder = (await client.get("/api/tracked-folders", headers=auth_headers)).json()["folders"][0]
    assert (await client.delete(f"/api/tracked-folders/{folder['id']}", headers=auth_headers)).status_code == 200
    sandbox_onedrive.add_file("file_fin_new", "New Rules.txt", "folder_finance", b"New rules apply from May.")
    await _sync(client, auth_headers)
    docs = await _docs(client, auth_headers)
    assert "New Rules.txt" not in docs and docs["Budget 2026.xlsx"]["status"] == "INDEXED"


async def test_a_failed_reindex_keeps_serving_the_previous_version(client, auth_headers, monkeypatch):
    from app.services.embedding_service import get_embedding_provider

    await _ingest(client, auth_headers, [{"id": "file_fin_expense", "drive_type": "onedrive"}])
    sandbox_onedrive.set_content("file_fin_expense", b"Meals are now reimbursed up to $90 per day.")
    provider = get_embedding_provider()

    async def down(self, texts):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr(type(provider), "embed_documents", down)
    summary = await _sync(client, auth_headers)
    assert summary["result"]["summary"] == {"failed": 1}
    doc = (await _docs(client, auth_headers))["Expense Policy.txt"]
    assert doc["status"] == "INDEXED" and doc["chunk_count"] > 0 and "previous version" in doc["error_message"]

    monkeypatch.undo()
    summary = await _sync(client, auth_headers)
    assert summary["result"]["summary"] == {"indexed": 1}
    res = await client.post("/api/chat/stream", headers=auth_headers, json={"query": "meal reimbursement per day"})
    assert "$90" in res.text


async def test_renames_update_the_document_header_without_reembedding(client, auth_headers):
    await _ingest(client, auth_headers, [{"id": "file_fin_expense", "drive_type": "onedrive"}])
    sandbox_onedrive.rename("file_fin_expense", "Travel and Expense Policy.txt")
    summary = await _sync(client, auth_headers)
    assert summary["result"]["summary"] == {"unchanged": 1}
    async with AsyncSessionLocal() as s:
        contexts = (await s.execute(select(DocumentChunk.context))).scalars().all()
    assert contexts and all(c.startswith("Document: Travel and Expense Policy.txt") for c in contexts)


async def test_too_many_queued_ingestion_jobs_are_refused(client, auth_headers):
    item = {"items": [{"id": "file_hr_leave", "drive_type": "onedrive"}]}
    for _ in range(3):
        assert (await client.post("/api/ingestion/start", headers=auth_headers, json=item)).status_code == 202
    assert (await client.post("/api/ingestion/start", headers=auth_headers, json=item)).status_code == 429


async def test_an_interrupted_job_is_handed_back(client, auth_headers, monkeypatch):
    from app.models.ingestion_job import IngestionJob
    from app.services import jobs

    res = await client.post("/api/ingestion/start", headers=auth_headers,
                            json={"items": [{"id": "file_hr_leave", "drive_type": "onedrive"}]})
    started = asyncio.Event()

    async def slow(job):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(jobs, "run_ingest", slow)
    job = await jobs.claim_next()
    task = asyncio.create_task(jobs.run_job(job))
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    async with AsyncSessionLocal() as s:
        assert (await s.get(IngestionJob, res.json()["job_id"])).status == "PENDING"

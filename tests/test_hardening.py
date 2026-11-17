"""End-to-end tests of the hardening work. They run against the Compose stack
started by test.sh (GEMINI_TEST_MODE=rate_limit, WORKER_TEST_CRASH_FILENAME=crash.pdf)."""

import time

from conftest import compose


def test_upload_pypdf_done(upload, poll, pdf_bytes, redis_client):
    """@spec: 002-hardening
    @regression
    """
    r = upload("sample.pdf", pdf_bytes(3))
    assert r.status_code == 202, r.text
    doc_id = r.json()["documents"][0]["document_id"]
    doc = poll(doc_id)
    assert doc["status"] == "done", doc
    assert len(doc["pages"]) == 3
    assert doc["summary_status"] == "failed"
    assert redis_client.hget(f"doc:{doc_id}", "attempts") == "1"


def test_non_pdf_415(upload):
    """@spec: 002-hardening
    @regression
    """
    r = upload("notes.txt", b"plain text, not a pdf", ctype="text/plain")
    assert r.status_code == 415, r.text


def test_encrypted_failed(upload, poll, encrypted_pdf, redis_client):
    """@spec: 002-hardening
    @regression
    """
    r = upload("locked.pdf", encrypted_pdf)
    assert r.status_code == 202, r.text
    doc_id = r.json()["documents"][0]["document_id"]
    doc = poll(doc_id)
    assert doc["status"] == "failed", doc
    assert doc["error_code"] == "encrypted"
    assert redis_client.hget(f"doc:{doc_id}", "attempts") == "1"


def test_gpt5mini_400(upload, pdf_bytes):
    """@spec: 002-hardening
    @regression
    """
    r = upload("sample.pdf", pdf_bytes(1), parser="gpt5mini")
    assert r.status_code == 400, r.text


def test_crash_dead_letter(upload, poll, pdf_bytes, redis_client, finish, worker_guard):
    """@spec: 002-hardening
    @regression
    The worker exits on crash.pdf. Docker restarts it. The third attempt is the last.
    """
    r = upload("crash.pdf", pdf_bytes(1))
    assert r.status_code == 202, r.text
    doc_id = r.json()["documents"][0]["document_id"]
    finish.append(doc_id)
    doc = poll(doc_id, limit=120)
    assert doc["status"] == "failed", doc
    assert doc["error_code"] == "max_attempts", doc
    assert redis_client.hget(f"doc:{doc_id}", "attempts") == "3"
    dead = [m for _, m in redis_client.xrange("documents:dead") if m.get("document_id") == doc_id]
    assert len(dead) == 1, dead
    # The worker runs again with no start command from the test.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if "worker" in compose("ps", "--status", "running", "--services").stdout.split():
            break
        time.sleep(1)
    else:
        raise AssertionError("the worker does not run after the crashes")


def test_restart_recovers(upload, poll, pdf_bytes, redis_client, finish, worker_guard):
    """@spec: 002-hardening
    @regression
    """
    r = upload("ten.pdf", pdf_bytes(10))
    assert r.status_code == 202, r.text
    doc_id = r.json()["documents"][0]["document_id"]
    finish.append(doc_id)
    poll(doc_id, until=lambda d: d["status"] == "summarizing", interval=1, limit=60)
    assert redis_client.hget(f"doc:{doc_id}", "attempts") == "1"
    compose("kill", "worker")
    compose("start", "worker")
    doc = poll(doc_id, limit=120)
    assert doc["status"] == "done", doc
    assert redis_client.hget(f"doc:{doc_id}", "attempts") == "2"

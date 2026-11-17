"""Fixtures for the end-to-end tests. They run against the Compose stack."""

import io
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
import redis
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parent.parent
FINAL = ("done", "failed")


def compose(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run `docker compose` in the repository root. COMPOSE_FILE comes from the environment."""
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=True,
    )


def make_pdf(pages: int) -> bytes:
    """A text PDF with `pages` pages, written as raw bytes."""
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(f"{3 + 2 * i} 0 R".encode() for i in range(pages))
        + b"] /Count " + str(pages).encode() + b" >>",
    ]
    font = 3 + 2 * pages
    for i in range(pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {4 + 2 * i} 0 R >>".encode()
        )
        text = f"BT /F1 18 Tf 72 700 Td (Test page {i + 1} of the sample document.) Tj ET".encode()
        objs.append(b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    data = bytes(out)
    assert len(PdfReader(io.BytesIO(data)).pages) == pages
    return data


def make_encrypted_pdf() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(1))))
    writer.encrypt("secret-password")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture(scope="session")
def pdf_bytes() -> Callable[[int], bytes]:
    return make_pdf


@pytest.fixture(scope="session")
def encrypted_pdf() -> bytes:
    return make_encrypted_pdf()


@pytest.fixture(scope="session")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=os.environ.get("API_URL", "http://localhost:8000"), timeout=30) as c:
        yield c


@pytest.fixture(scope="session")
def redis_client() -> Iterator[redis.Redis]:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        out = compose("port", "redis", "6379").stdout.strip().splitlines()[0]
        port = out.rsplit(":", 1)[1]
        url = f"redis://127.0.0.1:{port}/0"
    r = redis.Redis.from_url(url, decode_responses=True)
    yield r
    r.close()


@pytest.fixture
def upload(client: httpx.Client) -> Callable[..., httpx.Response]:
    def _upload(filename: str, content: bytes, parser: str = "pypdf", ctype: str = "application/pdf") -> httpx.Response:
        return client.post("/documents", data={"parser": parser}, files={"files": (filename, content, ctype)})
    return _upload


@pytest.fixture
def poll(client: httpx.Client) -> Callable[..., dict]:
    def _poll(document_id: str, until: Callable[[dict], bool] | None = None,
              interval: float = 2, limit: float = 60) -> dict:
        until = until or (lambda d: d["status"] in FINAL)
        deadline = time.monotonic() + limit
        body: dict = {}
        while time.monotonic() < deadline:
            body = client.get(f"/documents/{document_id}").json()
            if until(body):
                return body
            time.sleep(interval)
        pytest.fail(f"timeout after {limit}s, last reply: {body}")
    return _poll


def worker_running() -> bool:
    out = compose("ps", "--status", "running", "--services").stdout.split()
    return "worker" in out


def ensure_worker(limit: float = 60) -> None:
    """Make sure the worker runs. If not within `limit` seconds, end the whole run."""
    deadline = time.monotonic() + limit
    started = False
    while time.monotonic() < deadline:
        if worker_running():
            return
        if not started:
            compose("start", "worker")
            started = True
        time.sleep(1)
    pytest.exit("the worker does not run, run ended", returncode=2)


@pytest.fixture
def worker_guard() -> Iterator[None]:
    """After the test, the worker runs again. Use it in each test that stops the worker or crashes it."""
    yield
    ensure_worker()


@pytest.fixture
def finish(client: httpx.Client) -> Iterator[list[str]]:
    """Collects document ids. Each one must be final before the next test starts."""
    ids: list[str] = []
    yield ids
    deadline = time.monotonic() + 120
    for doc_id in ids:
        while time.monotonic() < deadline:
            try:
                if client.get(f"/documents/{doc_id}").json().get("status") in FINAL:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)

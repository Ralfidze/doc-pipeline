#!/usr/bin/env bash
set -euo pipefail

BASE="http://localhost:8000"
URL_DUMMY="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
URL_BTC="https://bitcoin.org/bitcoin.pdf"
URL_SCAN="https://raw.githubusercontent.com/ocrmypdf/OCRmyPDF/main/tests/resources/skew.pdf"

fail() { echo "FAIL: $*" >&2; exit 1; }

cd "$(dirname "$0")"

echo "== Step 1: start the stack =="
command -v docker >/dev/null || fail "docker not found"
command -v curl >/dev/null || fail "curl not found"
command -v python3 >/dev/null || fail "python3 not found"
docker compose up -d --build
ok=0
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health" || true)
  if [ "$code" = "200" ]; then ok=1; break; fi
  sleep 2
done
[ "$ok" = 1 ] || fail "GET /health did not return 200 within 60 s"
echo "health: 200"

echo "== Step 2: download 3 PDF files =="
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/dummy.pdf" "$URL_DUMMY"
curl -fsSL -o "$TMP/bitcoin.pdf" "$URL_BTC"
curl -fsSL -o "$TMP/skew.pdf" "$URL_SCAN"
ls -l "$TMP" | sed 1d

jget() { python3 -c 'import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }

echo "== Step 3: upload with parser=pypdf =="
REPLY_A=$(curl -fsS -F "files=@$TMP/dummy.pdf" -F "files=@$TMP/bitcoin.pdf" -F "files=@$TMP/skew.pdf" -F "parser=pypdf" "$BASE/documents")
echo "$REPLY_A"
BATCH_A=$(echo "$REPLY_A" | jget 'd["batch_id"]')

echo "== Step 4: upload with parser=gemini =="
REPLY_B=$(curl -fsS -F "files=@$TMP/skew.pdf" -F "files=@$TMP/dummy.pdf" -F "parser=gemini" "$BASE/documents")
echo "$REPLY_B"
BATCH_B=$(echo "$REPLY_B" | jget 'd["batch_id"]')

echo "== Step 5: poll the batches =="
poll() {
  local id=$1 body
  for _ in $(seq 1 60); do
    body=$(curl -fsS "$BASE/batches/$id")
    if echo "$body" | python3 -c '
import sys,json
d=json.load(sys.stdin)
docs=d.get("documents",[])
sys.exit(0 if docs and all(x.get("status") in ("done","failed") for x in docs) else 1)'; then
      echo "batch $id finished"
      return 0
    fi
    sleep 2
  done
  echo "$body"
  fail "batch $id not finished within 120 s"
}
poll "$BATCH_A"
poll "$BATCH_B"

echo "== Step 6: documents =="
IDS=$( (echo "$REPLY_A"; echo "$REPLY_B") | python3 -c '
import sys,json
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    d=json.loads(line)
    for x in d.get("documents",d.get("accepted",[])):
        print(x["document_id"])')
DOCS=""
for id in $IDS; do
  body=$(curl -fsS "$BASE/documents/$id")
  DOCS="$DOCS$body"$'\n'
done
printf '%s' "$DOCS" > "$TMP/docs.jsonl"
python3 - "$TMP/docs.jsonl" <<'PY'
import sys, json
for line in open(sys.argv[1]):
    if not line.strip():
        continue
    d = json.loads(line)
    pages = d.get("pages") or []
    first = (pages[0].get("content") or "")[:200] if pages else ""
    print("---")
    print("filename:", d.get("filename"))
    print("parser:", d.get("parser"))
    print("status:", d.get("status"))
    print("pages:", len(pages))
    print("warnings:", d.get("warnings"))
    print("summary_status:", d.get("summary_status"))
    print("page 1 (200 chars):", repr(first))
    if d.get("summary"):
        print("summary:", d.get("summary"))
    else:
        print("summary_error:", d.get("summary_error"))
PY

echo "== Step 7: queue metrics =="
curl -fsS "$BASE/metrics/queue"
echo

echo "== Step 8: check expected results =="
python3 - "$TMP/docs.jsonl" <<'PY'
import sys, json
docs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
errs = []
if len(docs) != 5:
    errs.append(f"expected 5 documents, got {len(docs)}")
for d in docs:
    n = f'{d.get("filename")} [{d.get("parser")}]'
    if d.get("status") != "done":
        errs.append(f"{n}: status is {d.get('status')}, expected done")
    pages = d.get("pages") or []
    if d.get("parser") == "pypdf":
        if d.get("filename", "").startswith("skew"):
            if d.get("warnings") != ["no_text_layer"]:
                errs.append(f"{n}: warnings {d.get('warnings')}, expected ['no_text_layer']")
            if d.get("summary_status") != "skipped":
                errs.append(f"{n}: summary_status {d.get('summary_status')}, expected skipped")
        elif d.get("summary_status") != "done":
            errs.append(f"{n}: summary_status {d.get('summary_status')}, expected done")
    elif d.get("parser") == "gemini":
        if d.get("summary_status") != "done":
            errs.append(f"{n}: summary_status {d.get('summary_status')}, expected done")
        if not pages or not (pages[0].get("content") or "").strip():
            errs.append(f"{n}: page 1 content is empty")
if errs:
    print("\n".join("FAIL: " + e for e in errs))
    sys.exit(1)
print("all checks ok")
PY

echo "ALL STEPS PASSED"
echo "Stop the stack with: docker compose down"

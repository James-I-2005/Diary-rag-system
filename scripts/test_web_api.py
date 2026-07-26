"""Web / API 完整连通性测试：静态页 + REST + 一轮真实问答。"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8765"


def req(method: str, path: str, body: dict | None = None, timeout: float = 180.0):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(
        BASE + path, data=data, headers=headers, method=method
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            elapsed = time.perf_counter() - t0
            if "application/json" in ctype or path.startswith("/api/"):
                payload = json.loads(raw.decode("utf-8") or "null")
            else:
                payload = raw
            return resp.status, payload, elapsed, None
    except urllib.error.HTTPError as e:
        elapsed = time.perf_counter() - t0
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        return e.code, detail, elapsed, e
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return None, str(e), elapsed, e


def ok(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}" + (f" — {detail}" if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))
        sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
    return cond


def _safe(s: str, n: int = 80) -> str:
    t = str(s or "").replace("\n", " ")[:n]
    return t.encode("ascii", errors="replace").decode("ascii")


def main() -> int:
    print(f"Web 连通性测试 → {BASE}\n")
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if ok(name, cond, detail):
            passed += 1
        else:
            failed += 1

    # 1) health
    status, payload, elapsed, err = req("GET", "/api/health", timeout=5)
    check(
        "GET /api/health",
        status == 200 and isinstance(payload, dict) and payload.get("status") == "ok",
        f"status={status} {elapsed:.2f}s {payload!r}"[:160],
    )

    # 2) index.html
    status, payload, elapsed, err = req("GET", "/", timeout=5)
    html_ok = status == 200 and isinstance(payload, (bytes, bytearray)) and b"html" in payload[:200].lower()
    check("GET /", html_ok, f"status={status} bytes={len(payload) if isinstance(payload,(bytes,bytearray)) else '?'} {elapsed:.2f}s")

    # 3) static js
    status, payload, elapsed, err = req("GET", "/static/js/app.js", timeout=5)
    check(
        "GET /static/js/app.js",
        status == 200 and isinstance(payload, (bytes, bytearray)) and len(payload) > 100,
        f"status={status} {elapsed:.2f}s",
    )

    # 4) schemes
    status, payload, elapsed, err = req("GET", "/api/retrieval/schemes", timeout=10)
    schemes_ok = (
        status == 200
        and isinstance(payload, dict)
        and "default" in payload
        and isinstance(payload.get("schemes"), list)
        and len(payload["schemes"]) >= 1
    )
    check(
        "GET /api/retrieval/schemes",
        schemes_ok,
        f"status={status} default={payload.get('default') if isinstance(payload, dict) else None} {elapsed:.2f}s",
    )
    default_scheme = payload.get("default") if isinstance(payload, dict) else "embedding_only"

    # 5) list conversations
    status, payload, elapsed, err = req("GET", "/api/conversations", timeout=10)
    check(
        "GET /api/conversations",
        status == 200 and isinstance(payload, list),
        f"status={status} n={len(payload) if isinstance(payload, list) else '?'} {elapsed:.2f}s",
    )

    # 6) create conversation
    status, payload, elapsed, err = req(
        "POST", "/api/conversations", {"title": "web-连通测试"}, timeout=10
    )
    cid = payload.get("id") if isinstance(payload, dict) else None
    check(
        "POST /api/conversations",
        status in (200, 201) and bool(cid),
        f"status={status} id={cid} {elapsed:.2f}s",
    )
    if not cid:
        print("\n无法创建会话，中止后续消息测试。")
        print(f"合计 PASS={passed} FAIL={failed}")
        return 1

    # 7) get conversation
    status, payload, elapsed, err = req("GET", f"/api/conversations/{cid}", timeout=10)
    check(
        "GET /api/conversations/{id}",
        status == 200 and isinstance(payload, dict) and payload.get("id") == cid,
        f"status={status} {elapsed:.2f}s",
    )

    # 8) send message (full RAG path) — may take long due to LLM + embedding
    print("\n… 发送一轮真实问答（可能较慢）…")
    status, payload, elapsed, err = req(
        "POST",
        f"/api/conversations/{cid}/messages",
        {
            "message": "我记得打羽毛球被夸奖的事",
            "use_vector": True,
            "scheme": default_scheme or "embedding_only",
        },
        timeout=300,
    )
    msg_ok = (
        status == 200
        and isinstance(payload, dict)
        and bool(payload.get("answer"))
        and payload.get("conversation_id") == cid
    )
    ans_preview = ""
    if isinstance(payload, dict):
        ans_preview = _safe(payload.get("answer") or "", 80)
    else:
        ans_preview = _safe(payload, 200)
    check(
        "POST /api/conversations/{id}/messages",
        msg_ok,
        f"status={status} {elapsed:.2f}s answer={ans_preview!r}",
    )
    if not msg_ok:
        print("  detail:", _safe(payload, 400))

    # 9) conversation has messages after send
    status, payload, elapsed, err = req("GET", f"/api/conversations/{cid}", timeout=10)
    n_msgs = len(payload.get("messages") or []) if isinstance(payload, dict) else 0
    check(
        "会话消息已持久化",
        status == 200 and n_msgs >= 2,
        f"status={status} n_messages={n_msgs}",
    )

    print()
    print("=" * 50)
    print(f"PASS={passed}  FAIL={failed}")
    print("=" * 50)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

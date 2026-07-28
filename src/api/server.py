"""FastAPI：为 Web 聊天界面提供 REST API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.context import ContextService
from src.engine.schemes import list_schemes, resolve_default_scheme_id

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"

_service: ContextService | None = None


def get_service() -> ContextService:
    global _service
    if _service is None:
        _service = ContextService()
    return _service


def _format_conversation(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row.get("title") or "新对话",
        "updated_at": row.get("updated_at") or "",
        "message_count": int(row.get("n_messages") or 0),
    }


def _auto_title_from_message(text: str, *, max_len: int = 24) -> str:
    t = " ".join(text.strip().split())
    if not t:
        return "新对话"
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _maybe_set_title_from_first_message(conversation_id: str, query: str) -> None:
    svc = get_service()
    rows = svc.conversation.list_conversations(limit=500)
    row = next((r for r in rows if r["id"] == conversation_id), None)
    if not row:
        return
    title = (row.get("title") or "").strip()
    n_msgs = int(row.get("n_messages") or 0)
    if n_msgs <= 2 and title in ("", "chat", "新对话"):
        svc.conversation.set_title(conversation_id, _auto_title_from_message(query))


class CreateConversationBody(BaseModel):
    title: str = ""


class SendMessageBody(BaseModel):
    message: str = Field(..., min_length=1)
    use_vector: bool = True
    scheme: str | None = None  # weighted_50_50 | union_max | tag_only | embedding_only
    # 召回日期集合（优先）；空/省略=不限制。兼容旧 date_from/date_to 闭区间。
    dates: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]
    scheme: dict[str, Any] | None = None
    dates: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None


class ImportLibraryBody(BaseModel):
    """本机日记根目录导入建库。"""

    root: str = Field(..., min_length=1, description="本机绝对或相对目录路径")
    use_agent: bool = False
    build_vectors: bool = True  # extract+ingest 后再跑 sentences+index


def _normalize_client_dates(values: list[str] | None) -> list[str]:
    from src.engine.date_range import normalize_date_list

    return normalize_date_list(values)


def _normalize_client_date(value: str | None) -> str | None:
    """校验 YYYY-MM-DD；空则不限制。"""
    import re

    s = (value or "").strip()
    if not s:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise HTTPException(
            status_code=400, detail=f"日期格式须为 YYYY-MM-DD，收到: {s}"
        )
    return s


app = FastAPI(title="Diary RAG Chat API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    index_path = WEB_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="web/index.html 不存在")
    return FileResponse(index_path)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/retrieval/schemes")
def retrieval_schemes() -> dict:
    default_id = resolve_default_scheme_id()
    schemes = [s.to_public() for s in list_schemes()]
    # 确保 default 存在
    ids = {s["id"] for s in schemes}
    if default_id not in ids:
        default_id = schemes[0]["id"] if schemes else "weighted_50_50"
    return {"default": default_id, "schemes": schemes}


@app.get("/api/library")
def library_status() -> dict:
    from src.library import get_import_status

    return get_import_status()


@app.post("/api/library/import")
def library_import(body: ImportLibraryBody) -> dict:
    """
    选择本机根目录 → extract → ingest →（可选）rag-sentence + 向量索引。
    浏览器无法直接传 OS 路径，由用户在前端填入本机目录。
    """
    from src.library import run_library_import

    try:
        return run_library_import(
            body.root,
            use_agent=body.use_agent,
            build_vectors=body.build_vectors,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/diary/calendar")
def diary_calendar() -> dict:
    """有日记的日期集合（日历着色用）。"""
    from src.diary_calendar import list_diary_dates

    return list_diary_dates()


@app.get("/api/diary/days/{day}")
def diary_day(day: str) -> dict:
    """某日 chunk 拼合原文。"""
    from src.diary_calendar import get_diary_by_date

    try:
        return get_diary_by_date(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/diary/days/{day}/wordcloud")
def diary_day_wordcloud(day: str) -> dict:
    """某日词云：jieba 词频，不调用 LLM。"""
    from src.day_insights import wordcloud_for_date

    try:
        return wordcloud_for_date(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/diary/months/{year_month}/wordcloud")
def diary_month_wordcloud(year_month: str, refresh: bool = False) -> dict:
    """整月词云：默认读缓存；refresh=true 强制重算。year_month=YYYY-MM。"""
    from src.day_insights import wordcloud_for_month

    m = (year_month or "").strip()
    if len(m) != 7 or m[4] != "-":
        raise HTTPException(status_code=400, detail="月份格式须为 YYYY-MM")
    try:
        year = int(m[:4])
        month = int(m[5:7])
        return wordcloud_for_month(year, month, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/diary/days/{day}/poetic")
def diary_day_poetic(day: str, refresh: bool = False) -> dict:
    """某日总结段落 + 诗句（默认读本地缓存；refresh=true 强制重生成）。"""
    from src.day_insights import poetic_summary_for_date

    try:
        return poetic_summary_for_date(day, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"诗意总结失败: {exc}") from exc


@app.get("/api/conversations")
def list_conversations(limit: int = 50) -> list[dict]:
    rows = get_service().conversation.list_conversations(limit=limit)
    return [_format_conversation(r) for r in rows]


@app.post("/api/conversations", status_code=201)
def create_conversation(body: CreateConversationBody | None = None) -> dict:
    title = (body.title if body else "") or "新对话"
    cid = get_service().conversation.create(title=title)
    return {"id": cid, "title": title}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    svc = get_service()
    try:
        state = svc.conversation.load(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rows = svc.conversation.list_conversations(limit=500)
    row = next((r for r in rows if r["id"] == conversation_id), None)
    title = (row.get("title") if row else None) or "新对话"

    return {
        "id": state.conversation_id,
        "title": title,
        "summary": state.summary,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.timestamp,
            }
            for m in state.messages
        ],
    }


@app.post("/api/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, body: SendMessageBody) -> ChatResponse:
    svc = get_service()
    try:
        svc.conversation.load(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    query = body.message.strip()
    if not query:
        raise HTTPException(status_code=400, detail="消息不能为空")

    date_from = _normalize_client_date(body.date_from)
    date_to = _normalize_client_date(body.date_to)
    dates = _normalize_client_dates(body.dates)

    result = svc.handle_turn(
        query,
        conversation_id=conversation_id,
        use_vector=body.use_vector,
        scheme=body.scheme or None,
        persist=True,
        date_from=date_from,
        date_to=date_to,
        dates=dates or None,
    )
    _maybe_set_title_from_first_message(conversation_id, query)

    state = svc.conversation.load(conversation_id)
    msgs = state.messages
    assistant_msg = msgs[-1] if msgs else None
    user_msg = msgs[-2] if len(msgs) >= 2 else None
    sq = result.get("structured_query") or {}

    return ChatResponse(
        conversation_id=result["conversation_id"],
        answer=result["answer"],
        scheme=result.get("scheme") or None,
        dates=sq.get("dates") or dates or None,
        date_from=sq.get("date_from") or date_from,
        date_to=sq.get("date_to") or date_to,
        user_message={
            "id": user_msg.id if user_msg else "",
            "role": "user",
            "content": query,
            "created_at": user_msg.timestamp if user_msg else "",
        },
        assistant_message={
            "id": assistant_msg.id if assistant_msg else "",
            "role": "assistant",
            "content": result["answer"],
            "created_at": assistant_msg.timestamp if assistant_msg else "",
        },
    )


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run("src.api.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()

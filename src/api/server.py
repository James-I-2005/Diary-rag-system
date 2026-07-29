"""FastAPI：为 Web 聊天界面提供 REST API。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.context import ContextService
from src.engine.schemes import list_schemes, resolve_default_scheme_id

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"

_service: ContextService | None = None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """启动时检查写日记跨日归档 → 入库，使日历可见昨日日记。"""
    try:
        from src.write_diary import ensure_day_rollover

        info = ensure_day_rollover()
        if info.get("rolled"):
            print(
                f"[write_diary] 跨日归档: archived={info.get('archived')} "
                f"ingested={info.get('ingested')}"
            )
    except Exception as exc:
        print(f"[write_diary] 启动归档失败（可忽略）: {exc}")
    yield


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


class WriteManuscriptsBody(BaseModel):
    mode: str | None = None
    items: list[dict[str, Any]] | None = None


class ExportDiaryBody(BaseModel):
    dates: list[str] = Field(..., min_length=1, max_length=366)


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


app = FastAPI(title="Diary RAG Chat API", version="0.2.0", lifespan=_lifespan)

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


@app.post("/api/diary/export")
def export_diary_days(body: ExportDiaryBody) -> StreamingResponse:
    """将选中日期的拼合原文导出为 ZIP；每天一个 YYYY-MM-DD.txt。"""
    from src.diary_calendar import get_diary_by_date

    dates = _normalize_client_dates(body.dates)
    if not dates:
        raise HTTPException(status_code=400, detail="请至少选择一个日期")

    archive = BytesIO()
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED) as zf:
        for day in dates:
            diary = get_diary_by_date(day)
            # UTF-8 BOM 方便 Windows 记事本直接正确识别中文。
            content = "\ufeff" + str(diary.get("text") or "")
            zf.writestr(f"{day}.txt", content.encode("utf-8"))

    archive.seek(0)
    first = dates[0].replace("-", "")
    last = dates[-1].replace("-", "")
    filename = f"diary_{first}.zip" if first == last else f"diary_{first}-{last}.zip"
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Exported-Days": str(len(dates)),
        },
    )


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


@app.get("/api/write/manuscripts")
def write_get_manuscripts() -> dict:
    """读取写日记文稿（必要时先跨日归档入库）。"""
    from src.write_diary import get_manuscripts

    return get_manuscripts()


@app.post("/api/write/manuscripts")
def write_sync_manuscripts(body: WriteManuscriptsBody) -> dict:
    """同步文稿到本地 data/write_diary；跨日则归档并建 chunk。"""
    from src.write_diary import sync_manuscripts

    try:
        return sync_manuscripts(mode=body.mode, items=body.items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/write/rollover")
def write_rollover() -> dict:
    """手动触发跨日检查（归档昨日 → 入库）。"""
    from src.write_diary import ensure_day_rollover

    return ensure_day_rollover()


@app.post("/api/write/archive-now")
def write_archive_now() -> dict:
    """把当前文稿按 active_day 立刻归档入库（不换日、不清空）。"""
    from src.write_diary import force_archive_active_day

    return force_archive_active_day()


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

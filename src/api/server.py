"""FastAPI：为 Web 聊天界面提供 REST API。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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
    try:
        from src.user_tags import ensure_system_folders

        ensure_system_folders()
    except Exception as exc:
        print(f"[user_tags] 初始化系统文件夹失败（可忽略）: {exc}")
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


class RenameConversationBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class SendMessageBody(BaseModel):
    message: str = Field(..., min_length=1)
    use_vector: bool = True
    scheme: str | None = None  # embedding_only 等
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


class CreateUserTagBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    folder_id: str | None = None
    color: str | None = None


class UpdateUserTagBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    color: str | None = None
    folder_id: str | None = None
    clear_folder: bool = False
    sort_order: int | None = None


class CreateTagFolderBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    parent_id: str | None = None


class UpdateTagFolderBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    parent_id: str | None = None
    clear_parent: bool = False
    sort_order: int | None = None


class BindTagBody(BaseModel):
    chunk_ids: list[str] = Field(..., min_length=1)


class UpdatePersonBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)


class UpdatePlaceBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)


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


@app.get("/api/explore/search")
def explore_search(q: str = "", limit: int = 50) -> dict:
    """原文 grep：先完全匹配，再相近匹配。"""
    from src.explore import search_chunks

    return search_chunks(q, limit=limit)


@app.get("/api/explore/entities")
def explore_entities(type: str = "person", limit: int = 200) -> dict:
    from src.explore import list_entities

    try:
        items = list_entities(type, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"entity_type": type, "items": items, "total": len(items)}


@app.get("/api/explore/entities/chunks")
def explore_entity_chunks(name: str, type: str = "person", limit: int = 30) -> dict:
    from src.explore import entity_chunks

    try:
        items = entity_chunks(name, type, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name, "entity_type": type, "items": items, "total": len(items)}


@app.get("/api/explore/tags")
def explore_tags(frequent_limit: int = 12) -> dict:
    """管理页首屏：常用 tag + 根目录树（与 /api/tags 同源）。"""
    from src.user_tags import management_home

    return management_home(frequent_limit=frequent_limit)


@app.get("/api/tags/palette")
def tags_palette() -> dict:
    from src.user_tags import list_preset_colors

    colors = list_preset_colors()
    return {"colors": colors}


@app.get("/api/tags/folders")
def tags_folders_list() -> dict:
    from src.user_tags import list_folders_flat

    items = list_folders_flat()
    return {"items": items, "total": len(items)}


@app.get("/api/tags/tree")
def tags_tree(folder_id: str | None = None) -> dict:
    from src.user_tags import list_tree

    fid = (folder_id or "").strip() or None
    try:
        return list_tree(folder_id=fid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/tags/recent")
def tags_recent(limit: int = 4) -> dict:
    from src.user_tags import list_recent

    items = list_recent(limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/api/tags/frequent")
def tags_frequent(limit: int = 12) -> dict:
    from src.user_tags import list_frequent

    items = list_frequent(limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/api/tags")
def tags_list_all() -> dict:
    """全部用户 tag 列表（提及渲染用）。"""
    from src.user_tags import list_all_tags

    items = list_all_tags()
    return {"items": items, "total": len(items)}


@app.post("/api/tags")
def tags_create(body: CreateUserTagBody) -> dict:
    from src.user_tags import create_tag

    try:
        return create_tag(body.name, folder_id=body.folder_id, color=body.color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/tags/{tag_id}")
def tags_update(tag_id: str, body: UpdateUserTagBody) -> dict:
    from src.user_tags import update_tag

    kwargs: dict[str, Any] = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.color is not None:
        kwargs["color"] = body.color
    if body.clear_folder:
        kwargs["folder_id"] = None
    elif body.folder_id is not None:
        kwargs["folder_id"] = body.folder_id
    if body.sort_order is not None:
        kwargs["sort_order"] = body.sort_order
    try:
        return update_tag(tag_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/tags/{tag_id}")
def tags_delete(tag_id: str) -> dict:
    from src.user_tags import delete_tag

    try:
        return delete_tag(tag_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/tags/folders")
def tags_folders_create(body: CreateTagFolderBody) -> dict:
    from src.user_tags import create_folder

    try:
        return create_folder(body.name, parent_id=body.parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/tags/folders/{folder_id}")
def tags_folders_update(folder_id: str, body: UpdateTagFolderBody) -> dict:
    from src.user_tags import update_folder

    kwargs: dict[str, Any] = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.clear_parent:
        kwargs["parent_id"] = None
    elif body.parent_id is not None:
        kwargs["parent_id"] = body.parent_id
    if body.sort_order is not None:
        kwargs["sort_order"] = body.sort_order
    try:
        return update_folder(folder_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/tags/folders/{folder_id}")
def tags_folders_delete(folder_id: str, move_up: bool = True) -> dict:
    from src.user_tags import delete_folder

    try:
        return delete_folder(folder_id, move_up=move_up)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tags/{tag_id}/bind")
def tags_bind(tag_id: str, body: BindTagBody) -> dict:
    from src.user_tags import bind_chunks

    try:
        return bind_chunks(tag_id, body.chunk_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/tags/{tag_id}/unbind")
def tags_unbind(tag_id: str, body: BindTagBody) -> dict:
    from src.user_tags import unbind_chunks

    try:
        return unbind_chunks(tag_id, body.chunk_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tags/{tag_id}/chunks")
def tags_chunks(tag_id: str, limit: int = 80) -> dict:
    """列出某 tag 绑定的全部 chunk（与人物详情同源）。"""
    from src.user_tags import UserTag

    try:
        return UserTag.get(tag_id).chunks_payload(limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/people")
def people_list() -> dict:
    from src.people import list_people

    items = list_people()
    return {"items": items, "total": len(items)}


@app.post("/api/people")
async def people_create(
    name: str = Form(...),
    photo: UploadFile | None = File(None),
) -> dict:
    """新建人物：同步在「人物」系统文件夹下创建同名 tag；可选上传头像。"""
    from src.people import create_person

    photo_bytes = None
    original_name = ""
    content_type = None
    if photo is not None and (photo.filename or "").strip():
        photo_bytes = await photo.read()
        original_name = photo.filename or ""
        content_type = photo.content_type
    try:
        return create_person(
            name,
            photo_data=photo_bytes,
            original_name=original_name,
            content_type=content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/people/{person_id}")
def people_get(person_id: str) -> dict:
    from src.people import get_person

    try:
        return get_person(person_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/people/{person_id}")
def people_update(person_id: str, body: UpdatePersonBody) -> dict:
    from src.people import update_person

    try:
        return update_person(person_id, name=body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/people/{person_id}")
def people_delete(person_id: str) -> dict:
    from src.people import delete_person

    try:
        return delete_person(person_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/people/{person_id}/photo")
async def people_upload_photo(
    person_id: str,
    file: UploadFile = File(...),
) -> dict:
    from src.people import save_person_photo

    data = await file.read()
    try:
        return save_person_photo(
            person_id,
            data=data,
            original_name=file.filename or "",
            content_type=file.content_type,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/people/{person_id}/photo")
def people_photo_file(person_id: str) -> FileResponse:
    from src.people import resolve_photo_file

    try:
        path, mime = resolve_photo_file(person_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime)


@app.get("/api/people/{person_id}/chunks")
def people_chunks(person_id: str, limit: int = 50) -> dict:
    from src.people import get_person, person_chunks

    try:
        person = get_person(person_id)
        items = person_chunks(person_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "person": person,
        "items": items,
        "total": len(items),
    }


@app.get("/api/places")
def places_list() -> dict:
    from src.places import list_places

    items = list_places()
    return {"items": items, "total": len(items)}


@app.post("/api/places")
async def places_create(
    name: str = Form(...),
    photo: UploadFile | None = File(None),
) -> dict:
    """新建地点：同步在「地点」系统文件夹下创建同名 tag；可选上传图片。"""
    from src.places import create_place

    photo_bytes = None
    original_name = ""
    content_type = None
    if photo is not None and (photo.filename or "").strip():
        photo_bytes = await photo.read()
        original_name = photo.filename or ""
        content_type = photo.content_type
    try:
        return create_place(
            name,
            photo_data=photo_bytes,
            original_name=original_name,
            content_type=content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/places/{place_id}")
def places_get(place_id: str) -> dict:
    from src.places import get_place

    try:
        return get_place(place_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/places/{place_id}")
def places_update(place_id: str, body: UpdatePlaceBody) -> dict:
    from src.places import update_place

    try:
        return update_place(place_id, name=body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/places/{place_id}")
def places_delete(place_id: str) -> dict:
    from src.places import delete_place

    try:
        return delete_place(place_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/places/{place_id}/photo")
async def places_upload_photo(
    place_id: str,
    file: UploadFile = File(...),
) -> dict:
    from src.places import save_place_photo

    data = await file.read()
    try:
        return save_place_photo(
            place_id,
            data=data,
            original_name=file.filename or "",
            content_type=file.content_type,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/places/{place_id}/photo")
def places_photo_file(place_id: str) -> FileResponse:
    from src.places import resolve_photo_file

    try:
        path, mime = resolve_photo_file(place_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime)


@app.get("/api/places/{place_id}/chunks")
def places_chunks(place_id: str, limit: int = 50) -> dict:
    from src.places import get_place, place_chunks

    try:
        place = get_place(place_id)
        items = place_chunks(place_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "place": place,
        "items": items,
        "total": len(items),
    }


@app.get("/api/retrieval/schemes")
def retrieval_schemes() -> dict:
    default_id = resolve_default_scheme_id()
    schemes = [s.to_public() for s in list_schemes()]
    # 确保 default 存在
    ids = {s["id"] for s in schemes}
    if default_id not in ids:
        default_id = schemes[0]["id"] if schemes else "embedding_only"
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


@app.get("/api/diary/days/{day}/images")
def diary_day_images(day: str) -> dict:
    """某日图片列表（元数据；文件另取 /file）。"""
    from src.day_images import list_images

    try:
        items = list_images(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"date": day, "images": items}


@app.post("/api/diary/days/{day}/images")
async def upload_diary_day_image(
    day: str,
    file: UploadFile = File(...),
) -> dict:
    """上传图片到指定日期（文件落盘 + SQLite 元数据）。"""
    from src.day_images import save_image

    data = await file.read()
    try:
        return save_image(
            day,
            data=data,
            original_name=file.filename or "",
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/diary/days/{day}/images/{image_id}/file")
def diary_day_image_file(day: str, image_id: str) -> FileResponse:
    from src.day_images import resolve_image_file

    try:
        path, mime = resolve_image_file(day, image_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=mime)


@app.delete("/api/diary/days/{day}/images/{image_id}")
def delete_diary_day_image(day: str, image_id: str) -> dict:
    from src.day_images import delete_image

    try:
        ok = delete_image(day, image_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="图片不存在")
    return {"ok": True, "id": image_id}


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


@app.get("/api/suggested-questions")
def suggested_questions() -> dict:
    """空状态推荐问题：默认召回时间段内随机抽 chunk，轻量 Agent 各生成一问。"""
    from src.suggested_questions import generate_suggested_questions

    try:
        return generate_suggested_questions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"推荐问题生成失败: {exc}") from exc


class UpdateSettingsBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@app.get("/api/settings")
def get_settings() -> dict:
    """设置页：白名单字段当前值（密钥脱敏）。"""
    from src.settings import get_settings_for_api

    return get_settings_for_api()


@app.put("/api/settings")
def put_settings(body: UpdateSettingsBody) -> dict:
    """设置页：写入 user_settings overlay 与 .env（仅白名单）。"""
    from src.settings import update_settings

    try:
        return update_settings(body.values or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存设置失败: {exc}") from exc


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


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: RenameConversationBody) -> dict:
    """仅更新 title；主键 id 不变，前端与其它引用仍按 id 定位。"""
    svc = get_service()
    title = (body.title or "").strip() or "新对话"
    try:
        svc.conversation.set_title(conversation_id, title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": conversation_id, "title": title}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    svc = get_service()
    ok = svc.conversation.delete(conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"conversation 不存在: {conversation_id}")
    return {"ok": True, "id": conversation_id}


@app.get("/api/conversations/{conversation_id}/export.md")
def export_conversation_markdown(conversation_id: str) -> StreamingResponse:
    """按 id 导出 Markdown；文件名用当前标题，正文内保留 id。"""
    import re

    svc = get_service()
    try:
        state = svc.conversation.load(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rows = svc.conversation.list_conversations(limit=500)
    row = next((r for r in rows if r["id"] == conversation_id), None)
    title = (row.get("title") if row else None) or "新对话"

    lines: list[str] = [
        f"# {title}",
        "",
        f"- conversation_id: `{conversation_id}`",
        "",
    ]
    for m in state.messages:
        role = "你" if m.role == "user" else "助手" if m.role == "assistant" else m.role
        lines.append(f"## {role}")
        lines.append("")
        lines.append((m.content or "").rstrip())
        lines.append("")

    raw = "\n".join(lines).rstrip() + "\n"
    safe = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "chat"
    safe = safe[:40]
    filename = f"{safe}_{conversation_id[:8]}.md"
    # RFC 5987 方便中文文件名
    from urllib.parse import quote

    disposition = (
        f"attachment; filename=\"{conversation_id[:8]}.md\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return StreamingResponse(
        iter([raw.encode("utf-8")]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


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

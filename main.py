"""日记 RAG 命令行入口。"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.markdown import Markdown

from src.answer import generate_answer

console = Console()


def cmd_extract(
    *,
    root: str | None = None,
    use_agent: bool = False,
    manifest: str | None = None,
) -> None:
    """扫盘 →（可选）Agent → regex/mtime → 写 Manifest。"""
    from src.extract.manifest import default_manifest_path
    from src.extract.pipeline import run_extract_pipeline

    mode = "agent+fallback" if use_agent else "regex→mtime（无 Agent）"
    console.print(f"[dim]extract 模式: {mode}[/dim]")
    manifest_obj = run_extract_pipeline(
        root=root,
        use_agent=use_agent,
        manifest_path=manifest,
    )
    out = manifest or str(default_manifest_path())
    stats = manifest_obj.stats or {}
    console.print(
        f"[green]extract 完成[/green] root={manifest_obj.root} "
        f"files={stats.get('files_total', 0)} entries={stats.get('entries_total', 0)} "
        f"by_source={stats.get('by_source', {})}"
    )
    if manifest_obj.errors:
        console.print(f"[yellow]errors={len(manifest_obj.errors)}[/yellow]")
    console.print(f"[dim]manifest → {out}[/dim]")


def cmd_ingest(
    *,
    manifest: str | None = None,
    legacy: bool = False,
    use_agent: bool = False,
) -> None:
    """默认：若已有 manifest 则按 manifest 建库；否则先 extract(无 Agent) 再 ingest。"""
    from pathlib import Path

    from src.extract.manifest import default_manifest_path
    from src.ingest import ingest_all, ingest_from_manifest
    from src.store import resolve_path

    if legacy:
        count = ingest_all(use_extract=False)
        console.print(f"[green]导入完成（legacy）[/green]，共 {count} 个 chunk")
        return

    mpath = Path(manifest) if manifest else default_manifest_path()
    if not mpath.is_absolute():
        mpath = resolve_path(str(mpath))

    if not mpath.is_file():
        console.print("[dim]无 manifest，先跑 extract（无 Agent）…[/dim]")
        cmd_extract(use_agent=use_agent, manifest=str(mpath))

    count = ingest_from_manifest(mpath)
    console.print(f"[green]导入完成[/green]，共 {count} 个 chunk（from manifest）")


def cmd_index() -> None:
    from src.embed import index_sentences

    total = index_sentences()
    console.print(f"[green]sentence 索引完成[/green]，共 {total} 条")


def cmd_sentences(*, force: bool = False) -> None:
    """chunk → rag-sentence → Chroma。默认只处理尚无 sentence 的 chunk；--force 全量按当前 prompt 重跑。"""
    from src.paraphrase.pipeline import run_paraphrase_pipeline

    if force:
        console.print("[yellow]force=True：将按当前 prompt_rag_sentence.md 重跑全部 chunk[/yellow]")
    result = run_paraphrase_pipeline(force=force)
    console.print(f"[green]paraphrase 完成[/green] {result}")


def cmd_tags() -> None:
    from src.extract_tags import extract_all_tags

    extract_all_tags()


def cmd_update() -> None:
    """增量更新：新日记 → paraphrase → 嵌入 → 标签。"""
    from src.embed import index_sentences_for_chunks
    from src.extract_tags import extract_tags_for_ids
    from src.ingest import ingest_incremental
    from src.paraphrase.agent import paraphrase_chunk
    from src.rag_sentences import save_sentences_for_chunk
    from src.store import get_db

    console.print("1/4 导入新日记...")
    new_ids = ingest_incremental()
    if not new_ids:
        console.print("没有新内容")
        return

    console.print(f"2/4 paraphrase {len(new_ids)} 个 chunk...")
    conn = get_db()
    try:
        placeholders = ",".join("?" * len(new_ids))
        rows = conn.execute(
            f"SELECT id, date, text, source_file FROM chunks WHERE id IN ({placeholders})",
            new_ids,
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            result = paraphrase_chunk(row["id"], row["text"], date=row["date"] or "")
            if result.sentences:
                save_sentences_for_chunk(
                    row["id"],
                    result.sentences,
                    date=row["date"] or "",
                    source_file=row["source_file"] or "",
                )
                console.print(f"  [ok] {row['id']}: {len(result.sentences)}")
        except Exception as exc:
            console.print(f"  [fail] {row['id']}: {exc}")

    console.print("3/4 嵌入 sentences...")
    index_sentences_for_chunks(new_ids)

    console.print("4/4 提取标签...")
    extract_tags_for_ids(new_ids)

    console.print("[green]更新完成[/green]")


def cmd_test() -> None:
    from tests.run_tests import run_all

    run_all()


def cmd_chat() -> None:
    from src.answer import generate_answer, reset_conversation, _service

    svc = _service()
    cid = reset_conversation()
    console.print("[bold]个人日记 RAG[/bold] — 多会话 Context 对话")
    console.print(f"[dim]当前 conversation={cid}[/dim]")
    console.print(
        "[dim]/new 新会话 | /list 列表 | /use <id> 切换 | /title <名> 改标题 | 空行退出[/dim]\n"
    )
    while True:
        try:
            question = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break

        if question == "/new":
            cid = reset_conversation()
            console.print(f"[green]已新建会话[/green] {cid}\n")
            continue
        if question == "/list":
            rows = svc.conversation.list_conversations()
            if not rows:
                console.print("[dim]暂无会话[/dim]\n")
                continue
            for r in rows:
                mark = "*" if r["id"] == cid else " "
                title = (r.get("title") or "chat")[:20]
                console.print(
                    f"{mark} {r['id'][:8]}…  msgs={r.get('n_messages', 0)}  {title}  {r.get('updated_at', '')}"
                )
            console.print()
            continue
        if question.startswith("/use "):
            target = question[5:].strip()
            rows = svc.conversation.list_conversations(limit=200)
            matched = next(
                (
                    r["id"]
                    for r in rows
                    if r["id"] == target or r["id"].startswith(target)
                ),
                None,
            )
            if not matched:
                console.print("[red]未找到该会话[/red]\n")
                continue
            cid = matched
            console.print(f"[green]已切换到[/green] {cid}\n")
            continue
        if question.startswith("/title "):
            title = question[7:].strip()
            svc.conversation.set_title(cid, title)
            console.print(f"[green]标题已更新[/green] → {title}\n")
            continue

        console.print("\n[dim]思考中...[/dim]")
        answer = generate_answer(question, conversation_id=cid, use_vector=True)
        console.print(Markdown(f"\n{answer}\n"))


def cmd_web(host: str = "127.0.0.1", port: int = 8765) -> None:
    from src.api.server import run

    console.print(f"[bold]Web UI[/bold] → http://{host}:{port}")
    console.print("[dim]Ctrl+C 停止服务[/dim]")
    run(host=host, port=port)


def main() -> None:
    parser = argparse.ArgumentParser(description="个人日记 RAG")
    parser.add_argument(
        "command",
        nargs="?",
        default="chat",
        help="extract | ingest | index | tags | sentences | update | test | chat | web | 或直接提问",
    )
    parser.add_argument("--host", default="127.0.0.1", help="web 服务监听地址")
    parser.add_argument("--port", type=int, default=8765, help="web 服务端口")
    parser.add_argument(
        "--force",
        action="store_true",
        help="sentences：强制按当前 prompt 重跑已有 chunk（忽略已有 sentence 缓存）",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="extract：日记根目录（默认 config/DIARY_DIR）",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="extract/ingest：manifest 路径（默认 data/extract_manifest.json）",
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help="extract：启用 Extract Agent 定日期后再 regex/mtime 兜底",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="ingest：旧逻辑（仅顶层 *.md + 正文标题，不经 manifest）",
    )
    # 支持：python main.py 吃了几次火锅（多词问题）
    parser.add_argument("rest", nargs="*", help=argparse.SUPPRESS)
    args = parser.parse_args()

    commands = {
        "extract": lambda: cmd_extract(
            root=args.root, use_agent=args.agent, manifest=args.manifest
        ),
        "ingest": lambda: cmd_ingest(
            manifest=args.manifest, legacy=args.legacy, use_agent=args.agent
        ),
        "index": cmd_index,
        "tags": cmd_tags,
        "sentences": lambda: cmd_sentences(force=args.force),
        "update": cmd_update,
        "test": cmd_test,
        "chat": cmd_chat,
        "web": lambda: cmd_web(args.host, args.port),
    }

    if args.command in commands and not args.rest:
        commands[args.command]()
        return

    # 当作问题：command + rest
    question = " ".join([args.command, *args.rest]).strip()
    if not question or question == "chat":
        cmd_chat()
        return

    answer = generate_answer(question)
    console.print(Markdown(answer))

if __name__ == "__main__":
    main()

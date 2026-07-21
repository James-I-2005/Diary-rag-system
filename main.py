"""日记 RAG 命令行入口。"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.markdown import Markdown

from src.answer import generate_answer

console = Console()


def cmd_ingest() -> None:
    from src.ingest import ingest_all

    count = ingest_all()
    console.print(f"[green]导入完成[/green]，共 {count} 个 chunk")


def cmd_index() -> None:
    from src.embed import index_all_chunks

    index_all_chunks()


def cmd_tags() -> None:
    from src.extract_tags import extract_all_tags

    extract_all_tags()


def cmd_update() -> None:
    """增量更新：新日记 → 嵌入 → 标签。"""
    from src.embed import index_new_chunks
    from src.extract_tags import extract_tags_for_ids
    from src.ingest import ingest_incremental

    console.print("1/3 导入新日记...")
    new_ids = ingest_incremental()
    if not new_ids:
        console.print("没有新内容")
        return

    console.print(f"2/3 嵌入 {len(new_ids)} 个新 chunk...")
    index_new_chunks(new_ids)

    console.print("3/3 提取标签...")
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
        help="ingest | index | tags | update | test | chat | web | 或直接提问",
    )
    parser.add_argument("--host", default="127.0.0.1", help="web 服务监听地址")
    parser.add_argument("--port", type=int, default=8765, help="web 服务端口")
    # 支持：python main.py 吃了几次火锅（多词问题）
    parser.add_argument("rest", nargs="*", help=argparse.SUPPRESS)
    args = parser.parse_args()

    commands = {
        "ingest": cmd_ingest,
        "index": cmd_index,
        "tags": cmd_tags,
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

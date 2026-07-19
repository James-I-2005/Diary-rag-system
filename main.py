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
    from src.answer import generate_answer, reset_conversation

    cid = reset_conversation()
    console.print("[bold]个人日记 RAG[/bold] — Context Engine 多轮对话")
    console.print(f"[dim]conversation_id={cid}（召回记忆不写入聊天历史）[/dim]\n")
    while True:
        try:
            question = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        console.print("\n[dim]思考中...[/dim]")
        answer = generate_answer(question, conversation_id=cid, use_vector=True)
        console.print(Markdown(f"\n{answer}\n"))


def main() -> None:
    parser = argparse.ArgumentParser(description="个人日记 RAG")
    parser.add_argument(
        "command",
        nargs="?",
        default="chat",
        help="ingest | index | tags | update | test | chat | 或直接提问",
    )
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

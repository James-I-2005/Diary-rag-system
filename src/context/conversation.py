"""Conversation Manager：只存对话消息与 summary，不存 Retrieved Memory。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.context.models import ConversationState, Message
from src.store import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_conversation_tables(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            summary_upto INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS conversation_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_conv_msg_cid
            ON conversation_messages(conversation_id, created_at);

        CREATE TABLE IF NOT EXISTS retrieval_traces (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            user_message_id TEXT,
            query TEXT,
            plan_json TEXT,
            candidates_json TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()
    # 兼容旧库
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    if "summary_upto" not in cols:
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN summary_upto INTEGER DEFAULT 0"
        )
        conn.commit()


class ConversationManager:
    """管理 Conversation State（Messages + Summary）。"""

    def create(self, title: str = "") -> str:
        cid = str(uuid.uuid4())
        now = _now()
        conn = get_db()
        try:
            _ensure_conversation_tables(conn)
            conn.execute(
                """
                INSERT INTO conversations
                (id, title, summary, summary_upto, created_at, updated_at)
                VALUES (?, ?, '', 0, ?, ?)
                """,
                (cid, title or "chat", now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return cid

    def list_conversations(self, *, limit: int = 50) -> list[dict]:
        conn = get_db()
        try:
            _ensure_conversation_tables(conn)
            rows = conn.execute(
                """
                SELECT id, title, summary, summary_upto, created_at, updated_at,
                       (SELECT COUNT(*) FROM conversation_messages m
                        WHERE m.conversation_id = c.id) AS n_messages
                FROM conversations c
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def set_title(self, conversation_id: str, title: str) -> None:
        conn = get_db()
        try:
            _ensure_conversation_tables(conn)
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip() or "chat", _now(), conversation_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_or_create(self, conversation_id: str | None = None, title: str = "") -> str:
        if conversation_id:
            conn = get_db()
            try:
                _ensure_conversation_tables(conn)
                row = conn.execute(
                    "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
                ).fetchone()
                if row:
                    return conversation_id
            finally:
                conn.close()
        return self.create(title=title)

    def load(self, conversation_id: str) -> ConversationState:
        conn = get_db()
        try:
            _ensure_conversation_tables(conn)
            row = conn.execute(
                "SELECT id, summary, summary_upto FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"conversation 不存在: {conversation_id}")
            msgs = conn.execute(
                """
                SELECT id, role, content, created_at
                FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (conversation_id,),
            ).fetchall()
            state = ConversationState(
                conversation_id=row["id"],
                summary=row["summary"] or "",
                summary_upto=int(row["summary_upto"] or 0),
                messages=[
                    Message(
                        id=m["id"],
                        role=m["role"],  # type: ignore[arg-type]
                        content=m["content"],
                        timestamp=m["created_at"] or "",
                    )
                    for m in msgs
                ],
            )
            return state
        finally:
            conn.close()

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
    ) -> str:
        mid = str(uuid.uuid4())
        now = _now()
        conn = get_db()
        try:
            _ensure_conversation_tables(conn)
            conn.execute(
                """
                INSERT INTO conversation_messages
                (id, conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (mid, conversation_id, role, content, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            conn.commit()
        finally:
            conn.close()
        return mid

    def update_summary(
        self,
        conversation_id: str,
        summary: str,
        *,
        summary_upto: int | None = None,
    ) -> None:
        conn = get_db()
        try:
            _ensure_conversation_tables(conn)
            if summary_upto is None:
                conn.execute(
                    "UPDATE conversations SET summary = ?, updated_at = ? WHERE id = ?",
                    (summary, _now(), conversation_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE conversations
                    SET summary = ?, summary_upto = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (summary, int(summary_upto), _now(), conversation_id),
                )
            conn.commit()
        finally:
            conn.close()

    def save_retrieval_trace(
        self,
        conversation_id: str,
        *,
        user_message_id: str | None,
        query: str,
        plan: list[str],
        candidates: list[dict],
    ) -> str:
        tid = str(uuid.uuid4())
        conn = get_db()
        try:
            _ensure_conversation_tables(conn)
            conn.execute(
                """
                INSERT INTO retrieval_traces
                (id, conversation_id, user_message_id, query, plan_json, candidates_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    conversation_id,
                    user_message_id,
                    query,
                    json.dumps(plan, ensure_ascii=False),
                    json.dumps(candidates, ensure_ascii=False),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return tid

    @staticmethod
    def window_message_count(max_turns: int) -> int:
        """一轮 ≈ user+assistant，窗口消息条数 = 2 * turns。"""
        return max(0, int(max_turns) * 2)

    def split_session_window(
        self,
        state: ConversationState,
        *,
        max_turns: int = 10,
    ) -> tuple[list[Message], list[Message]]:
        """
        按滑动窗口切分会话记忆：
        - overflow：超出窗口、应进入摘要的较早消息
        - recent：窗口内原文（短期记忆）
        """
        n = self.window_message_count(max_turns)
        msgs = list(state.messages)
        if n <= 0 or len(msgs) <= n:
            return [], msgs
        return msgs[:-n], msgs[-n:]

    def recent_messages(
        self,
        state: ConversationState,
        *,
        max_turns: int = 10,
    ) -> list[Message]:
        """最近 max_turns 轮原文（滑动窗口内短期记忆）。"""
        _, recent = self.split_session_window(state, max_turns=max_turns)
        return recent

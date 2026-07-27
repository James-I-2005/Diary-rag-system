"""项目数据通路 Mock 连通性测试。

覆盖：
  Offline: chunk 入库 → paraphrase → rag_sentences → (假)Chroma 索引 → tags
  Online : QueryAgent → Embedding/Tag Operator → hydrate(按 chunk 聚合)
           → Context(窗口+摘要+本轮/历史召回) → LLM → retrieval_traces 回灌

不依赖真实 OpenAI / Chroma 持久化 / SentenceTransformer 权重下载。
运行：
  .venv\\Scripts\\python.exe -m tests.test_mock_pathways
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# 保证从 My_rag 根目录可 import src.*
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCollection:
    """内存 Chroma collection：支持 upsert / query / delete / count。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def upsert(self, ids, embeddings, documents, metadatas):
        for i, sid in enumerate(ids):
            self.rows[sid] = {
                "id": sid,
                "embedding": list(embeddings[i]),
                "document": documents[i],
                "metadata": metadatas[i] or {},
            }

    def delete(self, ids=None):
        for sid in ids or []:
            self.rows.pop(sid, None)

    def count(self) -> int:
        return len(self.rows)

    def query(self, query_embeddings=None, n_results=5, include=None, where=None):
        items = list(self.rows.values())
        if where:
            # 极简过滤：支持 date $gte/$lte
            and_list = where.get("$and") or []
            for cond in and_list:
                if "date" in cond and "$gte" in cond["date"]:
                    items = [
                        r
                        for r in items
                        if (r["metadata"].get("date") or "") >= cond["date"]["$gte"]
                    ]
                if "date" in cond and "$lte" in cond["date"]:
                    items = [
                        r
                        for r in items
                        if (r["metadata"].get("date") or "") <= cond["date"]["$lte"]
                    ]
        items = items[: max(1, int(n_results))]
        if not items:
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }
        return {
            "ids": [[r["id"] for r in items]],
            "documents": [[r["document"] for r in items]],
            "metadatas": [[r["metadata"] for r in items]],
            "distances": [[0.1 + 0.05 * i for i in range(len(items))]],
        }


class FakeQueryAgent:
    def __init__(self, plan: list[str] | None = None):
        self.plan = plan or ["embedding"]
        self.calls: list[str] = []

    def process(self, query: str, state=None):
        from src.query_agent.models import StructuredQuery

        self.calls.append(query)
        return StructuredQuery(
            original_query=query,
            rewritten_query=query.strip(),
            query_sentences=[query.strip()],
            need_retrieval=True,
            retrieval_plan=list(self.plan),
            embedding_query=query.strip(),
            source="mock",
        )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class PathwayHarness:
    """临时 SQLite + 假向量库 + 统一 patch load_config。"""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="myrag_mock_")
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "test.db"
        self.collection = FakeCollection()
        self._cm = None
        self._real_load_config = None

    def __enter__(self):
        from src.store import load_config as real_load_config

        self._real_load_config = real_load_config
        base = copy.deepcopy(real_load_config())
        base["data"]["db_path"] = str(self.db_path)
        base["data"]["chroma_dir"] = str(self.root / "chroma")
        base.setdefault("context", {})
        base["context"]["save_debug_json"] = False
        base["context"]["include_prior_retrievals"] = True
        base["context"]["session_window_turns"] = 10
        base["context"]["memory_max_items"] = 20
        base.setdefault("query_agent", {})
        base["query_agent"]["save_debug_json"] = False
        base.setdefault("retrieval", {})
        base["retrieval"]["top_k"] = 5
        base.setdefault("extract", {})
        base["extract"]["root"] = ""
        base["extract"]["manifest_path"] = str(self.root / "extract_manifest.json")
        base["extract"]["extensions"] = [".md", ".txt"]
        base["diary"] = dict(base.get("diary") or {})
        base["diary"]["date_pattern"] = r"^# (\d{4}-\d{2}-\d{2})"

        def fake_cfg():
            return copy.deepcopy(base)

        self.cfg = base
        targets = [
            "src.store.load_config",
            "src.embed.load_config",
            "src.context.engine.load_config",
            "src.context.service.load_config",
            "src.engine.schemes.load_config",
            "src.tag_retrieve.load_config",
            "src.query_agent.agent.load_config",
            "src.extract.pipeline.load_config",
            "src.ingest.load_config",
        ]
        self._patches = [patch(t, side_effect=fake_cfg) for t in targets]
        self._patches += [
            patch("src.embed.embed_texts", side_effect=self._fake_embed),
            patch("src.embed.get_sentences_collection", return_value=self.collection),
            patch("src.embed.get_chroma_collection", return_value=self.collection),
            patch(
                "src.paraphrase.agent.paraphrase_chunk",
                side_effect=self._fake_paraphrase,
            ),
            patch(
                "src.paraphrase.pipeline.paraphrase_chunk",
                side_effect=self._fake_paraphrase,
            ),
            patch(
                "src.extract_tags.extract_tags_for_chunk",
                side_effect=self._fake_tags,
            ),
        ]
        for p in self._patches:
            p.start()

        # 初始化 DB 表
        from src.store import get_db

        conn = get_db()
        conn.close()
        return self

    def __exit__(self, *exc):
        for p in reversed(getattr(self, "_patches", [])):
            p.stop()
        self.tmp.cleanup()

    @staticmethod
    def _fake_embed(texts):
        out = []
        for t in texts:
            # 稳定伪向量：长度哈希进前几维
            v = [0.01] * 8
            v[0] = (sum(ord(c) for c in t) % 100) / 100.0
            out.append(v)
        return out

    @staticmethod
    def _fake_paraphrase(chunk_id: str, text: str, date: str = ""):
        from src.paraphrase.models import ParaphraseResult

        # 拆成 2 条伪 rag-sentence
        s1 = f"用户在{date or '某日'}提到：{text[:40]}"
        s2 = f"该段日记还包含后续内容：{text[40:80] or text[:20]}"
        return ParaphraseResult(chunk_id=chunk_id, sentences=[s1, s2], raw="mock")

    @staticmethod
    def _fake_tags(text: str, date: str) -> dict:
        return {
            "topics": ["运动"],
            "activities": ["打羽毛球"],
            "emotions": ["高兴"],
            "food_mentions": [],
            "people": ["卷发小胖墩"],
            "is_touching_moment": False,
            "touching_summary": "",
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMockPathways(unittest.TestCase):
    def test_01_offline_write_path(self):
        """chunk → paraphrase → rag_sentences → chroma upsert → tags。"""
        with PathwayHarness() as h:
            from src.extract_tags import save_tags
            from src.ingest import Chunk
            from src.paraphrase.pipeline import run_paraphrase_pipeline
            from src.rag_sentences import count_sentences, fetch_sentences
            from src.store import get_db, save_chunks

            chunk = Chunk(
                id="c_badminton_01",
                date="2024-06-01",
                text=(
                    "和高手打杀球是没有用的，人家一接再挑个后场就跟不上了。"
                    "不如在网前多和他打，吊球滑板还有轻杀可以起到很好的效果。"
                ),
                chunk_index=0,
                source_file="mock_diary.md",
                word_count=80,
            )
            conn = get_db()
            try:
                save_chunks([chunk], conn)
            finally:
                conn.close()

            stats = run_paraphrase_pipeline(chunk_id=chunk.id, force=True)
            self.assertGreaterEqual(stats.get("ok", 0), 1, msg=stats)
            self.assertEqual(count_sentences(), 2)
            self.assertEqual(h.collection.count(), 2)

            sents = fetch_sentences()
            self.assertTrue(all(s.chunk_id == chunk.id for s in sents))

            # tags 通路
            tags = h._fake_tags(chunk.text, chunk.date)
            conn = get_db()
            try:
                save_tags(chunk.id, tags, conn)
                conn.commit()
                row = conn.execute(
                    "SELECT topics, activities FROM chunk_tags WHERE chunk_id=?",
                    (chunk.id,),
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(row)
            self.assertIn("运动", json.loads(row["topics"]))

            print("[PASS] offline: chunks → sentences → chroma → tags")

    def test_02_hydrate_chunk_aggregation(self):
        """同 chunk 多 sentence 命中 → 聚合为 1 条 chunk，带 matched_sentences。"""
        with PathwayHarness() as h:
            from src.engine.candidate import Candidate
            from src.ingest import Chunk
            from src.paraphrase.pipeline import run_paraphrase_pipeline
            from src.query import hydrate_candidates
            from src.store import get_db, save_chunks

            chunk = Chunk(
                id="c_agg_01",
                date="2024-06-02",
                text="全文A。全文B。全文C。",
                chunk_index=0,
                source_file="agg.md",
                word_count=20,
            )
            conn = get_db()
            save_chunks([chunk], conn)
            conn.close()
            run_paraphrase_pipeline(chunk_id=chunk.id, force=True)

            cands = [
                Candidate(unit_id=f"{chunk.id}_s0", score=0.9, source="embedding"),
                Candidate(unit_id=f"{chunk.id}_s1", score=0.7, source="tag"),
            ]
            out = hydrate_candidates(cands, top_k=5)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["chunk_id"], chunk.id)
            self.assertEqual(out[0]["text"], chunk.text)
            self.assertEqual(len(out[0]["matched_sentences"]), 2)
            self.assertAlmostEqual(out[0]["score"], 0.9)
            print("[PASS] hydrate: sentence match → chunk aggregate")

    def test_03_online_turn_and_prior_reuse(self):
        """handle_turn 全链路 + 第二轮回灌窗口内曾召回 chunk。"""
        with PathwayHarness() as h:
            from src.context.service import ContextService
            from src.ingest import Chunk
            from src.paraphrase.pipeline import run_paraphrase_pipeline
            from src.store import get_db, save_chunks

            chunk = Chunk(
                id="c_online_01",
                date="2024-06-03",
                text="昨天打羽毛球被夸奖反应快，满头大汗但很高兴。",
                chunk_index=0,
                source_file="online.md",
                word_count=30,
            )
            conn = get_db()
            save_chunks([chunk], conn)
            conn.close()
            run_paraphrase_pipeline(chunk_id=chunk.id, force=True)
            self.assertGreater(h.collection.count(), 0)

            qa = FakeQueryAgent(plan=["embedding"])
            svc = ContextService(query_agent=qa)

            with patch.object(
                ContextService,
                "_call_llm",
                return_value="（mock）记得你打球很开心。",
            ):
                r1 = svc.handle_turn(
                    "我上次打球怎么样",
                    scheme="embedding_only",
                    persist=True,
                )

            self.assertTrue(r1["conversation_id"])
            self.assertIn("mock", r1["answer"])
            self.assertGreaterEqual(len(r1["memories_used"]), 1)
            self.assertEqual(r1["memories_used"][0]["recall_origin"], "current")
            self.assertEqual(r1["retrieval"]["type"], "retrieval")
            self.assertGreater(r1["retrieval"]["count"], 0)

            # 确认 trace 已落库
            from src.context.conversation import ConversationManager

            cm = ConversationManager()
            traces = cm.list_recent_retrieval_traces(r1["conversation_id"], limit=10)
            self.assertEqual(len(traces), 1)
            self.assertTrue(traces[0]["candidates"])
            self.assertTrue(traces[0]["candidates"][0].get("text"))

            # 第二轮：强制空检索，仍应带回 prior chunk
            empty_qa = FakeQueryAgent(plan=["embedding"])

            def _empty_retrieve(*args, **kwargs):
                return [], [], {}

            svc2 = ContextService(query_agent=empty_qa)
            with patch.object(ContextService, "_retrieve", side_effect=_empty_retrieve), patch.object(
                ContextService, "_call_llm", return_value="（mock）继续聊。"
            ):
                # need_retrieval True 但 _retrieve 返回空 → 只靠 prior
                r2 = svc2.handle_turn(
                    "再说细一点",
                    conversation_id=r1["conversation_id"],
                    scheme="embedding_only",
                    persist=True,
                )

            origins = {m["chunk_id"]: m["recall_origin"] for m in r2["memories_used"]}
            self.assertIn(chunk.id, origins)
            self.assertEqual(origins[chunk.id], "prior")

            # debug messages 含曾召回标记
            from src.context.engine import ContextEngine
            from src.context.models import ConversationState

            eng = ContextEngine()
            state = cm.load(r1["conversation_id"])
            built = eng.build_context(
                query="probe",
                state=state,
                memories=[],  # 本轮无新召回
            )
            mem_sys = [
                m["content"]
                for m in built.messages
                if m["role"] == "system" and "相关日记记忆" in m["content"]
            ]
            self.assertTrue(mem_sys)
            self.assertIn("窗口内曾召回", mem_sys[0])
            print("[PASS] online: retrieve → context → llm → prior reuse")

    def test_04_tag_operator_expands_to_sentences(self):
        """Tag 命中 chunk 后展开为 sentences，再 hydrate 回 chunk。"""
        with PathwayHarness() as h:
            from src.engine.operators.tag import TagOperator
            from src.ingest import Chunk
            from src.paraphrase.pipeline import run_paraphrase_pipeline
            from src.query import hydrate_candidates
            from src.store import get_db, save_chunks

            chunk = Chunk(
                id="c_tag_01",
                date="2024-06-04",
                text="和朋友吃火锅，很开心。",
                chunk_index=0,
                source_file="tag.md",
                word_count=15,
            )
            conn = get_db()
            save_chunks([chunk], conn)
            conn.close()
            run_paraphrase_pipeline(chunk_id=chunk.id, force=True)

            fake_hits = [
                {"id": chunk.id, "tag_score": 0.88, "score": 0.88},
            ]
            with patch(
                "src.engine.operators.tag.tag_match", return_value=fake_hits
            ):
                op = TagOperator(top_k=10)
                cands = op.execute("火锅", [])

            self.assertGreaterEqual(len(cands), 2)  # 2 sentences
            self.assertTrue(all(c.source == "tag" for c in cands))
            self.assertTrue(
                all(c.meta.get("parent_chunk_id") == chunk.id for c in cands)
            )

            hydrated = hydrate_candidates(cands, top_k=5)
            self.assertEqual(len(hydrated), 1)
            self.assertEqual(hydrated[0]["chunk_id"], chunk.id)
            print("[PASS] tag: chunk hit → sentence expand → chunk hydrate")

    def test_05_embedding_operator_uses_sentence_ann(self):
        """EmbeddingOperator 走 search_similar（假 ANN）返回 sentence id。"""
        with PathwayHarness() as h:
            from src.engine.operators.embedding import EmbeddingOperator
            from src.ingest import Chunk
            from src.paraphrase.pipeline import run_paraphrase_pipeline
            from src.store import get_db, save_chunks

            chunk = Chunk(
                id="c_emb_01",
                date="2024-06-05",
                text="今天学习 RAG，把句子做成检索基元。",
                chunk_index=0,
                source_file="emb.md",
                word_count=20,
            )
            conn = get_db()
            save_chunks([chunk], conn)
            conn.close()
            run_paraphrase_pipeline(chunk_id=chunk.id, force=True)

            op = EmbeddingOperator(top_k=5)
            cands = op.execute("检索基元", [])
            self.assertGreater(len(cands), 0)
            self.assertTrue(cands[0].unit_id.startswith(chunk.id))
            self.assertEqual(cands[0].meta.get("parent_chunk_id"), chunk.id)
            print("[PASS] embedding: fake ANN → sentence candidates")

    def test_06_extract_regex_mtime_then_ingest(self):
        """无 Agent：扫盘 → 正文正则 / mtime → Manifest → ingest_from_manifest。"""
        with PathwayHarness() as h:
            from src.extract.manifest import load_manifest
            from src.extract.pipeline import run_extract_pipeline
            from src.ingest import ingest_from_manifest
            from src.store import get_db

            diary_root = h.root / "diary_src"
            sub = diary_root / "nested"
            sub.mkdir(parents=True)

            # 有标准日期标题 → content_regex（即使文件名也有日期，正文覆盖）
            (diary_root / "2024-07-01_dated.md").write_text(
                "# 2024-07-01\n\n今天吃了火锅。\n\n"
                "# 2024-07-02\n\n去打球了。\n",
                encoding="utf-8",
            )
            # 文件名含日期、正文无标题 → path
            (diary_root / "2024-07-03_note.md").write_text(
                "文件名带来的日期。\n",
                encoding="utf-8",
            )
            # 正文内嵌点号日期（无 #）→ 应拆成两条 content_regex
            (diary_root / "tech_split.md").write_text(
                "2024.7.3\n第一段内容。\n2024.7.5\n第二段内容。\n",
                encoding="utf-8",
            )
            # 目录组合 YYYY/MM/DD
            ymd = diary_root / "2024" / "07"
            ymd.mkdir(parents=True)
            (ymd / "04.md").write_text("目录拼出的日期。\n", encoding="utf-8")
            # 无日期标题 → mtime
            bare = sub / "scrap.txt"
            bare.write_text("没有标题的一段随记。", encoding="utf-8")

            h.cfg["extract"]["root"] = str(diary_root)
            h.cfg["data"]["diary_dir"] = str(diary_root)

            with patch(
                "src.extract.pipeline.resolve_diary_dir",
                return_value=diary_root,
            ), patch(
                "src.store.resolve_diary_dir",
                return_value=diary_root,
            ):
                manifest = run_extract_pipeline(
                    root=diary_root,
                    use_agent=False,
                    manifest_path=h.cfg["extract"]["manifest_path"],
                )

            by_source = (manifest.stats or {}).get("by_source") or {}
            self.assertEqual(manifest.stats.get("files_total"), 5)
            # dated.md 2 条 + tech_split.md 2 条
            self.assertEqual(by_source.get("content_regex"), 4)
            self.assertEqual(by_source.get("path"), 2)
            self.assertEqual(by_source.get("mtime"), 1)

            split_e = [
                e for e in manifest.entries if e.path == "tech_split.md"
            ]
            self.assertEqual(len(split_e), 2)
            self.assertEqual({e.date for e in split_e}, {"2024-07-03", "2024-07-05"})
            self.assertTrue(all(e.date_source == "content_regex" for e in split_e))
            self.assertEqual(len(manifest.entries), 7)

            # 被覆盖：files note 应含 overridden_by
            dated_rec = next(f for f in manifest.files if f.path == "2024-07-01_dated.md")
            self.assertIn("overridden_by=content_regex", dated_rec.note)

            path_entries = {e.path: e for e in manifest.entries if e.date_source == "path"}
            self.assertEqual(path_entries["2024-07-03_note.md"].date, "2024-07-03")
            self.assertEqual(path_entries["2024/07/04.md"].date, "2024-07-04")
            mtime_entries = [e for e in manifest.entries if e.date_source == "mtime"]
            self.assertEqual(len(mtime_entries), 1)
            self.assertEqual(mtime_entries[0].path, "nested/scrap.txt")

            n = ingest_from_manifest(h.cfg["extract"]["manifest_path"])
            self.assertGreater(n, 0)
            print("[PASS] extract: 目录→正文(可覆盖)→mtime")

    def test_07_extract_agent_mock_then_fallback(self):
        """Agent 只处理「目录+正文」都未解决的文件。"""
        with PathwayHarness() as h:
            from src.extract import agent as agent_mod
            from src.extract.pipeline import run_extract_pipeline

            diary_root = h.root / "diary_agent"
            diary_root.mkdir()
            (diary_root / "from_agent.md").write_text(
                "整文件无标题，靠 Agent 给日期。",
                encoding="utf-8",
            )
            (diary_root / "need_regex.md").write_text(
                "# 2024-08-10\n\n正文正则在 Agent 之前解决。\n",
                encoding="utf-8",
            )
            (diary_root / "2024-08-20_named.md").write_text(
                "文件名已有日期。\n",
                encoding="utf-8",
            )

            class FakeExtractAgent:
                def resolve_dates(self, nodes):
                    seen = {n.path for n in nodes}
                    # 目录已解决、正文已解决的都不应进 Agent
                    assert "2024-08-20_named.md" not in seen
                    assert "need_regex.md" not in seen
                    assert seen == {"from_agent.md"}
                    return ({"from_agent.md": "2024-08-01"}, [])

            with patch.object(agent_mod, "ExtractAgent", FakeExtractAgent):
                manifest = run_extract_pipeline(
                    root=diary_root,
                    use_agent=True,
                    manifest_path=h.cfg["extract"]["manifest_path"],
                )

            by_source = (manifest.stats or {}).get("by_source") or {}
            self.assertEqual(by_source.get("agent"), 1)
            self.assertEqual(by_source.get("content_regex"), 1)
            self.assertEqual(by_source.get("path"), 1)
            self.assertEqual(manifest.agent_unresolved, [])

            agent_e = next(e for e in manifest.entries if e.path == "from_agent.md")
            self.assertEqual(agent_e.date_source, "agent")
            regex_e = next(e for e in manifest.entries if e.path == "need_regex.md")
            self.assertEqual(regex_e.date_source, "content_regex")
            path_e = next(e for e in manifest.entries if e.path == "2024-08-20_named.md")
            self.assertEqual(path_e.date_source, "path")
            print("[PASS] extract: 目录→正文→agent→mtime")


def run() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestMockPathways)
    # 按编号顺序
    suite = unittest.TestSuite(
        sorted(suite, key=lambda t: t.id())
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print()
    if result.wasSuccessful():
        print("=" * 60)
        print("全部通路 Mock 测试通过")
        print("=" * 60)
        return 0
    print("=" * 60)
    print(f"失败: {len(result.failures)}  错误: {len(result.errors)}")
    for t, tb in result.failures + result.errors:
        print("-" * 40)
        print(t)
        print(tb)
    print("=" * 60)
    return 1


if __name__ == "__main__":
    raise SystemExit(run())

# 个人日记 RAG

用本地日记构建可检索、可统计、可归纳的分析系统。日记与向量数据默认留在本机（Ollama + 本地嵌入）。

更细的命令速查见 [tool/tool.md](tool/tool.md)；分章原理见 [textbook/README.md](textbook/README.md)。

---

## 你将跑通什么

```
日记 .md  →  切块入库 (SQLite)
         →  向量索引 (Chroma)
         →  LLM 打标签
         →  提问 / 测试集验收
```

样例数据已自带：`data/diary/sample.md`（几篇 2025-06 的短日记）。

---

## 环境要求

| 组件 | 要求 | 用途 |
|------|------|------|
| Python | **3.11+** | 运行时 |
| 磁盘 / 内存 | 建议 ≥ 8GB 内存 | 嵌入模型 + 本地 LLM |
| Ollama | 最新版 | 标签提取与回答生成（默认） |
| 网络 | 首次需联网 | 下载 `bge-small-zh` 等模型 |

---

## 一、进入项目目录

所有后续命令都在 **`My_rag/`** 下执行（本 README 所在目录）：

```powershell
cd path\to\Personal_Rag\My_rag
```

---

## 二、配置 Python 环境

### 1. 创建并激活虚拟环境

```powershell
python --version
# 应为 3.11.x 或更高

python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

若提示无法执行脚本：

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

（macOS / Linux：`python -m venv .venv` 后执行 `source .venv/bin/activate`。）

### 2. 安装依赖

```powershell
pip install -r requirements.txt
```

首次安装会拉取 `sentence-transformers` 等包；之后第一次跑 `index` 时还会下载嵌入模型 `BAAI/bge-small-zh-v1.5`（约百兆级）。下载慢时可临时设置镜像：

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
```

### 3. 检查环境

```powershell
python scripts/check_env.py
```

期望看到 `✅ 环境就绪`。

---

## 三、配置本地 LLM（Ollama）

1. 安装：[https://ollama.com](https://ollama.com)
2. 拉取与 `config.yaml` 一致的模型：

```powershell
ollama pull qwen2.5:7b
ollama list
```

3. 确认服务在跑（默认 `http://localhost:11434`）：

```powershell
ollama run qwen2.5:7b "你好"
```

若连接失败，在另一终端执行 `ollama serve` 后再试。

默认 LLM 配置在 `config.yaml` 的 `llm` 段（`provider: ollama`、`model: qwen2.5:7b`）。若改用 OpenAI 兼容 API，改 `provider` / `base_url` / `api_key` / `model` 即可。

---

## 四、准备日记数据

- 默认目录：`data/diary/`
- 格式：Markdown，**每个日期一块标题**：

```markdown
# 2025-06-15

今天中午和朋友去吃了火锅……
```

- 首次跑通可直接用已有的 `data/diary/sample.md`，无需改动。
- 自用时把更多 `.md` 放进同一目录；日期正则见 `config.yaml` 的 `diary.date_pattern`。

---

## 五、完整跑通一次（首次全量）

按顺序执行：

```powershell
# 1) 解析切块 → SQLite（data/diary.db）
python main.py ingest

# 2) 嵌入 → Chroma（data/chroma/）；首次会下载嵌入模型，可能要几分钟
python main.py index

# 3) 对每个 chunk 用 LLM 提取标签（最慢，样例数据可能要几分钟到十几分钟）
python main.py tags
```

中间产物：

| 产物 | 路径 |
|------|------|
| 片段表 / 标签表 / 导入日志 | `data/diary.db` |
| 向量库 | `data/chroma/` |

---

## 六、验证：提问与测试

```powershell
# 单次提问（三类问题各试一条）
python main.py "这个月所有关于吃饭的内容"
python main.py "感动瞬间有多少次"
python main.py "这个月最喜欢做的事情是什么"

# 交互模式（空行退出）
python main.py

# 跑测试集 tests/test_queries.yaml
python main.py test
```

样例日记较小，检索/统计应对得上「火锅」「感动」等；若测试期望与你的日记内容不符，改 `tests/test_queries.yaml` 中的期望值即可。

---

## 七、日常使用：增量更新

之后只需往 `data/diary/` 增改 `.md`，然后：

```powershell
python main.py update
```

会只处理新增/变更文件，再增量嵌入与打标签。无新内容时会提示「没有新内容」。

---

## 流程总览

```
配置 venv + pip
    → check_env
    → Ollama 拉模型
    → 确认 data/diary/*.md
    → ingest → index → tags
    → 提问 / test
    →（以后）update
```

---

## 常见问题

| 现象 | 处理 |
|------|------|
| `Activate.ps1` 无法运行 | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| `check_env` 报缺少依赖 | 确认已激活 `.venv`，再 `pip install -r requirements.txt` |
| `index` 下载模型很慢 | 设置 `HF_ENDPOINT=https://hf-mirror.com` |
| `tags` / 提问报连接失败 | 确认 `ollama serve` 在跑；`ollama list` 有 `qwen2.5:7b` |
| `ingest` 后 chunk 为 0 | 检查日记是否为 `# YYYY-MM-DD` 标题，且在 `data/diary/` |
| 标签或回答质量差 | 先保证 Ollama 模型已下载；可改 `config.yaml` 换更大模型 |

更多调试查库命令见 [tool/tool.md](tool/tool.md)。

---

## 目录速览

```
My_rag/
├── main.py              # CLI 入口
├── config.yaml          # 路径 / 模型 / 切块参数
├── requirements.txt
├── data/diary/          # 原始日记
├── src/                 # ingest / embed / tags / query / answer
├── tests/               # 测试用例与 runner
├── scripts/check_env.py
├── tool/tool.md         # 命令速查
└── textbook/            # 从零教学章节
```

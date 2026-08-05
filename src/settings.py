"""用户设置：白名单 schema、user_settings overlay、.env 密钥读写。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.store import ROOT, load_config, user_settings_path

ENV_PATH = ROOT / ".env"

SETTINGS_GROUPS: list[dict[str, str]] = [
    {"id": "main", "label": "常用设置", "hint": "写入 data/user_settings.yaml，覆盖 config 对应项。"},
]

SETTINGS_FIELDS: list[dict[str, Any]] = [
    {
        "id": "llm.answer.model",
        "group": "main",
        "storage": "yaml",
        "path": "llm.answer.model",
        "type": "string",
        "label": "回答角色 model",
        "description": "聊天回答所用模型（llm.answer.model）。",
    },
    {
        "id": "default_recall_days",
        "group": "main",
        "storage": "yaml",
        "path": "default_recall_days",
        "type": "int",
        "label": "默认召回天数",
        "description": "今天往前数 N 天；推荐问题抽样等共用。",
        "min": 1,
        "max": 3650,
    },
    {
        "id": "retrieval.top_k",
        "group": "main",
        "storage": "yaml",
        "path": "retrieval.top_k",
        "type": "int",
        "label": "召回 top_k",
        "description": "检索方案最终进入 Context 的 chunk 数上限。",
        "min": 1,
        "max": 50,
    },
    {
        "id": "context.system_prompt",
        "group": "main",
        "storage": "yaml",
        "path": "context.system_prompt",
        "type": "text",
        "label": "系统提示词",
    },
]

_FIELD_BY_ID = {f["id"]: f for f in SETTINGS_FIELDS}


def _deep_get(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _deep_set(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def load_user_overlay() -> dict[str, Any]:
    path = user_settings_path()
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_user_overlay(overlay: dict[str, Any]) -> Path:
    path = user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(overlay, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return path


def _read_env_file() -> list[str]:
    if not ENV_PATH.is_file():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)


def _env_get(key: str) -> str:
    load_dotenv(ENV_PATH, override=True)
    return (os.getenv(key) or "").strip()


def _env_set(key: str, value: str) -> None:
    """更新或追加 .env 中的一行；保留其它行与注释。"""
    lines = _read_env_file()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    out: list[str] = []
    for line in lines:
        if pattern.match(line):
            out.append(f"{key}={value}\n")
            replaced = True
        else:
            out.append(line if line.endswith("\n") else line + "\n")
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        if out and out[-1].strip():
            out.append("\n")
        out.append(f"{key}={value}\n")
    ENV_PATH.write_text("".join(out), encoding="utf-8")
    load_dotenv(ENV_PATH, override=True)
    os.environ[key] = value


def _mask_secret(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "…" + s[-4:]


def _parse_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value or "").strip()
    if not s:
        return []
    parts = re.split(r"[,，\s]+", s)
    out: list[str] = []
    for p in parts:
        t = p.strip()
        if not t:
            continue
        if not t.startswith("."):
            t = "." + t
        out.append(t)
    return out


def _coerce_and_validate(field: dict[str, Any], value: Any) -> Any:
    ftype = field["type"]
    label = field["label"]

    if ftype == "secret":
        return str(value if value is not None else "").strip()

    if ftype == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        s = str(value).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"「{label}」须为布尔值")

    if ftype == "int":
        try:
            n = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"「{label}」须为整数") from exc
        lo = field.get("min")
        hi = field.get("max")
        if lo is not None and n < int(lo):
            raise ValueError(f"「{label}」不能小于 {lo}")
        if hi is not None and n > int(hi):
            raise ValueError(f"「{label}」不能大于 {hi}")
        return n

    if ftype == "enum":
        s = str(value if value is not None else "").strip()
        opts = list(field.get("options") or [])
        if s not in opts:
            raise ValueError(f"「{label}」须为其一：{', '.join(opts)}")
        return s

    if ftype == "string_list":
        items = _parse_string_list(value)
        if not items:
            raise ValueError(f"「{label}」不能为空")
        return items

    if ftype in ("string", "text"):
        return str(value if value is not None else "")

    raise ValueError(f"未知字段类型: {ftype}")


def _value_for_api(field: dict[str, Any], cfg: dict[str, Any]) -> Any:
    if field["storage"] == "env":
        raw = _env_get(field["path"])
        return {
            "set": bool(raw),
            "masked": _mask_secret(raw) if raw else "",
            "value": "",
        }

    val = _deep_get(cfg, field["path"])
    if field["type"] == "string_list":
        if isinstance(val, list):
            return ", ".join(str(x) for x in val)
        return str(val or "")
    if field["type"] == "bool":
        return bool(val)
    if field["type"] == "int":
        try:
            return int(val)
        except (TypeError, ValueError):
            return val
    return "" if val is None else val


def get_settings_for_api() -> dict[str, Any]:
    cfg = load_config()
    values: dict[str, Any] = {}
    fields_out: list[dict[str, Any]] = []
    for f in SETTINGS_FIELDS:
        values[f["id"]] = _value_for_api(f, cfg)
        meta = {
            "id": f["id"],
            "group": f["group"],
            "type": f["type"],
            "label": f["label"],
            "description": f.get("description") or "",
        }
        if f.get("options"):
            meta["options"] = list(f["options"])
        if f.get("min") is not None:
            meta["min"] = f["min"]
        if f.get("max") is not None:
            meta["max"] = f["max"]
        fields_out.append(meta)

    overlay = load_user_overlay()
    return {
        "groups": SETTINGS_GROUPS,
        "fields": fields_out,
        "values": values,
        "meta": {
            "overlay_path": str(user_settings_path().relative_to(ROOT)).replace("\\", "/"),
            "env_path": ".env",
            "has_overlay": bool(overlay),
        },
    }


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """根据白名单更新 overlay 与 .env。payload 为 { field_id: value, ... }。"""
    if not isinstance(payload, dict):
        raise ValueError("请求体须为对象")

    unknown = [k for k in payload.keys() if k not in _FIELD_BY_ID]
    if unknown:
        raise ValueError(f"未知设置项: {', '.join(unknown)}")

    overlay = load_user_overlay()
    env_changed = False
    yaml_changed = False

    for fid, raw in payload.items():
        field = _FIELD_BY_ID[fid]
        coerced = _coerce_and_validate(field, raw)

        if field["storage"] == "env":
            if coerced:
                _env_set(field["path"], coerced)
                env_changed = True
            continue

        _deep_set(overlay, field["path"], coerced)
        yaml_changed = True

    if yaml_changed:
        save_user_overlay(overlay)

    if env_changed or yaml_changed:
        try:
            from src.llm import clear_llm_cache

            clear_llm_cache()
        except Exception:
            pass
        load_dotenv(ENV_PATH, override=True)

    return get_settings_for_api()

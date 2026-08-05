"""LLM 客户端：按角色读取配置（标签 / 问答），支持 OpenRouter。"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from openai import OpenAI

from src.store import ROOT, load_config

load_dotenv(ROOT / ".env")


def resolve_llm_section(role: str) -> dict:
    """读取 config.yaml 中 llm.<role>，并用环境变量解析 api_key。"""
    cfg_all = load_config().get("llm") or {}
    if role not in cfg_all:
        raise KeyError(f"config.yaml 缺少 llm.{role} 配置")

    section = dict(cfg_all[role])
    env_name = section.get("api_key_env") or "OPENROUTER_API_KEY"
    raw_key = (section.get("api_key") or "").strip()

    # 占位符或空值 → 走环境变量
    if not raw_key or raw_key.lower() in {"env", "ollama", "changeme"}:
        api_key = os.getenv(env_name, "").strip()
    else:
        api_key = raw_key

    if not api_key:
        raise ValueError(
            f"缺少 LLM API Key（角色={role}）。"
            f"请在 .env 设置 {env_name}=...，或在 config.yaml 的 llm.{role}.api_key 填写。"
        )

    section["api_key"] = api_key
    section["api_key_env"] = env_name
    return section


@lru_cache(maxsize=4)
def get_llm_client(role: str = "answer") -> OpenAI:
    """按角色返回 OpenAI 兼容客户端（Ollama / OpenRouter 等）。"""
    section = resolve_llm_section(role)
    headers = {}
    if section.get("http_referer"):
        headers["HTTP-Referer"] = section["http_referer"]
    if section.get("x_title"):
        headers["X-Title"] = section["x_title"]

    return OpenAI(
        base_url=section["base_url"],
        api_key=section["api_key"],
        default_headers=headers or None,
    )


def get_llm_model(role: str = "answer") -> str:
    return resolve_llm_section(role)["model"]


def clear_llm_cache() -> None:
    """设置变更后清空客户端缓存，使 Key / base_url / model 立即生效。"""
    get_llm_client.cache_clear()
    # resolve 不缓存 model 名；重新 load_dotenv 由调用方负责
    load_dotenv(ROOT / ".env", override=True)

"""LLM client (OpenAI-compatible) with structured outputs."""

from kdv.llm.client import LLMClient, LLMError, LLMResponse
from kdv.llm.schema import json_schema_from_fields, build_tool_spec

__all__ = ["LLMClient", "LLMError", "LLMResponse", "json_schema_from_fields", "build_tool_spec"]

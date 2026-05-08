"""Conversation context with token budget + automatic compaction.

The agent loop appends messages here. When the running total approaches the
model's context window, the older middle of the conversation is replaced by
a one-paragraph LLM-generated summary so we keep:

    [system prompt]  +  [compacted history summary]  +  [recent N turns]

This is the same pattern as Anthropic Claude's conversation compaction —
keeps the agent grounded while staying under the limit indefinitely.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from kdv.llm.tokens import context_window_for, estimate_messages, estimate_tokens

logger = logging.getLogger(__name__)


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: Role
    content: str = ""
    name: str = ""              # tool name for role=tool
    tool_call_id: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0             # cached estimate

    def to_openai(self) -> dict[str, Any]:
        m: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            m["tool_calls"] = self.tool_calls
            # Some providers reject non-empty content with tool_calls; keep empty.
            if not self.content:
                m["content"] = ""
        if self.role == "tool":
            m["tool_call_id"] = self.tool_call_id
            if self.name:
                m["name"] = self.name
        return m


@dataclass
class ConversationContext:
    """Token-aware list of Messages with auto-compaction.

    The first message is treated as the immutable system prompt and never
    compacted. The last `keep_recent` messages are also preserved verbatim.
    Anything in between can be replaced by a single summary message produced
    by `compactor` on demand.
    """

    model: str
    max_context_tokens: int = 0          # 0 → look up from model name
    output_reserve_tokens: int = 4096    # leave room for response
    keep_recent: int = 8                 # always keep this many tail messages
    safety_margin: float = 0.85          # compact when above this ratio of budget
    messages: list[Message] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_context_tokens <= 0:
            self.max_context_tokens = context_window_for(self.model)

    # ---- adders -----------------------------------------------------------
    def add_system(self, content: str) -> None:
        msg = Message(role="system", content=content)
        msg.tokens = estimate_tokens(content) + 4
        # System prompt is always at index 0.
        if self.messages and self.messages[0].role == "system":
            self.messages[0] = msg
        else:
            self.messages.insert(0, msg)

    def add_user(self, content: str) -> Message:
        return self._append(Message(role="user", content=content,
                                    tokens=estimate_tokens(content) + 4))

    def add_assistant(
        self, content: str, tool_calls: list[dict[str, Any]] | None = None
    ) -> Message:
        m = Message(role="assistant", content=content or "",
                    tool_calls=tool_calls or [])
        m.tokens = estimate_messages([m.to_openai()])
        return self._append(m)

    def add_tool_result(self, *, tool_call_id: str, name: str, content: str) -> Message:
        m = Message(role="tool", name=name, tool_call_id=tool_call_id,
                    content=content)
        m.tokens = estimate_tokens(content) + 8
        return self._append(m)

    def _append(self, m: Message) -> Message:
        self.messages.append(m)
        return m

    # ---- export -----------------------------------------------------------
    def to_openai(self) -> list[dict[str, Any]]:
        return [m.to_openai() for m in self.messages]

    def total_tokens(self) -> int:
        # Recompute against to_openai() to include nested tool_calls overhead.
        return estimate_messages([m.to_openai() for m in self.messages])

    def budget(self) -> int:
        return max(0, self.max_context_tokens - self.output_reserve_tokens)

    def needs_compaction(self) -> bool:
        return self.total_tokens() >= int(self.budget() * self.safety_margin)

    # ---- compaction -------------------------------------------------------
    def _build_compaction_prompt(self) -> tuple[str, list[Message], list[Message], list[Message]]:
        head = self.messages[:1]
        keep = self.messages[-self.keep_recent:]
        middle = self.messages[1:-self.keep_recent]
        joined = []
        for m in middle:
            tag = m.role.upper()
            if m.role == "tool":
                tag = f"TOOL[{m.name}]"
            txt = m.content or ""
            if m.tool_calls:
                txt = txt + " | tool_calls=" + str(
                    [(tc.get("function", {}).get("name", "?"),
                      tc.get("function", {}).get("arguments", "")[:120])
                     for tc in m.tool_calls]
                )
            joined.append(f"[{tag}] {txt[:1200]}")
        prompt = (
            "下面是一段 agent 会话历史，请压缩成简洁要点（≤300字），保留：\n"
            "1. 已经获得的关键事实/数据\n"
            "2. 已尝试过的工具与重要观察\n"
            "3. 待办或下一步计划\n\n"
            "会话历史：\n\n" + "\n\n".join(joined)
        )
        return prompt, head, middle, keep

    def _apply_compaction(self, head, middle, keep, summary: str) -> int:
        synth = Message(
            role="system",
            content=f"[历史摘要]\n{summary}\n\n（之前 {len(middle)} 条消息已压缩）",
        )
        synth.tokens = estimate_tokens(synth.content) + 4
        self.messages = head + [synth] + keep
        logger.info(
            "compacted %d messages → 1 summary (now total=%d tokens)",
            len(middle),
            self.total_tokens(),
        )
        return len(middle)

    async def compact_async(self, async_summarizer) -> int:
        """Compact older messages via an async summarizer coroutine.

        `async_summarizer` is an async callable taking a prompt string and
        returning the summary. Use this from agent loops running on the same
        event loop as the LLM call.
        """
        if len(self.messages) <= 1 + self.keep_recent:
            return 0
        prompt, head, middle, keep = self._build_compaction_prompt()
        if not middle:
            return 0
        try:
            summary = await async_summarizer(prompt)
        except Exception:
            logger.exception("async compaction summarizer failed; keeping raw context")
            return 0
        return self._apply_compaction(head, middle, keep, summary)

    def compact_with(self, summarizer) -> int:
        """Sync version (for tests / non-async contexts only)."""
        if len(self.messages) <= 1 + self.keep_recent:
            return 0
        prompt, head, middle, keep = self._build_compaction_prompt()
        if not middle:
            return 0
        try:
            summary = summarizer(prompt)
        except Exception:
            logger.exception("compaction summarizer failed; keeping raw context")
            return 0
        return self._apply_compaction(head, middle, keep, summary)

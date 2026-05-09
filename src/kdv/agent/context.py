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
    # Cap per-message detail in the compaction prompt; tool result tables
    # past this length are tail-truncated (still useful for the summarizer).
    _PER_MSG_CHAR_BUDGET = 1200
    # Single-shot compaction prompt budget. Past this we chunk the middle
    # so each LLM call stays small and fast — see `_compact_chunked_async`.
    _SINGLE_SHOT_CHAR_BUDGET = 6000

    _COMPACTION_INSTRUCTION = (
        "下面是一段 agent 会话历史，请压缩成简洁要点（≤300字），保留：\n"
        "1. 已经获得的关键事实/数据\n"
        "2. 已尝试过的工具与重要观察\n"
        "3. 待办或下一步计划\n\n"
        "会话历史：\n\n"
    )

    def _format_message_for_compaction(self, m: Message) -> str:
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
        return f"[{tag}] {txt[:self._PER_MSG_CHAR_BUDGET]}"

    def _format_middle_lines(self, middle: list[Message]) -> list[str]:
        return [self._format_message_for_compaction(m) for m in middle]

    def _build_compaction_prompt(self) -> tuple[str, list[Message], list[Message], list[Message]]:
        head = self.messages[:1]
        keep = self.messages[-self.keep_recent:]
        middle = self.messages[1:-self.keep_recent]
        lines = self._format_middle_lines(middle)
        prompt = self._COMPACTION_INSTRUCTION + "\n\n".join(lines)
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

    @staticmethod
    def _chunk_lines_by_char_budget(
        lines: list[str], char_budget: int
    ) -> list[list[str]]:
        """Group consecutive formatted lines into chunks that each stay below
        `char_budget` total chars (joined with double-newlines).

        Each chunk preserves message order so per-chunk summaries read in the
        same chronological flow as the original conversation.
        """
        chunks: list[list[str]] = []
        cur: list[str] = []
        cur_len = 0
        sep = 2  # "\n\n"
        for ln in lines:
            ln_len = len(ln) + sep
            if cur and cur_len + ln_len > char_budget:
                chunks.append(cur)
                cur = []
                cur_len = 0
            cur.append(ln)
            cur_len += ln_len
        if cur:
            chunks.append(cur)
        return chunks

    async def compact_async(self, async_summarizer) -> int:
        """Compact older messages via an async summarizer coroutine.

        Single-shot when the middle joins to ≤ _SINGLE_SHOT_CHAR_BUDGET chars,
        otherwise hierarchical: split middle into char-bounded chunks, summarize
        each chunk separately (each LLM call stays small/fast → no timeout),
        then concatenate the chunk summaries into the replacement system
        message. No data is dropped — every chunk's numbers / observations
        flow through into the final compacted summary.

        Returns the number of original messages that were compacted (0 on
        failure or when there's nothing to do).
        """
        if len(self.messages) <= 1 + self.keep_recent:
            return 0
        head = self.messages[:1]
        keep = self.messages[-self.keep_recent:]
        middle = self.messages[1:-self.keep_recent]
        if not middle:
            return 0
        lines = self._format_middle_lines(middle)
        joined_total = sum(len(ln) + 2 for ln in lines)
        try:
            if joined_total <= self._SINGLE_SHOT_CHAR_BUDGET:
                prompt = self._COMPACTION_INSTRUCTION + "\n\n".join(lines)
                summary = await async_summarizer(prompt)
            else:
                summary = await self._summarize_chunked(lines, async_summarizer)
        except Exception:
            logger.exception("async compaction summarizer failed; keeping raw context")
            return 0
        if not (summary or "").strip():
            logger.warning("compaction returned empty summary; keeping raw context")
            return 0
        return self._apply_compaction(head, middle, keep, summary)

    async def _summarize_chunked(
        self, lines: list[str], async_summarizer
    ) -> str:
        chunks = self._chunk_lines_by_char_budget(lines, self._SINGLE_SHOT_CHAR_BUDGET)
        partial: list[str] = []
        for i, chunk in enumerate(chunks, start=1):
            prompt = (
                f"以下是一段 agent 会话历史的第 {i}/{len(chunks)} 段。"
                f"请用 ≤200 字提炼本段中：\n"
                f"1. 关键数字 / 事实（具体数值原样保留）\n"
                f"2. 调用过的工具及其结果要点\n"
                f"3. 与下一步计划相关的线索\n\n"
                f"片段内容：\n\n" + "\n\n".join(chunk)
            )
            piece = await async_summarizer(prompt)
            partial.append(f"### 第 {i} 段\n{(piece or '').strip() or '(空)'}")
        # No second-pass merge — concatenation already preserves all numbers
        # the chunk summaries kept verbatim, and a merge call would re-introduce
        # the long-prompt timeout risk we just engineered around.
        return "\n\n".join(partial)

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

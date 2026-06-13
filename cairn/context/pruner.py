"""
ContextPruner, trim context to save tokens.

- Long context (> summarize_threshold): LLM-based summarization (opt-in).
- Short context: truncation (default).
- Summarization uses a cheap model, negligible cost, large savings on the
  caller's input-token bill.
"""
from __future__ import annotations


class ContextPruner:
    def __init__(self, config: dict):
        cfg = config.get("context", {})
        self.enabled = cfg.get("enabled", True)
        self.default_max_tokens = cfg.get("max_tokens", 800)

        sm = cfg.get("summarize", {})
        self.summarize_enabled = sm.get("enabled", False)
        # Above this threshold, summarize via LLM instead of truncating.
        self.summarize_threshold = int(sm.get("threshold_tokens", 1200))
        self.summarize_model = sm.get("model", "groq/llama-3.3-70b-versatile")
        self.summarize_target = int(sm.get("target_tokens", 400))

    def prune(self, context: str, max_tokens: int | None = None) -> str:
        if not self.enabled or not context:
            return context

        limit = max_tokens or self.default_max_tokens

        if self.summarize_enabled and self.estimate_tokens(context) > self.summarize_threshold:
            summarized = self._llm_summarize(context, target_tokens=min(self.summarize_target, limit))
            if summarized:
                return summarized
            # fall through to truncation if summarization fails

        return self._truncate(context, limit)

    def _truncate(self, context: str, max_tokens: int) -> str:
        char_limit = max_tokens * 4
        if len(context) <= char_limit:
            return context
        pruned = context[:char_limit]
        last_newline = pruned.rfind("\n")
        if last_newline > char_limit * 0.8:
            pruned = pruned[:last_newline]
        return pruned + "\n[...context pruned for token efficiency...]"

    def _llm_summarize(self, context: str, target_tokens: int) -> str | None:
        """Compress context to a short summary that preserves key information."""
        try:
            import litellm
        except ImportError:
            return None

        sys_prompt = (
            "You compress prior conversation context for an AI agent. "
            "Keep all facts, decisions, names, numbers, file paths, and TODOs. "
            "Discard pleasantries and repetition. Use bullet form."
        )
        user_prompt = (
            f"Compress the following context to ~{target_tokens} tokens (~{target_tokens*4} chars). "
            "Preserve concrete information:\n\n" + context
        )
        try:
            resp = litellm.completion(
                model=self.summarize_model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max(target_tokens, 200),
            )
            text = resp.choices[0].message.content or ""
            return text.strip() or None
        except Exception:
            return None

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

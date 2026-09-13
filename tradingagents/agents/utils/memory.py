"""Append-only markdown decision log for TradingAgents."""

import re
from datetime import datetime
from pathlib import Path

from tradingagents.agents.utils.rating import parse_rating

# Fallback cap when config['event_summary_max_chars'] is absent.
DEFAULT_SUMMARY_MAX_CHARS = 1600


def _as_date(value) -> str:
    """Normalise a date-ish value to ``YYYY-MM-DD`` for string comparison.

    Time slicing compares dates as strings, so a ``datetime`` / ``Timestamp``
    (``"2026-03-04 00:00:00"``) would otherwise sort *after* the same calendar
    day and silently re-admit same-day material — the exact leak the slice
    exists to prevent. Non-strings and timestamps are therefore truncated.
    """
    return str(value or "")[:10]


class TradingMemoryLog:
    """Append-only markdown log of trading decisions and reflections."""

    # HTML comment: cannot appear in LLM prose output, safe as a hard delimiter
    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
    # Precompiled patterns — avoids re-compilation on every load_entries() call
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)

    def __init__(self, config: dict = None):
        cfg = config or {}
        # Read-only mode: every write path becomes a no-op while the read paths
        # keep working. Used by the paired A/B harness, which must observe a
        # FROZEN memory — otherwise a rep's own decision would be settled (and
        # turned into an L1 event) by the next run of the same ticker, showing
        # the "on" arm its own outcome.
        self._readonly = bool(cfg.get("memory_readonly", False))
        self._log_path = None
        path = cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # Optional cap on resolved entries. None disables rotation.
        self._max_entries = cfg.get("memory_log_max_entries")

        # Three-layer memory: L1 events + L3 rules live in a separate lessons
        # file so the append-only decision log keeps its audit semantics.
        self._lessons_path = None
        lessons = cfg.get("lessons_path")
        if lessons:
            self._lessons_path = Path(lessons).expanduser()
            self._lessons_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_event_entries = cfg.get("max_event_entries")
        self._event_protect_threshold = cfg.get("event_protect_threshold")
        self._min_per_direction = cfg.get("min_events_per_direction")
        # Hard cap for stored/injected summaries (see default_config).
        self._summary_max_chars = cfg.get("event_summary_max_chars") or DEFAULT_SUMMARY_MAX_CHARS

        # Evicted events are buffered here for the L3 distillation loop.
        self._distill_queue_path = None
        queue = cfg.get("distill_queue_path")
        if queue:
            self._distill_queue_path = Path(queue).expanduser()
            self._distill_queue_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Write path (Phase A) ---

    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
    ) -> None:
        """Append pending entry at end of propagate(). No LLM call."""
        if self._readonly or not self._log_path:
            return
        # Idempotency guard: fast raw-text scan instead of full parse
        if self._log_path.exists():
            raw = self._log_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("| pending]"):
                    return
        rating = parse_rating(final_trade_decision)
        tag = f"[{trade_date} | {ticker} | {rating} | pending]"
        entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # --- Lessons write path (L1 events) ---

    @staticmethod
    def _format_pct(value):
        if value is None:
            return "n/a"
        return f"{value:+.1%}"

    @staticmethod
    def _parse_pct(text: str) -> float | None:
        """Parse a '+5.0%' style string back to a float ratio, or None."""
        text = (text or "").strip()
        if not text or text.lower() in {"n/a", "none", "null", "-"}:
            return None
        try:
            return float(text.rstrip("%")) / 100.0
        except ValueError:
            return None

    def _cap_summary(self, text: str) -> str:
        """Hard-cap a summary so one event cannot blow the injection budget.

        Applied both when storing (new events) and when formatting for injection
        (entries written before the cap existed), because up to ``n_same``
        summaries are re-injected into every future prompt.
        """
        limit = self._summary_max_chars or 0
        if limit <= 0 or len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n...[truncated]"

    def store_event(
        self,
        entry_id: str,
        ticker: str,
        trade_date: str,
        rating: str,
        alpha: float,
        raw: float,
        summary: str,
        context_pointer: str,
        fingerprint: str = "",
    ) -> None:
        """Append an L1 EVENT entry to the lessons file.

        Called at settlement time (Phase B) when |alpha| >= event_alpha_threshold.
        The entry stores a structured summary + a pointer to the full debate
        trace JSON — never the full process — so prompt injection stays bounded.
        """
        if self._readonly or not self._lessons_path:
            return
        # Idempotency on entry_id (E-<date>-<ticker> = one decision). Re-running an
        # already-settled (ticker, date) — a plain re-run, or the same scenario in
        # two pools — used to append a SECOND event with the same id but a
        # possibly different rating, leaving the store asserting that the team
        # decided both "Buy" and "Hold" for the same day.
        if self._lessons_path.exists():
            if f"[EVENT | {entry_id} |" in self._lessons_path.read_text(encoding="utf-8"):
                return
        protected = abs(alpha) >= (self._event_protect_threshold or 0.0)
        summary = self._cap_summary(summary)
        tag = (
            f"[EVENT | {entry_id} | {trade_date} | {ticker} | {rating} | "
            f"{self._format_pct(alpha)} | {self._format_pct(raw)}]"
        )
        lines = [tag, "", "SUMMARY:", summary, "", f"CONTEXT_POINTER: {context_pointer}"]
        if fingerprint:
            lines.append(f"FINGERPRINT: {fingerprint}")
        lines.append(f"LAST_ACCESSED: never  |  HITS: 0  |  PROTECTED: {'true' if protected else 'false'}")
        entry = "\n".join(lines) + self._SEPARATOR
        with open(self._lessons_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # --- Read path (Phase A) ---

    def load_entries(self) -> list[dict]:
        """Parse all entries from log. Returns list of dicts."""
        if not self._log_path or not self._log_path.exists():
            return []
        text = self._log_path.read_text(encoding="utf-8")
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]
        entries = []
        for raw in raw_entries:
            parsed = self._parse_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    def get_pending_entries(self) -> list[dict]:
        """Return entries with outcome:pending (for Phase B)."""
        return [e for e in self.load_entries() if e.get("pending")]

    # --- Lessons read path (L1 events / L3 rules) ---

    def load_lessons(self) -> list[dict]:
        """Parse all entries from the lessons file (EVENT entries for now)."""
        if not self._lessons_path or not self._lessons_path.exists():
            return []
        text = self._lessons_path.read_text(encoding="utf-8")
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]
        entries = []
        for raw in raw_entries:
            parsed = self._parse_lessons_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    def _parse_lessons_entry(self, raw: str) -> dict | None:
        """Parse one EVENT entry. Non-EVENT tags (RULE, decision) return None for now."""
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag = lines[0].strip()
        if not (tag.startswith("[EVENT") and tag.endswith("]")):
            return None
        fields = [f.strip() for f in tag[1:-1].split("|")]
        if len(fields) < 7:
            return None
        entry = {
            "kind": fields[0],
            "entry_id": fields[1],
            "trade_date": fields[2],
            "ticker": fields[3],
            "rating": fields[4],
            "alpha": self._parse_pct(fields[5]),
            "raw": self._parse_pct(fields[6]),
            "summary": "",
            "context_pointer": "",
            "fingerprint": "",
            "last_accessed": "never",
            "hits": 0,
            "protected": False,
        }
        current = None
        for line in lines[1:]:
            if line.startswith("SUMMARY:"):
                current = "summary"
                entry["summary"] = line[len("SUMMARY:"):].strip()
            elif line.startswith("CONTEXT_POINTER:"):
                current = None
                entry["context_pointer"] = line[len("CONTEXT_POINTER:"):].strip()
            elif line.startswith("FINGERPRINT:"):
                current = None
                entry["fingerprint"] = line[len("FINGERPRINT:"):].strip()
            elif line.startswith("LAST_ACCESSED:"):
                current = None
                meta = line[len("LAST_ACCESSED:"):].strip()
                entry["last_accessed"] = meta.split("|")[0].strip()
                for part in meta.split("|"):
                    part = part.strip()
                    if part.startswith("HITS:"):
                        try:
                            entry["hits"] = int(part.split(":", 1)[1].strip())
                        except ValueError:
                            entry["hits"] = 0
                    elif part.startswith("PROTECTED:"):
                        entry["protected"] = part.split(":", 1)[1].strip().lower() == "true"
            elif current == "summary":
                if line.strip():
                    entry["summary"] = entry["summary"] + "\n" + line if entry["summary"] else line
        return entry

    def _render_event_entry(self, e: dict) -> str:
        """Serialize a parsed EVENT entry back to its markdown block."""
        tag = (
            f"[EVENT | {e['entry_id']} | {e['trade_date']} | {e['ticker']} | "
            f"{e['rating']} | {self._format_pct(e['alpha'])} | {self._format_pct(e['raw'])}]"
        )
        lines = [tag, "", "SUMMARY:", e["summary"], "", f"CONTEXT_POINTER: {e['context_pointer']}"]
        if e.get("fingerprint"):
            lines.append(f"FINGERPRINT: {e['fingerprint']}")
        lines.append(
            f"LAST_ACCESSED: {e.get('last_accessed', 'never')}  |  "
            f"HITS: {e.get('hits', 0)}  |  PROTECTED: {'true' if e.get('protected') else 'false'}"
        )
        return "\n".join(lines)

    def _rewrite_lessons(self, entries: list[dict]) -> None:
        """Atomically rewrite the lessons file from parsed entries.

        Keeps a trailing separator so a later store_event() append stays a
        separate block (matching the decision-log convention).

        Frozen mode short-circuits here rather than at each caller: this is the
        single chokepoint every lessons write goes through, and GET paths reach
        it too (``get_lessons_context`` bumps hit counters). Without the guard a
        "frozen" A/B run would still rewrite the store on every read — and since
        eviction priority is access-based (``_recency_key``), those reads would
        silently change which events survive future maintenance.
        """
        if self._readonly or not self._lessons_path or not entries:
            return
        text = self._SEPARATOR.join(self._render_event_entry(e) for e in entries) + self._SEPARATOR
        tmp_path = self._lessons_path.with_suffix(".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(self._lessons_path)

    def _format_event(self, e: dict) -> str:
        """Compact injection format for one EVENT entry (no full process)."""
        return (
            f"[{e['trade_date']} | {e['ticker']} | {e['rating']} | "
            f"{self._format_pct(e['alpha'])}]\n{self._cap_summary(e['summary'])}"
        )

    def get_lessons_context(self, ticker: str, n_same: int = 5, n_cross: int = 3,
                            as_of: str | None = None) -> str:
        """Return L1 event context for injection, and bump access metadata.

        Retrieval is recency-first with a per-direction floor: at least
        min_events_per_direction win AND loss events are surfaced, so a long
        winning streak can't crowd out the last bear-market lessons. Selected
        entries have hits / last_accessed rewritten back to disk (one small
        atomic write per run — negligible at a few hundred entries).

        ``as_of`` is the date of the decision being made. Events dated on or
        after it are FUTURE information for that decision and are excluded —
        without this a backtest leaks hindsight: an event settled in June would
        otherwise be injected into a January decision, and the "memory on" arm
        would win by clairvoyance rather than by using memory.
        """
        all_entries = self.load_lessons()
        if not all_entries:
            return ""
        entries = all_entries
        if as_of:
            cutoff = _as_date(as_of)
            entries = [e for e in all_entries if _as_date(e.get("trade_date", "")) < cutoff]
        if not entries:
            return ""

        floor = self._min_per_direction or 0
        same = sorted([e for e in entries if e["ticker"] == ticker],
                      key=lambda e: e.get("trade_date", ""), reverse=True)
        cross = sorted([e for e in entries if e["ticker"] != ticker],
                       key=lambda e: e.get("trade_date", ""), reverse=True)
        same = self._select_direction_balanced(same, n_same, floor)
        cross = self._select_direction_balanced(cross, n_cross, floor)
        selected_ids = {e["entry_id"] for e in same + cross}

        now = datetime.now().strftime("%Y-%m-%d")
        for e in entries:
            if e["entry_id"] in selected_ids:
                e["hits"] = e.get("hits", 0) + 1
                e["last_accessed"] = now
        # Write back the FULL set, never just the time-sliced subset. This call
        # rewrites the file, so passing the filtered list would DELETE every
        # event dated on/after as_of — a sliced read would silently destroy the
        # very future events it is meant to keep out of the prompt.
        self._rewrite_lessons(all_entries)

        parts = []
        if same:
            parts.append(f"Past extreme events for {ticker} (most recent first):")
            parts.extend(self._format_event(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker extreme events:")
            parts.extend(self._format_event(e) for e in cross)
        return "\n\n".join(parts)

    def get_rules_context(self, as_of: str | None = None) -> str:
        """Return distilled team rules as a prompt preamble. v1: no rules yet.

        ``as_of`` accepts the decision date for the same reason the L1 store does:
        once L3 distils rules from evicted events, a rule derived in June must not
        reach a January decision. v1 returns nothing, but the parameter is part of
        the interface so the L3 implementation cannot forget it.
        """
        return ""

    # --- Lessons maintenance (simplified forgetting) ---

    @staticmethod
    def _sign(alpha):
        """Direction of an event: 'win' / 'loss' / None (unknown)."""
        if alpha is None:
            return None
        return "win" if alpha >= 0 else "loss"

    def _recency_key(self, e):
        """Keep-priority key: most-recently-used first, newest trade_date, most hits."""
        last = e.get("last_accessed", "never")
        has_access = 0 if last == "never" else 1
        return (has_access, last if has_access else "", e.get("trade_date", ""), e.get("hits", 0))

    def _select_direction_balanced(self, events, n, floor):
        """Newest-first selection guaranteeing >= floor per outcome direction."""
        if floor <= 0 or not events:
            return events[:n]
        wins = [e for e in events if self._sign(e.get("alpha")) == "win"]
        losses = [e for e in events if self._sign(e.get("alpha")) == "loss"]
        picked, ids = [], set()
        for pool in (wins, losses):
            for e in pool[:floor]:
                picked.append(e)
                ids.add(e["entry_id"])
        if len(picked) < n:
            for e in events:
                if e["entry_id"] not in ids:
                    picked.append(e)
                    ids.add(e["entry_id"])
                    if len(picked) >= n:
                        break
        return picked

    def _dedupe_events(self, entries):
        """Remove literal duplicates (same ticker+trade_date+rating) and any
        repeat of an ``entry_id``.

        ``entry_id`` is the natural key (``E-<date>-<ticker>``): two entries
        sharing one means the same decision was settled twice. ``store_event``
        now refuses to create those, and this pass repairs stores written
        before that guard existed. Keeping the FIRST occurrence preserves the
        original postmortem (and, during a provider switch, the original
        provider's narrative).

        Semantic redundancy (same *situation* within a window) is deferred to
        v3: without the state fingerprint, a 30-day window chains and over-merges
        whole streaks into one entry — exactly the bear-memory loss we must avoid.
        """
        seen = set()
        seen_ids = set()
        kept, removed = [], []
        for e in entries:
            key = (e["ticker"], e["trade_date"], e["rating"])
            entry_id = e.get("entry_id")
            if key in seen or (entry_id and entry_id in seen_ids):
                removed.append(e)
            else:
                seen.add(key)
                if entry_id:
                    seen_ids.add(entry_id)
                kept.append(e)
        return kept, removed

    def _evict_to_cap(self, entries):
        """Drop lowest-value non-protected entries down to the cap.

        Returns (kept, evicted). Protected entries and the per-direction floor
        are never evicted, so a long bull can't erase the last bear lessons.
        """
        max_entries = self._max_event_entries
        if not max_entries or len(entries) <= max_entries:
            return entries, []
        floor = self._min_per_direction or 0
        protected = [e for e in entries if e.get("protected")]
        unprotected = [e for e in entries if not e.get("protected")]
        unprotected.sort(key=self._recency_key, reverse=True)

        wins = [e for e in unprotected if self._sign(e.get("alpha")) == "win"]
        losses = [e for e in unprotected if self._sign(e.get("alpha")) == "loss"]

        budget = max(0, max_entries - len(protected))
        kept, kept_ids = [], set()
        # floor: reserve the highest-priority entries of each direction
        for pool in (wins, losses):
            for e in pool[:floor]:
                kept.append(e)
                kept_ids.add(e["entry_id"])
        # fill remaining budget with highest-priority leftovers
        for e in unprotected:
            if len(kept) >= budget:
                break
            if e["entry_id"] not in kept_ids:
                kept.append(e)
                kept_ids.add(e["entry_id"])
        dropped = [e for e in unprotected if e["entry_id"] not in kept_ids]
        return protected + kept, dropped

    def _append_distill_queue(self, entries):
        """Buffer evicted entries for the L3 distillation loop (not hard-deleted).

        Guarded directly, not just via ``maintain_memory``: it writes a second
        file, and frozen mode must leave every artefact untouched even if a new
        caller reaches this path.
        """
        if self._readonly or not self._distill_queue_path or not entries:
            return
        text = self._SEPARATOR.join(self._render_event_entry(e) for e in entries) + self._SEPARATOR
        with open(self._distill_queue_path, "a", encoding="utf-8") as f:
            f.write(text)

    def maintain_memory(self) -> None:
        """Simplify the lessons store without losing information.

        Redundant entries are merged; over-cap entries are evicted — but evicted
        (and merged-out) entries are buffered to the distill queue for L3, and
        the per-direction floor keeps both big wins and big losses represented.
        """
        if self._readonly or not self._lessons_path or not self._lessons_path.exists():
            return
        entries = self.load_lessons()
        if not entries:
            return
        entries, deduped = self._dedupe_events(entries)
        entries, evicted = self._evict_to_cap(entries)
        queued = deduped + evicted
        if queued:
            self._append_distill_queue(queued)
        entries.sort(key=lambda e: e.get("trade_date", ""))  # keep chronological
        if entries:
            self._rewrite_lessons(entries)
        else:
            self._lessons_path.write_text("", encoding="utf-8")

    def get_past_context(self, ticker: str, n_same: int = 5, n_cross: int = 3,
                         as_of: str | None = None) -> str:
        """Return formatted past context string for agent prompt injection.

        ``as_of`` mirrors :meth:`get_lessons_context`: decisions dated on or
        after it are excluded so the legacy decision log is time-consistent too.
        Both arms get the same filter, so the only difference between them stays
        the L1 layer.
        """
        entries = [e for e in self.load_entries() if not e.get("pending")]
        if as_of:
            cutoff = _as_date(as_of)
            entries = [e for e in entries if _as_date(e.get("date", "")) < cutoff]
        if not entries:
            return ""

        same, cross = [], []
        for e in reversed(entries):
            if len(same) >= n_same and len(cross) >= n_cross:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)

        if not same and not cross:
            return ""

        parts = []
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")
            parts.extend(self._format_reflection_only(e) for e in cross)
        return "\n\n".join(parts)

    # --- Update path (Phase B) ---

    def update_with_outcome(
        self,
        ticker: str,
        trade_date: str,
        raw_return: float,
        alpha_return: float,
        holding_days: int,
        reflection: str,
    ) -> None:
        """Replace pending tag and append REFLECTION section using atomic write.

        Finds the first pending entry matching (trade_date, ticker), updates
        its tag with return figures, and appends a REFLECTION section.  Uses
        a temp-file + os.replace() so a crash mid-write never corrupts the log.
        """
        if self._readonly or not self._log_path or not self._log_path.exists():
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        pending_prefix = f"[{trade_date} | {ticker} |"
        raw_pct = f"{raw_return:+.1%}"
        alpha_pct = f"{alpha_return:+.1%}"

        updated = False
        new_blocks = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            if (
                not updated
                and tag_line.startswith(pending_prefix)
                and tag_line.endswith("| pending]")
            ):
                # Parse rating from the existing pending tag
                fields = [f.strip() for f in tag_line[1:-1].split("|")]
                rating = fields[2]
                new_tag = (
                    f"[{trade_date} | {ticker} | {rating}"
                    f" | {raw_pct} | {alpha_pct} | {holding_days}d]"
                )
                rest = "\n".join(lines[1:])
                new_blocks.append(
                    f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{reflection}"
                )
                updated = True
            else:
                new_blocks.append(block)

        if not updated:
            return

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)

    def batch_update_with_outcomes(self, updates: list[dict]) -> None:
        """Apply multiple outcome updates in a single read + atomic write.

        Each element of updates must have keys: ticker, trade_date,
        raw_return, alpha_return, holding_days, reflection.
        """
        if self._readonly or not self._log_path or not self._log_path.exists() or not updates:
            return

        text = self._log_path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        # Build lookup keyed by (trade_date, ticker) for O(1) dispatch
        update_map = {(u["trade_date"], u["ticker"]): u for u in updates}

        new_blocks = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()

            matched = False
            for (trade_date, ticker), upd in list(update_map.items()):
                pending_prefix = f"[{trade_date} | {ticker} |"
                if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]
                    raw_pct = f"{upd['raw_return']:+.1%}"
                    alpha_pct = f"{upd['alpha_return']:+.1%}"
                    new_tag = (
                        f"[{trade_date} | {ticker} | {rating}"
                        f" | {raw_pct} | {alpha_pct} | {upd['holding_days']}d]"
                    )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{upd['reflection']}"
                    )
                    del update_map[(trade_date, ticker)]
                    matched = True
                    break

            if not matched:
                new_blocks.append(block)

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = self._log_path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(self._log_path)

    # --- Helpers ---

    def _apply_rotation(self, blocks: list[str]) -> list[str]:
        """Drop oldest resolved blocks when their count exceeds max_entries.

        Pending blocks are always kept (they represent unprocessed work).
        Returns ``blocks`` unchanged when rotation is disabled or under cap.
        """
        if not self._max_entries or self._max_entries <= 0:
            return blocks

        # Tag each block with (kept, is_resolved) by parsing tag-line markers.
        decisions = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved))

        resolved_count = sum(1 for _, r in decisions if r)
        if resolved_count <= self._max_entries:
            return blocks

        to_drop = resolved_count - self._max_entries
        kept: list[str] = []
        for block, is_resolved in decisions:
            if is_resolved and to_drop > 0:
                to_drop -= 1
                continue
            kept.append(block)
        return kept

    def _parse_entry(self, raw: str) -> dict | None:
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]
        if len(fields) < 4:
            return None
        entry = {
            "date": fields[0],
            "ticker": fields[1],
            "rating": fields[2],
            "pending": fields[3] == "pending",
            "raw": fields[3] if fields[3] != "pending" else None,
            "alpha": fields[4] if len(fields) > 4 else None,
            "holding": fields[5] if len(fields) > 5 else None,
        }
        body = "\n".join(lines[1:]).strip()
        decision_match = self._DECISION_RE.search(body)
        reflection_match = self._REFLECTION_RE.search(body)
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""
        return entry

    def _format_full(self, e: dict) -> str:
        raw = e["raw"] or "n/a"
        alpha = e["alpha"] or "n/a"
        holding = e["holding"] or "n/a"
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    def _format_reflection_only(self, e: dict) -> str:
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]
        suffix = "..." if len(e["decision"]) > 300 else ""
        return f"{tag}\n{text}{suffix}"

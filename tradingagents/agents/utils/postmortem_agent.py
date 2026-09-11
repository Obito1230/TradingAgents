"""Postmortem agent: attributes an extreme outcome to the team's debate trace.

Fired at settlement (Phase B) when |alpha| >= event_alpha_threshold. Unlike the
existing Reflector (which only sees the final decision text + returns), the
postmortem agent consumes the full multi-agent state — analyst reports, bull/bear
debate history, risk debate history, trader plan, and final decision — so it can
answer "why did this outcome happen" rather than just "was the call right".

Follows the same structured-output pattern as the Trader / Portfolio Manager:
wrap the LLM with ``with_structured_output(EventPostmortem)`` and fall back to
free text on any failure (see ``agents/utils/structured.py``).
"""

from __future__ import annotations

from typing import Any

from tradingagents.agents.schemas import EventPostmortem, render_event_postmortem
from tradingagents.agents.utils.agent_utils import get_language_instruction
from tradingagents.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)


def _build_prompt(
    state: dict[str, Any],
    decision: str,
    raw_return: float,
    alpha_return: float,
    benchmark_name: str,
) -> str:
    """Assemble the postmortem context from a full-state dict + outcome figures."""
    inv = state.get("investment_debate_state") or {}
    risk = state.get("risk_debate_state") or {}
    trader_plan = (
        state.get("trader_investment_decision")
        or state.get("trader_investment_plan")
        or "(none)"
    )
    final_decision = decision or state.get("final_trade_decision") or "(none)"
    parts = [
        f"Trade date: {state.get('trade_date', '?')}",
        f"Ticker: {state.get('company_of_interest', '?')}",
        "",
        f"OUTCOME - raw return: {raw_return:+.1%}; alpha vs {benchmark_name}: {alpha_return:+.1%}",
        "",
        "FINAL DECISION:",
        final_decision,
        "",
        "=== ANALYST REPORTS ===",
        "Market report:",
        state.get("market_report") or "(none)",
        "Sentiment report:",
        state.get("sentiment_report") or "(none)",
        "News report:",
        state.get("news_report") or "(none)",
        "Fundamentals report:",
        state.get("fundamentals_report") or "(none)",
        "",
        "=== RESEARCH DEBATE (bull/bear) ===",
        inv.get("history") or "(none)",
        "",
        "=== RESEARCH MANAGER PLAN ===",
        state.get("investment_plan") or "(none)",
        "",
        "=== TRADER PLAN ===",
        trader_plan,
        "",
        "=== RISK DEBATE (aggressive/neutral/conservative) ===",
        risk.get("history") or "(none)",
    ]
    return "\n".join(parts)


def create_postmortem_agent(llm: Any):
    """Return a postmortem(state, decision, raw, alpha, benchmark_name) callable."""
    structured_llm = bind_structured(llm, EventPostmortem, "Postmortem Agent")

    def postmortem(
        state: dict[str, Any],
        decision: str,
        raw_return: float,
        alpha_return: float,
        benchmark_name: str = "SPY",
    ) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a post-mortem analyst reviewing an extreme trading outcome. "
                    "Attribute the realized result to what the team actually argued: "
                    "which evidence carried the decision, what was missed or underweighted, "
                    "and what concrete lesson to carry forward. Be specific and grounded "
                    "in the provided debate trace; never invent facts."
                    + NO_EXTERNAL_TOOLS
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": _build_prompt(state, decision, raw_return, alpha_return, benchmark_name),
            },
        ]
        return invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_event_postmortem,
            "Postmortem Agent",
        )

    return postmortem

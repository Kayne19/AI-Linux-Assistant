"""Prompt regression checks.

These tests are intentionally lightweight. They catch prompt drift after major
architecture changes, especially around removed subsystems like memory.
"""

from prompting.prompts import CHATBOT_SYSTEM_PROMPT, CONTEXTUALIZER_SYSTEM_PROMPT, build_classifier_system_prompt
from orchestration.routing_registry import get_allowed_labels, get_domain_map


def test_active_prompts_use_structured_memory_without_old_user_memory_path():
    assert "USER_MEMORY" not in CHATBOT_SYSTEM_PROMPT
    assert "KNOWN_SYSTEM_MEMORY" in CHATBOT_SYSTEM_PROMPT
    assert "RECENT_TURNS:" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "MEMORY:" not in CONTEXTUALIZER_SYSTEM_PROMPT

    classifier_prompt = build_classifier_system_prompt(get_allowed_labels(), get_domain_map())
    assert "system memory" in classifier_prompt.lower()
    assert "Prefer no_rag for short follow-up turns" in classifier_prompt
    assert 'recall / recap / status / "what next" within the same issue -> no_rag by default' in classifier_prompt
    assert "Do not route recap-style or environment-recap questions to domain retrieval" in classifier_prompt
    assert 'Distinguish "continue diagnosing this issue" from "retrieve new documentation"' in classifier_prompt
    assert "A short follow-up troubleshooting turn should not route to domain retrieval just because the issue topic belongs to a known domain." in classifier_prompt


def test_chatbot_prompt_allows_non_technical_conversational_replies_without_docs():
    assert "Conversational / Meta mode" in CHATBOT_SYSTEM_PROMPT
    assert "If CONTEXT_CHUNKS is empty and the request is conversational or meta, respond naturally." in CHATBOT_SYSTEM_PROMPT


def test_chatbot_prompt_uses_updated_grounded_response_shape():
    assert "Inputs to confirm:" not in CHATBOT_SYSTEM_PROMPT
    assert "Lookup mode:" in CHATBOT_SYSTEM_PROMPT
    assert "Troubleshooting mode:" in CHATBOT_SYSTEM_PROMPT
    assert "Recall / recap mode:" in CHATBOT_SYSTEM_PROMPT
    assert "ask exactly one direct question" in CHATBOT_SYSTEM_PROMPT
    assert "Ask for the most discriminating missing detail" in CHATBOT_SYSTEM_PROMPT
    assert "prefer conversation-history and structured-memory tools before fresh database retrieval" in CHATBOT_SYSTEM_PROMPT
    assert "prefer evidence that materially advances the active subtask" in CHATBOT_SYSTEM_PROMPT
    # Tool-loop style: model sets requested_evidence_goal, not evidence_gap
    assert "set an internal requested_evidence_goal before repeating database retrieval" in CHATBOT_SYSTEM_PROMPT
    assert "provide a repeat_reason instead of brute-force re-querying the same scope" in CHATBOT_SYSTEM_PROMPT
    # progress_assessment is now the model's self-evaluation mechanism
    assert "include `progress_assessment` describing whether the previous search helped" in CHATBOT_SYSTEM_PROMPT
    assert "prefer 1 to 3 tightly related follow-up questions over speculative extra retrieval" in CHATBOT_SYSTEM_PROMPT
    assert "TROUBLESHOOTING DISCIPLINE (MANDATORY)" in CHATBOT_SYSTEM_PROMPT
    assert "Do not treat the first plausible branch as proven." in CHATBOT_SYSTEM_PROMPT
    assert "Good troubleshooting is eliminating wrong branches quickly." in CHATBOT_SYSTEM_PROMPT
    assert 'If the user says "that is not it", "I already checked that", or provides contrary evidence' in CHATBOT_SYSTEM_PROMPT
    assert "Do not sound like an AI explaining its process." in CHATBOT_SYSTEM_PROMPT
    assert "Do not force every answer into the same shape" in CHATBOT_SYSTEM_PROMPT
    assert "On the first troubleshooting turn, default to diagnosis-first behavior" in CHATBOT_SYSTEM_PROMPT
    assert "do not jump to remediation before you have the evidence that distinguishes the leading branches" in CHATBOT_SYSTEM_PROMPT
    # Provider-native web search is now simply web_search tool
    assert "Local RAG remains primary. Use web_search only as a fallback" in CHATBOT_SYSTEM_PROMPT
    assert "mentally follow this order: identify the project, name the source that established that identity, then decide whether you have enough source quality for actionable guidance" in CHATBOT_SYSTEM_PROMPT
    assert "Do not guess what an unfamiliar project probably is." in CHATBOT_SYSTEM_PROMPT
    assert "confirm the canonical source first" in CHATBOT_SYSTEM_PROMPT
    assert 'Do not call a third-party blog, tutorial, or mirror "official".' in CHATBOT_SYSTEM_PROMPT
    assert "If identity or source quality is still uncertain, state the assumption briefly and ask for the repo/source instead of jumping straight to install commands." in CHATBOT_SYSTEM_PROMPT
    assert "do not fake recognition" in CHATBOT_SYSTEM_PROMPT
    assert "If web fallback identifies the software but the source quality is still weak, stop at identification and ask for the canonical repo/source before prescribing install steps." in CHATBOT_SYSTEM_PROMPT


def test_contextualizer_prompt_blocks_answer_shaped_rewrites():
    assert "The next thing to check is" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "The output must remain suitable as a retrieval query" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "Never turn the rewrite into a recommendation" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "Only replace pronouns/ellipsis with exact text copied from RECENT_TURNS." in CONTEXTUALIZER_SYSTEM_PROMPT

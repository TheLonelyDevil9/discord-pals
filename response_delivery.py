"""
Final response formatting before Discord delivery.

This module owns the boundary between model prose and Discord messages. Provider
output may arrive as one paragraph, several lines, or lightly broken grammar; the
send path should see only deliberate message parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from constants import MAX_MESSAGE_LENGTH, MAX_RESPONSE_MESSAGE_PARTS
from discord_utils import split_message


_NON_TERMINAL_ABBREVIATIONS = frozenset({
    "mr.",
    "mrs.",
    "ms.",
    "miss.",
    "mx.",
    "dr.",
    "prof.",
    "sr.",
    "jr.",
    "st.",
    "vs.",
    "etc.",
    "e.g.",
    "i.e.",
    "a.m.",
    "p.m.",
    "u.s.",
    "u.k.",
})

_SOFT_SENTENCE_BRIDGE_WORDS = frozenset({
    "a",
    "about",
    "after",
    "an",
    "and",
    "are",
    "artist",
    "as",
    "band",
    "because",
    "but",
    "by",
    "called",
    "earlier",
    "for",
    "from",
    "in",
    "is",
    "like",
    "mentioned",
    "named",
    "of",
    "on",
    "or",
    "song",
    "that",
    "the",
    "this",
    "to",
    "titled",
    "though",
    "until",
    "was",
    "were",
    "while",
    "with",
})

_SHORT_THOUGHT_STARTERS = frozenset({
    "actually",
    "also",
    "and",
    "anyway",
    "but",
    "did",
    "do",
    "does",
    "honestly",
    "i",
    "i'm",
    "i\u2019ve",
    "ive",
    "or",
    "so",
    "still",
    "that",
    "that's",
    "the",
    "then",
    "this",
    "you",
    "you're",
    "youre",
})

_INLINE_CONTINUATION_STARTERS = frozenset({
    "although",
    "and",
    "because",
    "but",
    "or",
    "since",
    "so",
    "then",
    "though",
    "unless",
    "until",
    "while",
})

_TITLE_STARTERS = frozenset({
    "dr",
    "jr",
    "miss",
    "mr",
    "mrs",
    "ms",
    "mx",
    "prof",
    "sr",
    "st",
})

_STANDALONE_OPENERS = (
    "good morning",
    "good afternoon",
    "good evening",
    "good night",
    "hi",
    "hello",
    "hey",
    "morning",
    "night",
    "okay",
    "ok",
    "sure",
    "yeah",
    "yep",
)


@dataclass(frozen=True)
class DeliveryFormatOptions:
    """Options for final response splitting."""

    max_message_length: int = MAX_MESSAGE_LENGTH
    max_parts: int = MAX_RESPONSE_MESSAGE_PARTS


def format_response_for_delivery(
    response: str,
    options: DeliveryFormatOptions | None = None,
) -> list[str]:
    """Turn one generated response into natural Discord message parts."""
    options = options or DeliveryFormatOptions()
    normalized = _normalize_response_text(response)
    if not normalized:
        return []

    logical_parts = _split_by_explicit_breaks(normalized)
    logical_parts = _expand_plain_paragraphs(logical_parts)
    logical_parts = [_repair_obvious_question_fragment(part) for part in logical_parts]
    logical_parts = _cap_logical_parts(logical_parts, options.max_parts)

    final_parts: list[str] = []
    for part in logical_parts:
        final_parts.extend(split_message(part, max_length=options.max_message_length))

    return [part.strip() for part in final_parts if part and part.strip()]


def _normalize_response_text(response: str) -> str:
    return (response or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _split_by_explicit_breaks(normalized: str) -> list[str]:
    logical_parts: list[str] = []
    for paragraph in re.split(r"\n{2,}", normalized):
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
        if not lines:
            continue

        current_part = lines[0]
        for next_line in lines[1:]:
            if _should_split_single_newline(current_part, next_line):
                logical_parts.append(_complete_split_boundary(current_part))
                current_part = next_line
            else:
                current_part = f"{current_part}\n{next_line}"
        logical_parts.append(current_part)

    return logical_parts or [normalized]


def _expand_plain_paragraphs(parts: Iterable[str]) -> list[str]:
    expanded_parts: list[str] = []
    for part in parts:
        sentence_parts = _split_plain_response_sentences(part)
        if sentence_parts:
            expanded_parts.extend(sentence_parts)
        else:
            expanded_parts.append(part)
    return expanded_parts


def _cap_logical_parts(parts: list[str], max_parts: int) -> list[str]:
    if max_parts <= 0 or len(parts) <= max_parts:
        return parts

    overflow = "\n\n".join(parts[max_parts - 1:])
    return parts[:max_parts - 1] + [overflow]


def _repair_obvious_question_fragment(part: str) -> str:
    """Repair short question-shaped fragments that providers punctuate as statements."""
    part = (part or "").strip()
    if not part or "\n" in part:
        return part

    text = _repair_what_about_you_fragment(part)
    text = _repair_question_stem_period(text)
    return text


def _repair_what_about_you_fragment(part: str) -> str:
    match = re.fullmatch(r"(?i)(what\s+about\s+you),\s+(after|before|during|with|for|on|in|at)\s+(.+?)\.", part)
    if not match:
        return part

    return f"{match.group(1)} {match.group(2)} {match.group(3)}?"


def _repair_question_stem_period(part: str) -> str:
    if not part.endswith("."):
        return part
    if not re.match(
        r"(?i)^(?:what\s+about|how\s+about|what\s+are|what\s+is|what\s+was|"
        r"where\s+are|where\s+is|where\s+was|when\s+are|when\s+is|when\s+was|"
        r"why\s+are|why\s+is|why\s+was|how\s+are|how\s+is|how\s+was|"
        r"do\s+you|did\s+you|does\s+that|are\s+you|is\s+that|"
        r"will\s+you|would\s+you|could\s+you|can\s+you|should\s+we)\b",
        part,
    ):
        return part

    return f"{part[:-1]}?"


def _starts_like_new_thought(text: str) -> bool:
    text = (text or "").lstrip()
    if not text:
        return False

    return (
        text[0].isupper()
        or text[0].isdigit()
        or text.startswith(("@", "<@", "*", "-", "\u2013", "\u2014", "\u2022", "\"", "'", "(", "["))
    )


def _ends_with_nonterminal_abbreviation(text: str) -> bool:
    trimmed = (text or "").strip()
    if not trimmed or "." not in trimmed:
        return False

    trimmed = trimmed.rstrip(")]}\"'>")
    match = re.search(r"([A-Za-z][A-Za-z.]*)$", trimmed)
    if not match:
        return False

    token = match.group(1).lower()
    return (
        token in _NON_TERMINAL_ABBREVIATIONS
        or re.fullmatch(r"(?:[A-Za-z]\.){2,}", token) is not None
        or re.fullmatch(r"[A-Za-z]\.", token) is not None
    )


def _should_split_single_newline(previous_line: str, next_line: str) -> bool:
    previous_line = (previous_line or "").strip()
    next_line = (next_line or "").strip()
    if not previous_line or not next_line:
        return False
    if not _starts_like_new_thought(next_line):
        return False
    if _ends_with_nonterminal_abbreviation(previous_line):
        return False
    if previous_line[-1] in ",:;/-([{":
        return False
    return True


def _complete_split_boundary(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text

    body = text.rstrip(")]}'\"\u203a\u00bb")
    if not body or body[-1] in ".!?\u2026":
        return text
    if body[-1].isalnum():
        return f"{text}."
    return text


def _looks_like_missing_sentence_boundary(previous_text: str, next_text: str) -> bool:
    previous_text = (previous_text or "").strip()
    next_text = (next_text or "").strip()
    if not previous_text or not next_text:
        return False
    if not _starts_like_new_thought(next_text):
        return False
    if previous_text[-1] in ",:;/-([{":
        return False
    if _ends_with_nonterminal_abbreviation(previous_text):
        return False

    previous_word_match = re.search(r"([A-Za-z][A-Za-z'-]*)\s*$", previous_text)
    if not previous_word_match:
        return False

    previous_word = previous_word_match.group(1).lower().strip("'")
    if previous_word in _SOFT_SENTENCE_BRIDGE_WORDS:
        return False
    if previous_word in {"anyway", "though"}:
        return False

    next_word = _first_word(next_text)
    if next_word in _TITLE_STARTERS:
        return False

    next_word_match = re.match(r"([A-Z][A-Za-z'\u2019-]+)\b", next_text)
    if not next_word_match:
        return False

    return True


def _split_plain_response_sentences(response: str) -> list[str]:
    normalized = (response or "").strip()
    if not normalized or "\n" in normalized:
        return []

    sentences: list[str] = []
    start = 0
    boundaries: list[tuple[int, int, str]] = []
    repaired_soft_boundary = False
    terminal_count = 0

    for match in re.finditer(r"[.!?\u2026]+(?=\s+)", normalized):
        next_start = match.end()
        while next_start < len(normalized) and normalized[next_start].isspace():
            next_start += 1
        boundaries.append((match.end(), next_start, "terminal"))
        terminal_count += 1

    for match in re.finditer(
        r"(?<=[a-z0-9\)\]\"'])\s+(?=(?:I['\u2019][A-Za-z]+|[A-Z][a-z][A-Za-z'\u2019\-]*)\b)",
        normalized,
    ):
        boundaries.append((match.start(), match.end(), "soft"))

    for boundary_end, next_start, boundary_type in sorted(boundaries, key=lambda item: item[1]):
        if boundary_end <= start or next_start <= start:
            continue

        candidate = normalized[start:boundary_end].strip()
        if not candidate:
            continue

        next_segment = normalized[next_start:]
        if not next_segment:
            continue
        if not _starts_like_new_thought(next_segment):
            continue

        if boundary_type == "terminal":
            boundary_text = normalized[boundary_end - 1:boundary_end]
            if boundary_text == "." and _is_nonterminal_period_boundary(candidate, next_segment):
                continue
        elif not _looks_like_missing_sentence_boundary(candidate, next_segment):
            continue

        if boundary_type == "soft":
            candidate = _complete_split_boundary(candidate)
            repaired_soft_boundary = True
        sentences.append(candidate)
        start = next_start

    remainder = normalized[start:].strip()
    if remainder:
        sentences.append(remainder)
    if len(sentences) < 2:
        return []

    return _group_sentence_bursts(sentences, repaired_soft_boundary, terminal_count)


def _group_sentence_bursts(
    sentences: list[str],
    repaired_soft_boundary: bool,
    terminal_count: int,
) -> list[str]:
    if _should_keep_compact_terminal_response_together(
        sentences,
        repaired_soft_boundary,
        terminal_count,
    ):
        return []

    if len(sentences) == 2 and _should_keep_two_sentence_burst_together(
        sentences[0],
        sentences[1],
        repaired_soft_boundary,
        terminal_count,
    ):
        return [] if not repaired_soft_boundary else [" ".join(sentences)]

    grouped: list[str] = []
    current_sentences = [sentences[0]]
    for sentence in sentences[1:]:
        current = " ".join(current_sentences).strip()
        candidate = f"{current} {sentence}".strip()
        if (
            len(current_sentences) >= 2
            or _should_emit_before_next_sentence(current, sentence, candidate, terminal_count)
        ):
            grouped.append(current)
            current_sentences = [sentence]
        else:
            current_sentences.append(sentence)
    grouped.append(" ".join(current_sentences).strip())

    return grouped if len(grouped) > 1 or repaired_soft_boundary else []


def _is_nonterminal_period_boundary(candidate: str, next_segment: str) -> bool:
    if not _ends_with_nonterminal_abbreviation(candidate):
        return False

    next_word = _first_word(next_segment)
    if not next_word:
        return True

    candidate_token = _last_word_token(candidate).lower()
    if candidate_token.rstrip(".") in _TITLE_STARTERS:
        return True

    return next_word in {"jr", "sr"} or len(next_word) == 1


def _last_word_token(text: str) -> str:
    match = re.search(r"([A-Za-z][A-Za-z.]*)\s*$", (text or "").strip().rstrip(")]}\"'>"))
    return match.group(1) if match else ""


def _should_keep_compact_terminal_response_together(
    sentences: list[str],
    repaired_soft_boundary: bool,
    terminal_count: int,
) -> bool:
    if repaired_soft_boundary:
        return False
    if len(sentences) <= 1 or len(sentences) > 3:
        return False
    if terminal_count != len(sentences) - 1:
        return False
    if any(len(sentence) > 72 for sentence in sentences):
        return False
    if len(" ".join(sentences)) > 140:
        return False
    if _should_split_after_short_sentence(sentences[0], sentences[1], repaired_soft_boundary=False):
        return False
    return True


def _should_keep_two_sentence_burst_together(
    first: str,
    second: str,
    repaired_soft_boundary: bool,
    terminal_count: int,
) -> bool:
    if _should_split_after_short_sentence(first, second, repaired_soft_boundary=repaired_soft_boundary):
        return False
    if terminal_count != 1:
        return False
    if len(first) >= 70 or len(second) >= 70:
        return False
    return len(f"{first} {second}") <= 110


def _should_emit_before_next_sentence(
    current: str,
    next_sentence: str,
    candidate: str,
    terminal_count: int,
) -> bool:
    if len(current) >= 90 or len(candidate) > 120:
        return True
    if len(current) <= 58 and _should_split_after_short_sentence(
        current,
        next_sentence,
        repaired_soft_boundary=terminal_count == 0,
    ):
        return True
    return False


def _starts_with_short_thought_starter(text: str) -> bool:
    match = re.match(r"([A-Za-z][A-Za-z'\u2019]*)\b", (text or "").strip())
    if not match:
        return False
    starter = match.group(1).lower().replace("\u2019", "'")
    return starter in _SHORT_THOUGHT_STARTERS


def _should_split_after_short_sentence(
    current: str,
    next_sentence: str,
    *,
    repaired_soft_boundary: bool,
) -> bool:
    current = (current or "").strip()
    next_sentence = (next_sentence or "").strip()
    if not current or not next_sentence:
        return False
    if _starts_with_inline_continuation(next_sentence):
        return False
    if _is_standalone_opener(current):
        return True
    if repaired_soft_boundary:
        return True
    return current.endswith(".")


def _starts_with_inline_continuation(text: str) -> bool:
    return _first_word(text) in _INLINE_CONTINUATION_STARTERS


def _is_standalone_opener(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    normalized = normalized.strip(" .!?\u2026,:;-")
    return any(
        normalized == opener or normalized.startswith(f"{opener} ")
        for opener in _STANDALONE_OPENERS
    )


def _first_word(text: str) -> str:
    match = re.match(r"([A-Za-z][A-Za-z'\u2019]*)\b", (text or "").strip())
    if not match:
        return ""
    return match.group(1).lower().replace("\u2019", "'")

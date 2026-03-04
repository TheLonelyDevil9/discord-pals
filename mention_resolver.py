"""
Discord Pals - Unified Mention Resolver
Deterministic mention resolution for users and bots across context + guild scope.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import logger as log


TAG_VERBS_RE = r"(?:tag|mention|ping|summon|notify|call|bring|get)"

STOPWORDS = {
    "please", "pls", "pleasee", "me", "you", "them", "him", "her", "someone", "anyone",
    "tag", "mention", "ping", "summon", "notify", "call", "bring", "get", "can", "could",
    "would", "and", "or", "to", "the", "a", "an", "my", "your", "here", "there", "now"
}

RELATION_GROUPS = [
    {"sis", "sister", "bro", "brother", "sibling"},
    {"creator", "owner", "maker", "developer", "dev", "author"},
]

_CACHE_TTL_SECONDS = 120
_guild_member_cache: Dict[Tuple[int, bool], dict] = {}


@dataclass
class MentionRecord:
    user_id: int
    is_bot: bool
    aliases: set[str] = field(default_factory=set)
    member: object | None = None
    context_hint: float = 0.0


@dataclass
class MentionResolutionResult:
    text: str
    resolved_ids: List[int] = field(default_factory=list)
    decisions: List[dict] = field(default_factory=list)
    dropped_fragments: int = 0


def _normalize_alias(value: str) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.replace("\u200b", "").replace("\ufeff", "").replace("\u2060", "")
    cleaned = re.sub(r"\s+", " ", cleaned.strip().lstrip("@"))
    return cleaned.lower()


def _canonical_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_alias(value))


def _alias_variants(value: str) -> set[str]:
    normalized = _normalize_alias(value)
    if not normalized:
        return set()

    variants = {normalized}
    canonical = _canonical_token(normalized)
    if len(canonical) >= 3:
        variants.add(canonical)

    for part in re.split(r"[\s._-]+", normalized):
        if len(part) >= 3 and part not in STOPWORDS:
            variants.add(part)
            part_canonical = _canonical_token(part)
            if len(part_canonical) >= 3:
                variants.add(part_canonical)
    return variants


def _member_display_name(member) -> str:
    for attr in ("display_name", "global_name", "name"):
        value = getattr(member, attr, None)
        if value:
            return str(value)
    return ""


def _member_is_bot(member) -> bool:
    return bool(getattr(member, "bot", False))


def _build_member_aliases(member) -> set[str]:
    names = {
        _member_display_name(member),
        getattr(member, "name", None),
        getattr(member, "global_name", None),
        getattr(member, "nick", None),
    }
    aliases = set()
    for name in names:
        if not name:
            continue
        aliases.update(_alias_variants(str(name)))
    return aliases


def _make_record_from_member(member) -> Optional[MentionRecord]:
    try:
        user_id = int(getattr(member, "id", 0))
    except (TypeError, ValueError):
        return None
    if user_id <= 0:
        return None
    return MentionRecord(
        user_id=user_id,
        is_bot=_member_is_bot(member),
        aliases=_build_member_aliases(member),
        member=member,
        context_hint=0.0,
    )


def _clone_records(records: Dict[int, MentionRecord]) -> Dict[int, MentionRecord]:
    cloned = {}
    for user_id, rec in records.items():
        cloned[user_id] = MentionRecord(
            user_id=rec.user_id,
            is_bot=rec.is_bot,
            aliases=set(rec.aliases),
            member=rec.member,
            context_hint=rec.context_hint,
        )
    return cloned


def _get_guild_records(guild, include_bots: bool) -> Dict[int, MentionRecord]:
    if not guild:
        return {}
    try:
        guild_id = int(getattr(guild, "id", 0))
    except (TypeError, ValueError):
        return {}
    cache_key = (guild_id, include_bots)
    now = time.time()
    cached = _guild_member_cache.get(cache_key)
    if cached and (now - cached.get("ts", 0)) < _CACHE_TTL_SECONDS:
        return _clone_records(cached.get("records", {}))

    records: Dict[int, MentionRecord] = {}
    for member in list(getattr(guild, "members", []) or []):
        rec = _make_record_from_member(member)
        if not rec:
            continue
        if not include_bots and rec.is_bot:
            continue
        records[rec.user_id] = rec

    _guild_member_cache[cache_key] = {"ts": now, "records": records}
    return _clone_records(records)


def _merge_context_records(records: Dict[int, MentionRecord], envelope: Optional[dict], include_bots: bool):
    if not envelope:
        return

    for candidate in envelope.get("mention_candidates", []) or []:
        try:
            user_id = int(candidate.get("user_id"))
        except (TypeError, ValueError):
            continue
        if user_id <= 0:
            continue

        handle = str(candidate.get("handle") or "").strip().lower()
        inferred_is_bot = handle.startswith("@b_") or str(candidate.get("priority") or "").lower() == "bot"
        if inferred_is_bot and not include_bots:
            continue

        rec = records.get(user_id)
        if not rec:
            rec = MentionRecord(user_id=user_id, is_bot=inferred_is_bot, aliases=set(), context_hint=0.0)
            records[user_id] = rec
        rec.context_hint = max(rec.context_hint, 1.0)
        for alias in candidate.get("aliases", []) or []:
            if isinstance(alias, str):
                rec.aliases.update(_alias_variants(alias))

    for participant in envelope.get("participants", []) or []:
        try:
            user_id = int(participant.get("user_id"))
        except (TypeError, ValueError):
            continue
        if user_id <= 0:
            continue

        inferred_is_bot = bool(participant.get("is_bot"))
        if inferred_is_bot and not include_bots:
            continue

        rec = records.get(user_id)
        if not rec:
            rec = MentionRecord(user_id=user_id, is_bot=inferred_is_bot, aliases=set(), context_hint=0.0)
            records[user_id] = rec

        rec.context_hint = max(rec.context_hint, 0.7)
        for alias_value in (
            participant.get("display_name"),
            participant.get("username"),
            participant.get("mention_handle"),
        ):
            if isinstance(alias_value, str):
                rec.aliases.update(_alias_variants(alias_value))


def _extract_recent_author_ids(envelope: Optional[dict]) -> set[int]:
    ids = set()
    if not envelope:
        return ids
    for msg in envelope.get("recent_messages", []) or []:
        try:
            author_id = int(msg.get("author_id"))
        except (TypeError, ValueError):
            continue
        if author_id > 0:
            ids.add(author_id)
    return ids


def _extract_plaintext_mentions(text: str) -> List[str]:
    if not text:
        return []
    items = []
    seen = set()
    pattern = re.compile(r"(?<!<)@([A-Za-z0-9][A-Za-z0-9 _.\'-]{1,63})")
    for match in pattern.finditer(text):
        value = re.sub(r"\s+", " ", match.group(1)).strip().rstrip(" >.,!?;:")
        if not value:
            continue
        normalized = _normalize_alias(value)
        if not normalized:
            continue
        if re.fullmatch(r"[ub]_\d{3,22}", normalized):
            continue
        if normalized not in seen:
            seen.add(normalized)
            items.append(value)
    return items


def _extract_request_terms(request_content: str) -> List[str]:
    if not request_content:
        return []
    text = str(request_content)
    has_intent = bool(re.search(rf"\b{TAG_VERBS_RE}\b", text, flags=re.IGNORECASE))
    explicit_at = bool(re.search(r"(?<!<)@[A-Za-z0-9]", text))
    if not has_intent and not explicit_at:
        return []

    terms = []
    seen = set()

    segments = re.findall(rf"\b{TAG_VERBS_RE}\b([^.!?\n\r]*)", text, flags=re.IGNORECASE)
    if not segments:
        segments = [text]

    for segment in segments:
        segment_tokens = []
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.\-']{1,31}", segment):
            t = token.strip().lower()
            if not t or t in STOPWORDS or len(t) < 3:
                continue
            if t not in seen:
                seen.add(t)
                terms.append(t)
                segment_tokens.append(t)

            canonical = _canonical_token(t)
            if canonical and canonical not in seen and len(canonical) >= 3 and canonical not in STOPWORDS:
                seen.add(canonical)
                terms.append(canonical)

        for i in range(len(segment_tokens)):
            for size in (2, 3):
                if i + size > len(segment_tokens):
                    continue
                phrase = " ".join(segment_tokens[i:i + size]).strip()
                if len(phrase) < 3 or phrase in seen:
                    continue
                seen.add(phrase)
                terms.append(phrase)
                phrase_canonical = _canonical_token(phrase)
                if phrase_canonical and len(phrase_canonical) >= 3 and phrase_canonical not in seen:
                    seen.add(phrase_canonical)
                    terms.append(phrase_canonical)

    for token in re.findall(r"(?<!<)@([A-Za-z0-9][A-Za-z0-9_.\-']{1,63})", text):
        t = token.strip().lower()
        if t and len(t) >= 3 and t not in STOPWORDS and t not in seen:
            seen.add(t)
            terms.append(t)
        canonical = _canonical_token(t)
        if canonical and len(canonical) >= 3 and canonical not in STOPWORDS and canonical not in seen:
            seen.add(canonical)
            terms.append(canonical)

    return terms[:24]


def _detect_relation_terms(text: str) -> set[str]:
    lowered = (text or "").lower()
    selected = set()
    for group in RELATION_GROUPS:
        if any(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered) for term in group):
            selected.update(group)
    return selected


def _alias_near_relation_terms(aliases: set[str], relation_terms: set[str], corpus: str) -> bool:
    if not aliases or not relation_terms or not corpus:
        return False
    lowered = corpus.lower()
    for alias in aliases:
        if len(alias) < 3:
            continue
        for match in re.finditer(re.escape(alias), lowered):
            start = max(0, match.start() - 96)
            end = min(len(lowered), match.end() + 96)
            window = lowered[start:end]
            if any(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", window) for term in relation_terms):
                return True
    return False


async def _query_members_for_terms(guild, terms: List[str], include_bots: bool) -> List[object]:
    if not guild or not terms:
        return []

    out = []
    seen_ids = set()
    seen_queries = set()
    for term in terms[:20]:
        query = re.sub(r"[^A-Za-z0-9 ._-]+", "", term).strip()
        if len(query) < 3:
            continue
        if query in seen_queries:
            continue
        seen_queries.add(query)

        try:
            queried = await guild.query_members(query=query[:100], limit=100, cache=True)
        except Exception:
            queried = []

        if not queried and " " in query:
            first_token = query.split()[0].strip()
            if len(first_token) >= 3:
                try:
                    queried = await guild.query_members(query=first_token[:100], limit=100, cache=True)
                except Exception:
                    queried = []

        for member in queried or []:
            rec = _make_record_from_member(member)
            if not rec:
                continue
            if not include_bots and rec.is_bot:
                continue
            if rec.user_id in seen_ids:
                continue
            seen_ids.add(rec.user_id)
            out.append(member)

    # Fallback for stale cache / offline users: do a bounded fetch pass only
    # when query_members found nothing.
    if out:
        return out

    if not hasattr(guild, "fetch_members"):
        return out

    normalized_terms = [_normalize_alias(t) for t in terms if _normalize_alias(t)]
    canonical_terms = [_canonical_token(t) for t in normalized_terms if _canonical_token(t)]
    if not normalized_terms:
        return out

    try:
        fetched_count = 0
        async for member in guild.fetch_members(limit=1200):
            fetched_count += 1
            rec = _make_record_from_member(member)
            if not rec:
                continue
            if not include_bots and rec.is_bot:
                continue
            if rec.user_id in seen_ids:
                continue

            aliases = rec.aliases
            matched = False
            for term in normalized_terms:
                if len(term) < 3:
                    continue
                if any(alias == term or alias.startswith(term) or term in alias for alias in aliases):
                    matched = True
                    break
            if not matched:
                for term in canonical_terms:
                    if len(term) < 3:
                        continue
                    if any(alias == term or alias.startswith(term) or term in alias for alias in aliases):
                        matched = True
                        break

            if matched:
                seen_ids.add(rec.user_id)
                out.append(member)
    except Exception:
        pass
    return out


def _merge_members_into_records(records: Dict[int, MentionRecord], members: List[object], include_bots: bool):
    for member in members:
        rec = _make_record_from_member(member)
        if not rec:
            continue
        if not include_bots and rec.is_bot:
            continue
        existing = records.get(rec.user_id)
        if not existing:
            records[rec.user_id] = rec
            continue
        existing.aliases.update(rec.aliases)
        if existing.member is None:
            existing.member = member
        existing.is_bot = existing.is_bot or rec.is_bot


def _score_record_for_term(
    term: str,
    rec: MentionRecord,
    recent_author_ids: set[int],
    explicit_at: bool,
    relation_terms: set[str],
    relation_corpus: str,
) -> Tuple[float, int, List[str]]:
    term_norm = _normalize_alias(term)
    term_can = _canonical_token(term_norm)
    if not term_norm and not term_can:
        return 0.0, 0, []

    score = 0.0
    exact_rank = 0
    reasons = []

    aliases = rec.aliases
    if term_norm in aliases:
        score = 12.0
        exact_rank = max(exact_rank, 5)
        reasons.append("exact")
    elif term_can and term_can in aliases:
        score = max(score, 11.0)
        exact_rank = max(exact_rank, 4)
        reasons.append("canonical_exact")

    for alias in aliases:
        if len(term_norm) >= 3 and alias.startswith(term_norm):
            score = max(score, 9.0)
            exact_rank = max(exact_rank, 3)
            reasons.append("prefix")
        if term_can and len(term_can) >= 3 and alias.startswith(term_can):
            score = max(score, 8.6)
            exact_rank = max(exact_rank, 3)
            reasons.append("canonical_prefix")
        if len(term_norm) >= 4 and term_norm in alias:
            score = max(score, 7.2)
            exact_rank = max(exact_rank, 2)
            reasons.append("substring")
        if term_can and len(term_can) >= 4 and term_can in alias:
            score = max(score, 6.8)
            exact_rank = max(exact_rank, 2)
            reasons.append("canonical_substring")

    term_tokens = {t for t in re.split(r"[\s._-]+", term_norm) if len(t) >= 3}
    alias_tokens = set()
    for alias in aliases:
        alias_tokens.update({t for t in re.split(r"[\s._-]+", alias) if len(t) >= 3})
    if term_tokens and alias_tokens:
        overlap = len(term_tokens & alias_tokens)
        if overlap > 0:
            score = max(score, 5.0 + min(2.0, overlap * 0.8))
            exact_rank = max(exact_rank, 1)
            reasons.append("token_overlap")

    score += rec.context_hint * 1.3
    if rec.context_hint > 0:
        reasons.append("context_hint")
    if rec.user_id in recent_author_ids:
        score += 0.5
        reasons.append("recent_author")
    if explicit_at:
        score += 0.8
        reasons.append("explicit_at")
    if relation_terms and relation_corpus and _alias_near_relation_terms(aliases, relation_terms, relation_corpus):
        score += 1.2
        reasons.append("relation_hint")

    return score, exact_rank, reasons


def _select_best_record(
    term: str,
    records: Dict[int, MentionRecord],
    include_bots: bool,
    recent_author_ids: set[int],
    explicit_at: bool,
    relation_terms: set[str],
    relation_corpus: str,
    min_score: float,
    ambiguity_policy: str,
) -> Tuple[Optional[int], float, List[str]]:
    scored = []
    for rec in records.values():
        if rec.is_bot and not include_bots:
            continue
        score, exact_rank, reasons = _score_record_for_term(
            term, rec, recent_author_ids, explicit_at, relation_terms, relation_corpus
        )
        if score <= 0:
            continue
        scored.append((score, exact_rank, rec.user_id, reasons))

    if not scored:
        return None, 0.0, []

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    best_score, _best_rank, best_id, best_reasons = scored[0]
    if best_score < max(0.1, min_score):
        return None, best_score, best_reasons

    if ambiguity_policy in {"no_tag", "clarify"} and len(scored) > 1:
        second_score = scored[1][0]
        if abs(best_score - second_score) < 0.75:
            return None, best_score, ["ambiguous"]

    return best_id, best_score, best_reasons


def _replace_plain_mentions(text: str, candidate_to_id: Dict[str, int]) -> str:
    resolved = text
    for candidate in sorted(candidate_to_id.keys(), key=len, reverse=True):
        mention = f"<@{candidate_to_id[candidate]}>"
        pattern = re.compile(r"(?<!<)@" + re.escape(candidate) + r"(?=[\W]|$)", re.IGNORECASE)
        resolved = pattern.sub(mention, resolved)
    return resolved


def _strip_unresolved_plain_mentions(text: str, unresolved_candidates: List[str]) -> str:
    """Remove leading @ from unresolved plaintext mentions to avoid fake pings."""
    if not text or not unresolved_candidates:
        return text
    resolved = text
    for candidate in sorted(set(unresolved_candidates), key=len, reverse=True):
        pattern = re.compile(r"(?<!<)@" + re.escape(candidate) + r"(?=[\W]|$)", re.IGNORECASE)
        resolved = pattern.sub(candidate, resolved)
    return resolved


def cleanup_malformed_mentions(text: str) -> str:
    if not text:
        return text

    placeholders = {}

    def _protect_valid_user_mention(match: re.Match) -> str:
        token = f"__VALID_USER_MENTION_{len(placeholders)}__"
        placeholders[token] = match.group(0)
        return token

    cleaned = re.sub(r"<@!?(\d{15,22})>", _protect_valid_user_mention, text)
    cleaned = re.sub(r"<@&\d{15,22}>", "", cleaned)
    cleaned = re.sub(r"<#\d{15,22}>", "", cleaned)

    cleaned = re.sub(
        r"<@!?\s*([^>\n\r]{1,80})>",
        lambda m: "@" + re.sub(r"\s+", " ", m.group(1)).strip(),
        cleaned
    )
    cleaned = re.sub(
        r"<@!?\s*([A-Za-z][A-Za-z0-9 _.\'-]{1,63})(?=[,!?;:.]|\s|$)",
        lambda m: "@" + m.group(1).strip(),
        cleaned
    )
    cleaned = re.sub(r"<@!?", "", cleaned)
    cleaned = re.sub(r"@([A-Za-z][A-Za-z0-9 _.\'-]{0,63})>", r"@\1", cleaned)
    cleaned = re.sub(r"(?<!<)@{2,}", "@", cleaned)
    cleaned = re.sub(r"(^|[\s(\[{])@(?=[\s)\]}.,!?;:]|$)", r"\1", cleaned)
    cleaned = re.sub(r"@\s*>", "", cleaned)

    for token, original in placeholders.items():
        cleaned = cleaned.replace(token, original)
    return cleaned.strip()


async def resolve_mentions_unified(
    response: str,
    request_content: str = "",
    context_envelope: Optional[dict] = None,
    guild=None,
    include_bots: bool = True,
    ambiguity_policy: str = "best_match",
    min_score: float = 5.0,
    relation_corpus: str = "",
) -> MentionResolutionResult:
    if not response:
        return MentionResolutionResult(text=response or "")

    envelope = context_envelope or {}
    records = _get_guild_records(guild, include_bots=include_bots)
    _merge_context_records(records, envelope, include_bots=include_bots)

    request_terms = _extract_request_terms(request_content or "")
    response_mentions = _extract_plaintext_mentions(response)
    relation_terms = _detect_relation_terms(request_content or "")
    recent_author_ids = _extract_recent_author_ids(envelope)

    query_terms = []
    seen_query = set()
    for item in request_terms + response_mentions:
        norm = _normalize_alias(item)
        if not norm or norm in seen_query:
            continue
        seen_query.add(norm)
        query_terms.append(item)

    queried_members = await _query_members_for_terms(guild, query_terms, include_bots=include_bots)
    _merge_members_into_records(records, queried_members, include_bots=include_bots)

    decisions = []
    requested_ids = []
    seen_ids = set()

    request_lower = (request_content or "").lower()
    for term in request_terms:
        explicit_at = bool(re.search(rf"(?<!<)@{re.escape(term)}(?=[\W]|$)", request_lower, re.IGNORECASE))
        selected_id, score, reasons = _select_best_record(
            term=term,
            records=records,
            include_bots=include_bots,
            recent_author_ids=recent_author_ids,
            explicit_at=explicit_at,
            relation_terms=relation_terms,
            relation_corpus=relation_corpus or "",
            min_score=min_score,
            ambiguity_policy=ambiguity_policy,
        )
        decisions.append({
            "stage": "request_term",
            "term": term,
            "selected_id": selected_id,
            "score": round(score, 3),
            "reasons": reasons,
        })
        if selected_id and selected_id not in seen_ids:
            seen_ids.add(selected_id)
            requested_ids.append(selected_id)

    candidate_to_id = {}
    unresolved_candidates = []
    resolved_ids = set(requested_ids)
    for candidate in response_mentions:
        selected_id, score, reasons = _select_best_record(
            term=candidate,
            records=records,
            include_bots=include_bots,
            recent_author_ids=recent_author_ids,
            explicit_at=True,
            relation_terms=relation_terms,
            relation_corpus=relation_corpus or "",
            min_score=min_score,
            ambiguity_policy=ambiguity_policy,
        )
        decisions.append({
            "stage": "response_plain_mention",
            "term": candidate,
            "selected_id": selected_id,
            "score": round(score, 3),
            "reasons": reasons,
        })
        if selected_id:
            candidate_to_id[candidate] = selected_id
            resolved_ids.add(selected_id)
        else:
            unresolved_candidates.append(candidate)

    rewritten = _replace_plain_mentions(response, candidate_to_id)
    rewritten = _strip_unresolved_plain_mentions(rewritten, unresolved_candidates)
    existing_ids = {int(mid) for mid in re.findall(r"<@!?(\d{15,22})>", rewritten)}
    prefixes = [f"<@{uid}>" for uid in requested_ids if uid not in existing_ids]
    if prefixes:
        rewritten = f"{' '.join(prefixes)} {rewritten}".strip()

    cleaned = cleanup_malformed_mentions(rewritten)
    dropped_fragments = 0
    if cleaned != rewritten:
        # Approximate count: number of removed/normalized dangling @ markers.
        before = len(re.findall(r"(?<!<)@(?=[\s)\]}.,!?;:]|$)", rewritten))
        after = len(re.findall(r"(?<!<)@(?=[\s)\]}.,!?;:]|$)", cleaned))
        dropped_fragments = max(0, before - after)

    if resolved_ids or dropped_fragments:
        log.debug(
            f"[MENTION-RESOLVER] resolved_ids={sorted(resolved_ids)} "
            f"dropped_fragments={dropped_fragments} decisions={decisions[:10]}"
        )

    return MentionResolutionResult(
        text=cleaned,
        resolved_ids=sorted(resolved_ids),
        decisions=decisions,
        dropped_fragments=dropped_fragments,
    )

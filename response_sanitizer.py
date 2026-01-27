"""
Discord Pals - Response Sanitizer
Cleans AI-generated responses by removing thinking tags, name prefixes, and artifacts.
"""

import re
import functools

# =============================================================================
# PRE-COMPILED REGEX PATTERNS
# =============================================================================

# Standard thinking tags
RE_THINKING_OPEN = re.compile(r'<thinking>.*?</thinking>', re.DOTALL | re.IGNORECASE)
RE_THINK_OPEN = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)
RE_GLM_BOX = re.compile(r'<\|begin_of_box\|>.*?<\|end_of_box\|>', re.DOTALL)

# Partial/orphaned tags
RE_THINKING_PARTIAL_START = re.compile(r'^.*?</thinking>', re.DOTALL | re.IGNORECASE)
RE_THINK_PARTIAL_START = re.compile(r'^.*?</think>', re.DOTALL | re.IGNORECASE)
RE_GLM_PARTIAL_START = re.compile(r'^.*?<\|end_of_box\|>', re.DOTALL)
RE_THINKING_ORPHAN_END = re.compile(r'<thinking>.*$', re.DOTALL | re.IGNORECASE)
RE_THINK_ORPHAN_END = re.compile(r'<think>.*$', re.DOTALL | re.IGNORECASE)
RE_GLM_ORPHAN_END = re.compile(r'<\|begin_of_box\|>.*$', re.DOTALL)

# Name/prefix patterns
RE_NAME_PREFIX = re.compile(r'^\s*\[[^\]]+\]:\s*', re.MULTILINE)
RE_REPLY_PREFIX = re.compile(r'^\s*\(replying to [^)]+\)\s*', re.IGNORECASE | re.MULTILINE)
RE_RE_PREFIX = re.compile(r'^\s*\(RE:?\s+[^)]+\)\s*', re.IGNORECASE | re.MULTILINE)

# Em-dash patterns
RE_EM_DASH_BETWEEN_WORDS = re.compile(r'(\w)\s*—\s*(\w)')
RE_EM_DASH_END = re.compile(r'—\s*$')

# Additional reasoning formats (local LLMs)
RE_REASONING_TAG = re.compile(r'<reasoning>.*?</reasoning>', re.DOTALL | re.IGNORECASE)
RE_REASON_TAG = re.compile(r'<reason>.*?</reason>', re.DOTALL | re.IGNORECASE)
RE_BRACKET_THINKING = re.compile(r'\[thinking\].*?\[/thinking\]', re.DOTALL | re.IGNORECASE)
RE_BRACKET_THINK = re.compile(r'\[think\].*?\[/think\]', re.DOTALL | re.IGNORECASE)
RE_MARKDOWN_THINKING = re.compile(r'\*\*(?:Thinking|Reasoning|Internal|Analysis):\*\*.*?(?=\n\n|\Z)', re.DOTALL | re.IGNORECASE)
RE_REASONING_PREFIX = re.compile(r'^(?:Thinking:|Reasoning:|Let me think|I need to think|First, I should).*$', re.MULTILINE | re.IGNORECASE)
RE_OUTPUT_WRAPPER = re.compile(r'<output>(.*?)</output>', re.DOTALL | re.IGNORECASE)
RE_RESPONSE_WRAPPER = re.compile(r'<response>(.*?)</response>', re.DOTALL | re.IGNORECASE)
RE_MULTIPLE_NEWLINES = re.compile(r'\n{3,}')

# GLM 4.7 plain-text reasoning
RE_GLM_THINK_START = re.compile(r'^think:', re.IGNORECASE)
RE_GLM_ACTUAL_OUTPUT = re.compile(r'(?:Actual\s*output|Final\s*Polish)\s*:\s*["\']?(.+?)["\']?\s*$', re.DOTALL | re.IGNORECASE)
RE_GLM_QUOTED_OUTPUT = re.compile(r'^["\'](.+)["\']$', re.DOTALL)
# Additional GLM reasoning patterns (from contributor)
RE_GLM_REASONING_PREFIX = re.compile(r'^(?:Reasoning:|Analysis:|Thinking:|Internal:)\s*$', re.MULTILINE | re.IGNORECASE)
RE_GLM_THINKING_PIPE = re.compile(r'\|\|thinking\|\|.*?\|\|/?end\|\|', re.DOTALL | re.IGNORECASE)
RE_GLM_MULTILINE_THINK = re.compile(r'(?:\n|^)(?:Think|Reasoning|Analysis|Internal)[:\s].*?(?=\n\n|\Z)', re.DOTALL | re.IGNORECASE)

# Internal processing labels
RE_INTERNAL_LABELS = re.compile(r'^(?:message\s+)?(?:duplication\s+)?glitch:?\s*', re.MULTILINE | re.IGNORECASE)
RE_READABLE_VERSION = re.compile(r'^readable\s+version:?\s*', re.MULTILINE | re.IGNORECASE)
RE_INTERNAL_NOTE = re.compile(r'^\s*\[(?:internal|note|debug|processing)\].*$', re.MULTILINE | re.IGNORECASE)
RE_STEP_LABELS = re.compile(r'^(?:step\s*\d+|phase\s*\d+|stage\s*\d+):.*$', re.MULTILINE | re.IGNORECASE)

# Deepseek/Qwen style
RE_DEEPSEEK_THINK = re.compile(r'<\|think\|>.*?<\|/think\|>', re.DOTALL)
RE_QWEN_THOUGHT = re.compile(r'<\|startofthought\|>.*?<\|endofthought\|>', re.DOTALL)
RE_INTERNAL_MONOLOGUE = re.compile(r'\[Internal:.*?\]', re.DOTALL | re.IGNORECASE)

# Extended thinking / reasoning model patterns (o1, DeepSeek R1, etc.)
RE_ATTEMPT_MARKER = re.compile(r'^(?:attempt|try)\s*\d+[:\s]', re.MULTILINE | re.IGNORECASE)
RE_THINKING_ALOUD = re.compile(r'^(?:let me think|thinking aloud|internal thought)[:\s]', re.MULTILINE | re.IGNORECASE)
RE_META_TRANSLATION = re.compile(r'\((?:translation|meaning|in other words)[:\s][^)]{10,}\)', re.IGNORECASE)

# GLM draft spam pattern - multiple lines starting with "Name: " (drafting leak)
RE_GLM_DRAFT_LINE = re.compile(r'^[A-Z][a-z]+:\s*.+$', re.MULTILINE)

# Conversation history dump pattern - AI reproducing "Name: (replying to X)..." format
RE_CONVERSATION_DUMP = re.compile(
    r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*:\s*\(replying to [^)]+\).*$',
    re.MULTILINE
)

# Meta-commentary about whether to respond (AI reasoning leaking)
RE_SHOULD_NOT_RESPOND = re.compile(
    r'^I\s+(?:should|will|would)\s+not\s+respond.*$',
    re.MULTILINE | re.IGNORECASE
)
RE_UNNATURAL_META = re.compile(
    r'^It\s+would\s+be\s+(?:unnatural|inappropriate|wrong)\s+(?:for|if)\s+\w+.*$',
    re.MULTILINE | re.IGNORECASE
)
RE_ADDRESSING_META = re.compile(
    r'^(?:Since|Because)\s+\w+\s+is\s+(?:specifically\s+)?(?:addressing|talking\s+to|asking).*$',
    re.MULTILINE | re.IGNORECASE
)
RE_NOT_ADDRESSED = re.compile(
    r'^(?:I\s+(?:am|was)\s+not\s+(?:addressed|mentioned|asked)|This\s+(?:message|question)\s+(?:is|was)\s+not\s+(?:for|directed\s+at)\s+me).*$',
    re.MULTILINE | re.IGNORECASE
)


# =============================================================================
# RESPONSE SANITIZATION FUNCTIONS
# =============================================================================

def remove_thinking_tags(text: str, character_name: str = None) -> str:
    """Remove all reasoning/thinking blocks from AI output.

    Handles:
    - <thinking>...</thinking>
    - <think>...</think>
    - <|begin_of_box|>...<|end_of_box|> (GLM)
    - Partial/unclosed tags at start or end of response
    - Various other reasoning formats from local LLMs
    - GLM 4.7 plain-text reasoning format (think:, Actual output:, Final Polish:)

    Args:
        text: The text to sanitize
        character_name: Unused (kept for API compatibility)
    """
    if not text:
        return text

    # EARLY EXIT: Skip expensive processing for clean text (majority of responses)
    # Only process if text contains markers that suggest reasoning blocks
    text_lower = text.lower()
    has_angle_brackets = '<' in text
    has_square_brackets = '[' in text
    has_think_keyword = 'think' in text_lower
    has_reason_keyword = 'reason' in text_lower
    has_pipe_markers = '|' in text
    has_system_marker = 'system:' in text_lower or 'analyze' in text_lower

    # Check for duplicate lines (GLM sometimes repeats the same line many times)
    lines = text.split('\n')
    has_duplicate_lines = len(lines) > 1 and len(set(line.strip().lower() for line in lines if line.strip())) < len([l for l in lines if l.strip()])

    if not (has_angle_brackets or has_square_brackets or has_think_keyword or
            has_reason_keyword or has_pipe_markers or has_system_marker or has_duplicate_lines):
        # Clean text - just normalize whitespace and return
        return text.strip()

    # Store original for logging
    original_length = len(text)

    # GLM SYSTEM: prefix reasoning format - AGGRESSIVE EXTRACTION
    # This handles cases where GLM ignores thinking:disabled and leaks reasoning
    if 'SYSTEM:' in text or 'Thinking Process' in text or 'Analyze the' in text:
        import logger as log
        # Strategy: Extract the last substantial paragraph that doesn't look like reasoning
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        if len(paragraphs) > 1:
            # Work backwards to find the actual response
            for para in reversed(paragraphs):
                # Skip empty paragraphs
                if not para or len(para) < 10:
                    continue

                # Check if this paragraph looks like reasoning (has colons in first 50 chars)
                reasoning_markers = [
                    'SYSTEM:', 'Analyze', 'Draft', 'Goal:', 'Target:', 'Reaction',
                    'Context:', 'Character:', 'Mood:', 'Traits:', 'Relationship',
                    'Refining', 'Final Polish:', 'Selected Response:', 'Decision:',
                    'Step ', 'Phase ', 'Attempt ', 'Let\'s ', 'Actually,', 'Wait,'
                ]

                # If first line has reasoning markers, skip it
                first_line = para.split('\n')[0]
                is_reasoning = any(marker.lower() in first_line.lower()[:50] for marker in reasoning_markers)

                # Also skip if it has a colon in the first 30 chars (likely a label)
                has_early_colon = ':' in first_line[:30]

                if not is_reasoning and not has_early_colon:
                    # This looks like actual response content
                    log.debug(f"GLM reasoning detected, extracted final response ({len(para)} chars)")
                    return para

            # Fallback: if all paragraphs look like reasoning, take the last one anyway
            if paragraphs:
                last_para = paragraphs[-1]
                if len(last_para) > 20:
                    log.warn(f"GLM reasoning detected but couldn't find clean response, using last paragraph")
                    return last_para

    # GLM 4.7 plain-text reasoning format - check first as it's most specific
    if RE_GLM_THINK_START.match(text.strip()):
        match = RE_GLM_ACTUAL_OUTPUT.search(text)
        if match:
            extracted = match.group(1).strip()
            quote_match = RE_GLM_QUOTED_OUTPUT.match(extracted)
            if quote_match:
                extracted = quote_match.group(1).strip()
            if extracted:
                return extracted

        # Fallback: extract content after last double newline
        parts = text.rsplit('\n\n', 1)
        if len(parts) > 1:
            last_part = parts[-1].strip()
            if not any(last_part.lower().startswith(p) for p in ['think:', 'context:', 'character check:',
                       'action:', 'tone:', 'constraint', 'mental sandbox:', 'check constraints']):
                quote_match = RE_GLM_QUOTED_OUTPUT.match(last_part)
                if quote_match:
                    last_part = quote_match.group(1).strip()
                if last_part:
                    return last_part

    # Remove standard thinking tags
    text = RE_THINKING_OPEN.sub('', text)
    text = RE_THINK_OPEN.sub('', text)

    # Remove GLM box tags
    text = RE_GLM_BOX.sub('', text)

    # Remove partial/unclosed tags at START of response
    text = RE_THINKING_PARTIAL_START.sub('', text)
    text = RE_THINK_PARTIAL_START.sub('', text)
    text = RE_GLM_PARTIAL_START.sub('', text)

    # Remove orphaned opening tags at END
    text = RE_THINKING_ORPHAN_END.sub('', text)
    text = RE_THINK_ORPHAN_END.sub('', text)
    text = RE_GLM_ORPHAN_END.sub('', text)

    # Additional patterns for local LLMs
    text = RE_REASONING_TAG.sub('', text)
    text = RE_REASON_TAG.sub('', text)
    text = RE_BRACKET_THINKING.sub('', text)
    text = RE_BRACKET_THINK.sub('', text)
    text = RE_MARKDOWN_THINKING.sub('', text)
    text = RE_REASONING_PREFIX.sub('', text)

    # Remove internal processing labels
    text = RE_INTERNAL_LABELS.sub('', text)
    text = RE_READABLE_VERSION.sub('', text)
    text = RE_INTERNAL_NOTE.sub('', text)
    text = RE_STEP_LABELS.sub('', text)

    # Remove Deepseek/Qwen style tags
    text = RE_DEEPSEEK_THINK.sub('', text)
    text = RE_QWEN_THOUGHT.sub('', text)
    text = RE_INTERNAL_MONOLOGUE.sub('', text)

    # Remove conversation history dumps (AI reproducing "Name: (replying to X)..." format)
    text = RE_CONVERSATION_DUMP.sub('', text)

    # Remove meta-commentary about whether to respond
    text = RE_SHOULD_NOT_RESPOND.sub('', text)
    text = RE_UNNATURAL_META.sub('', text)
    text = RE_ADDRESSING_META.sub('', text)
    text = RE_NOT_ADDRESSED.sub('', text)

    # Remove <output>/<response> wrappers
    text = RE_OUTPUT_WRAPPER.sub(r'\1', text)
    text = RE_RESPONSE_WRAPPER.sub(r'\1', text)

    # Remove extended thinking artifacts (o1, DeepSeek R1, etc.)
    text = remove_extended_thinking_artifacts(text)

    # Clean up multiple newlines
    text = RE_MULTIPLE_NEWLINES.sub('\n\n', text)

    # Remove duplicate consecutive lines (GLM sometimes repeats the same line many times)
    lines = text.split('\n')
    deduplicated = []
    prev_line_normalized = None

    for line in lines:
        line_normalized = line.strip().lower()

        # Keep empty lines for formatting
        if not line_normalized:
            deduplicated.append(line)
            prev_line_normalized = None
            continue

        # Skip if this is a duplicate of the previous line
        if line_normalized == prev_line_normalized:
            continue

        deduplicated.append(line)
        prev_line_normalized = line_normalized

    text = '\n'.join(deduplicated)

    # GLM draft spam detection - if response has many lines starting with "Name: "
    # this is GLM leaking its internal drafting process, extract just the last one
    if character_name:
        draft_pattern = re.compile(rf'^{re.escape(character_name)}:\s*', re.MULTILINE | re.IGNORECASE)
        draft_matches = list(draft_pattern.finditer(text))
        if len(draft_matches) > 2:
            # Multiple drafts detected - extract content after the last "Name: " prefix
            import logger as log
            log.warn(f"GLM draft spam detected ({len(draft_matches)} drafts), extracting final response")
            last_match = draft_matches[-1]
            # Get everything after the last "Name: " prefix
            final_response = text[last_match.end():].strip()
            # If there's a newline, only take the first line (the actual response)
            if '\n' in final_response:
                final_response = final_response.split('\n')[0].strip()
            if final_response and len(final_response) > 10:
                text = final_response

    # Log if we stripped significant content
    if len(text) < original_length * 0.5:
        import logger as log
        log.debug(f"Stripped {original_length - len(text)} chars of thinking content (GLM leak)")

    return text.strip()


def remove_extended_thinking_artifacts(text: str) -> str:
    """Remove artifacts from extended thinking/reasoning models.

    Handles plain-text reasoning that doesn't use tags:
    - Attempt markers ("attempt 1:", "try 2:")
    - Thinking aloud markers
    - Inline translation/explanation parentheticals

    This is MORE CONSERVATIVE than the reverted CoT filters - it only
    targets specific patterns unique to reasoning models, not general
    meta-commentary that could be legitimate dialogue.
    """
    if not text or len(text) < 20:
        return text

    # Quick check: does text contain attempt markers?
    text_lower = text.lower()
    if 'attempt' not in text_lower and 'try' not in text_lower:
        return text

    # Remove attempt markers at start of lines
    text = RE_ATTEMPT_MARKER.sub('', text)

    # Remove thinking aloud markers
    text = RE_THINKING_ALOUD.sub('', text)

    # Remove inline translation/explanation parentheticals (but keep short ones)
    text = RE_META_TRANSLATION.sub('', text)

    # Clean up empty lines left behind
    text = RE_MULTIPLE_NEWLINES.sub('\n\n', text)

    return text.strip()


@functools.lru_cache(maxsize=32)
def _get_character_name_patterns(character_name: str) -> tuple:
    """Cache compiled regex patterns for character name prefixes."""
    return (
        re.compile(rf'^{re.escape(character_name)}:\s*', re.IGNORECASE),
        re.compile(rf'^{re.escape(character_name)}\s*:\s*', re.IGNORECASE),
        re.compile(rf'^\*{re.escape(character_name)}\*:\s*', re.IGNORECASE),
    )


def clean_bot_name_prefix(text: str, character_name: str = None) -> str:
    """Remove bot persona name prefix and other LLM artifacts from output.

    Strips:
    - [Name]: prefixes (learned from history format)
    - (replying to X's message: "...") prefixes
    - CharacterName: prefixes
    - *CharacterName*: prefixes
    """
    if not text:
        return text

    # Strip [Name]: prefix pattern
    text = RE_NAME_PREFIX.sub('', text)

    # Strip (replying to X's message: "...") pattern
    text = RE_REPLY_PREFIX.sub('', text)

    # Strip (RE: ...) or (RE ...) patterns
    text = RE_RE_PREFIX.sub('', text)

    # Strip character-specific patterns if provided
    if character_name:
        for pattern in _get_character_name_patterns(character_name):
            text = pattern.sub('', text)

    return text.strip()


def clean_em_dashes(text: str) -> str:
    """Replace em-dashes with appropriate punctuation."""
    # Mid-sentence em-dashes become ", "
    text = RE_EM_DASH_BETWEEN_WORDS.sub(r'\1, \2', text)
    # End-sentence em-dashes become "-"
    text = RE_EM_DASH_END.sub('-', text)
    return text


def sanitize_response(text: str, character_name: str = None) -> str:
    """Apply all sanitization steps to a response.

    Args:
        text: Raw AI response
        character_name: Optional character name for prefix removal

    Returns:
        Cleaned response text
    """
    if not text:
        return text

    text = remove_thinking_tags(text)
    text = clean_bot_name_prefix(text, character_name)
    text = clean_em_dashes(text)
    return text.strip()

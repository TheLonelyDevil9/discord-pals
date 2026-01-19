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

# Internal processing labels
RE_INTERNAL_LABELS = re.compile(r'^(?:message\s+)?(?:duplication\s+)?glitch:?\s*', re.MULTILINE | re.IGNORECASE)
RE_READABLE_VERSION = re.compile(r'^readable\s+version:?\s*', re.MULTILINE | re.IGNORECASE)
RE_INTERNAL_NOTE = re.compile(r'^\s*\[(?:internal|note|debug|processing)\].*$', re.MULTILINE | re.IGNORECASE)
RE_STEP_LABELS = re.compile(r'^(?:step\s*\d+|phase\s*\d+|stage\s*\d+):.*$', re.MULTILINE | re.IGNORECASE)

# Deepseek/Qwen style
RE_DEEPSEEK_THINK = re.compile(r'<\|think\|>.*?<\|/think\|>', re.DOTALL)
RE_QWEN_THOUGHT = re.compile(r'<\|startofthought\|>.*?<\|endofthought\|>', re.DOTALL)
RE_INTERNAL_MONOLOGUE = re.compile(r'\[Internal:.*?\]', re.DOTALL | re.IGNORECASE)


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

    if not (has_angle_brackets or has_square_brackets or has_think_keyword or
            has_reason_keyword or has_pipe_markers):
        # Clean text - just normalize whitespace and return
        return text.strip()

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

    # Remove <output>/<response> wrappers
    text = RE_OUTPUT_WRAPPER.sub(r'\1', text)
    text = RE_RESPONSE_WRAPPER.sub(r'\1', text)

    # Clean up multiple newlines
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

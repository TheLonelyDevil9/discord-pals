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

# Chain-of-thought reasoning indicators (plain text, no tags)
# These phrases indicate meta-reasoning about how to respond, not actual dialogue
COT_REASONING_INDICATORS = [
    # Self-referential / instruction-aware
    "given the instructions",
    "based on the instructions",
    "according to my instructions",
    "following the instructions",

    # Response planning
    "i should respond",
    "i will respond",
    "i can respond",
    "i need to respond",
    "i must respond",
    "my response should",
    "i should reply",

    # Meta-analysis of user input
    "in the user's message",
    "the user is asking",
    "the user wants",
    "the user said",
    "the user's request",

    # Character/roleplay awareness
    "stay in character",
    "break character",
    "in-character",
    "my character would",

    # Internal planning
    "i should also note",
    "i should consider",
    "i need to consider",

    # Instruction acknowledgment
    "i must stay",
    "i must not",
    "i cannot break",
    "i should not break",
]

# Quick markers for early exit optimization (subset of most distinctive indicators)
COT_QUICK_MARKERS = [
    "given the instructions",
    "i should respond",
    "stay in character",
    "in the user's message",
]


# =============================================================================
# RESPONSE SANITIZATION FUNCTIONS
# =============================================================================

def _check_single_paragraph_cot(text: str) -> str:
    """Handle single-paragraph case with sentence-level analysis.

    When text has no paragraph breaks, analyze sentences for reasoning indicators.
    Strips leading sentences if 2+ consecutive sentences contain reasoning.
    """
    # Split on sentence boundaries (period followed by space or end)
    sentences = text.split('. ')

    if len(sentences) <= 2:
        return text  # Too short to split safely

    # Find where reasoning ends
    reasoning_end = 0
    consecutive_reasoning = 0

    for i, sentence in enumerate(sentences):
        sent_lower = sentence.lower()
        indicator_count = sum(
            1 for ind in COT_REASONING_INDICATORS
            if ind in sent_lower
        )

        if indicator_count >= 1:
            consecutive_reasoning += 1
            if consecutive_reasoning >= 2:
                reasoning_end = i + 1
        else:
            if consecutive_reasoning >= 2:
                break
            consecutive_reasoning = 0

    if reasoning_end > 0 and reasoning_end < len(sentences):
        remaining = sentences[reasoning_end:]
        return '. '.join(remaining).strip()

    return text


def remove_cot_reasoning(text: str) -> str:
    """Remove plain-text chain-of-thought reasoning from AI output.

    Detects and strips leading paragraphs that contain multiple
    meta-reasoning indicators (e.g., "I should respond...",
    "Given the instructions...").

    Only removes content if 2+ distinct indicators are found in
    the leading paragraph(s), reducing false positives.
    """
    if not text or len(text) < 50:
        return text

    text_lower = text.lower()

    # Quick check: does text contain any potential indicators?
    if not any(marker in text_lower for marker in COT_QUICK_MARKERS):
        return text

    # Split into paragraphs (double newline)
    paragraphs = text.split('\n\n')

    if len(paragraphs) <= 1:
        # Single paragraph - check sentences instead
        return _check_single_paragraph_cot(text)

    # Analyze leading paragraphs (up to first 3)
    paragraphs_to_check = min(3, len(paragraphs))
    reasoning_end_index = 0

    for i in range(paragraphs_to_check):
        para_lower = paragraphs[i].lower()

        # Count reasoning indicators in this paragraph
        indicator_count = sum(
            1 for indicator in COT_REASONING_INDICATORS
            if indicator in para_lower
        )

        if indicator_count >= 2:
            # This paragraph is reasoning - mark for removal
            reasoning_end_index = i + 1
        elif indicator_count == 1 and i == 0:
            # First paragraph with single indicator - check if next has any
            if i + 1 < len(paragraphs):
                next_para_lower = paragraphs[i + 1].lower()
                next_count = sum(
                    1 for ind in COT_REASONING_INDICATORS
                    if ind in next_para_lower
                )
                if next_count >= 1:
                    # Both paragraphs have indicators - remove first
                    reasoning_end_index = 1
        else:
            # Clean paragraph - stop checking
            break

    if reasoning_end_index > 0:
        # Remove leading reasoning paragraphs
        remaining = paragraphs[reasoning_end_index:]
        return '\n\n'.join(remaining).strip()

    return text


def remove_thinking_tags(text: str) -> str:
    """Remove all reasoning/thinking blocks from AI output.

    Handles:
    - <thinking>...</thinking>
    - <think>...</think>
    - <|begin_of_box|>...<|end_of_box|> (GLM)
    - Partial/unclosed tags at start or end of response
    - Various other reasoning formats from local LLMs
    - GLM 4.7 plain-text reasoning format (think:, Actual output:, Final Polish:)
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
    has_cot_markers = any(marker in text_lower for marker in COT_QUICK_MARKERS)

    if not (has_angle_brackets or has_square_brackets or has_think_keyword or
            has_reason_keyword or has_pipe_markers or has_cot_markers):
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

    # Remove plain-text chain-of-thought reasoning (untagged)
    text = remove_cot_reasoning(text)

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

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

# Corrupted reference patterns (model hallucination artifacts)
# Catches patterns like "See爸爸妈妈" where ASCII text is followed by random CJK characters
# Only matches at word boundaries to avoid false positives with legitimate mixed-script text
RE_CORRUPTED_TRAILING_CJK = re.compile(
    r'[,\s][A-Za-z]{2,10}[\u4e00-\u9fff\u3400-\u4dbf]{2,}$'  # separator + short ASCII + CJK at end
)

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

# Reasoning process labels (GLM structured thinking)
RE_REASONING_PROCESS_LABELS = re.compile(
    r'^(?:Identify the (?:User|Intent|Request)|'
    r'Analyze (?:the |.*?Reaction)|'
    r'(?:Drafting|Refining) (?:the |based on)|'
    r'Option \d+.*?:|'
    r'Draft:|'
    r'Adding (?:Emoji|Reaction)|'
    r'Check(?:ing)? constraints|'
    r'Text generation process|'
    r'System: Think silently|'
    r'Selected Response):?.*$',
    re.MULTILINE | re.IGNORECASE
)

# Final output extraction (after "Final Polish:" or similar)
RE_FINAL_OUTPUT_LABEL = re.compile(
    r'^(?:Final (?:Polish|Output|Response|Answer)|'
    r'Selected Response|'
    r'Actual (?:Output|Response)):?\s*',
    re.MULTILINE | re.IGNORECASE
)

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

    # Structured reasoning process (GLM/reasoning model style)
    "text generation process",
    "think silently",
    "identify the user",
    "identify the intent",
    "analyze the",
    "drafting the response",
    "refining based on",
    "final polish",
    "selected response",
    "check constraints",
    "let's go with",
    "let's try to",
    "let's make it",
    "let's stick to",
    "let's verify",
    "one more check",

    # Option enumeration
    "option 1",
    "option 2",
    "option 3",

    # Draft markers
    "draft:",
    "adding emoji",
    "adding reaction",

    # Thinking/analysis starters (reasoning model leaks)
    "alright, let's see",
    "alright, let me",
    "let's see here",
    "let me think through",
    "let me figure out",

    # Meta-response planning (highly specific)
    "needs to respond",
    "should respond with",
    "how to respond to",
    "backhanded compliment fits",
    "acknowledge their effort but",
    "downplay it as merely",
    "point out another flaw",
    "keep them working a bit",
    "adds a playful challenge",
    "ties back to their interest",
    "reinforces the playful dynamic",
    "the key is balancing",
    "keeping them engaged while",
    "maintaining her mischievous",
    "maintaining his",
    "playful dynamic",
    "fits her personality",
    "fits his personality",
]

# Quick markers for early exit optimization (subset of most distinctive indicators)
COT_QUICK_MARKERS = [
    "given the instructions",
    "i should respond",
    "stay in character",
    "in the user's message",
    "text generation process",
    "identify the intent",
    "drafting the response",
    "final polish",
    # Added for reasoning model leaks
    "alright, let's see",
    "needs to respond",
    "the key is balancing",
    "backhanded compliment",
    "playful dynamic",
]


# =============================================================================
# RESPONSE SANITIZATION FUNCTIONS
# =============================================================================

def _has_third_person_self_reference(text: str, character_name: str = None) -> bool:
    """Detect if text refers to the character in third person (meta-reasoning signal).

    When the LLM outputs reasoning like "Yae Miko needs to respond", it's clearly
    meta-reasoning rather than in-character dialogue.
    """
    if not character_name:
        return False

    text_lower = text.lower()
    name_lower = character_name.lower()

    # Patterns like "Yae Miko needs to respond" or "Ellen should"
    meta_patterns = [
        f"{name_lower} needs to",
        f"{name_lower} should",
        f"{name_lower} would",
        f"and {name_lower} needs",
        f", {name_lower} needs",
    ]

    return any(p in text_lower for p in meta_patterns)


def _is_full_reasoning_response(text: str, character_name: str = None) -> bool:
    """Detect if the entire response is meta-reasoning with no actual output.

    Returns True when the response appears to be pure chain-of-thought reasoning
    rather than an actual in-character response. Used to catch cases where the
    LLM outputs its thinking process instead of a proper response.
    """
    text_lower = text.lower().strip()

    # Must start with a reasoning opener
    reasoning_openers = ["alright,", "okay,", "let me", "let's", "first,", "so,", "hmm,"]
    if not any(text_lower.startswith(p) for p in reasoning_openers):
        return False

    # Count strong meta-indicators (highly specific phrases)
    strong_indicators = [
        "needs to respond",
        "should respond",
        "how to respond",
        "the key is balancing",
        "keeping them engaged",
        "reinforces the",
        "fits her personality",
        "fits his personality",
        "backhanded compliment",
        "playful dynamic",
        "acknowledge their effort",
        "point out another flaw",
    ]

    indicator_count = sum(1 for m in strong_indicators if m in text_lower)

    # Third-person self-reference is a strong signal
    has_self_ref = _has_third_person_self_reference(text, character_name)

    # Require opener + (2 indicators OR 1 indicator + self-reference)
    return indicator_count >= 2 or (indicator_count >= 1 and has_self_ref)


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


def _extract_final_output(text: str) -> str | None:
    """Try to extract final output from structured reasoning.

    Looks for patterns like:
    - "Final Polish:" followed by the actual response
    - "Selected Response:" followed by the actual response
    - "Actual Output:" followed by the actual response

    Returns extracted content or None if not found.
    """
    text_lower = text.lower()

    # Look for final output markers
    final_markers = [
        'final polish:',
        'final polish:\n',
        'final output:',
        'final response:',
        'selected response:',
        'actual output:',
        'actual response:',
    ]

    for marker in final_markers:
        idx = text_lower.rfind(marker)  # Use rfind to get the last occurrence
        if idx != -1:
            # Extract everything after the marker
            after_marker = text[idx + len(marker):].strip()
            if after_marker:
                # Remove any remaining reasoning labels from the extracted text
                lines = after_marker.split('\n')
                clean_lines = []
                for line in lines:
                    line_lower = line.lower().strip()
                    # Skip lines that look like reasoning labels
                    if any(line_lower.startswith(p) for p in [
                        'option ', 'draft:', 'check ', 'let\'s ', 'adding ',
                        'relationship:', 'context:', 'analysis:', 'note:'
                    ]):
                        continue
                    clean_lines.append(line)
                result = '\n'.join(clean_lines).strip()
                if result:
                    return result

    return None


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

    # First, try to extract final output if structured reasoning is detected
    if any(marker in text_lower for marker in ['final polish:', 'selected response:', 'text generation process']):
        extracted = _extract_final_output(text)
        if extracted:
            return extracted

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


def remove_thinking_tags(text: str, character_name: str = None) -> str:
    """Remove all reasoning/thinking blocks from AI output.

    Handles:
    - <thinking>...</thinking>
    - <think>...</think>
    - <|begin_of_box|>...<|end_of_box|> (GLM)
    - Partial/unclosed tags at start or end of response
    - Various other reasoning formats from local LLMs
    - GLM 4.7 plain-text reasoning format (think:, Actual output:, Final Polish:)
    - Full reasoning responses (entire output is meta-reasoning)

    Args:
        text: The text to sanitize
        character_name: Optional character name for third-person self-reference detection
    """
    if not text:
        return text

    # Check if entire response is reasoning (returns empty to trigger fallback)
    if _is_full_reasoning_response(text, character_name):
        return ""

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

    # Try to extract clean output from structured reasoning (do this early!)
    # This handles "Final Polish:", "Text generation process...", etc.
    text_lower_check = text.lower()
    if any(marker in text_lower_check for marker in ['final polish:', 'selected response:', 'text generation process', 'identify the intent']):
        extracted = _extract_final_output(text)
        if extracted:
            text = extracted

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

    # Remove reasoning process labels (GLM structured thinking)
    text = RE_REASONING_PROCESS_LABELS.sub('', text)

    # Remove "Final Polish:" and similar labels (keep content after)
    text = RE_FINAL_OUTPUT_LABEL.sub('', text)

    # Remove Deepseek/Qwen style tags
    text = RE_DEEPSEEK_THINK.sub('', text)
    text = RE_QWEN_THOUGHT.sub('', text)
    text = RE_INTERNAL_MONOLOGUE.sub('', text)

    # Remove <output>/<response> wrappers
    text = RE_OUTPUT_WRAPPER.sub(r'\1', text)
    text = RE_RESPONSE_WRAPPER.sub(r'\1', text)

    # Remove plain-text chain-of-thought reasoning (untagged)
    text = remove_cot_reasoning(text)

    # Remove corrupted trailing references (e.g., "See爸爸妈妈" artifacts)
    text = RE_CORRUPTED_TRAILING_CJK.sub('', text)

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

    # Remove corrupted trailing references (model hallucination artifacts)
    # Done here to catch cases that bypass remove_thinking_tags early exit
    text = RE_CORRUPTED_TRAILING_CJK.sub('', text)

    # Clean up trailing punctuation left by artifact removal
    text = text.rstrip(',;: ')

    return text.strip()

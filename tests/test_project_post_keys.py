"""Every Project post key comes from project_automation.mirror_key.

Post records, their send guards, the item records and the footer of each Discord post share
that one format. A second builder that drifts from it misses the existing records, so an item
is posted again or a send guard is never released.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# A post key starts with the server and the helper bot, and nothing else joins those two into text.
KEY_FIELDS = {"guild_id", "bot_name"}


def _names(node):
    """Return the variable names, attribute names and string constants inside an expression."""
    found = set()
    for part in ast.walk(node):
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            found.add(part.value)
        elif isinstance(part, ast.Name):
            found.add(part.id)
        elif isinstance(part, ast.Attribute):
            found.add(part.attr)
    return found


def _text(node):
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str))


def _added(node):
    """Return the operands of a chain of + operations."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _added(node.left) + _added(node.right)
    return [node]


def _builds_text(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in ("join", "format")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _text(node.left)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return any(_text(operand) for operand in _added(node))
    return isinstance(node, ast.JoinedStr)


class _Builders(ast.NodeVisitor):
    def __init__(self, module):
        self.module, self.functions, self.found = module, ["<module>"], set()

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def generic_visit(self, node):
        if _builds_text(node) and KEY_FIELDS <= _names(node):
            self.found.add(f"{self.module}:{self.functions[-1]}")
        super().generic_visit(node)


def post_key_builders(source, module):
    """Return "module:function" for each function that builds text from a guild_id and a bot_name value.

    It reads f-strings, str.join and str.format calls, % with a literal format, and + chains that
    hold a string literal or an f-string, and finds the two names inside them as subscript keys,
    attributes, variables or string constants. It cannot see a + chain of variables alone, names
    that reach the expression through a variable defined elsewhere, such as a tuple of field
    names, or a builder in a module it does not scan.
    """
    visitor = _Builders(module)
    visitor.visit(ast.parse(source))
    return visitor.found


def test_mirror_key_is_the_only_post_key_builder():
    found = set()
    for path in sorted(ROOT.glob("project_automation*.py")) + [ROOT / "tools" / "project_data.py"]:
        found |= post_key_builders(path.read_text(encoding="utf-8"), path.name)
    assert found == {"project_automation.py:mirror_key"}


@pytest.mark.parametrize("source", [
    "def key(c, k):\n    return f\"{c['guild_id']}:{c['bot_name']}:{c['issues_channel_id']}:{c['repository']}:{k}\"\n",
    "def key(c, k):\n    return ':'.join([c['guild_id'], c['bot_name'], c['repository'], k])\n",
    "def key(c, k):\n    return '{}:{}:{}'.format(c.get('guild_id'), c.get('bot_name'), k)\n",
    "def key(c, k):\n    return '%s:%s:%s' % (c['guild_id'], c['bot_name'], k)\n",
    "def key(binding, k):\n    return binding['guild_id'] + ':' + binding['bot_name'] + ':' + k\n",
    "async def key(guild_id, bot_name, k):\n    return f'{guild_id}:{bot_name}:{k}'\n",
])
def test_the_scan_finds_each_way_of_building_a_post_key(source):
    assert post_key_builders(source, "example.py") == {"example.py:key"}


@pytest.mark.parametrize("source", [
    "def same(a, b):\n    return all(a.get(name) == b.get(name) for name in ('guild_id', 'bot_name'))\n",
    "def receipt(c, k):\n    return json.dumps([c['guild_id'], c['bot_name'], k])\n",
    "def url(c, thread):\n    return f\"https://discord.com/channels/{c['guild_id']}/{thread}\"\n",
    "FIELDS = ('repository', 'bot_name', 'guild_id') + CHANNEL_FIELDS\n",
])
def test_the_scan_ignores_code_that_builds_no_post_key(source):
    assert post_key_builders(source, "example.py") == set()

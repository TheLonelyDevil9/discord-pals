"""Every Project post key comes from project_automation.mirror_key.

Post records, their send guards, the item records and the footer of each Discord post share
that one format. A second builder that drifts from it misses the existing records, so an item
is posted again or a send guard is never released.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


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
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _parts(node):
    """Return the parts that an f-string or a + chain joins, in order, with each f-string field as its expression."""
    if isinstance(node, ast.JoinedStr):
        return [part.value if isinstance(part, ast.FormattedValue) else part for part in node.values]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _parts(node.left) + _parts(node.right)
    return [node]


def _joined_parts(node):
    """Return the parts of a str.join call over a list or tuple literal, with its separator between them, or []."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join"
            and _text(node.func.value) is not None and len(node.args) == 1
            and isinstance(node.args[0], (ast.List, ast.Tuple))):
        return []
    parts = []
    for element in node.args[0].elts:
        parts += [node.func.value, element] if parts else [element]
    return parts


def _builds_post_key(parts):
    return any("guild_id" in _names(server) and _text(separator) == ":" and "bot_name" in _names(helper)
               for server, separator, helper in zip(parts, parts[1:], parts[2:]))


class _Builders(ast.NodeVisitor):
    def __init__(self, module):
        self.module, self.functions, self.found = module, ["<module>"], set()

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def generic_visit(self, node):
        parts = _parts(node) if isinstance(node, (ast.JoinedStr, ast.BinOp)) else _joined_parts(node)
        if _builds_post_key(parts):
            self.found.add(f"{self.module}:{self.functions[-1]}")
        super().generic_visit(node)


def post_key_builders(source, module):
    """Return "module:function" for each function that builds text in which a guild_id value is
    followed by ":" and then a bot_name value, the way a post key starts.

    A part counts as a guild_id or bot_name value when that name appears in it as a string
    constant, an attribute or a variable name, as in c['guild_id'], c.get('guild_id'),
    self.guild_id or guild_id. The scan reads f-strings, + chains of text and f-strings, and
    str.join over a list or tuple literal. It does not read str.format or % formatting, a join
    over a generator or over a tuple of field names, values held in variables with other names,
    or a builder in a module it does not scan.
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
    "async def key(guild_id, bot_name, k):\n    return f'Project {guild_id}:{bot_name}:{k}'\n",
    "def key(c, k):\n    return ':'.join([c['guild_id'], c['bot_name'], c['repository'], k])\n",
    "def key(c, k):\n    return ':'.join((c.get('guild_id'), c.get('bot_name'), k))\n",
    "def key(binding, k):\n    return binding['guild_id'] + ':' + binding['bot_name'] + ':' + k\n",
    "def key(binding, k):\n    return str(binding['guild_id']) + f\":{binding['bot_name']}:{k}\"\n",
])
def test_the_scan_finds_each_form_it_reads(source):
    assert post_key_builders(source, "example.py") == {"example.py:key"}


@pytest.mark.parametrize("source", [
    "def key(c, k):\n    return '{0[guild_id]}:{0[bot_name]}:{1}'.format(c, k)\n",
    "def key(c, k):\n    return '%(guild_id)s:%(bot_name)s:' % c + k\n",
    "def key(c, k):\n    return ':'.join(c[name] for name in ('guild_id', 'bot_name', 'repository')) + ':' + k\n",
    "def key(c, k):\n    server, helper = c['guild_id'], c['bot_name']\n    return f'{server}:{helper}:{k}'\n",
])
def test_the_scan_does_not_read_the_forms_its_docstring_names(source):
    assert post_key_builders(source, "example.py") == set()


@pytest.mark.parametrize("source", [
    "def same(a, b):\n    return all(a.get(name) == b.get(name) for name in ('guild_id', 'bot_name'))\n",
    "def receipt(c, k):\n    return 'pr:' + hashlib.sha256(json.dumps([c['guild_id'], c['bot_name'], k]).encode()).hexdigest()\n",
    "def url(c, thread):\n    return f\"https://discord.com/channels/{c['guild_id']}/{thread}\"\n",
    "FIELDS = ('repository', 'bot_name', 'guild_id') + CHANNEL_FIELDS\n",
    "def log(c):\n    warn(f\"Helper {c['bot_name']} lost access to server {c['guild_id']}.\")\n",
    "def missing(c):\n    return 'Set ' + ', '.join(name for name in ('guild_id', 'bot_name') if not c.get(name)) + '.'\n",
    "def path(c):\n    return os.path.join(c['guild_id'], c['bot_name'])\n",
])
def test_the_scan_ignores_text_that_is_no_post_key(source):
    assert post_key_builders(source, "example.py") == set()

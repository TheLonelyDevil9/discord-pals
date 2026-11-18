"""Write providers.json or bots.json from the values setup.sh collects.

Building the JSON here instead of with shell strings keeps a quote or a
backslash in a name from producing a file that startup validation refuses.
"""

import argparse
import json
import sys
from pathlib import Path

FIELDS = {
    "providers": ("name", "url", "key_env", "model"),
    "bots": ("name", "token_env", "character"),
}


def build_config(kind: str, values: list) -> dict:
    """Group ``values`` into entries of the kind's fields, in order; raise ValueError on a partial entry."""
    fields = FIELDS[kind]
    if len(values) % len(fields):
        raise ValueError(f"{kind} take {len(fields)} values per entry: {', '.join(fields)}")
    entries = [dict(zip(fields, values[start:start + len(fields)])) for start in range(0, len(values), len(fields))]
    if kind == "providers":
        return {"providers": entries, "timeout": 60}
    return {"bots": entries}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write providers.json or bots.json for setup.sh.")
    parser.add_argument("kind", choices=sorted(FIELDS))
    parser.add_argument("output", type=Path)
    parser.add_argument("values", nargs="*", help="field values in order; put -- before them")
    args = parser.parse_args(argv)
    try:
        config = build_config(args.kind, args.values)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

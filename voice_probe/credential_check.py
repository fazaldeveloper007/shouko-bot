"""Read-only, fixed-scope Kristy credential-location check. Never prints values/source."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

# Run from the existing repository folder. No bot import or execution.
root = Path.cwd().resolve()
if not (root / 'bot.py').is_file() or not (root / 'voice_probe/receive.py').is_file():
    print('CHECK_WRONG_FOLDER: run from ~/shouko-bot')
    sys.exit(1)

result = {'inherited_token_present': bool(os.environ.get('DISCORD_TOKEN')),
          'dotenv_disabled': os.environ.get('PYTHON_DOTENV_DISABLED', '').lower() in ('1', 'true', 't', 'yes', 'y'),
          'bot_local_changes': 'unknown', 'bot_token_assignment': 'unknown',
          'bot_bare_load_dotenv_call': False, 'dotenv_candidates': []}
try:
    status = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'bot.py'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    result['bot_local_changes'] = {0: False, 1: True}.get(status.returncode, 'unknown')
except (OSError, subprocess.TimeoutExpired):
    pass

# Parse only the named bot entry point. No evaluation, imports or source output.
try:
    if (root / 'bot.py').stat().st_size > 2000000:
        raise ValueError()
    tree = ast.parse((root / 'bot.py').read_text())
    assignments = []
    top_level = {id(n) for n in tree.body}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == 'DISCORD_TOKEN' for t in targets):
                value = node.value
                assignments.append(('top_level:' if id(node) in top_level else 'nested:') + ('literal_nonempty' if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value else
                                   'literal_empty' if isinstance(value, ast.Constant) and value.value in ('', None) else 'expression'))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'load_dotenv' and not node.args and not node.keywords:
            result['bot_bare_load_dotenv_call'] = True
    result['bot_token_assignment'] = assignments or ['not_found']
except (OSError, SyntaxError, ValueError, UnicodeError, RecursionError):
    result['bot_token_assignment'] = 'unreadable_or_unparseable'

try:
    from dotenv import dotenv_values
    dotenv_available = True
except ImportError:
    dotenv_available = False
result['dotenv_library_available'] = dotenv_available

# Exactly the entry-point ancestor chain, not recursive directory searching.
nearest_seen = False
for level, folder in enumerate((root, *root.parents)):
    path = folder / '.env'
    item = {'ancestor_level': level, 'file_exists': path.is_file(), 'selected_by_normal_lookup': False}
    if item['file_exists']:
        item['selected_by_normal_lookup'] = not nearest_seen
        nearest_seen = True
        if dotenv_available and item['selected_by_normal_lookup']:
            try:
                if path.stat().st_size > 262144:
                    item['token_key'] = 'file_too_large_to_check'
                else:
                    # No variable expansion or environment changes, no output from parser.
                    import contextlib
                    with open(os.devnull, 'w') as sink, contextlib.redirect_stderr(sink):
                        values = dotenv_values(path, interpolate=False)
                    item['token_key'] = 'nonempty' if values.get('DISCORD_TOKEN') else 'empty' if 'DISCORD_TOKEN' in values else 'absent'
                    del values
            except (OSError, UnicodeError):
                item['token_key'] = 'unreadable'
    result['dotenv_candidates'].append(item)

print('CREDENTIAL_LOCATION_CHECK ' + json.dumps(result, separators=(',', ':')))
print('Presence only, not token validation. No login, network request, source/credential value output or file changes performed.')

import json, os, glob, sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

proj_base = os.path.expanduser('~/.claude/projects/')

all_proj_dirs = [
    proj_base + 'C--Users-jemak/',
    proj_base + 'C--Users-jemak-Desktop-Projects-Order-Samurai/',
    proj_base + 'C--Users-jemak-Desktop-Agentica-OS/',
    proj_base + 'C--Users-jemak-Desktop-Agentica-OS-Governance/',
]

all_spawns = []

for proj_dir in all_proj_dirs:
    if not os.path.exists(proj_dir):
        continue
    jsonl_files = sorted(glob.glob(proj_dir + '*.jsonl'), key=os.path.getmtime)
    for f in jsonl_files[-5:]:
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d %H:%M')
        with open(f, encoding='utf-8', errors='ignore') as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                    msg = entry.get('message', {})
                    content = msg.get('content', [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') == 'Agent':
                                inp = block.get('input', {})
                                all_spawns.append({
                                    'file': os.path.basename(f),
                                    'mtime': mtime,
                                    'project': os.path.basename(os.path.normpath(proj_dir)),
                                    'description': inp.get('description', ''),
                                    'subagent_type': inp.get('subagent_type', 'general-purpose'),
                                    'prompt': inp.get('prompt', ''),
                                    'background': inp.get('run_in_background', False),
                                })
                except Exception:
                    continue

print(f"Total Agent spawns found: {len(all_spawns)}\n")
for i, s in enumerate(all_spawns, 1):
    proj_short = s['project'][:50]
    prompt_safe = s['prompt'][:300].encode('ascii', errors='replace').decode('ascii')
    print(f"[{i}] {s['mtime']} | {proj_short}")
    print(f"     type={s['subagent_type']} | bg={s['background']}")
    print(f"     desc: {s['description']}")
    print(f"     prompt: {prompt_safe}")
    print()

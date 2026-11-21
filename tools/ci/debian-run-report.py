#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Report actual CI job/step timing without inferring skipped work as success."""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess


def duration(item):
    start, end = item.get('started_at'), item.get('completed_at')
    if not start or not end:
        return '—'
    seconds = round((datetime.fromisoformat(end.replace('Z', '+00:00')) - datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds())
    return f'{seconds // 60}m {seconds % 60}s'


repository = os.environ['GITHUB_REPOSITORY']
run_id = os.environ['GITHUB_RUN_ID']
pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp', f'repos/{repository}/actions/runs/{run_id}/jobs?per_page=100'], text=True, timeout=120))
jobs = [job for page in pages for job in page['jobs']]
Path('debian-stage-times.json').write_text(json.dumps(jobs, indent=2) + '\n')
lines = ['| Job / stage | Result | Duration |', '| --- | --- | --- |']
for job in jobs:
    lines.append(f'| {job["name"]} | {job.get("conclusion") or job["status"]} | {duration(job)} |')
    for step in job.get('steps', []):
        if step['name'].startswith(('Set up job', 'Post ', 'Complete job')):
            continue
        lines.append(f'| ↳ {step["name"]} | {step.get("conclusion") or step["status"]} | {duration(step)} |')
report = '\n'.join(lines) + '\n'
print(report)
with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as summary:
    summary.write(report)

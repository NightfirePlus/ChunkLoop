"""Recompute the frozen empirical report without model calls or downloads."""
from __future__ import annotations

import json
from pathlib import Path

from chunkloop.datasets.writingbench import WritingBenchCase
from tools.summarize_adaptive_extension import summarize


def verify(bundle: dict) -> dict:
    selected = [row['index'] for row in bundle['selection']['selected']]
    if len(selected) != len(set(selected)):
        raise ValueError('duplicate selected case')
    tables = {}
    for name in ('cases', 'details', 'direct_scores', 'changed_scores'):
        rows = bundle[name]
        table = {row['index']: row for row in rows}
        if len(table) != len(rows):
            raise ValueError(f'duplicate index in {name}')
        tables[name] = table
    for name in ('cases', 'details', 'direct_scores'):
        if set(tables[name]) != set(selected):
            raise ValueError(f'incomplete or extra cases in {name}')
    changed = {i for i, row in tables['details'].items()
               if row['adaptive'].get('source_mode') == 'chunkloop'}
    if set(tables['changed_scores']) != changed:
        raise ValueError('changed scores do not match selected chunk outputs')
    for name, mode in (('direct_scores', 'direct'), ('changed_scores', 'adaptive')):
        for row in tables[name].values():
            grade = row[mode]
            criteria = grade['criteria']
            expected_names = [c['name'] for c in tables['cases'][row['index']]['checklist']]
            if [c['name'] for c in criteria] != expected_names:
                raise ValueError('score criteria do not match the source task')
            if len(criteria) != 5 or any(type(c['score']) is not int or
                                        not 1 <= c['score'] <= 10 or not c['reason'].strip()
                                        for c in criteria):
                raise ValueError(f'invalid rubric score: {row["index"]}')
            if grade['mean_10'] != round(sum(c['score'] for c in criteria) / 5, 2):
                raise ValueError('score mean does not match criteria')
    for i, row in tables['details'].items():
        if i not in changed and row['direct']['response'] != row['adaptive']['response']:
            raise ValueError('reused score refers to a different response')
        if i in changed and row['chunkloop']['response'] != row['adaptive']['response']:
            raise ValueError('selected response differs from the chunk output')
    cases = {i: WritingBenchCase(**row) for i, row in tables['cases'].items()}
    report = summarize(bundle['selection'], tables['details'], tables['direct_scores'],
                       tables['changed_scores'], cases)
    if report['summary'] != bundle['expected']['summary'] or report['rows'] != bundle['expected']['rows']:
        raise ValueError('recomputed results differ from frozen report')
    return report


def main() -> None:
    path = Path(__file__).resolve().parents[1] / 'research' / 'extension-evidence.json'
    report = verify(json.loads(path.read_text(encoding='utf-8')))
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    print('Verified: all selected cases, individual rubric means, output counts and report.')


if __name__ == '__main__':
    main()

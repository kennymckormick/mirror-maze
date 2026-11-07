"""Keep known host execution anomalies out of clean trajectory pass rates."""
import json
from pathlib import Path


def clean_eligible(case,row=None,result=None):
    row=row or{};result=result or{};case=Path(case)
    path=case/'statistics-policy.json'
    if path.exists():
        policy=json.loads(path.read_text())
        assert policy['schema']=='mirror-maze-case-statistics-policy/v1'and policy['user_authorized']
        if policy.get('exclude_from_clean_pass_rate'):return False
    interventions=case/'host-interventions.json'
    if interventions.exists()and json.loads(interventions.read_text()).get('clean_protocol_comparison_eligible')is False:return False
    return row.get('clean_protocol_comparison_eligible',True)is not False and result.get('clean_protocol_comparison_eligible',True)is not False


def clean_statistics(rows):
    completed=[r for r in rows if r.get('audited')and r['status']in('success','planning_cap','cap')]
    clean=[r for r in completed if r.get('clean_protocol_comparison_eligible',True)]
    passed=sum(r['status']=='success'for r in clean)
    return {'clean_completed':len(clean),'clean_passed':passed,'clean_pass_rate':passed/len(clean)if clean else None,
            'clean_excluded':sum(not r.get('clean_protocol_comparison_eligible',True)for r in rows),
            'clean_excluded_completed':len(completed)-len(clean)}

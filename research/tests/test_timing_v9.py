"""Regression tests for monotonic phase-9 timing and log-based attribution."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from timing_v9 import PeriodicDeadline
from research.cadence_v9 import analyze_run, parse_events, receipt_intervals
from research.campaign_v3 import make_plan


class SchedulerTests(unittest.TestCase):
    def test_rejects_invalid_period(self):
        for val in (0, -2, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                PeriodicDeadline(val,start=0.)

    def test_fixed_schedule_does_not_add_work_to_period(self):
        sched=PeriodicDeadline(2.,start=10.)
        self.assertEqual(sched.advance(10.7),0)
        self.assertEqual(sched.due,12.)
        self.assertEqual(sched.advance(12.9),0)
        self.assertEqual(sched.due,14.)

    def test_overrun_skips_slots_without_burst(self):
        sched=PeriodicDeadline(2.,start=10.)
        missed=sched.advance(17.8)
        self.assertEqual(missed,3)
        self.assertEqual(sched.due,18.)
        self.assertEqual(sched.skipped_total,3)
        self.assertAlmostEqual(sched.delay_s(17.8),.2)

    def test_lag_is_not_a_cross_container_clock_difference(self):
        sched=PeriodicDeadline(2.,start=5.)
        self.assertEqual(sched.lag_s(4.),0.)
        self.assertAlmostEqual(sched.lag_s(5.12),.12)


class CadenceAuditTests(unittest.TestCase):
    def test_run_filter_and_invalid_record(self):
        marker='HDT_AGENT_TIMING '
        text='edge-001 | '+marker+'{"run_id":"r1","tick":1}\n'+marker+'{"run_id":"other"}\n'+marker+'NOT_JSON\n'
        events, errors=parse_events(text,marker,'r1')
        self.assertEqual(len(events),1)
        self.assertEqual(errors,1)

    def test_missing_logs_report_unknown_not_healthy(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)
            (run/'metadata.json').write_text(json.dumps({
                'run_id':'r1','start_utc':'2026-11-03T10:00:00Z',
                'end_utc':'2026-11-03T10:00:06Z','node_ids':['node-000'],'n':1}))
            r=analyze_run(run)
            self.assertEqual(r['timing_log_status'],'missing_or_partial')
            self.assertEqual(r['agent']['schedule_lag_ms']['n'],0)
            self.assertIsNone(r['agent']['schedule_lag_ms']['p95'])

    def test_separate_attempt_vs_receipt_failures(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)
            logs=run/'scheduler_logs'; logs.mkdir()
            (run/'metadata.json').write_text(json.dumps({
                'run_id':'r1','start_utc':'2026-11-03T10:00:00+00:00',
                'end_utc':'2026-11-03T10:00:04+00:00','node_ids':['node-000'],'n':1}))
            a=[{'run_id':'r1','node_id':'node-000','tick':0,'schedule_lag_ms':2,
                 'reference_ok':True,'heartbeat_ok':True,'slots_skipped_after_tick':0,
                 'reference_http_ms':50,'heartbeat_http_ms':30},
               {'run_id':'r1','node_id':'node-000','tick':1,'schedule_lag_ms':700,
                 'reference_ok':False,'heartbeat_ok':True,'slots_skipped_after_tick':1,
                 'reference_http_ms':2400,'heartbeat_http_ms':50}]
            (logs/'edge_000.log').write_text('\n'.join('edge | HDT_AGENT_TIMING '+json.dumps(x) for x in a))
            (logs/'policy.log').write_text('policy | HDT_POLICY_TIMING '+json.dumps({
                'run_id':'r1','tick':0,'schedule_lag_ms':15,
                'cycle_wall_ms':40,'slots_skipped_after_tick':0,'cycle_failed':False}))
            (run/'reference.csv').write_text('ts,node_id\n2026-11-03T10:00:00Z,node-000\n')
            (run/'heartbeats.csv').write_text('ts,node_id\n2026-11-03T10:00:01Z,node-000\n2026-11-03T10:00:03Z,node-000\n')
            (run/'decisions.csv').write_text('ts,run_id\n2026-11-03T10:00:00Z,r1\n')
            result=analyze_run(run)
            self.assertEqual(result['timing_log_status'],'available')
            self.assertEqual(result['agent']['reference_http_failures'],1)
            self.assertEqual(result['agent']['heartbeat_http_failures'],0)
            self.assertEqual(result['agent']['ticks_skipped'],1)
            self.assertEqual(result['database_receipts']['reference']['rows'],1)
            self.assertEqual(result['database_receipts']['heartbeat']['rows'],2)
            self.assertEqual(result['database_receipts']['heartbeat']['interval_s']['mean'],2.)

    def test_docker_timing_log_export_is_post_measurement_and_non_destructive(self):
        from exp_iotj_run import export_scheduler_logs
        with tempfile.TemporaryDirectory() as d:
            start=datetime(2026,11,3,10,0,tzinfo=timezone.utc)
            end=start+timedelta(seconds=4)
            with patch('exp_iotj_run.docker',return_value=SimpleNamespace(stdout='HDT_AGENT_TIMING {}\n',stderr='')) as fake:
                failures=export_scheduler_logs(['policy','edge_000'],start,end,Path(d),override=Path(d)/'compose.yml')
            self.assertEqual(failures,[])
            self.assertTrue((Path(d)/'policy.log').is_file())
            self.assertTrue((Path(d)/'edge_000.log').is_file())
            self.assertEqual(fake.call_count,2)
            argv=fake.call_args_list[0].args
            self.assertEqual(argv[0],'logs')
            self.assertIn('--until',argv)
            self.assertNotIn('down',argv)

    def test_predeclared_cadence_plan(self):
        plan=make_plan('cadence',seed=20261103)
        self.assertEqual(len(plan),4)
        self.assertEqual({p['variant'] for p in plan},{'fixed_2s','fixed_3s','aoi_6s','budget_1500'})
        self.assertTrue(all(p['n']==4 and p['churn_fraction']==0 for p in plan))
        self.assertEqual(plan,make_plan('cadence',seed=20261103))


if __name__=='__main__':unittest.main()

"""Decision level diagnostic tests; they do not change the controller."""
import io
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from diagnose_phase11 import extract_decision_reasons

class DiagnosticTests(unittest.TestCase):
    def test_multiple_reasons_counted_once_per_decision(self):
        p='id,feasible,details\n'
        values=[{'violations':['latency_limit','latency_limit','error_limit']}, {'violations':[]}]
        for i,v in enumerate(values):
            import csv
            b=io.StringIO();w=csv.writer(b);w.writerow([i,'f' if i==0 else 't',json.dumps(v)])
            p+=b.getvalue()
        result=extract_decision_reasons(p.encode())
        self.assertEqual(result['decisions_parsed'],2)
        self.assertEqual(result['infeasible_decisions'],1)
        self.assertEqual(result['flag_latency_limit'],1)
        self.assertEqual(result['flag_error_limit'],1)
    def test_no_violations(self):
        result=extract_decision_reasons(b'id,feasible,details\n0,t,"{""violations"": []}"\n')
        self.assertEqual(result['infeasible_decisions'],0)
        self.assertEqual(result['detail_parse_errors'],0)

if __name__=='__main__': unittest.main()

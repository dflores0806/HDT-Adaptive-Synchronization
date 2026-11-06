import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assess_cadence_v10 import pareto_dominates

class CadenceAuditTests(unittest.TestCase):
    def test_strict_dominance(self):
        a={'operational_http_bytes':60,'nmae_dense_0p5s':0.01}
        b={'operational_http_bytes':80,'nmae_dense_0p5s':0.02}
        self.assertTrue(pareto_dominates(a,b))
        self.assertFalse(pareto_dominates(b,a))
    def test_tradeoff_not_dominance(self):
        a={'operational_http_bytes':60,'nmae_dense_0p5s':0.02}
        b={'operational_http_bytes':80,'nmae_dense_0p5s':0.01}
        self.assertFalse(pareto_dominates(a,b))
        self.assertFalse(pareto_dominates(b,a))
    def test_tie_is_not_strict_dominance(self):
        a={'operational_http_bytes':60,'nmae_dense_0p5s':0.01}
        self.assertFalse(pareto_dominates(a,a))

if __name__=='__main__': unittest.main()

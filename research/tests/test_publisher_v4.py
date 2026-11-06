"""MQTT control-plane batching contract without requiring a broker."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
class FakeMqtt:
    openings=0
    publications=[]
    def __init__(self,*args,**kwargs): pass
    async def __aenter__(self):
        FakeMqtt.openings+=1
        return self
    async def __aexit__(self,*args): pass
    async def publish(self,topic,payload,qos=0):
        FakeMqtt.publications.append((topic,payload,qos))

try:
    import aiomqtt
except ImportError:
    import types
    sys.modules['aiomqtt'] = types.SimpleNamespace(Client=FakeMqtt)
from middleware.services.publisher import PolicyPublisher

class PublisherTests(unittest.TestCase):
    def test_single_broker_connection_for_multiple_nodes(self):
        FakeMqtt.openings=0
        FakeMqtt.publications=[]
        payload={'delta_t':2.,'participate':True,'run_id':'run'}
        with patch('middleware.services.publisher.Client',FakeMqtt):
            result=asyncio.run(PolicyPublisher().send_many({'node-1':payload,'node-2':payload,'node-3':payload}))
        self.assertEqual(result,{})
        self.assertEqual(FakeMqtt.openings,1)
        self.assertEqual(len(FakeMqtt.publications),3)
        self.assertTrue(all(p[2]==1 for p in FakeMqtt.publications))

if __name__=='__main__':unittest.main()

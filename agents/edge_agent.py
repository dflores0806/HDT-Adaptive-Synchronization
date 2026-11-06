#!/usr/bin/env python3
"""Deterministic, correlated **synthetic** edge-state source (not a CPD trace).

Three traffic classes are deliberately separated:
  * /telemetry/state: operational DT state (policy-controlled),
  * /telemetry/heartbeat: fixed-rate small context / local rate proxy,
  * /telemetry/reference: *evaluation-only* reference state (fixed-rate).
All transmitted application payload bytes must be reported in comparisons.
"""
import argparse
import asyncio
import json
import os
import signal
import socket
import sys
import time
from contextlib import suppress
from datetime import datetime, timezone

import requests
from synthetic_process import node_seed, physical_state
from timing_v9 import PeriodicDeadline
from aiomqtt import Client, MqttError

if sys.platform.startswith('win'):
    with suppress(Exception):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class Agent:
    def __init__(self, node_id, api, broker, port):
        self.node_id, self.api, self.broker, self.port = node_id, api, broker, port
        self.delta_t = 2.
        self.participate = True
        self.last_policy_time = ''
        self.stop = asyncio.Event()
        self.offered = 0
        self.failed = 0
        self.last_latency_ms = 0.
        self.seed = node_seed(node_id)
        self.subsystem = ['compute', 'cooling', 'rack'][self.seed % 3]
        self.previous_sample = None
        self.local_rate = 0.003
        self.sequence = 0
        self.last_size = 256
        self.started = time.time()
        self.physical_epoch = float(os.getenv("PHYSICAL_EPOCH", str(self.started)))
        self.run_id = os.getenv("RUN_ID", "dev")
        self.synthetic_scenario_id = int(os.getenv('SYNTH_SCENARIO_ID', '0'))
        self._wake_update = asyncio.Event()
        # Controlled application-layer transport disturbance (NOT IP-layer netem).
        self.impair_profile = os.getenv('HDT_IMPAIR_PROFILE', 'none')
        if self.impair_profile not in ('none', 'delay', 'delay_loss'):
            raise ValueError('Invalid HDT_IMPAIR_PROFILE')
        self.impair_start_s = float(os.getenv('HDT_IMPAIR_START_S', '65'))
        self.impair_duration_s = float(os.getenv('HDT_IMPAIR_DURATION_S', '35'))
        self.impair_delay_ms = int(os.getenv('HDT_IMPAIR_DELAY_MS', '0'))
        self.impair_drop_every = int(os.getenv('HDT_IMPAIR_DROP_EVERY', '0'))
        if not 0 <= self.impair_delay_ms <= 1500 or not 0 <= self.impair_drop_every <= 100:
            raise ValueError('Invalid controlled perturbation parameters')
        self.impair_attempts = {'state': 0, 'heartbeat': 0}


    def physical_state(self, elapsed):
        return physical_state(self.node_id, elapsed, self.synthetic_scenario_id)

    @staticmethod
    def json_bytes(obj):
        return json.dumps(obj, separators=(',', ':'), allow_nan=False).encode('utf-8')

    def post(self, endpoint, payload):
        """HTTP with a logged, reproducible operational-only impairment.

        Reference observations bypass injection. Delay/loss is induced BEFORE
        requests reaches the HTTP stack, not genuine IP packet loss or queueing.
        """
        body = self.json_bytes(payload)
        elapsed = time.time() - self.physical_epoch
        phase = ('pre' if elapsed < self.impair_start_s else
                 'stress' if elapsed < self.impair_start_s + self.impair_duration_s
                 else 'recovery')
        active = (phase == 'stress' and endpoint in ('state', 'heartbeat')
                  and self.impair_profile != 'none')
        delay_ms = self.impair_delay_ms if active else 0
        dropped = False
        if active:
            self.impair_attempts[endpoint] += 1
            if (self.impair_profile == 'delay_loss' and self.impair_drop_every > 0
                    and self.impair_attempts[endpoint] % self.impair_drop_every == 0):
                dropped = True
        t0 = time.perf_counter()
        ok = False
        if delay_ms:
            time.sleep(delay_ms / 1000.)
        try:
            if not dropped:
                response = requests.post(f'{self.api}/telemetry/{endpoint}', data=body,
                    headers={'Content-Type': 'application/json'}, timeout=5)
                response.raise_for_status()
                ok = bool(response.json().get('ok'))
            # A controlled drop is deliberately NOT sent. Thus it is an
            # offered operation, never a received network frame.
        except Exception as ex:
            print(f'[{self.node_id}] {endpoint} request error: {ex}', flush=True)
        elapsed_ms = (time.perf_counter() - t0) * 1000.
        if endpoint in ('state','heartbeat'):
            self.last_latency_ms = elapsed_ms
        print('HDT_HTTP_OBSERVATION ' + json.dumps({
            'run_id': self.run_id, 'node_id': self.node_id,
            'endpoint': endpoint, 'phase': phase, 'profile': self.impair_profile,
            'utc': datetime.now(timezone.utc).isoformat(),
            'injection_active': active, 'injected_delay_ms': delay_ms,
            'injected_drop': dropped, 'application_ok': ok,
            'request_wall_ms': round(elapsed_ms, 3),
            'body_bytes_attempted': len(body)
        },separators=(',', ':')), flush=True)
        return ok, len(body)

    async def ref_and_heartbeat(self):
        """2-s fixed-epoch references with explicit skipped-slot and HTTP timing logs.

        References are evaluation-only; the online controller cannot consume them.
        The former implementation waited two seconds *after* both HTTP requests,
        unintentionally adding their durations to the nominal acquisition period.
        """
        period_s = 2.0
        loop = asyncio.get_running_loop()
        schedule = PeriodicDeadline(period_s, loop.time())
        tick = 0
        while not self.stop.is_set():
            if schedule.delay_s(loop.time()) > 0:
                await self.sleep(schedule.delay_s(loop.time()))
            if self.stop.is_set():
                break
            actual = loop.time()
            lag_ms = 1000.0 * schedule.lag_s(actual)
            timestamp = datetime.now(timezone.utc).isoformat()
            elapsed = time.time() - self.physical_epoch
            state = self.physical_state(elapsed)
            self.sequence += 1
            seq = self.sequence
            if self.previous_sample is not None:
                t_old, s_old = self.previous_sample
                dt = max(.001, elapsed-t_old)
                self.local_rate = min(.1, sum(abs(state[k]-s_old[k])/r for k,r in
                    (('temperature_c', 40.), ('load_fraction', 1.), ('cooling_fraction', 1.))) / (3*dt))
            self.previous_sample = (elapsed, state)
            payload = dict(run_id=self.run_id, node_id=self.node_id, sample_time=timestamp, seq=seq, state=state)
            call_start = loop.time()
            ref_ok, ref_bytes = await asyncio.to_thread(self.post, 'reference', payload)
            ref_http_ms = 1000.0*(loop.time()-call_start)
            hb = dict(run_id=self.run_id, node_id=self.node_id, subsystem=self.subsystem,
                      latency_ms=round(self.last_latency_ms, 4),
                      energy_w=round(6. + 8.*state['load_fraction'], 4),
                      drift_rate_norm_s=round(self.local_rate, 7),
                      bytes_per_update=self.last_size,
                      updates_offered=self.offered, updates_failed=self.failed)
            call_start = loop.time()
            hb_ok, hb_bytes = await asyncio.to_thread(self.post, 'heartbeat', hb)
            hb_http_ms = 1000.0*(loop.time()-call_start)
            finished = loop.time()
            missed = schedule.advance(finished)
            print('HDT_AGENT_TIMING ' + json.dumps({
                'run_id':self.run_id, 'node_id':self.node_id, 'tick':tick,
                'time_utc':timestamp, 'period_s':period_s,
                'schedule_lag_ms':round(lag_ms,3),
                'tick_wall_ms':round(1000*(finished-actual),3),
                'reference_http_ms':round(ref_http_ms,3),
                'heartbeat_http_ms':round(hb_http_ms,3),
                'reference_ok':ref_ok, 'heartbeat_ok':hb_ok,
                'reference_body_bytes_attempted':ref_bytes,
                'heartbeat_body_bytes_attempted':hb_bytes,
                'slots_skipped_after_tick':missed,
                'updates_offered_total':self.offered,
                'updates_failed_total':self.failed
            },separators=(',', ':')), flush=True)
            tick += 1

    async def updates(self):
        while not self.stop.is_set():
            if self.participate:
                elapsed = time.time() - self.physical_epoch
                self.sequence += 1
                payload = dict(run_id=self.run_id, node_id=self.node_id,
                    sample_time=datetime.now(timezone.utc).isoformat(), seq=self.sequence,
                    state=self.physical_state(elapsed))
                self.offered += 1
                interval_at_attempt = self.delta_t
                attempt_utc = datetime.now(timezone.utc).isoformat()
                request_start = asyncio.get_running_loop().time()
                ok, size = await asyncio.to_thread(self.post, 'state', payload)
                request_ms = 1000*(asyncio.get_running_loop().time()-request_start)
                print('HDT_UPDATE_ATTEMPT ' + json.dumps({
                    'run_id':self.run_id, 'node_id':self.node_id,
                    'attempt_utc':attempt_utc,'seq':payload['seq'],
                    'commanded_interval_s':interval_at_attempt,
                    'body_bytes_attempted':size,'application_ok':ok,
                    'request_wall_ms':round(request_ms,3)
                },separators=(',', ':')), flush=True)
                self.last_size = size
                if not ok:
                    self.failed += 1
            # A newly readmitted node sends its first update immediately.
            try:
                await asyncio.wait_for(self._wake_update.wait(), timeout=self.delta_t)
                self._wake_update.clear()
            except asyncio.TimeoutError:
                pass

    async def listener(self):
        topic = f'hdt/edge/{self.node_id}/policy'
        backoff = 1.
        while not self.stop.is_set():
            try:
                async with Client(self.broker, self.port) as client:
                    await client.subscribe(topic)
                    backoff = 1.
                    async for message in client.messages:
                        try:
                            payload = json.loads(message.payload.decode('utf8'))
                            if payload.get('run_id') != self.run_id:
                                continue
                            issued_at = str(payload.get('issued_at', ''))
                            if issued_at and issued_at < self.last_policy_time:
                                continue
                            self.last_policy_time = issued_at
                            self.delta_t = max(.2, min(60., float(payload['delta_t'])))
                            previous = self.participate
                            self.participate = bool(payload['participate'])
                            if self.participate and not previous:
                                self._wake_update.set()
                            ack = {'node_id': self.node_id, 'issued_at': issued_at,
                                'applied': True, 'delta_t': self.delta_t,
                                'participate': self.participate, 'time': time.time()}
                            await client.publish(f'hdt/edge/{self.node_id}/ack', self.json_bytes(ack))
                        except Exception as ex:
                            print(f'[{self.node_id}] Invalid policy: {ex}', flush=True)
            except (MqttError, OSError) as ex:
                print(f'[{self.node_id}] MQTT unavailable: {ex}', flush=True)
                await self.sleep(backoff)
                backoff = min(10., 2*backoff)

    async def sleep(self, period):
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=max(.05, period))
        except asyncio.TimeoutError:
            pass

    async def run(self):
        await asyncio.gather(self.listener(), self.ref_and_heartbeat(), self.updates())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', default=os.getenv('NODE_ID', socket.gethostname()))
    parser.add_argument('--api', default=os.getenv('API_URL', 'http://localhost:8000'))
    parser.add_argument('--broker', default=os.getenv('MQTT_BROKER', 'localhost'))
    parser.add_argument('--port', type=int, default=int(os.getenv('MQTT_PORT', '1883')))
    args = parser.parse_args()
    agent = Agent(args.node, args.api, args.broker, args.port)
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(Exception):
            signal.signal(sig, lambda *_: agent.stop.set())
    asyncio.run(agent.run())

if __name__ == '__main__':
    main()

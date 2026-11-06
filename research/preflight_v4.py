#!/usr/bin/env python3
"""Non-destructive host readiness report for the isolated IoTJ Docker pilot.

Run static checks anywhere; --docker verifies daemon/compose/port availability.
Never starts or deletes containers, volumes or experiment data.
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_docker():
    if not shutil.which('docker'):
        raise RuntimeError('docker no está instalado o no está en PATH')
    for command in (['docker', 'info', '--format', '{{.ServerVersion}}'],
                    ['docker', 'compose', 'version']):
        check = subprocess.run(command, capture_output=True, text=True, timeout=25)
        if check.returncode:
            raise RuntimeError('Error ejecutando %s: %s' % (' '.join(command), check.stderr[:500]))
        print('OK:', ' '.join(command), '=>', check.stdout.strip())
    config = subprocess.run(['docker', 'compose', '-p', os.getenv('HDT_COMPOSE_PROJECT','hdt_iotj26'),
                             '-f', str(ROOT/'docker-compose.yml'), 'config', '--quiet'],
                            capture_output=True, text=True, timeout=25)
    if config.returncode:
        raise RuntimeError('docker compose config: ' + config.stderr[:1000])
    print('OK: docker compose config')
    for key, default in [('HDT_API_PORT', 18000), ('HDT_PG_PORT',55432), ('HDT_MQTT_PORT',18883)]:
        port = int(os.environ.get(key, default))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(.2)
            occupied = sock.connect_ex(('127.0.0.1', port)) == 0
        if occupied:
            raise RuntimeError(f'Puerto local {port} ocupado ({key}). Configure otro puerto antes de ejecutar.')
        print('OK: puerto libre', key, port)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--docker',action='store_true',help='Comprobar Docker y puertos locales')
    args=p.parse_args()
    print('Python:',sys.version.split()[0], '| directorio:',ROOT)
    required=['docker-compose.yml','exp_iotj_run.py','analyze_v2.py',
              'research/campaign_v3.py','middleware/app.py','agents/edge_agent.py',
              'synthetic_process.py']
    missing=[x for x in required if not (ROOT/x).is_file()]
    if missing:
        raise SystemExit('Archivos ausentes: '+', '.join(missing))
    print('OK: archivos de campaña presentes')
    if args.docker:
        try:
            verify_docker()
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            raise SystemExit('FALLO de preflight: '+str(exc))
    else:
        print('Comprobación estática solamente. Use --docker en el host de pruebas.')

if __name__=='__main__':main()

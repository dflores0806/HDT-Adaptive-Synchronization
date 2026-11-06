"""Declared deterministic synthetic physical process for reproducible tests.

Knowledge of this function belongs ONLY to synthetic evaluation, not to the
operational decision worker. It does not represent measured physical data.
"""
import hashlib
import math


def node_seed(node_id):
    return int.from_bytes(hashlib.sha256(node_id.encode()).digest()[:4], 'big')


def physical_state(node_id, elapsed_s, scenario_id=0):
    """Same deterministic source for all policies in a replicate.

    scenario_id=0 is bitwise-compatible with the earlier synthetic process.
    Positive IDs define explicitly different deterministic dynamics; these are
    *synthetic scenarios*, not independent real-world physical measurements.
    """
    if not isinstance(scenario_id, int) or not 0 <= scenario_id <= 99:
        raise ValueError("scenario_id must be an integer in 0..99")
    seed = node_seed(node_id)
    phase = (seed % 503) / 503. * 2 * math.pi
    if scenario_id:
        scenario_seed = node_seed(f'{node_id}:scenario:{scenario_id}')
        phase += ((scenario_seed % 1009)/1009. - .5) * 2.2
        elapsed_s = elapsed_s * (0.82 + 0.12*(scenario_id % 4))
    load = max(0., min(1., .48 + .27*math.sin(elapsed_s/19. + phase) + .06*math.sin(elapsed_s/4.3)))
    cooling = max(0., min(1., .40 + .23*math.sin(elapsed_s/27. + phase/2.) + .22*load))
    temperature = 23. + 3.2*load - 1.5*cooling + .7*math.sin(elapsed_s/40. + phase)
    return {'temperature_c':round(temperature, 5), 'load_fraction':round(load,5),
            'cooling_fraction':round(cooling,5)}

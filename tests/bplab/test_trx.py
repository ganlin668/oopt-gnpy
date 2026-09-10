# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# test_bplab_trx
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
Unit tests for gnpy.bplab.trx
"""
import json
from pathlib import Path

import pytest

from gnpy.bplab.trx import TrxMode, launch_power_dbm, load_equipment_with_module_power, register_trx_type
from gnpy.core.equipment import trx_mode_params
from gnpy.tools.json_io import DEFAULT_EQPT_CONFIG, load_equipment, load_json

TFLN_800G = TrxMode(format='800G ZR+ TFLN BOL', baud_rate=131.3e9, OSNR=24.5, bit_rate=800e9,
                    roll_off=0.05, tx_osnr=41, tx_power=-6, min_spacing=150e9, cost=1)


def test_register_trx_type():
    """代码定义的模块能被 gnpy 原生 trx_mode_params 取到，tx_power 映射为 tx_channel_power_max_dbm"""
    equipment = load_equipment(Path(DEFAULT_EQPT_CONFIG))
    register_trx_type(equipment, 'TFLN_800G', [TFLN_800G], f_min=186.275e12, f_max=196.075e12)
    params = trx_mode_params(equipment, 'TFLN_800G', '800G ZR+ TFLN BOL')
    assert params['baud_rate'] == 131.3e9
    assert params['OSNR'] == 24.5
    assert params['tx_osnr'] == 41
    assert params['min_spacing'] == 150e9
    assert params['tx_power'] == -6
    assert params['tx_channel_power_max_dbm'] == -6
    assert params['f_min'] == 186.275e12 and params['f_max'] == 196.075e12


def test_register_trx_type_does_not_mutate_mode():
    """注册后 TrxMode 本身不被 json_io.Transceiver 原地改写"""
    equipment = load_equipment(Path(DEFAULT_EQPT_CONFIG))
    register_trx_type(equipment, 'TFLN_800G', [TFLN_800G], f_min=191.35e12, f_max=196.1e12)
    assert TFLN_800G.penalties == [] and TFLN_800G.detailed_rx == {}


def test_adapter_matches_load_equipment_for_upstream_file():
    """对不含 tx_power 的上游文件，适配器结果与 gnpy 原生 load_equipment 完全一致"""
    adapted = load_equipment_with_module_power(DEFAULT_EQPT_CONFIG)
    reference = load_equipment(Path(DEFAULT_EQPT_CONFIG))
    assert sorted(adapted) == sorted(reference)
    assert adapted['SI']['default'].__dict__ == reference['SI']['default'].__dict__
    assert sorted(adapted['Edfa']) == sorted(reference['Edfa'])
    assert adapted['Edfa']['std_low_gain'].__dict__ == reference['Edfa']['std_low_gain'].__dict__


def test_load_equipment_with_module_power(tmp_path):
    """含 tx_power 的设备库能被加载，且 tx_power 映射到 tx_channel_power_max_dbm"""
    data = load_json(Path(DEFAULT_EQPT_CONFIG))
    mode = data['Transceiver'][1]['mode'][0]
    mode['tx_power'] = -6.5
    filename = tmp_path / 'eqpt_config.json'
    filename.write_text(json.dumps(data), encoding='utf-8')

    equipment = load_equipment_with_module_power(filename)
    params = trx_mode_params(equipment, data['Transceiver'][1]['type_variety'], mode['format'])
    assert params['tx_power'] == -6.5
    assert params['tx_channel_power_max_dbm'] == -6.5


def test_load_equipment_with_module_power_rejects_other_invalid_fields(tmp_path):
    """适配器只放行 tx_power，其它非法字段仍会被 YANG 校验拒绝"""
    data = load_json(Path(DEFAULT_EQPT_CONFIG))
    data['Transceiver'][1]['mode'][0]['not_a_field'] = 1
    filename = tmp_path / 'eqpt_config.json'
    filename.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(Exception):
        load_equipment_with_module_power(filename)


def test_launch_power_dbm_defaults_to_module_max_power():
    assert launch_power_dbm({'tx_power': -6}) == -6


def test_launch_power_dbm_clamps_and_warns(caplog):
    with caplog.at_level('WARNING'):
        assert launch_power_dbm({'tx_power': -6}, 0) == -6
    assert 'clamping' in caplog.text


def test_launch_power_dbm_below_module_max_power():
    assert launch_power_dbm({'tx_power': -6}, -10) == -10


def test_launch_power_dbm_without_module_power():
    assert launch_power_dbm({}, -3) == -3
    assert launch_power_dbm({}) is None

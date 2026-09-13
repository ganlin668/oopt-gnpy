# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# test_bplab_edfa
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
Unit tests for the gain dependent noise figure EDFA in gnpy.bplab.edfa
"""
from pathlib import Path

import pytest
from numpy import concatenate, interp
from numpy.testing import assert_allclose

from gnpy.bplab.edfa import (NF_CURVE_KEY, GainNfEdfa, GainNfMultibandAmplifier, attach_nf_curves,
                             extract_nf_curves, parse_nf_curve)
from gnpy.bplab.trx import load_equipment_with_module_power
from gnpy.bplab.utils import band_center_frequencies
from gnpy.core.elements import Edfa
from gnpy.core.exceptions import EquipmentConfigError
from gnpy.core.info import create_arbitrary_spectral_information
from gnpy.core.utils import dbm2watt
from gnpy.tools.json_io import load_json

SPACING = 150e9
EQPT_CONFIG = Path(__file__).parents[2] / 'scripts' / 'ganlin' / 'test_gnpy' / 'eqpt_config.json'
C96_VARIETY = 'C96_SGA_22dBm'
L96_VARIETY = 'L96_SGA_21dBm'
# 与设备库中的占位表一致
CURVE = [{'gain': 20.0, 'nf': 7.0}, {'gain': 25.0, 'nf': 5.5}]


@pytest.fixture(scope='module')
def equipment():
    return load_equipment_with_module_power(EQPT_CONFIG)


def make_si(bands=('L96', 'C96')):
    """按模块 150 GHz 栅格取波道的频谱（默认 C96 + L96 全谱）"""
    frequency = concatenate([band_center_frequencies(band, SPACING) for band in bands])
    return create_arbitrary_spectral_information(
        frequency=frequency, pch=dbm2watt(-6), baud_rate=131.3e9, tx_osnr=41,
        slot_width=SPACING, roll_off=0.05, tx_power=dbm2watt(-6))


def make_amp(variety, gain_target, equipment):
    """按设备库参数建单波段光放（operational 里给出工作增益）"""
    return GainNfEdfa(uid='OA1', params=dict(equipment['Edfa'][variety].__dict__),
                      operational={'gain_target': gain_target, 'tilt_target': 0})


def table_of(variety, equipment):
    return parse_nf_curve(getattr(equipment['Edfa'][variety], NF_CURVE_KEY))


def test_parse_nf_curve_sorts_by_gain():
    gains, nfs = parse_nf_curve([{'gain': 25.0, 'nf': 5.5}, {'gain': 20.0, 'nf': 7.0}])
    assert_allclose(gains, [20.0, 25.0])
    assert_allclose(nfs, [7.0, 5.5])


@pytest.mark.parametrize('entries', (None, []))
def test_parse_nf_curve_absent_means_no_lookup(entries):
    assert parse_nf_curve(entries) is None


@pytest.mark.parametrize('entries', (
    [{'gain': 20.0, 'nf': 7.0}],                             # 只有 1 个增益点
    [{'gain': 22.0, 'nf': 7.0}, {'gain': 22.0, 'nf': 6.0}],  # 增益不严格递增
    [{'gain': 20.0}],                                        # 缺 nf 字段
))
def test_parse_nf_curve_rejects_invalid_tables(entries):
    with pytest.raises(EquipmentConfigError):
        parse_nf_curve(entries)


def test_loader_attaches_nf_curve_to_edfa_entries(equipment):
    """设备库 json 里的 nf_vs_gain 不经过 YANG 校验，但必须被加载器带回设备库"""
    raw = load_json(EQPT_CONFIG)
    expected = {entry['type_variety']: entry[NF_CURVE_KEY]
                for entry in raw['Edfa'] if NF_CURVE_KEY in entry}
    assert set(expected) == {C96_VARIETY, L96_VARIETY}
    for variety, curve in expected.items():
        assert getattr(equipment['Edfa'][variety], NF_CURVE_KEY) == curve
    # 多波段条目不含该字段，不应凭空多出属性
    assert not hasattr(equipment['Edfa']['C96L96_SGA_multiband'], NF_CURVE_KEY)


def test_extract_nf_curves_handles_other_name_and_removes_field():
    json_data = {'Edfa': [{'type_variety': 'A', 'other_name': ['A1'], NF_CURVE_KEY: CURVE},
                          {'type_variety': 'B'}]}
    curves = extract_nf_curves(json_data)
    assert set(curves) == {'A', 'A1'}
    assert NF_CURVE_KEY not in json_data['Edfa'][0]


def test_attach_nf_curves_ignores_unknown_variety():
    class DummyAmp:
        pass

    equipment = {'Edfa': {'A': DummyAmp()}}
    attach_nf_curves(equipment, {'A': CURVE, 'missing': CURVE})
    assert equipment['Edfa']['A'].nf_vs_gain == CURVE


@pytest.mark.parametrize('gain_target', (20.0, 22.5, 25.0, 18.0, 27.0))
def test_gain_nf_edfa_interpolates_and_clamps(gain_target, equipment):
    """表内线性插值，表外（18 / 27 dB）钳位到端点值"""
    amp = make_amp(C96_VARIETY, gain_target, equipment)
    gains, nfs = table_of(C96_VARIETY, equipment)
    assert amp._calc_nf(avg=True) == pytest.approx(interp(gain_target, gains, nfs))


def test_gain_nf_edfa_leaves_att_in_at_zero(equipment):
    amp = make_amp(C96_VARIETY, 18.0, equipment)
    amp._calc_nf(avg=True)
    assert amp.att_in == 0


def test_gain_nf_edfa_nf_is_flat_over_the_band(equipment):
    amp = make_amp(C96_VARIETY, 22.0, equipment)
    si = make_si(bands=('C96',))
    amp.interpol_params(si)

    assert amp.nf.size == si.number_of_channels
    assert_allclose(amp.nf, amp.nf[0], atol=1e-12)
    gains, nfs = table_of(C96_VARIETY, equipment)
    # 饱和后 effective_gain 可能低于 operational.gain_target，故按实际工作增益查表
    assert amp.nf[0] == pytest.approx(interp(amp.effective_gain, gains, nfs))


def test_gain_nf_edfa_without_curve_falls_back_to_upstream_model(equipment):
    params = dict(equipment['Edfa'][C96_VARIETY].__dict__)
    params.pop(NF_CURVE_KEY)
    operational = {'gain_target': 22.0, 'tilt_target': 0}
    ours = GainNfEdfa(uid='OA1', params=params, operational=operational)
    upstream = Edfa(uid='OA1', params=params, operational=operational)

    assert ours.nf_curve is None
    assert ours._calc_nf(avg=True) == pytest.approx(upstream._calc_nf(avg=True))


def test_gain_nf_multiband_amplifier_uses_each_band_own_table(equipment):
    gain_target = 22.0

    def band_amp(variety):
        return {'type_variety': variety,
                'params': dict(equipment['Edfa'][variety].__dict__),
                'operational': {'gain_target': gain_target, 'tilt_target': 0, 'out_voa': 0, 'in_voa': 0}}

    amp = GainNfMultibandAmplifier(
        uid='OA1', type_variety='C96L96_SGA_multiband',
        params=dict(equipment['Edfa']['C96L96_SGA_multiband'].__dict__),
        amplifiers=[band_amp(L96_VARIETY), band_amp(C96_VARIETY)])

    assert set(amp.amplifiers) == {'LBAND', 'CBAND'}
    assert all(isinstance(sub_amp, GainNfEdfa) for sub_amp in amp.amplifiers.values())

    amp(make_si())

    for band_name, sub_amp in amp.amplifiers.items():
        gains, nfs = table_of(sub_amp.params.type_variety, equipment)
        assert sub_amp.nf.mean() == pytest.approx(interp(sub_amp.effective_gain, gains, nfs)), band_name

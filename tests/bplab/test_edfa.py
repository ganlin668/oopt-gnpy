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
from numpy import arange, concatenate, interp, polyfit, polyval
from numpy.testing import assert_allclose

from gnpy.bplab.edfa import (BPLAB_EXTRA_CONFIGS, LINEAR_DGT_CONFIG, LINEAR_DGT_CONFIG_NAME, NF_CURVE_KEY,
                             GainNfEdfa, GainNfMultibandAmplifier, attach_nf_curves,
                             extract_nf_curves, parse_nf_curve, restore_gain_range,
                             stub_gain_range_for_curves)
from gnpy.bplab.trx import load_equipment_with_module_power
from gnpy.bplab.utils import DWDM_BAND_RANGES, band_center_frequencies, band_filling_center_frequencies
from gnpy.core.elements import Edfa
from gnpy.core.exceptions import EquipmentConfigError
from gnpy.core.info import create_arbitrary_spectral_information
from gnpy.core.utils import dbm2watt
from gnpy.tools.json_io import load_json

SPACING = 150e9
EQPT_CONFIG = Path(__file__).parents[2] / 'scripts' / 'ganlin' / 'test_gnpy' / 'eqpt_config.json'
C96_VARIETY = 'C96_SGA_22dBm'
L96_VARIETY = 'L96_SGA_21dBm'
# 高功率版本（25 / 24 dBm），增益-NF 表与低功率版本各自独立
C96_HIGH_POWER_VARIETY = 'C96_SGA_25dBm'
L96_HIGH_POWER_VARIETY = 'L96_SGA_24dBm'
# 带增益-NF 表的单波段型号
CURVE_VARIETIES = (L96_VARIETY, C96_VARIETY, L96_HIGH_POWER_VARIETY, C96_HIGH_POWER_VARIETY)
# 多波段型号（不含增益-NF 表）；低功率版由 L96_SGA_21dBm + C96_SGA_22dBm 组成
MULTIBAND_VARIETY = 'C96L96_SGA_multiband_22/21'
MULTIBAND_VARIETIES = (MULTIBAND_VARIETY, 'C96L96_SGA_multiband_25/24')
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


def make_amp(variety, gain_target, equipment, tilt_target=0):
    """按设备库参数建单波段光放（operational 里给出工作增益与倾斜）"""
    return GainNfEdfa(uid='OA1', params=dict(equipment['Edfa'][variety].__dict__),
                      operational={'gain_target': gain_target, 'tilt_target': tilt_target})


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
    assert set(expected) == set(CURVE_VARIETIES)
    for variety, curve in expected.items():
        assert getattr(equipment['Edfa'][variety], NF_CURVE_KEY) == curve
    # 多波段条目不含该字段，不应凭空多出属性
    for variety in MULTIBAND_VARIETIES:
        assert not hasattr(equipment['Edfa'][variety], NF_CURVE_KEY)


def test_loader_restores_declared_gain_range(equipment):
    """L96 声明的增益窗口上游 2 级拟合不接受，加载时用占位值，加载后必须还原成配置值"""
    raw = load_json(EQPT_CONFIG)
    for entry in raw['Edfa']:
        if 'gain_min' not in entry:     # 多波段条目只有 amplifiers 列表
            continue
        amp = equipment['Edfa'][entry['type_variety']]
        assert amp.gain_min == entry['gain_min']
        assert amp.gain_flatmax == entry['gain_flatmax']
        # 上游把 nf_min/nf_max 收进 nf_model，Amp 上不再保留同名字段
        assert amp.nf_model.orig_nf_min == entry['nf_min']
        assert amp.nf_model.orig_nf_max == entry['nf_max']


def test_linear_dgt_config_is_strictly_linear():
    """bplab 自带的 linear_dgt.json：DGT 是严格直线，且不引入 gain / nf 纹波"""
    dgt = LINEAR_DGT_CONFIG['dgt']
    index = arange(len(dgt))
    residual = dgt - polyval(polyfit(index, dgt, 1), index)
    assert max(abs(residual)) < 1e-12
    assert LINEAR_DGT_CONFIG['gain_ripple'] == [0.0]
    assert LINEAR_DGT_CONFIG['nf_ripple'] == [0.0]


def test_loader_provides_bplab_extra_configs(equipment):
    """设备库条目用 default_config_from_json 引用 'linear_dgt.json'，由加载器自动并入"""
    assert LINEAR_DGT_CONFIG_NAME in BPLAB_EXTRA_CONFIGS
    for variety in (C96_VARIETY, L96_VARIETY):
        amp = equipment['Edfa'][variety]
        assert list(amp.dgt) == LINEAR_DGT_CONFIG['dgt']
        assert list(amp.gain_ripple) == [0.0]


def test_gain_profile_is_strictly_linear_with_tilt(equipment):
    """linear_dgt 下施加 tilt 后增益谱是精确直线：跨度 = tilt_target × 使用频带 / 放大器频带"""
    amp = make_amp(C96_VARIETY, 22.0, equipment, tilt_target=-2.0)
    amp.interpol_params(make_si(bands=('C96',)))

    index = arange(len(amp.gprofile))
    residual = amp.gprofile - polyval(polyfit(index, amp.gprofile, 1), index)
    assert max(abs(residual)) < 1e-9
    expected = 2.0 * (amp.channel_freq[-1] - amp.channel_freq[0]) / (amp.params.f_max - amp.params.f_min)
    assert amp.gprofile[-1] - amp.gprofile[0] == pytest.approx(expected, rel=1e-6)


def make_low_power_si(n_channels=4, pch_dbm=-25):
    """低功率入纤：p_max - pin 足够大，增益不受饱和门限限制，便于单独验证规范钳制"""
    frequency = band_filling_center_frequencies(*DWDM_BAND_RANGES['C96'], SPACING)[:n_channels]
    return create_arbitrary_spectral_information(
        frequency=frequency, pch=dbm2watt(pch_dbm), baud_rate=131.3e9, tx_osnr=41,
        slot_width=SPACING, roll_off=0.05, tx_power=dbm2watt(pch_dbm))


def test_gain_above_curve_range_is_clamped_to_table_max(equipment, caplog):
    """要求增益高于增益-NF 表上限：增益被钳到表上限（而不是按超规格运行）"""
    gains, nfs = table_of(C96_VARIETY, equipment)
    amp = make_amp(C96_VARIETY, gains[-1] + 2, equipment)

    with caplog.at_level('WARNING'):
        amp.interpol_params(make_low_power_si())

    assert amp.effective_gain == gains[-1]
    assert 'clamped' in caplog.text
    assert_allclose(amp.nf.mean(), nfs[-1], atol=1e-9)


def test_gain_below_curve_range_is_clamped_to_table_min(equipment, caplog):
    """要求增益低于增益-NF 表下限：增益被钳到表下限"""
    gains, nfs = table_of(C96_VARIETY, equipment)
    amp = make_amp(C96_VARIETY, gains[0] - 2, equipment)

    with caplog.at_level('WARNING'):
        amp.interpol_params(make_low_power_si())

    assert amp.effective_gain == gains[0]
    assert 'clamped' in caplog.text
    assert_allclose(amp.nf.mean(), nfs[0], atol=1e-9)


def test_gain_within_curve_range_is_not_clamped(equipment, caplog):
    """增益在表范围内：不钳制、不告警"""
    amp = make_amp(C96_VARIETY, 20.0, equipment)

    with caplog.at_level('WARNING'):
        amp.interpol_params(make_low_power_si())

    assert amp.effective_gain == 20.0
    assert 'clamped' not in caplog.text


def test_stub_gain_range_only_touches_unfittable_entries_with_curve():
    json_data = {'Edfa': [
        # 能通过上游拟合
        {'type_variety': 'fittable', 'type_def': 'variable_gain', 'gain_min': 20, 'gain_flatmax': 25,
         'nf_min': 5.5, 'nf_max': 7, NF_CURVE_KEY: CURVE},
        # 拟合不合法
        {'type_variety': 'unfittable', 'type_def': 'variable_gain', 'gain_min': 12, 'gain_flatmax': 30,
         'nf_min': 5.6, 'nf_max': 12.2, NF_CURVE_KEY: CURVE},
        # 拟合不合法但没有增益-NF 表：不属于本机制，不应改动
        {'type_variety': 'no_curve', 'type_def': 'variable_gain', 'gain_min': 12, 'gain_flatmax': 30,
         'nf_min': 5.6, 'nf_max': 12.2},
    ]}

    saved = stub_gain_range_for_curves(json_data)

    assert set(saved) == {'unfittable'}
    assert json_data['Edfa'][0]['gain_min'] == 20
    assert json_data['Edfa'][1]['gain_min'] != 12
    assert json_data['Edfa'][2]['gain_min'] == 12

    class DummyAmp:
        pass

    amp = DummyAmp()
    amp.gain_min = json_data['Edfa'][1]['gain_min']
    restore_gain_range({'Edfa': {'unfittable': amp}}, saved)
    assert amp.gain_min == 12


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


def test_gain_nf_edfa_warns_below_table_minimum(equipment, caplog):
    amp = make_amp(C96_VARIETY, 11.0, equipment)
    with caplog.at_level('WARNING'):
        amp._calc_nf(avg=True)
    assert 'below' in caplog.text
    assert '11.00 dB' in caplog.text
    assert '12.00 dB' in caplog.text


def test_gain_nf_edfa_warns_above_table_maximum(equipment, caplog):
    amp = make_amp(C96_VARIETY, 31.0, equipment)
    with caplog.at_level('WARNING'):
        amp._calc_nf(avg=True)
    assert 'above' in caplog.text
    assert '31.00 dB' in caplog.text
    assert '30.00 dB' in caplog.text


@pytest.mark.parametrize('gain_target', (12.0, 20.0, 30.0))
def test_gain_nf_edfa_does_not_warn_inside_table_range(gain_target, equipment, caplog):
    amp = make_amp(C96_VARIETY, gain_target, equipment)
    with caplog.at_level('WARNING'):
        amp._calc_nf(avg=True)
    assert caplog.text == ''


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
        uid='OA1', type_variety=MULTIBAND_VARIETY,
        params=dict(equipment['Edfa'][MULTIBAND_VARIETY].__dict__),
        amplifiers=[band_amp(L96_VARIETY), band_amp(C96_VARIETY)])

    assert set(amp.amplifiers) == {'LBAND', 'CBAND'}
    assert all(isinstance(sub_amp, GainNfEdfa) for sub_amp in amp.amplifiers.values())

    amp(make_si())

    for band_name, sub_amp in amp.amplifiers.items():
        gains, nfs = table_of(sub_amp.params.type_variety, equipment)
        assert sub_amp.nf.mean() == pytest.approx(interp(sub_amp.effective_gain, gains, nfs)), band_name

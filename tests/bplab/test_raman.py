# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# test_bplab_raman
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
Unit tests for the stimulated Raman scattering solver in gnpy.bplab.raman
"""
from pathlib import Path

import pytest
from numpy import array, asarray, exp, sqrt
from numpy.testing import assert_allclose
from scipy.constants import c

from gnpy.bplab.raman import (DEFAULT_GR_PEAK, DEFAULT_RAMAN_NS, DEFAULT_RAMAN_REFERENCE_WAVELENGTH, RAMAN_GAIN_KEY,
                             RamanParams, RamanSolver, SrsFiber, _get_gR_dat, raman_gain_table)
from gnpy.bplab.trx import load_equipment_with_module_power
from gnpy.bplab.utils import DWDM_BAND_RANGES, band_filling_center_frequencies
from gnpy.core.elements import Fiber
from gnpy.core.exceptions import EquipmentConfigError
from gnpy.core.info import create_arbitrary_spectral_information
from gnpy.core.parameters import DEFAULT_RAMAN_COEFFICIENT
from gnpy.core.science_utils import RamanSolver as UpstreamRamanSolver
from gnpy.core.science_utils import StimulatedRamanScattering
from gnpy.core.utils import dbm2watt, lin2db
from gnpy.tools.json_io import load_json

SPACING = 150e9
EQPT_CONFIG = Path(__file__).parents[2] / 'scripts' / 'ganlin' / 'test_gnpy' / 'eqpt_config.json'
FIBER_VARIETY = 'G.652.D'
LENGTH_KM = 80.0
LOSS_COEF = 0.25
CON_OUT_DB = 8.0                    # 跨度损耗 = 0.25 dB/km * 80 km + 8 dB = 28 dB
LOCATION = {'location': {'city': '', 'region': '', 'latitude': 0, 'longitude': 0}}
L96_NCH = len(band_filling_center_frequencies(*DWDM_BAND_RANGES['L96'], SPACING))


@pytest.fixture(scope='module')
def equipment():
    return load_equipment_with_module_power(EQPT_CONFIG)


def make_fiber(equipment, cls=SrsFiber, raman_params=None, **overrides):
    """按设备库参数建光纤（默认 SrsFiber），只覆盖与具体链路相关的参数"""
    params = dict(equipment['Fiber'][FIBER_VARIETY].__dict__)
    params.update(length=LENGTH_KM, length_units='km', loss_coef=LOSS_COEF,
                  att_in=0, con_in=0, con_out=CON_OUT_DB)
    params.update(overrides)
    extra = {'raman_params': raman_params} if raman_params is not None else {}
    return cls(uid='Span1', type_variety=FIBER_VARIETY, params=params, metadata=LOCATION, **extra)


def make_si(frequencies, pch_dbm=-6):
    """按模块 150 GHz 栅格给定频点的频谱"""
    return create_arbitrary_spectral_information(
        frequency=array(frequencies), pch=dbm2watt(pch_dbm), baud_rate=131.3e9, tx_osnr=41,
        slot_width=SPACING, roll_off=0.05, tx_power=dbm2watt(pch_dbm))


def c_l_band_frequencies():
    """C96 + L96 全谱（各 32 波，升序）"""
    l96 = band_filling_center_frequencies(*DWDM_BAND_RANGES['L96'], SPACING)
    c96 = band_filling_center_frequencies(*DWDM_BAND_RANGES['C96'], SPACING)
    assert len(l96) == L96_NCH == 32
    return array(list(l96) + list(c96))


def test_raman_gain_table_shape_and_peak():
    """谱型取上游 gamma_raman，按自身最大值归一化后峰值恰为 gR_peak"""
    upstream = asarray(DEFAULT_RAMAN_COEFFICIENT['gamma_raman'])
    table = raman_gain_table(0.38e-3)
    assert table.shape == (2, upstream.size)
    assert_allclose(table[0], DEFAULT_RAMAN_COEFFICIENT['frequency_offset'])
    assert_allclose(table[1], upstream / upstream.max() * 0.38e-3)
    assert table[1].max() == 0.38e-3
    assert_allclose(table[0][table[1].argmax()], 12.75e12)


@pytest.mark.parametrize('gR_peak', (0, -1e-3))
def test_raman_gain_table_rejects_non_positive_peak(gR_peak):
    with pytest.raises(EquipmentConfigError, match='gR_peak'):
        raman_gain_table(gR_peak)


@pytest.mark.parametrize('kwargs', ({'gR_peak': 0}, {'gR_peak': -1e-3}, {'reference_wavelength': 5e-8},
                                    {'reference_wavelength': 2e-5}, {'ns': 0}, {'solver_spatial_resolution': 0},
                                    {'result_spatial_resolution': -1}))
def test_raman_params_rejects_invalid_values(kwargs):
    with pytest.raises(EquipmentConfigError):
        RamanParams(**kwargs)


def test_raman_params_reference_frequency_from_wavelength():
    params = RamanParams()
    assert_allclose(params.reference_frequency, c / 1480e-9)
    assert params.flag is True
    assert params.to_json()['reference_wavelength'] == 1480e-9


def test_get_gR_dat_signs_and_pump_depletion():
    """上三角为泵浦对信号的增益（正），下三角为泵浦被抽走的功率（负），对角为 0"""
    fch = array([191.0e12, 191.5e12, 196.0e12])
    gR_dat = _get_gR_dat(fch, raman_gain_table(), c / 1480e-9, 2.313)

    assert gR_dat.shape == (3, 3)
    assert_allclose(gR_dat.diagonal(), 0)
    # 泵浦只作用于比它低的波道（升序下标里就是上三角）
    assert gR_dat[0, 1] > 0 and gR_dat[0, 2] > 0 and gR_dat[1, 2] > 0
    # 泵浦自身损耗在同一列的低频波道处（下三角），且满足频率换算关系
    assert gR_dat[1, 0] < 0 and gR_dat[2, 0] < 0 and gR_dat[2, 1] < 0
    assert_allclose(gR_dat[1, 0], -gR_dat[0, 1] * fch[1] / fch[0])


def test_single_channel_matches_pure_attenuation(equipment):
    """单波道时 SRS 不产生任何转移，结果等于纯衰减（容差取 ODE 的 rtol=1e-6 量级）"""
    fiber = make_fiber(equipment)
    si = make_si([193.414e12])
    srs = RamanSolver.calculate_stimulated_raman_scattering(si, fiber)
    alpha = fiber.alpha(si.frequency)
    assert_allclose(srs.loss_profile[0, -1], exp(-alpha * fiber.params.length), rtol=1e-5)


def test_two_channel_power_transfer(equipment):
    """高频波道被抽走功率、低频波道获得增益，且净转移远小于单向转移量"""
    fiber = make_fiber(equipment)
    si = make_si([191.35e12, 196.0e12])
    srs = RamanSolver.calculate_stimulated_raman_scattering(si, fiber)
    pure = RamanSolver.calculate_attenuation_profile(si, fiber)

    transfer_lin = srs.loss_profile[:, -1] / pure.loss_profile[:, -1]
    assert transfer_lin[0] > 1 > transfer_lin[1]
    delta_w = srs.power_profile[:, -1] - pure.power_profile[:, -1]
    assert abs(delta_w.sum()) < 0.2 * abs(delta_w).sum()


def test_solver_returns_gnpy_compatible_profile(equipment):
    """返回上游 StimulatedRamanScattering，字段口径与 gnpy 一致"""
    fiber = make_fiber(equipment)
    si = make_si([191.35e12, 193.0e12, 196.0e12])
    srs = RamanSolver.calculate_stimulated_raman_scattering(si, fiber)

    assert isinstance(srs, StimulatedRamanScattering)
    assert srs.power_profile.shape == (si.number_of_channels, srs.z.size)
    assert srs.z[-1] == fiber.params.length
    assert_allclose(srs.loss_profile[:, 0], 1, atol=1e-12)
    assert_allclose(srs.loss_profile, srs.power_profile / si.pch[:, None])
    assert_allclose(srs.rho, sqrt(srs.loss_profile))
    assert_allclose(srs.frequency, si.frequency)


def test_flag_false_equals_upstream_attenuation_profile(equipment):
    """flag=False 时退化为纯衰减，与上游同名方法逐点一致"""
    fiber = make_fiber(equipment, raman_params=RamanParams(flag=False))
    si = make_si([191.35e12, 196.0e12])
    ours = RamanSolver.calculate_stimulated_raman_scattering(si, fiber)
    upstream = UpstreamRamanSolver.calculate_attenuation_profile(si, fiber)
    assert_allclose(ours.power_profile, upstream.power_profile)
    assert_allclose(ours.loss_profile, upstream.loss_profile)


def test_srs_fiber_flag_false_matches_upstream_fiber(equipment):
    """SrsFiber 关掉 SRS 后与上游 Fiber 的传播结果一致"""
    params = dict(equipment['Fiber'][FIBER_VARIETY].__dict__)
    params.update(length=LENGTH_KM, length_units='km', loss_coef=LOSS_COEF, att_in=0, con_in=0, con_out=CON_OUT_DB)
    frequencies = c_l_band_frequencies()
    ours = SrsFiber(uid='Span1', type_variety=FIBER_VARIETY, params=dict(params), metadata=LOCATION,
                    raman_params=RamanParams(flag=False))
    upstream = Fiber(uid='Span1', type_variety=FIBER_VARIETY, params=dict(params), metadata=LOCATION)
    si_ours, si_upstream = make_si(frequencies), make_si(frequencies)

    ours.propagate(si_ours)
    upstream.propagate(si_upstream)
    assert_allclose(si_ours.pch_dbm, si_upstream.pch_dbm, rtol=1e-9)
    assert_allclose(si_ours.ase, si_upstream.ase, rtol=1e-9, atol=0)


def test_loader_extracts_and_attaches_raman_gain(equipment):
    """设备库里的 raman_gain 段（YANG 不接受）能被加载器摘出并回填"""
    raw = load_json(EQPT_CONFIG)
    from_json = next(entry[RAMAN_GAIN_KEY] for entry in raw['Fiber'] if entry['type_variety'] == FIBER_VARIETY)
    assert getattr(equipment['Fiber'][FIBER_VARIETY], RAMAN_GAIN_KEY) == from_json


def test_srs_fiber_reads_raman_params_from_equipment(equipment):
    """SrsFiber 从设备库条目取拉曼参数，且不把该扩展字段留给 FiberParams"""
    settings = getattr(equipment['Fiber'][FIBER_VARIETY], RAMAN_GAIN_KEY)
    fiber = make_fiber(equipment)
    assert fiber.raman_params.gR_peak == settings['gR_peak']
    assert fiber.raman_params.reference_wavelength == settings['reference_wavelength']
    assert fiber.raman_params.ns == settings['ns']
    assert_allclose(fiber.raman_params.reference_frequency, c / settings['reference_wavelength'])
    assert not hasattr(fiber.params, RAMAN_GAIN_KEY)


def test_srs_fiber_falls_back_to_default_raman_params(equipment):
    """设备库条目没有拉曼段时用 G652D 默认值"""
    params = dict(equipment['Fiber'][FIBER_VARIETY].__dict__)
    params.pop(RAMAN_GAIN_KEY)
    params.update(length=LENGTH_KM, length_units='km', loss_coef=LOSS_COEF,
                  att_in=0, con_in=0, con_out=CON_OUT_DB)
    fiber = SrsFiber(uid='Span1', type_variety=FIBER_VARIETY, params=params, metadata=LOCATION)
    assert fiber.raman_params.gR_peak == DEFAULT_GR_PEAK
    assert fiber.raman_params.reference_wavelength == DEFAULT_RAMAN_REFERENCE_WAVELENGTH
    assert fiber.raman_params.ns == DEFAULT_RAMAN_NS


def test_lumped_losses_are_rejected(equipment):
    """集总损耗是阶跃损耗，ODE 无法表达：明确报错而不是静默算错"""
    fiber = make_fiber(equipment, lumped_losses=[{'position': 40.0, 'loss': 0.1}])
    si = make_si([191.35e12, 196.0e12])
    with pytest.raises(EquipmentConfigError, match='lumped losses'):
        RamanSolver.calculate_stimulated_raman_scattering(si, fiber)


def test_full_c_l_band_srs_transfer(equipment):
    """C96 + L96 全谱（每波道 +5 dBm，波段总功率约 20 dBm）：SRS 把功率从 C 转移到 L

    波段总功率口径与 scripts/ganlin/test_gnpy/test2.py 的打印一致。
    """
    fiber = make_fiber(equipment)
    si = make_si(c_l_band_frequencies(), pch_dbm=5)
    srs = RamanSolver.calculate_stimulated_raman_scattering(si, fiber)
    pure = RamanSolver.calculate_attenuation_profile(si, fiber)

    slices = {'L96': slice(0, L96_NCH), 'C96': slice(L96_NCH, None)}
    transfer_db = {band: lin2db(sum(srs.power_profile[sl, -1]) / sum(pure.power_profile[sl, -1]))
                   for band, sl in slices.items()}
    assert transfer_db['C96'] < 0 < transfer_db['L96']
    assert 0.2 < transfer_db['L96'] < 3
    assert -3 < transfer_db['C96'] < -0.2

# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# test_bplab_fibers
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
Unit tests for the wavelength dependent fiber attenuation models in gnpy.bplab.fibers
"""
from math import log10, e

import pytest
from numpy import isfinite
from numpy.testing import assert_allclose

from gnpy.bplab.fibers import FIBER_ATTENUATION_MODELS, fiber_attenuation_db_per_km, loss_coef_table
from gnpy.bplab.utils import band_center_frequencies
from gnpy.core.elements import Fiber
from gnpy.core.exceptions import EquipmentConfigError
from gnpy.core.utils import wavelength2freq

# 各类型在 1550 nm 处的衰减系数 [dB/km]，取自 scripts/ganlin/test_gnpy/fiber_data.py 的实测输出
ATTENUATION_AT_1550NM = {
    'ssmf': 0.2575,
    'ssmf_ideal': 0.2575,
    'G.655.C': 0.2575,
    'leaf': 0.2575,
    'G.652.D': 0.2500,
    'G.652.D barefiber': 0.2075,
    'G.655 barefiber': 0.2075,
    'G.652.D Type2': 0.2825,
    'G.654.E': 0.1901,
    'G.654.E 150um': 0.1901,
    'G.654.E barefiber': 0.1706,
    'G.654.E 110um MicroJet': 0.1756,
    'G.654.E MicroJet': 0.1756,
    'G.654.E Subsea': 0.1556,
    'pscf': 0.2282,
    'nanf_check': 0.2207,
    'smf_wangziwei': 0.2060,
    'g654e_wangziwei': 0.1897,
    'leaf_wangziwei': 0.2186,
    'smf_flatatt': 0.2000,
    'smf_user_defined': 0.2000,
}

REFERENCE_WAVELENGTH_M = 1550e-9
C96_FREQUENCIES = band_center_frequencies('C96', 50e9)


def test_registry_covers_expected_types():
    assert set(FIBER_ATTENUATION_MODELS) == set(ATTENUATION_AT_1550NM)


@pytest.mark.parametrize('fiber_type', sorted(ATTENUATION_AT_1550NM))
def test_attenuation_at_1550nm_matches_source(fiber_type):
    attenuation = fiber_attenuation_db_per_km(fiber_type, REFERENCE_WAVELENGTH_M)
    assert_allclose(attenuation, ATTENUATION_AT_1550NM[fiber_type], atol=1e-4)


def test_attenuation_accepts_arrays_and_keeps_shape():
    wavelengths = [1550e-9, 1560e-9, 1600e-9]
    attenuation = fiber_attenuation_db_per_km('G.652.D', wavelengths)
    assert attenuation.shape == (len(wavelengths),)
    assert_allclose(attenuation, [fiber_attenuation_db_per_km('G.652.D', w) for w in wavelengths])


def test_flat_types_are_wavelength_independent():
    assert_allclose(fiber_attenuation_db_per_km('smf_flatatt', [1550e-9, 1310e-9, 1620e-9]), 0.2)


def test_unknown_fiber_type_raises():
    with pytest.raises(EquipmentConfigError):
        fiber_attenuation_db_per_km('not_a_fiber', REFERENCE_WAVELENGTH_M)


def test_loss_coef_table_keys_units_and_order():
    table = loss_coef_table('G.652.D', C96_FREQUENCIES)

    assert set(table) == {'value', 'frequency'}
    assert len(table['value']) == len(C96_FREQUENCIES)
    assert table['frequency'] == C96_FREQUENCIES.tolist()
    # 值单位是 dB/km：1550 nm 附近等于曲线自身值 0.25
    assert_allclose(fiber_attenuation_db_per_km('G.652.D', REFERENCE_WAVELENGTH_M), 0.25, atol=1e-4)
    assert isfinite(table['value']).all()


def test_loss_coef_table_anchors_reference_loss():
    anchored = loss_coef_table('G.652.D', C96_FREQUENCIES, ref_loss_db_per_km=0.275)
    native = loss_coef_table('G.652.D', C96_FREQUENCIES)

    assert anchored['frequency'] == native['frequency']
    # 锚点处 1550 nm 恰好为给定值，其余点整体平移同样的常数量
    shift = 0.275 - fiber_attenuation_db_per_km('G.652.D', REFERENCE_WAVELENGTH_M)
    assert_allclose(anchored['value'], [v + shift for v in native['value']], rtol=1e-12)


def test_fiber_element_consumes_table():
    """把表喂给上游 Fiber，确认 loss_coef_func / loss / alpha 三个口径对得上"""
    length_km, con_out = 80.0, 6.0
    frequency = C96_FREQUENCIES
    fiber = Fiber(uid='Span1', type_variety='G.652.D',
                  params={'length': length_km, 'length_units': 'km', 'pmd_coef': 0,
                          'con_in': 0.0, 'con_out': con_out,
                          'loss_coef': loss_coef_table('G.652.D', frequency, ref_loss_db_per_km=0.275)})

    ref_frequency = wavelength2freq(REFERENCE_WAVELENGTH_M)
    loss_coef_db_per_km = fiber.loss_coef_func(ref_frequency) * 1e3
    # 表内锚点是精确值，但 ref_frequency 落在栅格点之间，插值后有极小偏差
    assert loss_coef_db_per_km == pytest.approx(0.275, abs=1e-4)
    # 单跨损耗按 1550 nm 结算（FiberParams.ref_wavelength 默认 1550 nm）
    assert fiber.loss == pytest.approx(0.275 * length_km + con_out, abs=1e-2)
    # 逐波道都能插值出有限值，alpha = loss_coef / (10 * log10(e)) [Neper/m]
    assert isfinite(fiber.loss_coef_func(frequency)).all()
    assert_allclose(fiber.alpha(frequency),
                    fiber.loss_coef_func(frequency) / (10 * log10(e)), rtol=1e-12)

# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# test_bplab_utils
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
Unit tests for the frequency grid helpers in gnpy.bplab.utils
"""
import pytest
from numpy import arange
from numpy.testing import assert_allclose

from gnpy.bplab.utils import DWDM_BAND_RANGES, band_center_frequencies, itu_grid_center_frequencies
from gnpy.core.info import create_input_spectral_information

SPACINGS = (50e9, 75e9, 100e9, 150e9, 200e9, 250e9, 300e9)
# 各 spacing 下完整落在占用范围内的信道数
EXPECTED_NCH = {
    'C96': (96, 63, 47, 31, 23, 18, 15),
    'L96': (96, 63, 47, 32, 23, 18, 15),
}
# 1.9e14 Hz 量级的双精度浮点误差约 0.05 Hz，取 1 Hz 作为比较容差
FREQ_EPS = 1.0


def test_dwdm_band_ranges():
    assert DWDM_BAND_RANGES['C96'] == (191.275e12, 196.075e12)
    assert DWDM_BAND_RANGES['L96'] == (186.275e12, 191.075e12)


@pytest.mark.parametrize('band_name', ('C96', 'L96'))
def test_channel_count_per_spacing(band_name):
    assert tuple(len(band_center_frequencies(band_name, s)) for s in SPACINGS) == EXPECTED_NCH[band_name]


def test_c96_on_50ghz_grid():
    centers = band_center_frequencies('C96')
    assert len(centers) == 96
    assert centers[0] == pytest.approx(191.3e12, abs=FREQ_EPS)
    assert centers[-1] == pytest.approx(196.05e12, abs=FREQ_EPS)
    assert_allclose(centers, 191.3e12 + 50e9 * arange(96), atol=FREQ_EPS)


def test_band_name_is_case_insensitive():
    assert_allclose(band_center_frequencies('c96'), band_center_frequencies('C96'))
    assert_allclose(band_center_frequencies('l96', 100e9), band_center_frequencies('L96', 100e9))


def test_unknown_band_name():
    with pytest.raises(KeyError):
        band_center_frequencies('C80')


@pytest.mark.parametrize('band_name', ('C96', 'L96'))
def test_grid_is_uniform_anchored_and_within_band(band_name):
    f_min, f_max = DWDM_BAND_RANGES[band_name]
    for spacing in SPACINGS:
        centers = band_center_frequencies(band_name, spacing)
        assert len(centers) > 0
        # 等间隔
        assert_allclose(centers[1:] - centers[:-1], spacing)
        # 全部落在 193.1 THz 锚点的栅格上
        n = (centers - 193.1e12) / spacing
        assert_allclose(n, n.round())
        # 每个信道的整个 slot 都在占用范围内
        assert (centers - spacing / 2 >= f_min - FREQ_EPS).all()
        assert (centers + spacing / 2 <= f_max + FREQ_EPS).all()
        # 极大性：栅格上下各再取一个点都会越界
        assert centers[0] - spacing < f_min + spacing / 2 + FREQ_EPS
        assert centers[-1] + spacing > f_max - spacing / 2 - FREQ_EPS


def test_no_channel_fits():
    assert len(itu_grid_center_frequencies(191.3e12, 191.4e12, 1e12)) == 0
    assert len(band_center_frequencies('C96', 5000e9)) == 0


def test_consistency_with_create_input_spectral_information():
    centers = band_center_frequencies('C96')
    si = create_input_spectral_information(f_min=centers[0], f_max=centers[-1], roll_off=0.15,
                                           baud_rate=32e9, spacing=50e9, tx_osnr=40.0, tx_power=1e-3)
    assert si.number_of_channels == 96
    assert_allclose(si.frequency, centers)

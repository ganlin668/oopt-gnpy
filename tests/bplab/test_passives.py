# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# test_bplab_passives
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
Unit tests for the band dependent passive elements in gnpy.bplab.passives
"""
from pathlib import Path

import pytest
from numpy import array, concatenate
from numpy.testing import assert_allclose

from gnpy.bplab.passives import BandAttenuator, PassiveParams, load_passive_library
from gnpy.bplab.utils import band_center_frequencies
from gnpy.core.info import create_arbitrary_spectral_information
from gnpy.core.utils import dbm2watt

SPACING = 50e9
EQPT_CONFIG = Path(__file__).parents[2] / 'scripts' / 'ganlin' / 'test_gnpy' / 'eqpt_config.json'


def make_si(frequency=None):
    """C96 + L96 共 192 波（升序）的频谱，每波道 0 dBm"""
    if frequency is None:
        frequency = concatenate([band_center_frequencies('L96', SPACING),
                                 band_center_frequencies('C96', SPACING)])
    return create_arbitrary_spectral_information(
        frequency=frequency, pch=dbm2watt(0), baud_rate=32e9, tx_osnr=40,
        slot_width=SPACING, roll_off=0.15, tx_power=dbm2watt(0))


def test_passive_params_defaults():
    params = PassiveParams()
    assert params.type_variety == ''
    assert params.loss == {}


def test_passive_params_from_config_entry():
    params = PassiveParams(type_variety='Mux', loss={'C96': 3.0, 'L96': 3.5})
    assert params.type_variety == 'Mux'
    assert params.loss == {'C96': 3.0, 'L96': 3.5}
    # 传入的 loss 不应与调用方共享同一个 dict
    entry_loss = {'C96': 1.0}
    assert PassiveParams(loss=entry_loss).loss is not entry_loss


def test_band_attenuator_applies_each_band_own_loss():
    """逐波道衰减必须精确等于本波段的配置值，波段交界处不串扰"""
    si = make_si()
    pch_before = si.pch_dbm.copy()
    knee = len(band_center_frequencies('L96', SPACING))

    BandAttenuator(uid='Mux', params={'type_variety': 'Mux', 'loss': {'C96': 3.0, 'L96': 3.5}})(si)

    assert_allclose(si.pch_dbm[:knee], pch_before[:knee] - 3.5, atol=1e-9)
    assert_allclose(si.pch_dbm[knee:], pch_before[knee:] - 3.0, atol=1e-9)


def test_band_attenuator_amplifies_nothing_and_is_passive():
    element = BandAttenuator(uid='FIU', params={'type_variety': 'FIU', 'loss': {}})
    assert element.passive is True
    si = make_si()
    pch_before = si.pch_dbm.copy()
    element(si)
    # 空 loss 配置不改变任何波道
    assert_allclose(si.pch_dbm, pch_before, atol=1e-12)


def test_band_attenuator_leaves_out_of_band_channels_untouched():
    si = make_si(frequency=array([200e12, 180e12]))
    pch_before = si.pch_dbm.copy()
    BandAttenuator(uid='VOA', params={'type_variety': 'VOA', 'loss': {'C96': 5.0, 'L96': 5.5}})(si)
    assert_allclose(si.pch_dbm, pch_before, atol=1e-12)


def test_band_attenuator_returns_same_spectral_information():
    si = make_si()
    assert BandAttenuator(uid='Demux', params={'loss': {'C96': 3.0}})(si) is si


def test_load_passive_library_reads_local_eqpt_config():
    library = load_passive_library(EQPT_CONFIG)
    assert set(library) == {'Mux', 'VOA', 'FIU', 'Demux'}
    assert library['Mux'].loss == {'C96': 3.0, 'L96': 3.5}
    assert library['VOA'].loss == {'C96': 5.0, 'L96': 5.5}
    assert library['FIU'].loss == {'C96': 1.5, 'L96': 2.0}
    assert library['Demux'].loss == {'C96': 3.0, 'L96': 3.5}
    # 每个器件的 L 波段插损都应高于 C 波段
    for params in library.values():
        assert params.loss['L96'] > params.loss['C96']


@pytest.mark.parametrize('variety', ('Mux', 'VOA', 'FIU', 'Demux'))
def test_library_values_are_exactly_applied(variety):
    """把配置里的插损当探针，验证器件真的按配置生效"""
    library = load_passive_library(EQPT_CONFIG)
    si = make_si()
    pch_before = si.pch_dbm.copy()
    knee = len(band_center_frequencies('L96', SPACING))

    BandAttenuator(uid=variety, params={'type_variety': variety,
                                        'loss': library[variety].loss})(si)

    loss = library[variety].loss
    assert_allclose(si.pch_dbm[:knee], pch_before[:knee] - loss['L96'], atol=1e-9)
    assert_allclose(si.pch_dbm[knee:], pch_before[knee:] - loss['C96'], atol=1e-9)

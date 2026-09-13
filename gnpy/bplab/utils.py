# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.bplab.utils: BPLab add-on frequency grid utilities
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
gnpy.bplab.utils
================

BPLab 自建的频点工具函数（纯新增，不修改 gnpy 原有代码）。
"""

from numpy import arange, array, ceil, floor

# DWDM 波段占用带宽边缘 [Hz]：C/L 96 波各为 96 x 50 GHz = 4.8 THz，C/L 120 波各为 120 x 50 GHz = 6 THz
DWDM_BAND_RANGES = {
    'C96': (191.275e12, 196.075e12),
    'L96': (186.275e12, 191.075e12),
    'C120': (190.675e12, 196.675e12),
    'L120': (185.075e12, 191.075e12),
}


def itu_grid_center_frequencies(f_min, f_max, spacing, anchor_frequency=193.1e12):
    """Center frequencies of the channels fitting in a band, on an ITU-T G.694.1 grid

    The grid is anchored at 193.1 THz: the nominal center frequencies are
    193.1 + n * spacing, with n a positive or negative integer including 0.
    Only the channels whose whole slot ([center - spacing / 2, center + spacing / 2])
    fits in [f_min, f_max] are returned.

    :param f_min Lowest frequency of the band (occupied spectrum lower edge) [Hz]
    :param f_max Highest frequency of the band (occupied spectrum upper edge) [Hz]
    :param spacing Grid/channel spacing [Hz]
    :param anchor_frequency Frequency the grid is anchored at [Hz]
    :return Sorted array of the channel center frequencies [Hz], empty if none fits

    >>> len(itu_grid_center_frequencies(191.275e12, 196.075e12, 50e9))
    96
    >>> round(itu_grid_center_frequencies(191.275e12, 196.075e12, 50e9)[0] * 1e-12, 4)
    191.3
    >>> round(itu_grid_center_frequencies(191.275e12, 196.075e12, 50e9)[-1] * 1e-12, 4)
    196.05
    >>> len(itu_grid_center_frequencies(191.275e12, 196.075e12, 75e9))
    63
    >>> len(itu_grid_center_frequencies(191.3e12, 191.4e12, 1e12))
    0
    """
    tolerance = 1e-9
    first_n = int(ceil((f_min + spacing / 2 - anchor_frequency) / spacing - tolerance))
    last_n = int(floor((f_max - spacing / 2 - anchor_frequency) / spacing + tolerance))
    if last_n < first_n:
        return array([])
    return anchor_frequency + arange(first_n, last_n + 1) * spacing


def band_center_frequencies(band_name, spacing=50e9):
    """Center frequencies of the channels of a predefined DWDM band

    :param band_name Band name, one of the keys of DWDM_BAND_RANGES ('C96', 'L96', 'C120', 'L120') [str]
    :param spacing Grid/channel spacing [Hz]
    :return Sorted array of the channel center frequencies [Hz]

    >>> len(band_center_frequencies('C96'))
    96
    >>> round(band_center_frequencies('C96')[0] * 1e-12, 4)
    191.3
    >>> round(band_center_frequencies('L96')[-1] * 1e-12, 4)
    191.05
    >>> len(band_center_frequencies('C96', 100e9))
    47
    """
    f_min, f_max = DWDM_BAND_RANGES[band_name.upper()]
    return itu_grid_center_frequencies(f_min, f_max, spacing)


def band_filling_center_frequencies(f_min, f_max, spacing):
    """Center frequencies when the band is filled with channels of a given spacing

    The first channel slot starts exactly at f_min, so the plan is
    f_min + spacing / 2 + k * spacing with k = 0 ... floor((f_max - f_min) / spacing) - 1.
    Unlike :func:`itu_grid_center_frequencies` (grid anchored at 193.1 THz, which requires the
    band edges to fall on that grid), this fills the band edge to edge; it is meant for bands
    whose occupied width is an integer multiple of the spacing, e.g. C96 and L96
    (4.8 THz = 32 x 150 GHz -> 32 channels each). Using :func:`band_center_frequencies`
    with a 150 GHz spacing instead only yields 31 channels for C96 (191.45 ~ 195.95 THz),
    because 191.275 THz is not on the 193.1 THz anchored 150 GHz grid.

    :param f_min Lowest frequency of the band (occupied spectrum lower edge) [Hz]
    :param f_max Highest frequency of the band (occupied spectrum upper edge) [Hz]
    :param spacing Grid/channel spacing [Hz]
    :return Sorted array of the channel center frequencies [Hz], empty if none fits

    >>> len(band_filling_center_frequencies(191.275e12, 196.075e12, 150e9))
    32
    >>> round(band_filling_center_frequencies(191.275e12, 196.075e12, 150e9)[0] * 1e-12, 4)
    191.35
    >>> round(band_filling_center_frequencies(191.275e12, 196.075e12, 150e9)[-1] * 1e-12, 4)
    196.0
    >>> len(band_filling_center_frequencies(186.275e12, 191.075e12, 150e9))
    32
    >>> len(band_filling_center_frequencies(191.275e12, 196.075e12, 50e9))
    96
    >>> len(band_filling_center_frequencies(191.6e12, 191.9e12, 150e9))
    2
    """
    tolerance = 1e-9
    n_channels = int(floor((f_max - f_min) / spacing + tolerance))
    if n_channels <= 0:
        return array([])
    return f_min + spacing / 2 + arange(n_channels) * spacing

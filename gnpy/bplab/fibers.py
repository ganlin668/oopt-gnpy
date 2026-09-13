# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.bplab.fibers: BPLab add-on wavelength dependent fiber attenuation models
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
gnpy.bplab.fibers
=================

BPLab 自建的光纤衰减模型（纯新增，不修改 gnpy 原有代码）。

上游 gnpy 只支持逐点查表的 ``loss_coef``（标量，或 ``{'value': [...], 'frequency': [...]}``），
没有解析式衰减曲线。本模块给出常用单模光纤的解析式衰减谱，并直接产出上游
``FiberParams.loss_coef`` 需要的逐波长表：

- :func:`fiber_attenuation_db_per_km`：给定波长算衰减系数 [dB/km]
- :func:`loss_coef_table`：生成 ``{'value': [... dB/km ...], 'frequency': [... Hz ...]}``
- :data:`FIBER_ATTENUATION_MODELS`：类型名 -> 模型系数

系数移植自本地脚本 ``scripts/ganlin/test_gnpy/fiber_data.py``（数值一致，单位统一换算成 gnpy 的
dB/km），共 21 个不依赖外部数据文件的类型名；该脚本另外 4 个需要读 .dat/.xlsx 的类型
（``SSMF-28e`` / ``G.652.D.Exp`` / ``G.652.D.Exp_v1`` / ``SSMF_SHANGJIAO``）未纳入。

三类模型（λ 单位 m，结果 dB/km）：

- ``'rayleigh'``：``A/λ^4 + B*exp(-C/λ) + corr``（瑞利散射 + 吸收项 + 熔接/余量修正）
- ``'log'``：``(A/λ)^4 + exp(B - C/λ)``
- ``'constant'``：与波长无关的常数

用法（手工建链时直接喂给 ``Fiber`` / ``FiberParams``）::

    from gnpy.bplab.fibers import loss_coef_table
    from gnpy.bplab.utils import band_center_frequencies

    frequency = band_center_frequencies('C96', 50e9)
    # FiberParams.ref_wavelength 默认 1550 nm，这里把 1550 nm 处锚定到 0.275 dB/km
    loss_coef = loss_coef_table('G.652.D', frequency, ref_loss_db_per_km=0.275)
"""

from typing import Dict, Optional, Tuple, Union

from numpy import array, asarray, exp, full_like

from gnpy.core.exceptions import EquipmentConfigError
from gnpy.core.utils import freq2wavelength

# 锚定波长 [m]：与上游 FiberParams.ref_wavelength 的默认值（1550 nm）一致，
# 即 Fiber.loss（单跨损耗）天然按该波长结算
REFERENCE_WAVELENGTH_M = 1550e-9

# A/λ^4 + B*exp(-C/λ) + corr 的公共系数
_RAYLEIGH_COEFFICIENTS = (1.12550881e-24, 1.20801544384579e12, 4.991e-5)
# G.654.E 家族（大有效面积）的系数
_G654E_COEFFICIENTS = (1.07235462999891e-24, 2.19054e11, 4.70049440969118e-5)
# pscf / nanf_check 的系数
_PSCF_COEFFICIENTS = (9.48912349840053e-25, 2.960322484755e12, 5.11436876199752e-5)

# 类型名 -> (模型, 系数)；系数单位均为 dB/km（原始脚本里的 dB/m 已整体 ×1e3，其中 C 是波长尺度参数不换算）
FIBER_ATTENUATION_MODELS: Dict[str, Tuple[str, tuple]] = {
    # --- A/λ^4 + B*exp(-C/λ) + corr ---
    'ssmf': ('rayleigh', _RAYLEIGH_COEFFICIENTS + (0.05,)),
    'ssmf_ideal': ('rayleigh', _RAYLEIGH_COEFFICIENTS + (0.05,)),
    'G.655.C': ('rayleigh', _RAYLEIGH_COEFFICIENTS + (0.05,)),
    'leaf': ('rayleigh', _RAYLEIGH_COEFFICIENTS + (0.05,)),
    'G.652.D': ('rayleigh', _RAYLEIGH_COEFFICIENTS + (0.0425,)),
    'G.652.D barefiber': ('rayleigh', _RAYLEIGH_COEFFICIENTS + (0.0,)),
    'G.655 barefiber': ('rayleigh', _RAYLEIGH_COEFFICIENTS + (0.0,)),
    'G.652.D Type2': ('rayleigh', _RAYLEIGH_COEFFICIENTS + (0.075,)),
    'G.654.E': ('rayleigh', _G654E_COEFFICIENTS + (-0.0105,)),
    'G.654.E 150um': ('rayleigh', _G654E_COEFFICIENTS + (-0.0105,)),
    'G.654.E barefiber': ('rayleigh', _G654E_COEFFICIENTS + (-0.03,)),
    'G.654.E 110um MicroJet': ('rayleigh', _G654E_COEFFICIENTS + (-0.025,)),
    'G.654.E MicroJet': ('rayleigh', _G654E_COEFFICIENTS + (-0.025,)),
    'G.654.E Subsea': ('rayleigh', _G654E_COEFFICIENTS + (-0.045,)),
    'pscf': ('rayleigh', _PSCF_COEFFICIENTS + (0.05,)),
    'nanf_check': ('rayleigh', _PSCF_COEFFICIENTS + (0.0425,)),
    # --- (A/λ)^4 + exp(B - C/λ) ---
    'smf_wangziwei': ('log', (1.02372502895989e-6, 25.447693137258, 4.58792333272982e-5)),
    'g654e_wangziwei': ('log', (1.00181920725003e-6, 26.8534675154108, 4.81167354509278e-5)),
    'leaf_wangziwei': ('log', (1.03858272544868e-6, 25.5215151620792, 4.58737406725094e-5)),
    # --- 与波长无关 ---
    'smf_flatatt': ('constant', (0.2,)),
    'smf_user_defined': ('constant', (0.2,)),
}


def fiber_attenuation_db_per_km(fiber_type: str, wavelength_m: Union[float, array]):
    """给定光纤类型与波长，返回衰减系数 [dB/km]

    :param fiber_type: 光纤类型名，见 :data:`FIBER_ATTENUATION_MODELS`
    :param wavelength_m: 波长 [m]，标量或数组
    :return: 衰减系数 [dB/km]，标量输入返回 float，数组输入返回同形数组
    :raises EquipmentConfigError: 类型名不在 :data:`FIBER_ATTENUATION_MODELS` 中

    >>> round(float(fiber_attenuation_db_per_km('G.652.D', 1550e-9)), 4)
    0.25
    >>> round(float(fiber_attenuation_db_per_km('G.654.E barefiber', 1550e-9)), 4)
    0.1706
    >>> float(fiber_attenuation_db_per_km('smf_flatatt', 1550e-9))
    0.2
    """
    try:
        model, coefficients = FIBER_ATTENUATION_MODELS[fiber_type]
    except KeyError as e:
        raise EquipmentConfigError(f'Unknown fiber type {fiber_type}: available types are '
                                   f'{sorted(FIBER_ATTENUATION_MODELS)}') from e
    wavelength_m = asarray(wavelength_m, dtype=float)
    if model == 'constant':
        values = full_like(wavelength_m, coefficients[0])
    elif model == 'rayleigh':
        a, b, c, corr = coefficients
        values = a / wavelength_m ** 4 + b * exp(-c / wavelength_m) + corr
    elif model == 'log':
        a, b, c = coefficients
        values = (a / wavelength_m) ** 4 + exp(b - c / wavelength_m)
    else:
        raise EquipmentConfigError(f'Unknown attenuation model {model} for fiber type {fiber_type}')
    return float(values) if wavelength_m.ndim == 0 else values


def loss_coef_table(fiber_type: str, frequency, ref_loss_db_per_km: Optional[float] = None,
                    ref_wavelength_m: float = REFERENCE_WAVELENGTH_M) -> Dict[str, list]:
    """生成上游 ``FiberParams.loss_coef`` 需要的逐波长表

    :param fiber_type: 光纤类型名，见 :data:`FIBER_ATTENUATION_MODELS`
    :param frequency: 频率 [Hz]，**需升序**（上游插值不做排序）
    :param ref_loss_db_per_km: 给定则把整条曲线平移，使 ``ref_wavelength_m`` 处正好等于该值；
        None 表示直接用曲线自身在该波长的值（如 G.652.D 为 0.25 dB/km）
    :param ref_wavelength_m: 锚定波长 [m]，默认 1550 nm
    :return: ``{'value': [... dB/km ...], 'frequency': [... Hz ...]}``

    >>> table = loss_coef_table('G.652.D', [193.1e12, 193.4e12], ref_loss_db_per_km=0.275)
    >>> table['frequency']
    [193100000000000.0, 193400000000000.0]
    >>> [round(value, 4) for value in table['value']]
    [0.2744, 0.275]
    """
    frequency = asarray(frequency, dtype=float)
    values = fiber_attenuation_db_per_km(fiber_type, freq2wavelength(frequency))
    if ref_loss_db_per_km is not None:
        ref_value = fiber_attenuation_db_per_km(fiber_type, ref_wavelength_m)
        values = values - ref_value + ref_loss_db_per_km
    return {'value': asarray(values).tolist(), 'frequency': frequency.tolist()}

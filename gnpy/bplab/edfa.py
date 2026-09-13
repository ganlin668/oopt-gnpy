# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.bplab.edfa: BPLab add-on gain dependent noise figure EDFA
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
gnpy.bplab.edfa
===============

BPLab 自建的"NF 随增益变化"的光放（纯新增，不修改 gnpy 原有代码）。

上游 gnpy 的 `variable_gain` 模型只能用 (gain_min -> nf_max, gain_flatmax -> nf_min)
两个端点拟合出 NF vs gain 的双曲线，无法直接使用实测的逐增益点 NF。本模块让 NF 改为
按工作增益查表插值，且认为 NF 在波段内平坦。

设备库中 Edfa 条目里的扩展字段（YANG 模型不识别，由
:func:`gnpy.bplab.trx.load_equipment_with_module_power` 在 YANG 校验前摘掉、校验后回填）：

    "nf_vs_gain": [
      {"gain": 20.0, "nf": 7.0},
      {"gain": 25.0, "nf": 5.5}
    ]

- :func:`parse_nf_curve`：解析并校验该表
- :class:`GainNfEdfa`：按表插值 NF 的 EDFA（无该表时行为与上游一致）
- :class:`GainNfMultibandAmplifier`：子光放改用 :class:`GainNfEdfa` 的多波段光放
- :func:`extract_nf_curves` / :func:`attach_nf_curves`：设备库加载时的摘除与回填
"""

from logging import getLogger
from typing import Dict, List, Optional, Tuple

from numpy import array, interp

from gnpy.core.elements import Edfa, Multiband_amplifier, _Node
from gnpy.core.exceptions import EquipmentConfigError, ParametersError
from gnpy.core.parameters import FrequencyBand, MultiBandParams, find_band_name

logger = getLogger(__name__)

# 设备库 Edfa 条目里"增益 -> NF"表的字段名（dB / dB）；YANG 模型不接受该字段
NF_CURVE_KEY = 'nf_vs_gain'


def parse_nf_curve(entries: Optional[List[Dict]]) -> Optional[Tuple[array, array]]:
    """把设备库里的增益-NF 表解析成 (gains, nfs) 两个按增益升序排列的数组

    :param entries: [{'gain': 20.0, 'nf': 7.0}, ...]；None 或空表示不使用查表
    :return: (gains, nfs)，或 None

    >>> gains, nfs = parse_nf_curve([{'gain': 25.0, 'nf': 5.5}, {'gain': 20.0, 'nf': 7.0}])
    >>> list(gains), list(nfs)
    ([20.0, 25.0], [7.0, 5.5])
    >>> parse_nf_curve(None) is None
    True
    >>> parse_nf_curve([]) is None
    True
    """
    if not entries:
        return None
    try:
        pairs = sorted((float(entry['gain']), float(entry['nf'])) for entry in entries)
    except (KeyError, TypeError, ValueError) as e:
        raise EquipmentConfigError(f'{NF_CURVE_KEY} entries must be {{"gain": <dB>, "nf": <dB>}} objects: {e}') from e
    gains = array([gain for gain, _ in pairs])
    nfs = array([nf for _, nf in pairs])
    if gains.size < 2:
        raise EquipmentConfigError(f'{NF_CURVE_KEY} needs at least 2 gain points, got {gains.size}')
    if any(gains[1:] <= gains[:-1]):
        raise EquipmentConfigError(f'{NF_CURVE_KEY} gain values must be strictly increasing, got {list(gains)}')
    return gains, nfs


def extract_nf_curves(json_data: Dict) -> Dict[str, List[Dict]]:
    """从原始设备库 json 的 Edfa 条目里摘出 nf_vs_gain

    该字段不在 YANG 模型中，必须在 :func:`gnpy.tools.convert_legacy_yang.yang_to_legacy`
    校验之前摘掉；与 json_io 的 other_name 别名处理保持一致，别名型号同样登记。

    :param json_data: 原始设备库 json（会被就地修改，去掉 nf_vs_gain 字段）
    :return: {型号: 增益-NF 表}
    """
    extracted = {}
    for entry in json_data.get('Edfa', []):
        curve = entry.pop(NF_CURVE_KEY, None)
        if not curve:
            continue
        for variety in entry.get('other_name', []) + [entry['type_variety']]:
            extracted[variety] = curve
    return extracted


def attach_nf_curves(equipment: Dict, curves: Dict[str, List[Dict]]) -> None:
    """把摘出的增益-NF 表挂到设备库的 Edfa 条目上

    上游 json_io.Amp 与 EdfaParams 都只认自己登记的键，未知键会被静默丢弃，
    因此必须在 :func:`gnpy.tools.json_io._equipment_from_json` 之后显式挂上。

    :param equipment: 设备库字典
    :param curves: :func:`extract_nf_curves` 的返回值
    """
    for variety, curve in curves.items():
        amp = equipment.get('Edfa', {}).get(variety)
        if amp is None:
            logger.warning(f'{NF_CURVE_KEY} defined for unknown Edfa type_variety {variety}: ignored')
            continue
        setattr(amp, NF_CURVE_KEY, curve)


class GainNfEdfa(Edfa):
    """NF 由工作增益查表插值得到的 EDFA

    ``params`` 里带 :data:`NF_CURVE_KEY` 时按表线性插值（超出表格范围时钳位到端点值），
    不带时完全走上游的 NF 模型。

    :param params: 同 :class:`gnpy.core.elements.Edfa`，可额外带增益-NF 表
    """

    def __init__(self, *args, params=None, **kwargs):
        # EdfaParams 只认自己登记的键，扩展表必须在 super() 之前取出来
        curve = parse_nf_curve((params or {}).get(NF_CURVE_KEY))
        super().__init__(*args, params=params, **kwargs)
        self.nf_curve = curve

    def _calc_nf(self, avg=False):
        """返回各频率切片的 NF [dB]；avg=True 时返回标量平均 NF（上游自动设计使用）"""
        if self.nf_curve is None:
            return super()._calc_nf(avg)
        gains, nfs = self.nf_curve
        self.att_in = 0  # 查表模型不做输入垫损
        nf_avg = interp(self.effective_gain, gains, nfs)
        return nf_avg if avg else self.interpol_nf_ripple + nf_avg

    def __repr__(self):
        return f'{type(self).__name__}(uid={self.uid!r}, type_variety={self.params.type_variety!r})'


class GainNfMultibandAmplifier(Multiband_amplifier):
    """多波段光放，子光放改用 :class:`GainNfEdfa` 以支持增益-NF 表

    本类与上游 :class:`gnpy.core.elements.Multiband_amplifier` 同构，唯一区别是子光放类型；
    上游该类在 ``__init__`` 里写死了 ``Edfa``，无法注入子类，故在此复写建带逻辑。
    上游若调整建带逻辑，本类需同步。
    """

    def __init__(self, *args, amplifiers: List[dict], params: dict, **kwargs):
        self.variety_list = kwargs.pop('variety_list', None)
        try:
            # 绕开上游 Multiband_amplifier.__init__，直接调 _Node 初始化
            _Node.__init__(self, params=MultiBandParams(**params), **kwargs)
        except ParametersError as e:
            raise ParametersError(f'{kwargs["uid"]}: {e}') from e
        self.amplifiers = {}
        if 'type_variety' in kwargs:
            kwargs.pop('type_variety')
        self.passive = False
        for amp_dict in amplifiers:
            # amplifiers dict uses default names as key to represent the band
            amp = GainNfEdfa(**amp_dict, **kwargs)
            band = next(b for b in amp.params.bands)
            band_name = find_band_name(FrequencyBand(f_min=band['f_min'], f_max=band['f_max']))
            if band_name not in self.amplifiers and band not in self.params.bands:
                self.params.bands.append(band)
                self.amplifiers[band_name] = amp
            elif band_name not in self.amplifiers and band in self.params.bands:
                self.amplifiers[band_name] = amp
            else:
                raise ParametersError(f'{kwargs["uid"]}: has more than one amp defined for the same band')

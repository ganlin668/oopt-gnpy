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
- :func:`stub_gain_range_for_curves` / :func:`restore_gain_range`：带表的光放旁路上游
  `variable_gain` 的 2 级 NF 拟合合法性校验（该模型对带表光放无用，但拟合不合法会拒绝加载）
"""

from logging import getLogger
from typing import Dict, List, Optional, Tuple

from numpy import array, interp

from gnpy.core.elements import Edfa, Multiband_amplifier, _Node
from gnpy.core.exceptions import EquipmentConfigError, ParametersError
from gnpy.core.parameters import FrequencyBand, MultiBandParams, find_band_name
from gnpy.core.science_utils import estimate_nf_model

logger = getLogger(__name__)

# 设备库 Edfa 条目里"增益 -> NF"表的字段名（dB / dB）；YANG 模型不接受该字段
NF_CURVE_KEY = 'nf_vs_gain'

# 旁路上游 2 级拟合时，候选的占位增益跨度 [dB]（相对 gain_flatmax）；按顺序取第一个可拟合的
PLACEHOLDER_GAIN_SPANS_DB = (5, 8, 10, 12, 14, 15, 16, 17, 11, 13, 18)


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


def _fitting_gain_min(gain_flatmax: float, nf_min: float, nf_max: float) -> Optional[float]:
    """在给定 nf 端点下，找一个上游 2 级拟合能接受的 gain_min 占位值

    :param gain_flatmax: 最大平坦增益 [dB]
    :param nf_min: 最大增益处的 NF [dB]
    :param nf_max: 最小增益处的 NF [dB]
    :return: 可拟合的 gain_min，找不到时返回 None
    """
    for span in PLACEHOLDER_GAIN_SPANS_DB:
        gain_min = gain_flatmax - span
        try:
            estimate_nf_model('placeholder', gain_min, gain_flatmax, nf_min, nf_max)
            return gain_min
        except EquipmentConfigError:
            continue
    return None


def stub_gain_range_for_curves(json_data: Dict) -> Dict[str, float]:
    """带增益-NF 表的光放：上游 2 级拟合不接受其增益窗口时，临时换成可拟合的占位窗口

    上游 `variable_gain` 要求把 (gain_min, gain_flatmax, nf_min, nf_max) 拟合成 2 级 NF 模型，
    拟合不合法（ΔP 越界）就会拒绝加载；但带 nf_vs_gain 的光放 NF 完全由表决定，并不需要该模型。
    这里在加载前把这类条目的 gain_min 换成占位值，加载后由 :func:`restore_gain_range` 还原。

    :param json_data: 原始设备库 json（就地修改）
    :return: {型号: 配置里的 gain_min}，供加载后还原
    """
    saved = {}
    for entry in json_data.get('Edfa', []):
        if not entry.get(NF_CURVE_KEY) or entry.get('type_def', 'variable_gain') != 'variable_gain':
            continue
        variety = entry['type_variety']
        try:
            estimate_nf_model(variety, entry['gain_min'], entry['gain_flatmax'], entry['nf_min'], entry['nf_max'])
            continue    # 原参数本来就能通过拟合校验，不做任何改动
        except EquipmentConfigError:
            pass
        stub = _fitting_gain_min(entry['gain_flatmax'], entry['nf_min'], entry['nf_max'])
        if stub is None:
            raise EquipmentConfigError(
                f'{variety}: with nf_min={entry["nf_min"]} and nf_max={entry["nf_max"]} the upstream two-coil '
                f'NF model accepts no gain window, so {NF_CURVE_KEY} cannot be loaded')
        logger.info(f'{variety}: upstream NF model rejects gain_min={entry["gain_min"]} dB, '
                    f'loading with {stub} dB and restoring afterwards (NF comes from {NF_CURVE_KEY})')
        saved[variety] = entry['gain_min']
        entry['gain_min'] = stub
    return saved


def restore_gain_range(equipment: Dict, saved: Dict[str, float]) -> None:
    """把 :func:`stub_gain_range_for_curves` 临时替换掉的 gain_min 还原到设备库条目上

    :param equipment: 设备库字典
    :param saved: :func:`stub_gain_range_for_curves` 的返回值
    """
    for variety, gain_min in saved.items():
        amp = equipment.get('Edfa', {}).get(variety)
        if amp is not None:
            amp.gain_min = gain_min


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
        if self.effective_gain < gains[0]:
            logger.warning(f'{self.uid}: gain {self.effective_gain:.2f} dB is below the {NF_CURVE_KEY} table '
                           f'minimum {gains[0]:.2f} dB: NF is clamped to {nfs[0]:.2f} dB')
        elif self.effective_gain > gains[-1]:
            logger.warning(f'{self.uid}: gain {self.effective_gain:.2f} dB is above the {NF_CURVE_KEY} table '
                           f'maximum {gains[-1]:.2f} dB: NF is clamped to {nfs[-1]:.2f} dB')
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

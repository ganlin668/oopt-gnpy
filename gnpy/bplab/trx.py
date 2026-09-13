# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.bplab.trx: BPLab add-on transceiver/module helpers
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
gnpy.bplab.trx
==============

BPLab 自建的收发模块（Transceiver）工具（纯新增，不修改 gnpy 原有代码）：

- :class:`TrxMode` / :func:`register_trx_type`：用代码定义模块性能并注册进设备库
- :func:`load_equipment_with_module_power`：加载设备库，并把模块模式里的 ``tx_power``
  （模块最大出光功率，YANG 模型不接受该字段）映射为 gnpy 消费的 ``tx_channel_power_max_dbm``
- :func:`launch_power_dbm`：每波道发射功率 = ``min(请求值, tx_power)``，请求值缺省时取 ``tx_power``
"""

from copy import deepcopy
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path
from typing import Dict, List, Optional, Union

from gnpy.bplab.edfa import (BPLAB_EXTRA_CONFIGS, attach_nf_curves, extract_nf_curves, restore_gain_range,
                             stub_gain_range_for_curves)
from gnpy.tools.convert_legacy_yang import yang_to_legacy
from gnpy.tools.default_edfa_config import DEFAULT_EXTRA_CONFIG
from gnpy.tools.json_io import Transceiver, _equipment_from_json, load_json

logger = getLogger(__name__)

# 模块模式里"模块最大出光功率"的字段名（dBm）；YANG 模型不接受该字段
MODULE_MAX_POWER_KEY = 'tx_power'
# legacy json / 设备库里的等价字段（连字符命名，交给 json_io.Transceiver 时必须是这种写法）
LEGACY_MAX_POWER_KEY = 'tx-channel-power-max-dbm'
# gnpy 消费时使用的下划线命名
GNPY_MAX_POWER_KEY = 'tx_channel_power_max_dbm'


@dataclass
class TrxMode:
    """用代码描述一个收发模块模式（等价于设备库 Transceiver.mode 里的一项）

    :param format: 模式名（唯一）
    :param baud_rate: 波特率 [Hz]
    :param OSNR: 模块 B2B 无误码所需 OSNR（0.1 nm 参考）[dB]
    :param bit_rate: 比特率 [bit/s]
    :param roll_off: 滚降系数
    :param tx_osnr: 发射机自身 OSNR [dB]
    :param tx_power: 模块最大出光功率 [dBm]，映射为 tx_channel_power_max_dbm
    """

    format: str
    baud_rate: float
    OSNR: float
    bit_rate: Optional[float] = None
    roll_off: float = 0.15
    tx_osnr: Optional[float] = None
    tx_power: Optional[float] = None
    min_spacing: Optional[float] = None
    cost: Optional[float] = None
    tx_channel_power_min_dbm: Optional[float] = None
    tx_channel_power_max_dbm: Optional[float] = None
    rx_channel_power_min_dbm: Optional[float] = None
    rx_channel_power_max_dbm: Optional[float] = None
    penalties: List[Dict] = field(default_factory=list)
    equalization_offset_db: float = 0
    detailed_rx: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """转成设备库的 mode 项（dict）

        ``tx_power`` 会原样保留（供 bplab / 脚本读取），并同步写入
        ``tx-channel-power-max-dbm``（gnpy 真正消费的字段）。
        """
        max_power_dbm = self.tx_channel_power_max_dbm if self.tx_channel_power_max_dbm is not None \
            else self.tx_power
        return {
            'format': self.format,
            'baud_rate': self.baud_rate,
            'OSNR': self.OSNR,
            'bit_rate': self.bit_rate,
            'roll_off': self.roll_off,
            'tx_osnr': self.tx_osnr,
            MODULE_MAX_POWER_KEY: self.tx_power,
            'min_spacing': self.min_spacing,
            'cost': self.cost,
            LEGACY_MAX_POWER_KEY: max_power_dbm,
            'tx-channel-power-min-dbm': self.tx_channel_power_min_dbm,
            'rx-channel-power-min-dbm': self.rx_channel_power_min_dbm,
            'rx-channel-power-max-dbm': self.rx_channel_power_max_dbm,
            'penalties': deepcopy(self.penalties),
            'equalization_offset_db': self.equalization_offset_db,
            'detailed_rx': deepcopy(self.detailed_rx),
        }


def register_trx_type(equipment: Dict, type_variety: str, modes: List[Union[TrxMode, Dict]],
                      f_min: float, f_max: float) -> None:
    """把代码定义的收发模块注册进设备库，之后可用 gnpy.core.equipment.trx_mode_params 取用

    :param equipment: 设备库字典（gnpy.tools.json_io.load_equipment 的返回值）
    :param type_variety: 模块型号名
    :param modes: 模式列表（TrxMode 或 dict）
    :param f_min: 模块支持的最低频率 [Hz]
    :param f_max: 模块支持的最高频率 [Hz]
    """
    mode_dicts = [m.to_dict() if isinstance(m, TrxMode) else deepcopy(m) for m in modes]
    equipment['Transceiver'][type_variety] = Transceiver(
        type_variety=type_variety, frequency={'min': f_min, 'max': f_max}, mode=mode_dicts)


def _extract_module_power(json_data: Dict) -> Dict:
    """从原始（未转换）json 里摘出各模式 tx_power，并把它从 dict 里移除

    YANG 模型不接受 mode 里的 tx_power，必须先摘掉才能让其余字段正常走 YANG 校验。
    """
    extracted = {}
    for trx in json_data.get('Transceiver', []):
        for mode in trx.get('mode', []):
            if MODULE_MAX_POWER_KEY in mode:
                extracted[(trx.get('type_variety'), mode.get('format'))] = mode.pop(MODULE_MAX_POWER_KEY)
    return extracted


def _inject_module_power(json_data: Dict, extracted: Dict) -> None:
    """把之前摘出的 tx_power 回填进 legacy json，并同步写入 gnpy 消费的连字符字段"""
    for trx in json_data.get('Transceiver', []):
        for mode in trx.get('mode', []):
            tx_power = extracted.get((trx.get('type_variety'), mode.get('format')))
            if tx_power is None:
                continue
            mode[MODULE_MAX_POWER_KEY] = tx_power
            # 注意：此处必须用连字符命名，json_io.Transceiver 会做 '-' -> '_' 转换；
            # 直接写下划线 key 会被它改写回 None
            if mode.get(LEGACY_MAX_POWER_KEY) is None:
                mode[LEGACY_MAX_POWER_KEY] = tx_power


def load_equipment_with_module_power(filename: Union[str, Path],
                                     extra_configs: Dict = DEFAULT_EXTRA_CONFIG) -> Dict:
    """加载设备库，并支持模块模式里的 tx_power（模块最大出光功率）

    与 gnpy.tools.json_io.load_equipment 的唯一区别：先把 mode 里的 tx_power 摘出来
    （YANG 模型不接受该字段），其余字段仍照常走 gnpy 的 YANG 校验与转换；
    转换后再把 tx_power 回填，并同步写入 gnpy 消费的 tx-channel-power-max-dbm。

    同样手法处理 Edfa 条目的 bplab 扩展字段 nf_vs_gain（增益 -> NF 表，YANG 模型也不接受）：
    校验前摘掉，校验后挂到 equipment['Edfa'][型号] 上，供 gnpy.bplab.edfa.GainNfEdfa 使用。
    带该表的光放不再需要上游 variable_gain 的 2 级 NF 模型，但其拟合合法性校验会拒绝加载
    （拟合出的 ΔP 越界时），故加载前把这类条目的 gain_min 换成占位值、加载后还原。

    另外自动并入 bplab 自带的光放附加配置（见 gnpy.bplab.edfa.BPLAB_EXTRA_CONFIGS），
    设备库条目可直接用 default_config_from_json 引用其中的文件名（如 'linear_dgt.json'）。

    :param filename: 设备库 json 路径
    :param extra_configs: 附加配置（advanced/default_config_from_json 引用），默认 gnpy 自带的，
        与 bplab 自带的合并后使用
    :return: 设备库字典
    """
    extra_configs = {**extra_configs, **BPLAB_EXTRA_CONFIGS}
    raw = load_json(Path(filename))
    extracted = _extract_module_power(raw)
    # 必须在 extract_nf_curves 之前：后者会把 nf_vs_gain 摘掉，本函数据此判断哪些光放走查表
    stubbed_gain_min = stub_gain_range_for_curves(raw)
    nf_curves = extract_nf_curves(raw)
    # YANG 模型不识别 bplab 扩展段 Passive（由 gnpy.bplab.passives.load_passive_library 单独解析），
    # 与 tx_power 同一手法：先摘掉才能通过 libyang 校验
    raw.pop('Passive', None)
    json_data = yang_to_legacy(raw)
    _inject_module_power(json_data, extracted)
    equipment = _equipment_from_json(json_data, extra_configs)
    attach_nf_curves(equipment, nf_curves)
    restore_gain_range(equipment, stubbed_gain_min)
    return equipment


def launch_power_dbm(mode: Dict, requested_dbm: Optional[float] = None) -> Optional[float]:
    """每波道发射功率：以模块最大出光功率 tx_power 为默认值，并把它作为上限

    :param mode: 模式 dict（trx_mode_params 的返回值），应含 'tx_power'
    :param requested_dbm: 期望的每波道功率 [dBm]；None 表示用模块最大出光功率
    :return: 每波道发射功率 [dBm]

    >>> launch_power_dbm({'tx_power': -6})
    -6
    >>> launch_power_dbm({'tx_power': -6}, -10)
    -10
    """
    tx_power_dbm = mode.get(MODULE_MAX_POWER_KEY)
    if requested_dbm is None:
        return tx_power_dbm
    if tx_power_dbm is None:
        return requested_dbm
    if requested_dbm > tx_power_dbm:
        logger.warning(f'Requested launch power {requested_dbm} dBm exceeds the module max output power '
                       f'{tx_power_dbm} dBm: clamping to {tx_power_dbm} dBm')
        return tx_power_dbm
    return requested_dbm

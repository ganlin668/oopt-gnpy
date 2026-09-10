# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.bplab.passives: BPLab add-on band dependent passive elements
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
gnpy.bplab.passives
===================

BPLab 自建的按波段区分插损的无源器件（纯新增，不修改 gnpy 原有代码）。

gnpy 自带的 Fused 元素只有一个标量插损，无法表示 C / L 波段不同的插损；
本模块提供 BandAttenuator：插损按波段给出，波段频率边界复用 DWDM_BAND_RANGES。

设备库中对应的配置段（YANG 不识别该扩展段，由 load_passive_library 单独解析）：

    "Passive": [
      {"type_variety": "Mux", "loss": {"C96": 3.0, "L96": 3.5}}
    ]
"""

from pathlib import Path
from typing import Dict

from numpy import zeros

from gnpy.bplab.utils import DWDM_BAND_RANGES
from gnpy.core.elements import _Node
from gnpy.core.info import is_in_band
from gnpy.core.parameters import Parameters
from gnpy.tools.json_io import load_json


class PassiveParams(Parameters):
    """无源器件参数

    :param type_variety: 型号名
    :param loss: 各波段插损 [dB]，如 {'C96': 3.0, 'L96': 3.5}
    """

    def __init__(self, **kwargs):
        self.type_variety = kwargs.get('type_variety', '')
        self.loss = dict(kwargs.get('loss', {}))


class BandAttenuator(_Node):
    """纯衰减器件（Mux / Demux / VOA / FIU）：对落在各波段内的波道施加对应插损

    只缩放 pch 即可：signal / ase / nli 都是 pch 乘以其比例值，三者同步衰减。
    未落在任何已定义波段内的波道衰减 0 dB。
    """

    def __init__(self, *args, params=None, **kwargs):
        super().__init__(*args, params=PassiveParams(**(params or {})), **kwargs)
        self.passive = True

    def __call__(self, si):
        attenuation_db = zeros(si.number_of_channels)
        for band_name, loss_db in self.params.loss.items():
            f_min, f_max = DWDM_BAND_RANGES[band_name]
            in_band = is_in_band(si.frequency, si.slot_width, {'f_min': f_min, 'f_max': f_max})
            attenuation_db[in_band] = loss_db
        si.apply_attenuation_db(attenuation_db)
        return si

    def __repr__(self):
        return f'{type(self).__name__}(uid={self.uid!r}, loss={self.params.loss!r})'

    def __str__(self):
        return f'{type(self).__name__} {self.uid}\n  loss (dB): {self.params.loss}'


def load_passive_library(filename: Path) -> Dict[str, PassiveParams]:
    """读取设备库 json 中的 Passive 段

    :param filename: 设备库 json 路径
    :return: {type_variety: PassiveParams}
    """
    entries = load_json(Path(filename)).get('Passive', [])
    return {entry['type_variety']: PassiveParams(**entry) for entry in entries}

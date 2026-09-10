# 用代码自定义模块性能 + 支持 tx_power（模块最大出光功率）并在功率/OSNR 中生效

## Summary

新增两个文件、改一个脚本，**不动任何 gnpy 上游文件**：

1. **新增** `gnpy/bplab/trx.py`（自建包，符合项目规则）：
   - `TrxMode`（dataclass）+ `register_trx_type()`：**用代码定义模块性能**并注册进设备库，之后可直接用 gnpy 原生 `trx_mode_params()` 取用。
   - `load_equipment_with_module_power()`：**加载设备库**，并把模块模式里的 `tx_power`（模块最大出光功率，YANG 模型不接受该字段）映射为 gnpy 真正消费的 `tx_channel_power_max_dbm`。
   - `launch_power_dbm(mode, requested_dbm=None)`：**每波道发射功率**语义 = 默认取 `tx_power`，并把 `tx_power` 作为上限钳位（超过时告警）。
2. **新增** `tests/bplab/test_trx.py`：覆盖注册、tx_power 映射、适配器与 `load_equipment` 等价性、钳位语义。
3. **改** `scripts/ganlin/test_gnpy/test2.py`：设备库改为从同目录的 `eqpt_config.json` 载入，案例切到 `Huawei` / `800G ZR+ TFLN BOL`（131.3 GBaud、150 GHz 栅格、-6 dBm/ch、OSNR 24.5），发射功率由 `tx_power` 驱动。

## Current State Analysis

### 问题根因（已实测）
- `gnpy/example-data/eqpt_config.json` 的改动**已被你撤回**（`git status` 里已不再出现该文件）。
- 你新建了 [scripts/ganlin/test_gnpy/eqpt_config.json](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/eqpt_config.json)：Edfa/Fiber/Span/Roadm/SI 与上游一致，`Transceiver` 段只有 `Huawei` 一个 type_variety、一个模式 `800G ZR+ TFLN BOL`，频段 186.275~196.075 THz，含 `"tx_power": -6`。
- `load_equipment()` **一律做 YANG 校验**：[json_io.py#L472-L476](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/tools/json_io.py#L472-L476) → `load_gnpy_json` → [json_io.py#L841](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/tools/json_io.py#L841) `yang_to_legacy(load_json(...))`。而 YANG 的 `mode` 节点（[gnpy-eqpt-config@2026-05-28.yang#L1046-L1089](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/yang/gnpy-eqpt-config@2026-05-28.yang#L1046-L1089)）**没有 `tx_power` 叶子**，所以当前这个本地文件用 `load_equipment` 加载会直接抛
  `libyang: Node "tx_power" not found as a child of "mode" node`（已实测复现）。

### gnpy 里"模块最大出光功率"的等价字段
- `tx_channel_power_max_dbm` 是 YANG 允许、且**已被 gnpy 消费**的字段：[json_io.py#L948-L950](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/tools/json_io.py#L948-L950) 在请求未显式给功率时把它当作每波道发射功率；[request.py#L446-L470](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/topology/request.py#L446-L470) 在自动选模式时同样使用。
- `tx_power` 在 gnpy 里是**死字段**（模式级无人读取）；`tx_channel_power_min_dbm` 也只搬运不消费，且 gnpy **没有任何发端功率钳位逻辑**。

### 适配器可行性（已实测）
- `yang_to_legacy(load_json(f)) == load_gnpy_json(f)` → **True**，且用 `_equipment_from_json(该结果, DEFAULT_EXTRA_CONFIG)` 得到的设备库里 `SI.default.__dict__`、`Edfa['std_low_gain'].__dict__`、各段条目名都与 `load_equipment(f)` **完全一致**。即在"基础转换"上复用 gnpy 官方链路是安全的。
- **关键坑（已实测）**：`yang_to_legacy` 会把缺失叶子物化成 `None`，且此时这四个功率字段用的是**连字符命名**；[json_io.Transceiver.__init__](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/tools/json_io.py#L176-L182) 随后做 `'-' → '_'` 转换，**若在 legacy dict 里写的是下划线 key，会被它改写回的 None 覆盖**。因此回填必须写 `mode['tx-channel-power-max-dbm']`（连字符），转换后才会得到 `tx_channel_power_max_dbm = -6`（已实测通过）。

### 案例数值基线（已用 .venv 实测，供实现后核对）
本地模块 `Huawei/800G ZR+ TFLN BOL` = 131.3 GBaud、roll_off 0.05、OSNR 24.5、tx_osnr 41、tx_power −6 dBm、min_spacing 150 GHz；按 150 GHz 栅格取 C96/L96 频点：

- 波数 **63**（L96 32 波 + C96 31 波），186.35 ~ 195.95 THz，每波道 −6 dBm，合成功率 12.06 dBm
- `BW*(1+Rolloff)` = 131.3 × 1.05 = **137.865 GHz**
- SRS 转移（有 SRS 相对纯衰减，光纤自身剖面的首/末波长）：**+0.175 dB / −0.185 dB**；光纤输出首/末 −21.83 / −22.18 dBm
- ch1：SNR_NLI 55.19、SNR_ASE 18.39、SNR_TRX 24.5、SNR_total **17.44**（输出功率 −5.78 dBm）
- ch63：SNR_NLI 54.09、SNR_ASE 17.85、SNR_TRX 24.5、SNR_total **17.00**（−6.10 dBm）
- SNR_total：平均 **17.22**、最差 **17.00**、最好 **17.44** dB

## Proposed Changes

### 文件 1（新建）：gnpy/bplab/trx.py

```python
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

    :param filename: 设备库 json 路径
    :param extra_configs: 附加配置（advanced_config_from_json 引用），默认 gnpy 自带的
    :return: 设备库字典
    """
    raw = load_json(Path(filename))
    extracted = _extract_module_power(raw)
    json_data = yang_to_legacy(raw)
    _inject_module_power(json_data, extracted)
    return _equipment_from_json(json_data, extra_configs)


def launch_power_dbm(mode: Dict, requested_dbm: Optional[float] = None) -> Optional[float]:
    """每波道发射功率：以模块最大出光功率 tx_power 为默认值，并把它作为上限

    :param mode: 模式 dict（trx_mode_params 的返回值），应含 'tx_power'
    :param requested_dbm: 期望的每波道功率 [dBm]；None 表示用模块最大出光功率
    :return: 每波道发射功率 [dBm]

    >>> launch_power_dbm({'tx_power': -6})
    -6
    >>> launch_power_dbm({'tx_power': -6}, -10)
    -10
    >>> launch_power_dbm({'tx_power': -6}, 0)
    -6
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
```

> doctest 会被 `pytest.ini` 的 `--doctest-modules` 执行，注意返回值 `-6`/`-10`（int/float 混用：`{'tx_power': -6}` 是 int，`min(-10, -6)` 走 `return requested_dbm` 分支返回 `-10.0`）。**实现时以真实 doctest 输出为准**（若输出为 `-10.0` 就写成 `-10.0`），先跑 `pytest gnpy/bplab/trx.py` 校准。

### 文件 2（新建）：tests/bplab/test_trx.py

```python
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# test_bplab_trx
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
Unit tests for gnpy.bplab.trx
"""
import json
from pathlib import Path

import pytest

from gnpy.bplab.trx import TrxMode, launch_power_dbm, load_equipment_with_module_power, register_trx_type
from gnpy.core.equipment import trx_mode_params
from gnpy.tools.json_io import DEFAULT_EQPT_CONFIG, load_equipment, load_json

TFLN_800G = TrxMode(format='800G ZR+ TFLN BOL', baud_rate=131.3e9, OSNR=24.5, bit_rate=800e9,
                    roll_off=0.05, tx_osnr=41, tx_power=-6, min_spacing=150e9, cost=1)


def test_register_trx_type():
    """代码定义的模块能被 gnpy 原生 trx_mode_params 取到，tx_power 映射为 tx_channel_power_max_dbm"""
    equipment = load_equipment(Path(DEFAULT_EQPT_CONFIG))
    register_trx_type(equipment, 'TFLN_800G', [TFLN_800G], f_min=186.275e12, f_max=196.075e12)
    params = trx_mode_params(equipment, 'TFLN_800G', '800G ZR+ TFLN BOL')
    assert params['baud_rate'] == 131.3e9
    assert params['OSNR'] == 24.5
    assert params['tx_osnr'] == 41
    assert params['min_spacing'] == 150e9
    assert params['tx_power'] == -6
    assert params['tx_channel_power_max_dbm'] == -6
    assert params['f_min'] == 186.275e12 and params['f_max'] == 196.075e12


def test_register_trx_type_does_not_mutate_mode():
    """注册后 TrxMode 本身不被 json_io.Transceiver 原地改写"""
    equipment = load_equipment(Path(DEFAULT_EQPT_CONFIG))
    register_trx_type(equipment, 'TFLN_800G', [TFLN_800G], f_min=191.35e12, f_max=196.1e12)
    assert TFLN_800G.penalties == [] and TFLN_800G.detailed_rx == {}


def test_adapter_matches_load_equipment_for_upstream_file():
    """对不含 tx_power 的上游文件，适配器结果与 gnpy 原生 load_equipment 完全一致"""
    adapted = load_equipment_with_module_power(DEFAULT_EQPT_CONFIG)
    reference = load_equipment(Path(DEFAULT_EQPT_CONFIG))
    assert sorted(adapted) == sorted(reference)
    assert adapted['SI']['default'].__dict__ == reference['SI']['default'].__dict__
    assert sorted(adapted['Edfa']) == sorted(reference['Edfa'])
    assert adapted['Edfa']['std_low_gain'].__dict__ == reference['Edfa']['std_low_gain'].__dict__


def test_load_equipment_with_module_power(tmp_path):
    """含 tx_power 的设备库能被加载，且 tx_power 映射到 tx_channel_power_max_dbm"""
    data = load_json(Path(DEFAULT_EQPT_CONFIG))
    mode = data['Transceiver'][1]['mode'][0]
    mode['tx_power'] = -6.5
    filename = tmp_path / 'eqpt_config.json'
    filename.write_text(json.dumps(data), encoding='utf-8')

    equipment = load_equipment_with_module_power(filename)
    params = trx_mode_params(equipment, data['Transceiver'][1]['type_variety'], mode['format'])
    assert params['tx_power'] == -6.5
    assert params['tx_channel_power_max_dbm'] == -6.5


def test_load_equipment_with_module_power_rejects_other_invalid_fields(tmp_path):
    """适配器只放行 tx_power，其它非法字段仍会被 YANG 校验拒绝"""
    data = load_json(Path(DEFAULT_EQPT_CONFIG))
    data['Transceiver'][1]['mode'][0]['not_a_field'] = 1
    filename = tmp_path / 'eqpt_config.json'
    filename.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(Exception):
        load_equipment_with_module_power(filename)


def test_launch_power_dbm_defaults_to_module_max_power():
    assert launch_power_dbm({'tx_power': -6}) == -6


def test_launch_power_dbm_clamps_and_warns(caplog):
    with caplog.at_level('WARNING'):
        assert launch_power_dbm({'tx_power': -6}, 0) == -6
    assert 'clamping' in caplog.text


def test_launch_power_dbm_below_module_max_power():
    assert launch_power_dbm({'tx_power': -6}, -10) == -10


def test_launch_power_dbm_without_module_power():
    assert launch_power_dbm({}, -3) == -3
    assert launch_power_dbm({}) is None
```

> `test_load_equipment_with_module_power_rejects_other_invalid_fields` 用 `pytest.raises(Exception)`：libyang 抛的是 `oopt_gnpy_libyang.Error`，避免在测试里 import 该包。实现时先确认确实抛异常，若换成断言具体类型更稳就改。

### 文件 3（修改）：scripts/ganlin/test_gnpy/test2.py

改动点（其余全部保留：SRS、SNR 四列、波长横轴绘图）：

```python
from gnpy.bplab.trx import launch_power_dbm, load_equipment_with_module_power
...
from gnpy.core.equipment import trx_mode_params
...
# ---------------------------------------------------------------- 设备库 / 模块参数
# 设备库改为同目录下、含 800G 模块性能的本地副本（上游 gnpy/example-data/eqpt_config.json 不动）
EQPT_CONFIG = Path(__file__).parent / 'eqpt_config.json'
equipment = load_equipment_with_module_power(EQPT_CONFIG)
si_default = equipment['SI']['default']

# 模块性能直接来自设备库里的模式定义
trx_mode = trx_mode_params(equipment, 'Huawei', '800G ZR+ TFLN BOL')
snr_trx_db = trx_mode['OSNR']          # 模块 B2B 无误码所需 OSNR，24.5 dB
tx_osnr_db = trx_mode['tx_osnr']       # 发射机自身 OSNR，41 dB
# 模块最大出光功率 tx_power = -6 dBm，作为每波道发射功率的默认值并作为上限
launch_dbm = launch_power_dbm(trx_mode)
```

链路参数段：`spacing` 改为 `trx_mode['min_spacing']`（150 GHz），并新增 `baud_rate`/`roll_off` 变量：

```python
spacing = trx_mode['min_spacing']      # 150 GHz
baud_rate = trx_mode['baud_rate']      # 131.3 GBaud
roll_off = trx_mode['roll_off']        # 0.05
```

频谱构造改为：

```python
si = create_arbitrary_spectral_information(
    frequency=frequency, slot_width=spacing, pch=dbm2watt(launch_dbm),
    baud_rate=baud_rate, roll_off=roll_off, tx_osnr=tx_osnr_db,
    tx_power=dbm2watt(launch_dbm), required_osnr_db_01nm=snr_trx_db)
```

打印头两行改为体现模块与发射功率：

```python
print(f'模块 {trx_mode["format"]}（{baud_rate * 1e-9:.1f} GBaud / roll_off {roll_off}），'
      f'模块最大出光功率 tx_power = {launch_dbm:.1f} dBm/波')
print(f'跨度损耗 {span_loss_db:.1f} dB，EDFA 增益 {edfa.operational.gain_target:.1f} dB，'
      f'放大频带 {edfa.params.f_min * 1e-12:.3f} ~ {edfa.params.f_max * 1e-12:.3f} THz')
print(f'频谱：{si.number_of_channels} 波（L96 {len(l96_centers)} + C96 {len(c96_centers)}），'
      f'{si.frequency[0] * 1e-12:.3f} ~ {si.frequency[-1] * 1e-12:.3f} THz，'
      f'{baud_rate * 1e-9:.1f} GBaud / {spacing * 1e-9:.0f} GHz，'
      f'每波道 {launch_dbm:.1f} dBm（共 {si.ptot_dbm:.2f} dBm）')
```

其余（SNR 四列的口径说明、SRS 转移量打印、分波段汇总、波形图）**不改**，只是 `bw_eff = si.baud_rate * (1 + si.roll_off)` 自动变成 137.865 GHz。

## Assumptions & Decisions

1. **不改任何 gnpy 上游文件**（含 YANG、json_io、equipment）：`tx_power` 由 bplab 适配器在加载阶段剥离/回填，其余字段仍走 gnpy 原生 YANG 校验（这也是选择"先摘 tx_power 再 `yang_to_legacy`"而不是"直接 `load_json` 后交给 `_equipment_from_json`"的原因——后者会丢掉全部校验）。
2. **真正驱动发射功率的是 `tx_channel_power_max_dbm`**：`tx_power` 是它的别名，回填时两者都写；若文件里已显式写了 `tx-channel-power-max-dbm`，以文件里的值为准（只在为 None 时用 tx_power 覆盖）。
3. **生效语义（按你的选择）**：`launch_power_dbm` = 默认值 + 上限钳位；钳位时用 `logger.warning` 告警（不中断）。test2.py 里请求值为 None，即直接用 `tx_power`。
4. **`TrxMode` + `register_trx_type` 保留**：这是你第一轮明确选择的"用代码直接自定义性能"的能力；本次它用在单测里，test2.py 走本地 JSON 路径（按你第二轮的指示）。
5. 案例参数随之变化：栅格 150 GHz → C96/L96 各 31/32 波（共 63 波，不是 96/96），发射功率 −6 dBm，SNR_TRX = 24.5 dB。这是"改用 800G 模块"的必然结果，输出结构不变。
6. `gnpy/bplab/trx.py` 与 `tests/bplab/test_trx.py` 都必须带仓库标准文件头（`tests/test_opensource_compliancy.py::test_file_headers` 会校验）。

## Verification

1. 新单测：`.\.venv\Scripts\python.exe -m pytest tests/bplab -q` → 全绿（含既有 `test_utils.py` 的 19 个）。
2. doctest：`.\.venv\Scripts\python.exe -m pytest gnpy/bplab/trx.py gnpy/bplab/utils.py -q` → 全绿。
3. 文件头合规：`.\.venv\Scripts\python.exe -m pytest tests/test_opensource_compliancy.py::test_file_headers -q` → 通过。
4. 静态检查：`.\.venv\Scripts\python.exe -m flake8 gnpy/bplab tests/bplab --max-line-length=120` → 无告警。
5. 脚本端到端（无 GUI）：`$env:MPLBACKEND='Agg'; .\.venv\Scripts\python.exe scripts\ganlin\test_gnpy\test2.py` → exit 0，并核对与"案例数值基线"一致：
   - 表头出现 `tx_power = -6.0 dBm/波`、`BW*(1+Rolloff) = 137.86 GHz`、`SNR_TRX = 24.5 dB`、`TX_OSNR = 41.00 dB`；
   - `63 波（L96 32 + C96 31）`，`186.350 ~ 195.950 THz`；
   - ch1 `SNR_NLI 55.19 / SNR_ASE 18.39 / SNR_total 17.44`，ch63 `54.09 / 17.85 / 17.00`；
   - `SNR_total` 平均 17.22、最差 17.00、最好 17.44；SRS 转移 +0.175 / −0.185 dB；
   - 图 `spectrum_c96_l96.png` 重新生成。
6. 上游未被污染：`git status --short` 只应出现 `gnpy/bplab/trx.py`、`tests/bplab/test_trx.py`、`scripts/ganlin/test_gnpy/test2.py`（以及既有的本地 json / png），**不得出现 `gnpy/example-data/eqpt_config.json`、`gnpy/yang/*`、`gnpy/tools/*`**。

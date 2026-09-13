# 在 bplab 中新增光纤类型（衰减的波长相关性）

## Summary

把 `scripts/ganlin/test_gnpy/fiber_data.py` 里的**解析式衰减模型**按 gnpy 风格移植进 gnpy 侧，新增模块
`gnpy/bplab/fibers.py`，覆盖 21 个不依赖外部数据文件的类型名（`ssmf` / `G.652.D` / `G.654.E` / `pscf` /
`leaf` / `*_wangziwei` 等），对外提供：

1. `fiber_attenuation_db_per_km(fiber_type, wavelength_m)` —— 纯函数，给波长算衰减系数 [dB/km]；
2. `loss_coef_table(fiber_type, frequency, ref_loss_db_per_km=None, ref_wavelength_m=1550e-9)` —— 生成
   `FiberParams.loss_coef` 需要的 `{'value': [...dB/km...], 'frequency': [...Hz...]}` 表，可选地在
   1550 nm 处锚定到指定值（与上游 `loss_coef_ripple` 的“相对 1550 nm 起伏 + 静态锚点”语义一致）。

**不改动 `gnpy/` 下任何上游文件**（遵守 `.claude/rules/bplab-add-on.md`）；`fiber_data.py` 作为参考原样保留。

## Current State Analysis

### 上游现状：只有逐点 LUT，没有衰减模型

- gnpy 支持的“波长相关衰减”只有两种逐点查表形式：
  - 拓扑元素参数：`loss_coef = {'value': [...dB/km...], 'frequency': [...Hz...]}` ——
    [parameters.py#L353-L359](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/parameters.py#L353-L359)；
  - 设备库 Fiber 条目的 `loss_coef_ripple` 列表，由 [json_io.py#L758-L766](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/tools/json_io.py#L758-L766)
    在其上叠加拓扑里的标量 `loss_coef` 后变成上面的 dict（仅在加载**拓扑 JSON** 时生效）。
- 全仓库搜索 `acoeff` / `Att_fit` / `A/λ^4 + B·exp(-C/λ)` **无任何命中**（唯一命中就是本地脚本
  `fiber_data.py`），即 gnpy 侧目前没有解析式衰减曲线。
- 消费链路已确认可用：`Fiber.loss_coef_func()`（dB/m，可插值）→ `Fiber.alpha()`（Neper/m）→
  `RamanSolver`/`NliSolver`（[elements.py#L1096-L1138](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1096-L1138)、
  [science_utils.py#L99-L101](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/science_utils.py#L99-L101)）。
  因此**只要产出一张合法的 `loss_coef` 表，无需改上游就能全程生效**。
- `FiberParams.ref_wavelength` 默认 **1550 nm**（[parameters.py#L288-L290](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/parameters.py#L288-L290)），
  即单跨损耗 `Fiber.loss` 天然按 1550 nm 结算 —— 与“1550 nm 锚点”的语义吻合。
- 注意：`FiberParams` / `loss_coef_func` / `interpolate_parameter_over_spectrum` **都不排序、不校验**，
  `interp1d` 越界才抛 `SpectrumError`（[elements.py#L1068-L1094](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1068-L1094)）。
  ⇒ 产出的表必须频率升序。

### 参考文件的模型与类型清单（`fiber_data.py`）

三类模型，输入 `wave_m` [m]，输出 **dB/m**：

| 模型 | 表达式 | 出现的类型 |
|---|---|---|
| 瑞利 + OH 吸收 | `A/λ⁴ + B·exp(-C/λ) + corr` | ssmf, ssmf_ideal, G.652.D, G.652.D barefiber, G.655 barefiber, G.652.D Type2, G.654.E(+150um), G.654.E barefiber, G.654.E (110um) MicroJet, G.654.E Subsea, pscf, G.655.C, leaf, nanf_check |
| 对数吸收 | `(A/λ)⁴ + exp(B - C/λ)` | smf_wangziwei, g654e_wangziwei, leaf_wangziwei |
| 常数 | `0.2 dB/km` | smf_flatatt, smf_user_defined |

共 21 个类型名（`G.652.D barefiber` / `G.655 barefiber` 同参数；`G.654.E` / `G.654.E 150um` 同参数；
`G.654.E 110um MicroJet` / `G.654.E MicroJet` 同参数）。

另有 4 个类型（`SSMF-28e`、`G.652.D.Exp`、`G.652.D.Exp_v1`、`SSMF_SHANGJIAO`）依赖
`GlobalControl.datafile_path` + `pd.read_excel` + 仓库中**不存在**的 .dat/.xlsx 数据文件，
`GlobalControl` 在整个仓库中也无定义 ⇒ **本次不纳入**（用户已确认）。

### 已实测的 1550 nm 参考值（来自当前 `fiber_data.py`，作为移植保真度基准）

```
ssmf 0.2575 | ssmf_ideal 0.2575 | G.652.D 0.2500 | G.652.D barefiber 0.2075
G.655 barefiber 0.2075 | G.652.D Type2 0.2825 | G.654.E 0.1901 | G.654.E 150um 0.1901
G.654.E barefiber 0.1706 | G.654.E 110um MicroJet 0.1756 | G.654.E MicroJet 0.1756
G.654.E Subsea 0.1556 | pscf 0.2282 | G.655.C 0.2575 | leaf 0.2575 | nanf_check 0.2207
smf_wangziwei 0.2060 | g654e_wangziwei 0.1897 | leaf_wangziwei 0.2186
smf_flatatt 0.2000 | smf_user_defined 0.2000            （单位 dB/km）
```

（与文件内注释一致：G.652.D 注释写 0.250019、G.652.D barefiber 写 0.207、G.654.E barefiber 写 0.170、
smf_wangziwei 写 0.2060。）

### bplab 约定（新模块必须遵守）

- 5 行文件头（coding / SPDX / 模块说明 / Copyright / AUTHORS），否则
  `tests/test_opensource_compliancy.py::test_file_headers` 会红；
- numpy **扁平导入**（`from numpy import ...`），不用 `import numpy as np`；
- 函数 docstring 用中文 + `:param:`/`:return:`，**纯函数补 `>>>` doctest**（`pytest.ini` 全局
  `--doctest-modules`，doctest 会被真实执行）；
- 行宽 ≤ 120、字符串以单引号为主；新测试放 `tests/bplab/`，不新增 `tests/bplab/__init__.py`。

## Proposed Changes

### 1. 新建 `gnpy/bplab/fibers.py`

**文件头（照抄 bplab 现有格式）：**

```python
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.bplab.fibers: BPLab add-on wavelength dependent fiber attenuation models
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors
```

（`Copyright (C) 2025 Telecom Infra Project and GNPy contributors` 与其它 bplab 文件保持一致。）

**模块 docstring**：说明模型来源（移植自 `scripts/ganlin/test_gnpy/fiber_data.py`）、三类模型语义、
单位（入参波长 m / 频率 Hz，输出 dB/km）、以及接入方式（产出 `FiberParams.loss_coef` 的 dict）。

**导入：**

```python
from typing import Dict, Optional, Union

from numpy import asarray, exp, full_like, power

from gnpy.core.exceptions import EquipmentConfigError
from gnpy.core.utils import freq2wavelength
```

**常量与注册表（21 个类型，系数已换算到“λ 用 m、结果 dB/km”的口径）：**

```python
REFERENCE_WAVELENGTH_M = 1550e-9

# 类型名 -> (模型, 系数)
#  'rayleigh': A/λ^4 + B*exp(-C/λ) + corr      —— 系数 (A, B, C, corr)，单位 dB/km
#  'log':      (A/λ)^4 + exp(B - C/λ)          —— 系数 (A, B, C)，单位 dB/km
#  'constant': 与波长无关                       —— 系数 (value,)，单位 dB/km
FIBER_ATTENUATION_MODELS = {
    'ssmf': ('rayleigh', (1.12550881e-27, 1.20801544384579e9, 4.991e-5, 0.05)),
    'ssmf_ideal': ('rayleigh', (1.12550881e-27, 1.20801544384579e9, 4.991e-5, 0.05)),
    'G.652.D': ('rayleigh', (1.12550881e-27, 1.20801544384579e9, 4.991e-5, 0.0425)),
    'G.652.D barefiber': ('rayleigh', (1.12550881e-27, 1.20801544384579e9, 4.991e-5, 0.0)),
    'G.655 barefiber': ('rayleigh', (1.12550881e-27, 1.20801544384579e9, 4.991e-5, 0.0)),
    'G.652.D Type2': ('rayleigh', (1.12550881e-27, 1.20801544384579e9, 4.991e-5, 0.075)),
    'G.654.E': ('rayleigh', (1.07235462999891e-27, 2.19054e8, 4.70049440969118e-5, -0.0105)),
    'G.654.E 150um': ('rayleigh', (1.07235462999891e-27, 2.19054e8, 4.70049440969118e-5, -0.0105)),
    'G.654.E barefiber': ('rayleigh', (1.07235462999891e-27, 2.19054e8, 4.70049440969118e-5, -0.03)),
    'G.654.E 110um MicroJet': ('rayleigh', (1.07235462999891e-27, 2.19054e8, 4.70049440969118e-5, -0.025)),
    'G.654.E MicroJet': ('rayleigh', (1.07235462999891e-27, 2.19054e8, 4.70049440969118e-5, -0.025)),
    'G.654.E Subsea': ('rayleigh', (1.07235462999891e-27, 2.19054e8, 4.70049440969118e-5, -0.045)),
    'pscf': ('rayleigh', (9.48912349840053e-28, 2.960322484755e9, 5.11436876199752e-5, 0.05)),
    'G.655.C': ('rayleigh', (1.12550881e-27, 1.20801544384579e9, 4.991e-5, 0.05)),
    'leaf': ('rayleigh', (1.12550881e-27, 1.20801544384579e9, 4.991e-5, 0.05)),
    'nanf_check': ('rayleigh', (9.48912349840053e-28, 2.960322484755e9, 5.11436876199752e-5, 0.0425)),
    'smf_wangziwei': ('log', (1.02372502895989e-6, 25.447693137258, 4.58792333272982e-5)),
    'g654e_wangziwei': ('log', (1.00181920725003e-6, 26.8534675154108, 4.81167354509278e-5)),
    'leaf_wangziwei': ('log', (1.03858272544868e-6, 25.5215151620792, 4.58737406725094e-5)),
    'smf_flatatt': ('constant', (0.2,)),
    'smf_user_defined': ('constant', (0.2,)),
}
```

**换算说明（相对 `fiber_data.py` 的三处改写，务必在注释里写明）：**

1. 原文件返回 **dB/m**，本模块统一 **dB/km**（乘 1e3，即原式里的 `/1e3` 一律不写）；
   原 `Att_corr = (40 * x / 80) / 1e3` → 本模块 `corr = 40 * x / 80`（如 0.05、0.0425、-0.0105、-0.025、-0.045）。
2. `G.654.E` 家族原写 `1.07235462999891e-24 / 1e3` 等，其 `/1e3` 是**同一单位换算**的一部分，
   故本模块存 `1.07235462999891e-27`、`2.19054e8`、`4.70049440969118e-5`；
   `pscf` / `nanf_check` 同理：`9.48912349840053e-28`、`2.960322484755e9`、`5.11436876199752e-5`。
3. `*_wangziwei` 家族原式为 `((A/λ)^4 + exp(B - C/λ)) / 1e3`，即 dB/km = `(A/λ)^4 + exp(B - C/λ)`。

**函数 1（纯函数 + doctest）：**

```python
def fiber_attenuation_db_per_km(fiber_type: str, wavelength_m: Union[float, list]):
    """给定光纤类型与波长，返回衰减系数 [dB/km]

    :param fiber_type: 类型名，见 FIBER_ATTENUATION_MODELS
    :param wavelength_m: 波长 [m]，标量或数组
    :return: 衰减系数 [dB/km]，与输入同形

    >>> round(float(fiber_attenuation_db_per_km('G.652.D', 1550e-9)), 4)
    0.25
    >>> round(float(fiber_attenuation_db_per_km('G.654.E barefiber', 1550e-9)), 4)
    0.1706
    >>> float(fiber_attenuation_db_per_km('smf_flatatt', 1550e-9))
    0.2
    """
```

实现：查表 → `'constant'` 用 `full_like`（标量输入则直接返回该常量）→ `'rayleigh'` / `'log'` 按公式用
扁平导入的 `power`/`exp` 计算；类型名不存在时 `raise EquipmentConfigError(f'Unknown fiber type {fiber_type}: ...')`
（与 `gnpy.bplab.edfa` 的异常风格一致），并在 docstring 里写明。

**函数 2（产出 gnpy 的 loss_coef 表）：**

```python
def loss_coef_table(fiber_type: str, frequency, ref_loss_db_per_km: Optional[float] = None,
                    ref_wavelength_m: float = REFERENCE_WAVELENGTH_M) -> Dict[str, list]:
    """生成 FiberParams.loss_coef 需要的逐波长表

    :param fiber_type: 类型名，见 FIBER_ATTENUATION_MODELS
    :param frequency: 频率 [Hz]，需**升序**（gnpy 的 interp1d 不做排序）
    :param ref_loss_db_per_km: 给定则整条曲线平移，使 ref_wavelength_m 处正好等于该值；
        None 表示用曲线自身在该波长的值（如 G.652.D 为 0.25 dB/km）
    :param ref_wavelength_m: 锚定波长 [m]，默认 1550 nm（与 FiberParams.ref_wavelength 一致）
    :return: {'value': [... dB/km ...], 'frequency': [... Hz ...]}

    >>> table = loss_coef_table('G.652.D', [193.1e12, 193.4e12], ref_loss_db_per_km=0.275)
    >>> table['frequency']
    [193100000000000.0, 193400000000000.0]
    >>> [round(v, 4) for v in table['value']]   # 193.4 THz = 1550.0 nm 处被锚到 0.275
    [...]
    """
```

实现：`wavelength_m = freq2wavelength(asarray(frequency))` → 曲线值；`ref_loss_db_per_km` 非 None 时
`values = values - fiber_attenuation_db_per_km(fiber_type, ref_wavelength_m) + ref_loss_db_per_km`；
返回 `{'value': values.tolist(), 'frequency': asarray(frequency).tolist()}`。
（第二个 doctest 的期望值在实现时用真实输出校准，与 `gnpy/bplab/trx.py` 里 doctest 的既有做法一致。）

### 2. 新建 `tests/bplab/test_fibers.py`

文件头同上；导入 `pytest`、`from numpy import array, isclose`、`from numpy.testing import assert_allclose`、
`FIBER_ATTENUATION_MODELS, fiber_attenuation_db_per_km, loss_coef_table`、
`from gnpy.core.elements import Fiber`、`from gnpy.core.exceptions import EquipmentConfigError`、
`from gnpy.core.utils import wavelength2freq`。

用例：

1. `test_registry_covers_expected_types`：`set(FIBER_ATTENUATION_MODELS)` 等于 21 个名字的集合
   （显式列出，防止后续误删）。
2. `test_attenuation_at_1550nm_matches_source`：`@pytest.mark.parametrize` 覆盖全部 21 个类型，
   与 Current State Analysis 里记录的 1550 nm 参考值 `assert_allclose(..., atol=1e-4)`。
3. `test_unknown_fiber_type_raises`：未知类型 → `pytest.raises(EquipmentConfigError)`。
4. `test_loss_coef_table_keys_units_and_order`：`loss_coef_table('G.652.D', 升序频率)` 的 keys 为
   `{'value', 'frequency'}`、两个列表等长、频率原样（Hz）、值单位 dB/km（1550 nm 处 ≈0.25）。
5. `test_loss_coef_table_anchors_reference_loss`：`ref_loss_db_per_km=0.275` 时，
   在 `wavelength2freq(1550e-9)` 处插值结果 `pytest.approx(0.275)`；不传时等于曲线自身值。
6. `test_fiber_element_consumes_table`（与上游打通）：用 `Fiber` 构造
   `params={'length': 80.0, 'length_units': 'km', 'pmd_coef': 0, 'con_in': 0.0, 'con_out': 6.0,
   'loss_coef': loss_coef_table('G.652.D', frequencies, ref_loss_db_per_km=0.275)}`，
   断言 `loss_coef_func(wavelength2freq(1550e-9)) * 1e3 == approx(0.275)`、
   `loss == approx(0.275 * 80 + 6.0)`、`alpha(f) == loss_coef_func(f) / (10 * log10(e))`，
   并确认 `loss_coef_func` 对整条 C+L 频率数组返回有限值（覆盖 63 波场景）。

### 3. 不改动的部分（明确边界）

- `gnpy/` 上游文件：零改动；`fiber_data.py` 原样保留（作为对照/来源记录）。
- `scripts/ganlin/test_gnpy/test2.py`：**本次不改**。附录给出可选改法（若希望脚本也切到新模型）：
  把 `LOSS_COEF_RIPPLE` 常量与手写平移替换为
  `fiber_params.update(loss_coef=loss_coef_table('G.652.D', frequency, ref_loss_db_per_km=loss_coef))`
  （`loss_coef = 0.275` 即 1550 nm 锚点，语义与当前实现完全一致）；注意换模型后 C+L 频段的衰减形状
  会由“示例 LUT”变为 G.652.D 曲线，`SNR/OSNR` 数值随之变化。

## Assumptions & Decisions

1. **范围**：只做解析式 21 个类型（用户已确认）；4 个依赖 `GlobalControl` + 外部 .dat/.xlsx 的类型不纳入，
   模块 docstring 里注明“如后续需要，可在本模块扩展 `datafile_path` 接口”。
2. **集成深度**：只提供纯函数 + `loss_coef` 表（用户已确认），**不做**设备库 `loss_coef_ripple` 注入、
   不新增 Fiber 元素子类；接入点就是上游现成的 `FiberParams.loss_coef` 字典。
3. **单位**：对外统一 dB/km（gnpy 口径），模块内不出现 dB/m；换算关系在注册表注释里逐条写明。
4. **锚点**：默认用曲线自身的 1550 nm 值；`ref_loss_db_per_km` 提供时才平移。平移量
   `ref_loss - 曲线值(ref_wavelength)` 对全波段是常数，因此“相对起伏”形状不变（与上游
   `loss_coef_ripple` 的语义一致）。
5. **不做**：频率排序/越界校验（与上游一致，在 docstring 注明“frequency 需升序”）；不做外推合法性告警
   （解析模型在 C+L 之外的外推行为与原脚本一致，保持忠实移植）。
6. **异常**：未知类型抛 `gnpy.core.exceptions.EquipmentConfigError`（与 `gnpy/bplab/edfa.py` 一致）。
7. 新增 `.py` 必须有仓库标准文件头；`gnpy/bplab/` 的纯函数带 doctest（`--doctest-modules` 会真实执行）。

## Verification

```powershell
# 1) 新增单测 + 模块 doctest（同时回归既有 bplab 用例）
.\.venv\Scripts\python.exe -m pytest tests/bplab gnpy/bplab -q

# 2) 文件头合规
.\.venv\Scripts\python.exe -m pytest tests/test_opensource_compliancy.py::test_file_headers -q

# 3) 静态检查
.\.venv\Scripts\python.exe -m flake8 gnpy/bplab tests/bplab

# 4) 抽样核对（21 个类型 1550 nm 值应与 fiber_data.py 完全一致）
.\.venv\Scripts\python.exe -c "from gnpy.bplab.fibers import FIBER_ATTENUATION_MODELS as M, fiber_attenuation_db_per_km as a; print(len(M)); print({k: round(float(a(k, 1550e-9)), 4) for k in ('ssmf','G.652.D','G.654.E','G.654.E Subsea','smf_wangziwei','pscf')})"
```

判据：`pytest tests/bplab gnpy/bplab -q` 全绿（含新模块 doctest）；文件头用例通过；flake8 无告警；
抽样值与 `fiber_data.py` 实测基准逐项一致；`git status` 仅新增 `gnpy/bplab/fibers.py`、
`tests/bplab/test_fibers.py` 与本计划文档，**无 `gnpy/` 上游文件改动**。

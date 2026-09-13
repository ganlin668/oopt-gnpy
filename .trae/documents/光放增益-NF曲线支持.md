# 光放支持"NF 随增益变化"（增益 → NF 查表）

## Summary

给光放（EDFA）增加"按工作增益查 NF"的能力：

1. 设备库 `eqpt_config.json` 的 Edfa 条目新增扩展字段 `nf_vs_gain`（对象数组 `[{gain, nf}]`），
   `C96_SGA_22dBm` / `L96_SGA_21dBm` 都先填入若干增益点的占位 NF（数值略有差异，后续人工填写真实值）；
2. bplab 新增 `gnpy/bplab/edfa.py`：负责解析该表、以及两个元素类
   `GainNfEdfa`（按表插值 NF）与 `GainNfMultibandAmplifier`（子光放改用 `GainNfEdfa`）；
3. `gnpy/bplab/trx.py` 的设备库加载函数在 **YANG 校验前摘掉** `nf_vs_gain`、**校验后回填**到
   `equipment['Edfa'][型号]`（沿用现有处理 `tx_power` / `Passive` 的同一手法）；
4. `scripts/ganlin/test_gnpy/test2.py` 改用 `GainNfMultibandAmplifier`，并在"光放工作点"一行补印实际 NF；
5. 新增 `tests/bplab/test_edfa.py`。

**不改动 `gnpy/` 下任何上游文件**（遵守 `.claude/rules/bplab-add-on.md`）。

## Current State Analysis

### 上游 NF 计算链路（只读，不修改）

- 装备 JSON → `EdfaParams`：
  [json_io.py#L241-L333](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/tools/json_io.py#L241-L333) 的 `Amp.from_json`，
  对 `type_def == 'variable_gain'` **强制要求** `nf_min` / `nf_max`，并用
  [science_utils.py#L692-L737](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/science_utils.py#L692-L737)
  的 `estimate_nf_model()` 把 (gain_min→nf_max, gain_flatmax→nf_min) 两端点拟合成两段物理模型
  `Model_vg(nf1, nf2, delta_p, orig_nf_min, orig_nf_max)`。
- 运行时：
  [elements.py#L1539-L1575](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1539-L1575) `Edfa.interpol_params()` 先算
  `interpol_nf_ripple`（频率维度纹波），再 `self.nf = self._calc_nf()`；
  [elements.py#L1582-L1650](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1582-L1650) `_nf()` / `_calc_nf()`：
  `variable_gain` 分支 → `nf_avg = lin2db(db2lin(nf1) + db2lin(nf2)/db2lin(g1a))`，返回 `interpol_nf_ripple + nf_avg`。
- `self.nf` 只被 [elements.py#L1689](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1689) `noise_profile()` 使用
  （`ase = h * baud_rate * frequency * db2lin(self.nf)`），因此**只需覆盖 `_calc_nf()` 即可改变 ASE/OSNR**。
- 另有一处调用 `_calc_nf(avg=True)`：[network.py#L41-L69](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/network.py#L41-L69) 的 `edfa_nf()`（自动设计用），
  它会把 `pin_db/nch/slot_width` 置成 0/88/50e9 后取标量 NF → 覆盖时 `avg` 分支不要依赖 `pin_db`。
- 多波段光放：[elements.py#L1857-L1880](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1857-L1880)
  `Multiband_amplifier.__init__` 里**写死了 `Edfa(**amp_dict, **kwargs)`**（第 1871 行），
  没有注入自定义子类的位置，所以需要 bplab 自己复写建带逻辑。

### bplab 现有约定

- 设备库加载器 [trx.py#L140-L159](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/bplab/trx.py#L140-L159)
  `load_equipment_with_module_power()`，已建立"**先摘掉 YANG 不认的字段 → `yang_to_legacy()` 校验转换 → 再回填**"的手法
  （`tx_power`、顶层 `Passive` 段）。
- `tests/bplab/test_trx.py::test_load_equipment_with_module_power_rejects_other_invalid_fields`
  证明 **Edfa/Transceiver 条目里的未登记字段会被 YANG 校验直接拒绝**，所以 `nf_vs_gain` 必须先摘掉再校验。
- 上游 `Amp.__init__` 走 `_JsonThing.update_attr()`（[json_io.py#L63-L75](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/tools/json_io.py#L63-L75)），
  只遍历 `EdfaParams.default_values` 的键，**未知键静默丢弃**；`EdfaParams.__init__` 同样忽略额外键。
  ⇒ 摘掉的表要在 `_equipment_from_json()` 之后**显式挂到 `Amp` 对象上**，否则会丢。
- 元素类可自由继承上游：[passives.py#L48-L66](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/bplab/passives.py#L48-L66) 已继承 `_Node` 的先例。
- 测试放在 `tests/bplab/`，设备库路径用 `Path(__file__).parents[2] / 'scripts' / 'ganlin' / 'test_gnpy' / 'eqpt_config.json'`
  （见 [test_passives.py#L23](file:///e:/ganlin/Codes/github/oopt-gnpy/tests/bplab/test_passives.py#L23)）。

### 相关常量

- `C96_SGA_22dBm`：`gain_min=20`、`gain_flatmax=25`、`nf_min=5.5`、`nf_max=7`、`p_max=22`
- `L96_SGA_21dBm`：同上，仅 `f_min/f_max` 与 `p_max=21` 不同
- `test2.py` 当前 OA1/OA2 的工作增益约 21 dB（C96 侧 23.09，见脚本输出）

## Proposed Changes

### 1. `scripts/ganlin/test_gnpy/eqpt_config.json`（本地设备库，可直接改）

在 `C96_SGA_22dBm` 与 `L96_SGA_21dBm` 两个条目内、`nf_max` 之后各新增一段占位表
（`nf_min` / `nf_max` 保留：它们仍是上游 `variable_gain` 模型的必填项，也是表的端点来源）：

```json
      "nf_min": 5.5,
      "nf_max": 7,
      "nf_vs_gain": [
        {"gain": 20.0, "nf": 7.00},
        {"gain": 21.0, "nf": 6.70},
        {"gain": 22.0, "nf": 6.40},
        {"gain": 23.0, "nf": 6.10},
        {"gain": 24.0, "nf": 5.80},
        {"gain": 25.0, "nf": 5.50}
      ],
```

- 取值规则：覆盖 `gain_min=20` ~ `gain_flatmax=25`，1 dB 步长共 6 点；
  NF 从 `nf_max=7.0`（最小增益）线性降到 `nf_min=5.5`（最大增益），即与现有 `nf_min/nf_max` 语义自洽。
- 两个型号数值相同（两者 `nf_min/nf_max` 本就相同），属**占位值**，由用户后续按实测替换。
- 多波段条目 `C96L96_SGA_multiband` 不加该字段（它本身不产生 NF）。

### 2. 新建 `gnpy/bplab/edfa.py`

文件头按规则四件套（coding / SPDX / 说明 / Copyright + AUTHORS）。内容：

```python
from logging import getLogger
from typing import Dict, List, Optional

from numpy import array, interp

from gnpy.core.elements import Edfa, Multiband_amplifier, _Node
from gnpy.core.exceptions import EquipmentConfigError, ParametersError
from gnpy.core.parameters import MultiBandParams, find_band_name, FrequencyBand

NF_CURVE_KEY = 'nf_vs_gain'
```

**(a) `parse_nf_curve(entries) -> Optional[Tuple[array, array]]`（纯函数，带 doctest）**

- `None`/空 → 返回 `None`（表示不使用查表，走上游模型）；
- 输入 `[{'gain': .., 'nf': ..}, ...]`，按 gain **升序排序**，返回 `(gains, nfs)` 两个 `numpy` 数组；
- 校验：至少 2 点、gain 严格递增，否则 `raise EquipmentConfigError(...)`；
- doctest 用 `list(gains), list(nfs)` 形式断言（避免 numpy repr 随 legacy printoptions 变化）。

**(b) `GainNfEdfa(Edfa)`**

```python
class GainNfEdfa(Edfa):
    def __init__(self, *args, params=None, **kwargs):
        # EdfaParams 只认自己的键，扩展表必须在 super() 之前取出来
        curve = parse_nf_curve((params or {}).get(NF_CURVE_KEY))
        super().__init__(*args, params=params, **kwargs)
        self.nf_curve = curve

    def _calc_nf(self, avg=False):
        if self.nf_curve is None:
            return super()._calc_nf(avg)
        gains, nfs = self.nf_curve
        self.att_in = 0
        nf_avg = interp(self.effective_gain, gains, nfs)   # 超范围 → 端点钳位
        return nf_avg if avg else self.interpol_nf_ripple + nf_avg
```

- 查表用 `self.effective_gain`（已含 `p_max - pin_db` 饱和钳位，见 `interpol_params` 第 1568 行）；
- `interpol_nf_ripple` 在 `interpol_params` 中先于 `_calc_nf()` 赋值，本案例 `nf_ripple=0`，故 NF 在波段内**平坦**；
- `avg=True` 分支不依赖 `pin_db`，兼容 `network.edfa_nf()` 的自动设计调用。

**(c) `GainNfMultibandAmplifier(Multiband_amplifier)`**

复写 `__init__`，逻辑与 [elements.py#L1857-L1880](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1857-L1880) 逐行一致，
唯一区别是把 `Edfa(**amp_dict, **kwargs)` 换成 `GainNfEdfa(**amp_dict, **kwargs)`；
`super().__init__` 处改为直接调 `_Node.__init__(self, params=MultiBandParams(**params), **kwargs)`
（绕开上游 `Multiband_amplifier.__init__`；`passives.py` 已有直接引用 `_Node` 的先例）。
`__call__` / `to_json` 等全部继承，不再复写。
注释里写明"与上游同构，仅子光放类型不同；上游若改动需同步"。

**(d) 装载辅助函数**

```python
def extract_nf_curves(json_data: Dict) -> Dict[str, List[Dict]]:
    """从原始设备库 json 的 Edfa 条目里 pop 出 nf_vs_gain；兼容 other_name 别名"""

def attach_nf_curves(equipment: Dict, curves: Dict) -> None:
    """把曲线挂到 equipment['Edfa'][型号] 上（Amp 会丢弃未知键，必须显式挂）"""
```

- `extract_nf_curves`：对每个 `Edfa` 条目 `pop(NF_CURVE_KEY, None)`，非空则按
  `other_name + [type_variety]` 逐个登记（对齐 `_equipment_from_json` 的别名处理）；
- `attach_nf_curves`：`setattr(equipment['Edfa'][variety], NF_CURVE_KEY, curve)`，
  型号不存在时跳过（不抛错）。

### 3. `gnpy/bplab/trx.py`（改 3 处，约 4 行）

`load_equipment_with_module_power()` 内：

```python
    raw = load_json(Path(filename))
    extracted = _extract_module_power(raw)
    nf_curves = extract_nf_curves(raw)          # 新增：YANG 校验前摘掉 nf_vs_gain
    raw.pop('Passive', None)
    json_data = yang_to_legacy(raw)
    _inject_module_power(json_data, extracted)
    equipment = _equipment_from_json(json_data, extra_configs)
    attach_nf_curves(equipment, nf_curves)      # 新增：校验后回填到 Amp 对象
    return equipment
```

- 顶部 `from gnpy.bplab.edfa import attach_nf_curves, extract_nf_curves`（无循环导入：`edfa.py` 不 import `trx.py`）；
- 函数名保持 `load_equipment_with_module_power` 不变（`tests/bplab/test_trx.py` 与 `test2.py` 都在用，
  且规则要求不改 `tests/` 既有文件），docstring 补一句"同时支持 Edfa 条目的 bplab 扩展字段 `nf_vs_gain`"。

### 4. `scripts/ganlin/test_gnpy/test2.py`

- 导入与 `gnpy.core.elements` 里 `Multiband_amplifier` 的导入调整：
  `from gnpy.bplab.edfa import GainNfMultibandAmplifier`（不再直接用 `Multiband_amplifier`）；
- `build_multiband_amp()` 用 `GainNfMultibandAmplifier` 替换 `Multiband_amplifier`，其余参数不变
  （各波段子光放的 `params` 仍是 `equipment['Edfa'][型号].__dict__`，其中已带上 `nf_vs_gain`）；
- 「光放工作点」那段打印补上实际 NF（便于确认查表生效）：

```python
        print(f'  {uid} {band_name:>6} {sub_amp.params.type_variety}：增益 {sub_amp.effective_gain:.2f} dB，'
              f'{sub_amp.pin_db:.2f} dBm → {sub_amp.pout_db:.2f} dBm，'
              f'NF {sub_amp.nf.mean():.2f} dB')
```

### 5. 新建 `tests/bplab/test_edfa.py`

文件头同规则四件套；`EQPT_CONFIG` 用 `Path(__file__).parents[2] / 'scripts' / 'ganlin' / 'test_gnpy' / 'eqpt_config.json'`。
用例（沿用 `test_passives.py` 的 `create_arbitrary_spectral_information` 建 C96+L96 频谱的做法）：

1. `parse_nf_curve`：乱序输入被排序；`None`/`[]` → `None`；单点、重复 gain、缺键 → `EquipmentConfigError`。
2. 加载器：`load_equipment_with_module_power(EQPT_CONFIG)` 后
   `equipment['Edfa']['C96_SGA_22dBm'].nf_vs_gain` 存在且与 json 一致；`L96_SGA_21dBm` 同理。
3. `GainNfEdfa` 查表：取 `dict(equipment['Edfa']['C96_SGA_22dBm'].__dict__)` 建元素，
   `operational={'gain_target': g, 'tilt_target': 0}`；对表内点（如 20 / 22.5 / 25）与表外点（如 18 / 27）
   验证 NF 等于 `numpy.interp` 且被端点钳位；用 1 波/多波 SI 调 `interpol_params()` 后断言 `amp.nf` 在波段内**平坦**（全等）。
4. 回归：不带 `nf_vs_gain` 的型号（如 `C96L96_SGA_multiband` 之外的普通 `Edfa` 参数）走上游模型，
   `_calc_nf(True)` 与 `Edfa` 的结果一致。
5. 多波段：用 `GainNfMultibandAmplifier` 按 `test2.py` 的方式搭 C96+L96 双波段光放，
   调 `interpol_params()`（或整链 `__call__`）后各子光放的 `nf.mean()` 等于查表值。

## Assumptions & Decisions

1. **字段名/格式**：`nf_vs_gain: [{"gain": .., "nf": ..}]`（用户已确认），YANG 不识别 → 走 bplab 摘除/回填。
2. **查表方式**：对 `effective_gain` 线性插值（`numpy.interp`），**超范围端点钳位**（用户已确认），
   不沿用上游"增益 < gain_min 时加 pad 让 NF 恶化"的语义，`att_in` 置 0。
3. **NF 平坦**：按用户要求"人为光放的 NF 是平坦的"——NF 只随增益变、不随频率变；
   实现上仍叠加 `interpol_nf_ripple`，当前配置 `nf_ripple=0` 故为平坦。
4. **占位表**：20~25 dB 共 6 点，NF 由 `nf_max` 线性降到 `nf_min`；两个型号数值相同，等用户填真实值。
5. **兼容性**：`nf_min`/`nf_max` 保留在 json 中（`variable_gain` 的 YANG 必填项）；
   不带 `nf_vs_gain` 的放大器行为与上游完全一致（opt-in）。
6. **不做单波段（非多波段）版本的额外封装**：`GainNfEdfa` 本身即可单用，脚本仍走多波段类。
7. **不改上游文件、不改 `tests/` 既有文件、不加 `tests/bplab/__init__.py`**。
8. 预期影响：以 OA1（工作增益 ~23.1 dB）为例，占位表给出 NF≈6.07 dB，与上游两段模型的结果不同，
   因此 `test2.py` 的 SNR/OSNR 数值会发生可解释的变化（这是功能生效的证据，不是回归）。

## Verification

```powershell
# 1) 新增单测 + bplab doctest（含 utils/passives/trx 既有用例）
.\.venv\Scripts\python.exe -m pytest tests/bplab gnpy/bplab -q

# 2) 文件头合规
.\.venv\Scripts\python.exe -m pytest tests/test_opensource_compliancy.py::test_file_headers -q

# 3) 端到端：脚本应正常跑完，"光放工作点"出现 NF 列，且 OSNR 随 NF 变化
.\.venv\Scripts\python.exe scripts/ganlin/test_gnpy/test2.py

# 4) 静态检查（注：当前 .venv 未装 flake8，需先按 setup.cfg 的 tests extras 安装：
#    .\.venv\Scripts\python.exe -m pip install "flake8>=5.0.4,<6"）
.\.venv\Scripts\python.exe -m flake8 gnpy/bplab tests/bplab scripts/ganlin/test_gnpy/test2.py
```

回归判据：`pytest tests/bplab gnpy/bplab -q` 全绿；`test2.py` 正常结束、63 波未丢、OA1/OA2 输出仍为额定功率；
仅 NF/SNR/OSNR 数值按新表变化。

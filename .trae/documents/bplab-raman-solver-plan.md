# bplab 新增 SRS 求解器（RamanParams / RamanSolver / ODE 适配 gnpy）实施计划

## 1. 概要

把 `scripts/ganlin/raman_file.py`（OMC 风格的 `solve_ivp` 拉曼 ODE）移植进 `gnpy/bplab/`，按 gnpy 的数据结构重写：

- 新增 `gnpy/bplab/raman.py`：`RamanParams`（G652D 拉曼参数）、`RamanSolver`（接口与上游一致）、适配后的 `raman_ode_foreward` / `_get_gR_dat`、以及 `SrsFiber`（`Fiber` 子类，`propagate()` 内部改用新求解器）。
- 拉曼参数写进设备库 `scripts/ganlin/test_gnpy/eqpt_config.json` 的光纤条目（新增 `raman_gain` 段），由 `gnpy/bplab/trx.py` 的加载器在校验前摘除、校验后回填（与既有 `nf_vs_gain` / `Passive` / `tx_power` 同一手法）。
- 新增 `tests/bplab/test_raman.py`。

数值设定（G652D）：`gR_peak = 0.38e-3` 1/(W·m)，谱型取上游 `DEFAULT_RAMAN_COEFFICIENT['gamma_raman']`（90 点、0~42 THz、峰值在 12.75 THz），按自身最大值归一化后再乘 `gR_peak`，得到 `raman_ode_foreward` 的 `gR`；`raman_fref = c / 1480 nm`（202.5624 THz）；`raman_ns = 2.313`。

不改任何上游文件（遵守 `.claude/rules/bplab-add-on.md`）。

## 2. 现状分析（已核实）

### 2.1 上游调用点与数据契约

- `Fiber.propagate()` 写死调用上游求解器，并用返回对象的 `loss_profile[:, -1]` 作为光纤衰减；NLI 计算也吃同一个对象：
  [elements.py#L1231-L1261](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1231-L1261)：
  `stimulated_raman_scattering = RamanSolver.calculate_stimulated_raman_scattering(spectral_info, self)`
  → `NliSolver.compute_nli(spectral_info, srs, self)` → `spectral_info.apply_attenuation_lin(srs.loss_profile[:, -1])`。
- 返回对象必须是 [StimulatedRamanScattering](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/science_utils.py#L58-L71)：字段 `power_profile` [N, nz] (W)、`loss_profile` [N, nz]（线性）、`frequency`、`z`，并自带 `rho = sqrt(loss_profile)`。
- 上游 `calculate_attenuation_profile()`（flag=False 时用）只做纯衰减，见 [science_utils.py#L86-L102](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/science_utils.py#L86-L102)。
- `solve_ivp` 的输入口径：`fiber.alpha(f)` 为 **Np/m**（[elements.py#L1131-L1138](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1131-L1138)），`spectral_info.pch` 为 **W**，频率升序。
- 默认 NLI 方法 `gn_model_analytic` 不使用 `srs`（[science_utils.py#L355-L361](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/science_utils.py#L355-L361)），但 GGN 系列会用 `srs`，因此新对象仍用上游 `StimulatedRamanScattering` 构造，保持完全兼容。

### 2.2 上游 RamanParams

[RamanParams](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/parameters.py#L43-L58)：`flag=False, method='perturbative', order=2, result_spatial_resolution=10e3, solver_spatial_resolution=10e3`；本案例 test2.py 现用 `flag=True, solver=50 m, result=10 km`。

### 2.3 设备库校验与扩展段

- `Fiber` 条目 YANG 只允许 `common-fiber` 分组（type_variety / dispersion / gamma / pmd_coef / effective_area / loss_coef_ripple / ref_frequency|ref_wavelength），见 [gnpy-eqpt-config@2026-05-28.yang#L365-L457](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/yang/gnpy-eqpt-config@2026-05-28.yang#L365-L457) 与 [list Fiber](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/yang/gnpy-eqpt-config@2026-05-28.yang#L677-L682)。
- `yang_to_legacy()` 会做 libyang 校验（[convert_legacy_yang.py#L200-L204](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/tools/convert_legacy_yang.py#L200-L204)），**不认识的键会导致加载失败** → 新段必须在校验前摘除（既有 `nf_vs_gain`、`Passive`、`tx_power` 均如此：[trx.py#L163-L177](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/bplab/trx.py#L163-L177)）。
- 设备库里 `equipment['Fiber'][型号]` 是 `json_io.Fiber` 实例（[json_io.py#L213-L233](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/tools/json_io.py#L213-L233)），脚本以 `dict(entry.__dict__)` 取出后传给 `Fiber(params=...)`；`FiberParams.__init__` 只读已知键、**忽略多余键**（[parameters.py#L269-L307](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/parameters.py#L269-L307)），因此把拉曼段挂在条目对象上、由 `SrsFiber` 在构造时弹出是可行的。

### 2.4 参考实现（待移植）

[raman_file.py](file:///e:/ganlin/Codes/github/oopt-gnpy/scripts/ganlin/raman_file.py)：

- `raman_ode_foreward(alpha, fch, gR, pch_in, zdat, raman_fref, raman_ns)`：入口把频率翻成高频在前，`_get_gR_dat` 组装耦合矩阵，`solve_ivp` 解 `dP/dz = (gR_dat·P)⊙P − alpha⊙P`（`rtol=1e-6, atol=1e-16`），返回归一化 `p`、`z`、`real_power_dBm`。
- `_get_gR_dat(fch, gR, raman_fref, raman_ns)`：`gR` 为 `(2, N)` 表（第 0 行频差、第 1 行归一化增益），插值后乘 `(fch/raman_fref)**raman_ns`；`gR_dat[findex, k]` 为泵浦 k 对低频信号的增益，`gR_dat[k, findex]` 为泵浦自身损耗（含 `fch[k]/fch` 因子）。矩阵构造只用显式频率比较，**与数组顺序无关**。
- 未支持：拉曼泵浦（文件注释"没有背向拉曼泵浦"）、集总损耗（`lumped_losses`）。

## 3. 变更方案

### 3.1 新增 `gnpy/bplab/raman.py`

标准文件头（coding / SPDX / 说明 / Copyright / AUTHORS）。模块 docstring 说明"bplab 自建的 SRS 求解器（纯新增）、数值来源、单位口径、限制"。

**(a) 常量与纯函数**

```python
RAMAN_GAIN_KEY = 'raman_gain'          # 设备库 Fiber 条目里的 bplab 扩展段（YANG 不接受，加载时摘除/回填）
DEFAULT_G652D_RAMAN_GAIN = {'gR_peak': 0.38e-3, 'reference_wavelength': 1480e-9, 'ns': 2.313}

def raman_gain_table(gR_peak, reference_wavelength=1480e-9) -> ndarray:
    """返回 (2, N) 表：第 0 行频差 [Hz]、第 1 行归一化拉曼增益 [1/(W·m)]
    谱型取上游 DEFAULT_RAMAN_COEFFICIENT['gamma_raman']，按自身最大值归一化后乘 gR_peak"""
```

- 带 doctest：`raman_gain_table(0.38e-3).shape == (2, 90)`、`max(row1) == 0.38e-3`、峰值频差 12.75 THz、`row0[0] == 0`。
- 用 `from numpy import asarray, interp` 等扁平导入；数值来自 `gnpy.core.parameters.DEFAULT_RAMAN_COEFFICIENT`（复用上游表，不复制数值）。

**(b) `RamanParams`**

```python
class RamanParams:
    def __init__(self, gR_peak=0.38e-3, reference_wavelength=1480e-9, ns=2.313,
                 flag=True, solver_spatial_resolution=50.0, result_spatial_resolution=10e3):
```

- 字段：`gR_peak` [1/(W·m)]、`reference_wavelength` [m]、`reference_frequency = c/λ` [Hz]、`ns`、`flag`、`solver_spatial_resolution` [m]、`result_spatial_resolution` [m]。
- 校验：`gR_peak > 0`、`1e-7 < reference_wavelength < 1e-5`、`ns > 0`、两个分辨率 `> 0`，非法值抛 `EquipmentConfigError`（与 `gnpy/bplab/fibers.py` 一致）。
- `to_json()`（与上游 `Parameters` 风格一致，便于打印/调试）。
- 默认值 = G652D 那套值：既让 `RamanParams()` 可独立构造，又允许设备库条目覆盖（设备库为权威来源）。

**(c) 适配后的 ODE（保留参考实现的函数名）**

```python
def raman_ode_foreward(alpha, fch, gR, pch_in, zdat, raman_fref, raman_ns):
    """dP/dz = (gR_dat·P)⊙P − alpha⊙P，gnpy 口径

    :param alpha: 线性衰减系数 [Np/m]，与 fch 同序（gnpy 为升序）
    :param fch: 频率 [Hz]（gnpy 升序，不再内部翻转）
    :param gR: (2, N) 归一化拉曼增益表
    :param pch_in: 入纤每波道功率 [W]
    :param zdat: z 采样点 [m]
    :return: (power_profile [N, nz] W, zdat)
    """
```

与参考实现的差异（适配 gnpy）：不做高频在前的翻转（gnpy 一律升序，矩阵构造本就与顺序无关）；返回**绝对功率 [W]** 而不是归一化值/dBm（上游 `StimulatedRamanScattering` 要 W；gnpy 另有 `watt2dbm`，不需要 `mw2dbm`）；`res.y.T` 直接对齐 `spectral_info.frequency`；保留 `rtol=1e-6, atol=1e-16` 与 `1e-100` 下限保护。

```python
def _get_gR_dat(fch, gR, raman_fref, raman_ns):
    """与参考实现同公式，逐条注释其含义（泵浦对信号增益 / 泵浦自身损耗）"""
```

**(d) `RamanSolver`**

```python
class RamanSolver:
    @staticmethod
    def calculate_attenuation_profile(spectral_info, fiber) -> StimulatedRamanScattering   # 纯衰减，等价上游
    @staticmethod
    def calculate_stimulated_raman_scattering(spectral_info, fiber) -> StimulatedRamanScattering
```

`calculate_stimulated_raman_scattering` 流程（对齐上游 [science_utils.py#L104-L185](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/science_utils.py#L104-L185)）：

1. `fiber.raman_params`（由 `SrsFiber` 注入）`flag=False` → 直接走 `calculate_attenuation_profile`；
2. `alpha = fiber.alpha(spectral_info.frequency)`；`gR = raman_gain_table(...)`；
3. z 网格：`z = append(arange(0, L, solver_spatial_resolution), L)`；`z_final = append(arange(0, L, result_spatial_resolution), L)`；
4. 解 ODE → `power_profile` [N, nz]；
5. `loss_profile = power_profile / outer(spectral_info.pch, ones(nz))`；
6. `power_profile/loss_profile` 用 `interp1d(z, ..., axis=1)(z_final)` 重采样（与上游一致，消费者拿到同样的 z 采样）；
7. `return StimulatedRamanScattering(power_profile, loss_profile, spectral_info.frequency, z_final)`。

限制（明确报错，不静默算错）：`fiber.lumped_losses` 非空 → 抛 `EquipmentConfigError`（ODE 无法表达阶跃损耗）；不支持拉曼泵浦（`RamanParams`/`SrsFiber` 不提供 `raman_pumps`）。

**(e) `SrsFiber(Fiber)`**

```python
class SrsFiber(Fiber):
    """用 bplab 的 SRS 求解器（solve_ivp ODE）替代上游 RamanSolver 的光纤"""

    def __init__(self, *args, params=None, raman_params=None, **kwargs):
        params = dict(params or {})
        settings = params.pop(RAMAN_GAIN_KEY, None)          # 设备库附带的拉曼段
        super().__init__(*args, params=params, **kwargs)
        self.raman_params = raman_params or RamanParams(**(settings or {}))
```

- 覆写 `propagate()`：逐行照搬上游 [Fiber.propagate](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/core/elements.py#L1231-L1261) 的流程（`con_in + att_in` → SRS → NLI → CD/PMD → latency → `apply_attenuation_lin(loss_profile[:, -1])` → `con_out` → 记录 `pch_out_dbm`/`propagated_labels`），只把 `RamanSolver.calculate_stimulated_raman_scattering` 换成 `gnpy.bplab.raman.RamanSolver`，NLI 仍用上游 `NliSolver`。`__call__` 直接继承（行为与上游一致）。
- 设备库没有拉曼段时用 `RamanParams()` 默认值（G652D），并在 docstring 注明。

**(f) 设备库扩展段的摘除 / 回填**

```python
def extract_raman_gain(json_data: Dict) -> Dict:   # 摘除 Fiber/RamanFiber 条目里的 raman_gain，key 为 type_variety
def attach_raman_gain(equipment: Dict, extracted: Dict) -> None:   # 校验后挂回 equipment['Fiber'][型号].raman_gain
```

写法照搬 [edfa.py 的 extract_nf_curves/attach_nf_curves](file:///e:/ganlin/Codes/github/oopt-gnpy/gnpy/bplab/edfa.py#L104-L176)（含"必须在 YANG 校验前摘除"的注释）。

### 3.2 修改 `gnpy/bplab/trx.py`（`load_equipment_with_module_power`）

- 导入 `extract_raman_gain` / `attach_raman_gain`；
- 在 `nf_curves = extract_nf_curves(raw)` 附近加 `raman_gain = extract_raman_gain(raw)`（摘除），`_equipment_from_json` 之后加 `attach_raman_gain(equipment, raman_gain)`；
- 函数 docstring 补一段说明（与既有 `nf_vs_gain` 段落并列），`git diff` 只新增这几行。

### 3.3 修改 `scripts/ganlin/test_gnpy/eqpt_config.json`

在 `Fiber` 的 `G.652.D` 条目里新增（其余条目不动，保持"只给用到的型号配拉曼参数"）：

```json
{
  "type_variety": "G.652.D",
  "dispersion": 1.67e-05,
  "effective_area": 83e-12,
  "pmd_coef": 1.265e-15,
  "raman_gain": {
    "gR_peak": 0.38e-3,
    "reference_wavelength": 1480e-9,
    "ns": 2.313
  }
}
```

### 3.4 新增 `tests/bplab/test_raman.py`

标准文件头；用例：

1. `test_raman_gain_table_shape_and_peak`：形状 `(2, 90)`、第 1 行最大值 == `gR_peak`、首点频差 0、峰值频差 12.75 THz、与上游 `DEFAULT_RAMAN_COEFFICIENT['gamma_raman']` 形状一致（归一化前比值恒定）。
2. `test_get_gR_dat_signature_and_signs`：对角为 0；`gR_dat[i, j] > 0` 当且仅当 `f_i < f_j`；泵浦行（`gR_dat[k, findex]`）为负；校验 `gR_dat[k, i] ≈ -gR_dat[i, k] * fch[k]/fch[i]`。
3. `test_single_channel_matches_pure_attenuation`：单波道时 `loss_profile[:, -1] == exp(-alpha*L)`（SRS 转移为 0）。
4. `test_two_channel_power_transfer`：高频波道相对纯衰减变小、低频波道变大；两波道总功率变化远小于单波道变化量（能量转移量级自洽）。
5. `test_solver_returns_stimulated_raman_scattering`：返回类型、`power_profile.shape == (N, nz)`、`loss_profile[:, 0] == 1`、`z[-1] == length`、`loss_profile = power_profile / pch_in`。
6. `test_flag_false_equals_upstream_attenuation_profile`：`flag=False` 时与上游 `RamanSolver.calculate_attenuation_profile` 逐点相同。
7. `test_srs_fiber_flag_false_matches_upstream_fiber`：同一 `spectral_info` 分别过 `SrsFiber(flag=False)` 与上游 `Fiber`，输出 `pch_dbm` 一致。
8. `test_loader_extracts_and_attaches_raman_gain`：`load_equipment_with_module_power(EQPT_CONFIG)` 成功（证明 YANG 校验前摘除生效），且 `equipment['Fiber']['G.652.D'].raman_gain` 等于 JSON 里的值。
9. `test_srs_fiber_reads_raman_params_from_equipment`：用 `dict(entry.__dict__)` 构造 `SrsFiber`，其 `raman_params.gR_peak/reference_wavelength/ns` 与设备库一致，`reference_frequency ≈ c/1480nm`。
10. `test_lumped_losses_raise`：带集总损耗的光纤调用求解器 → `EquipmentConfigError`。
11. `RamanParams` 非法值（`gR_peak<=0`、`ns<=0`、分辨率<=0）→ `EquipmentConfigError`。
12. doctest：`raman_gain_table`、`RamanParams.__init__` 的 `>>>`（`pytest.ini` 已全局 `--doctest-modules`）。

## 4. 假设与决策

| 项 | 决策 | 说明 |
| --- | --- | --- |
| 集成方式 | 类 + `SrsFiber` 子类（用户已确认） | 上游 `Fiber.propagate` 不动，子类覆写以真正换求解器；`propagate` 需照搬上游约 25 行流程 |
| 参数位置 | 写入设备库 `eqpt_config.json` 的 `G.652.D` 条目（用户已确认） | 键名 `raman_gain`（避免与 YANG 已有的 Edfa 布尔 `raman`、RamanFiber 的 `raman_efficiency` 混淆） |
| `RamanParams` 默认值 | 用同一套 G652D 值作构造默认 | 使 `RamanParams()` 可独立构造；设备库缺段时回退到默认并在 docstring 注明；设备库为权威来源 |
| 求解设置 | `flag` / 两个空间分辨率留在 `RamanParams`（默认 `True` / 50 m / 10 km） | 属仿真参数而非器件数据，与上游 `RamanParams` 语义一致 |
| 谱型来源 | 运行时读上游 `DEFAULT_RAMAN_COEFFICIENT['gamma_raman']` 归一化 | 不在设备库重复 90 点表，只给峰值 |
| 单位 | `alpha` Np/m、`pch` W、`gR` 1/(W·m)、z m | 全部沿用 gnpy 口径；不使用 `mw2dbm`（gnpy 用 `watt2dbm`） |
| 泵浦 / 集总损耗 | 不支持，明确抛错 | 参考实现本身不含泵浦；ODE 无法表达阶跃损耗 |
| 频率顺序 | 不做内部翻转，保持 gnpy 升序 | 降低转置风险；矩阵构造与顺序无关，结果与参考实现一致 |
| `test2.py` | 本次不改 | 需要用时把光纤换成 `SrsFiber`（两行：`params=dict(entry.__dict__)` + 类名）即可，留待你确认 |

## 5. 验证步骤

```powershell
# 1) 新增测试 + 全量 bplab 测试 + doctest
.\.venv\Scripts\python.exe -m pytest tests/bplab gnpy/bplab -q

# 2) 文件头合规
.\.venv\Scripts\python.exe -m pytest tests/test_opensource_compliancy.py::test_file_headers -q

# 3) 静态检查
.\.venv\Scripts\python.exe -m flake8 --select=F,E9 gnpy/bplab tests/bplab

# 4) 设备库仍可加载（校验前摘除生效）
.\.venv\Scripts\python.exe -c "from gnpy.bplab.trx import load_equipment_with_module_power as L; \
e=L(r'scripts/ganlin/test_gnpy/eqpt_config.json'); print(e['Fiber']['G.652.D'].raman_gain)"
```

5) 端到端对照（不新增脚本，用 `python -c`）：同一 `spectral_info`（C96+L96 64 波）与同一 `G.652.D` 光纤，分别用上游 `RamanSolver` 与 `gnpy/bplab/raman.RamanSolver` 求解，打印波道级输出功率差、波段 SRS 总功率变化（与 test2.py 现在的 `Σ有SRS/Σ纯衰减` 口径对照）与求解耗时；预期两者量级一致（同为 1~2 dB 的 C→L 转移），差异来自增益谱口径（gR_peak/fref/ns vs 上游 A_eff 模型）。

6) `git status` 确认只动了 `gnpy/bplab/trx.py`、`scripts/ganlin/test_gnpy/eqpt_config.json`，新增 `gnpy/bplab/raman.py`、`tests/bplab/test_raman.py`，未触碰上游 `gnpy/` 既有文件。

## 6. 不在本次范围

- 不支持拉曼泵浦（`RamanFiber` 的 co/counter-propagating pumps）与集总损耗；
- 不改 `test2.py`（如需切到新求解器，另起一步）；
- 不把 `scripts/ganlin/raman_file.py` 删改（保留为参考实现；`scripts/` 不参与 bplab 导入）。

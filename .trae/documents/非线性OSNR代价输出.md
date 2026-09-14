# test2.py 输出非线性 OSNR 代价

## Summary

在 [test2.py](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/test2.py) 里新增一节，按用户提供的
[egn.py#L1-L14](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/egn.py#L1-L14) 最后一句的公式，
**分波段**输出非线性（NLI）OSNR 代价：

```
代价[dB] = -10 * log10(1 - OSNR_b2b * Σ_span (12.5e9 / baud) * NSR_nli)
```

其中 `OSNR_b2b` 是模块 B2B required OSNR、`NSR_nli` 是 GN 模型算出的非线性噪信比。
只加分波段汇总，不加逐波长表、不加图。

## Current State Analysis

1. **公式语义**（已推导并与实测对齐）：
   - `nsr2OnsrConv = 12.5 / baudrate * 1e9` 是把"信号带宽内的噪信比"折算到 0.1 nm（12.5 GHz）参考带宽，
     因此 `nsr2OnsrConv * NSR_nli = 1 / OSNR_NLI(0.1nm)`（`OSNR_0.1nm = baud/(12.5e9*NSR)` 的倒数）。
   - `osnr_b2b_lin * onsr_nli_acum = OSNR_b2b / OSNR_NLI`，于是
     `-10log10(1-x) = 10log10(OSNR_req_with_NLI / OSNR_b2b)`：即"为了让 ASE+NLI 合计仍满足 B2B 要求、
     ASE 侧必须额外付出的 OSNR"，就是常见的 NLI 代价定义。物理上 `OSNR_NLI → ∞` 时代价 → 0；
     `OSNR_NLI → OSNR_b2b` 时代价发散。
   - egn.py 里 `eta_nli_tot[span_idx]` 的差分表示"第 span_idx 跨新增的 NLI 噪信比"，累加得到整条链路的
     NLI 噪信比。test2.py 只有 **1 跨**，累加退化为该跨的值。
2. **test2.py 里 NLI 已经算好了**：[SrsFiber.propagate](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/bplab/raman.py#L279-L311)
   调用 `NliSolver.compute_nli`（默认方法 `gn_model_analytic`，见
   [parameters.py#L68-L70](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/parameters.py#L68-L70)）并 `add_nli`。
   由于 `add_ase` 会同步缩放 `_signal_ratio` / `_nli_ratio`，**`si.nli/si.signal` 沿后续器件保持不变**，
   因此在光路走完后直接取用即可。gnpy 没有 Carena EGN，只有 GN / GGN（EGN 仅在
   [biblio.bib](file:///c:/g00572545/codes/github/oopt-gnpy/docs/biblio.bib#L1220) 里被引用）。
3. **模块 B2B required OSNR**：本地设备库
   [eqpt_config.json#L237-L248](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/eqpt_config.json#L237-L248)
   的 `Huawei` / `800G ZR+ 150GHz TFLN BOL` 模式 `OSNR` = **24.5 dB**，脚本里已有 `trx_mode`，直接取 `trx_mode['OSNR']`。
4. **实测基线**（在 .venv 里把 `savefig/show` 打成空操作跑通现有脚本，只读，不落盘）：

   | 波段 | 波数 | 最差 | 平均 | 最好 |
   |---|---|---|---|---|
   | L96 | 32 | 0.106 | 0.082 | 0.048 dB |
   | C96 | 32 | 0.190 | 0.140 | 0.084 dB |

   对应的 NLI 噪信比 −33.90 ~ −27.98 dB、OSNR_NLI@0.1nm 38.20 ~ 44.11 dB、`x` = 0.0109 ~ 0.0427。
5. **依赖问题（与本改动无关，但影响验证）**：脚本第 34-35 行 `import rich`，而 .venv 与系统 Python
   都没装 `rich`（`ModuleNotFoundError: No module named 'rich'`），所以脚本当前在本机跑不起来。
   验证时用内存 stub 顶掉 rich，不改动环境、不装包。

## Proposed Changes

只改一个文件：[test2.py](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/test2.py)。不新增文件、不改 `gnpy/**`。

### 1. import 增补（第 33 行）

```python
from numpy import concatenate, errstate, isfinite, log10
```

（`log10` 为新增，用于与 egn.py 的 `-10 * np.log10(...)` 写法保持一致。）

### 2. 文件头 docstring 增补一条（第 24-26 行附近）

在"逐波长输出 SNR_NLI / SNR_ASE"之后加一条：

```
- 分波段输出非线性 OSNR 代价（公式参考 egn.py：-10log10(1 - OSNR_b2b/OSNR_NLI)，
  OSNR_b2b 取模块 B2B required OSNR，OSNR_NLI 由 GN 模型的非线性噪信比折算）
```

### 3. 新增一节（插在第 381 行"各器件单波功率"表之后、"功率谱"之前）

放在这里的原因：该处 `fmt()`（[第 327-329 行](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/test2.py#L327-L329)）
已在作用域内，可直接复用它对非有限值的 `'-'` 处理；同时它紧跟三张汇总表，读起来是一段链路级小结。

```python
# ---------------------------------------------------------------- 非线性 OSNR 代价
# 公式参考 egn.py：
#   代价 = -10*log10(1 - OSNR_b2b * Σ_span (12.5e9 / baud) * NSR_nli)
# 其中 (12.5e9/baud)*NSR_nli 就是 1/OSNR_NLI（NSR 折算到 0.1 nm 参考带宽），
# 因此括号内为 OSNR_b2b/OSNR_NLI；物理含义是"为了让 ASE+NLI 合计仍满足 B2B 要求，
# ASE 侧需额外付出的 OSNR"。本链路只有 1 跨，故对跨的累加退化为该跨的值。
osnr_b2b_db = trx_mode['OSNR']                      # 模块 B2B required OSNR
nsr_nli = si.nli / si.signal                        # GN 模型算出的非线性噪信比（信号带宽内）
nsr2osnr_conv = 12.5e9 / baud_rate                  # 折算到 0.1 nm / 12.5 GHz
x = db2lin(osnr_b2b_db) * nsr2osnr_conv * nsr_nli   # = OSNR_b2b / OSNR_NLI
nli_penalty_db = -10 * log10(1 - x)                 # 非线性 OSNR 代价 [dB]

print(f'\n非线性 OSNR 代价（GN model analytic；模块 B2B required OSNR {osnr_b2b_db:.2f} dB；'
      f'NLI 噪信比折算到 0.1 nm 后取 1/OSNR_NLI）：')
for band, band_slice in slices.items():
    band_penalty = nli_penalty_db[band_slice]
    print(f'  {band}（{band_penalty.size} 波）：最差 {fmt(band_penalty.max())} dB，'
          f'平均 {fmt(band_penalty.mean())} dB，最好 {fmt(band_penalty.min())} dB')
```

- `db2lin` 已在现有 import 中（第 47 行），无需增补。
- 复用现有的 `slices`（第 238 行）做 C96/L96 划分，与其它分波段输出口径一致。
- 代价越大越差，所以"最差"取 `max`。

## Assumptions & Decisions

1. **用 gnpy 默认 GN analytic 的 NLI 结果**（用户选定），即直接取 `si.nli/si.signal`，不额外调用 `NliSolver`；
   输出里显式标注模型名，避免被误读成 EGN。
2. **只加分波段汇总**（用户选定）：不加逐波长表、不加图。
3. **B2B OSNR 取当前模块的 `OSNR` 字段 = 24.5 dB**（`800G ZR+ 150GHz TFLN BOL`，BOL 规格）。
   设备库里另有 EOL 模式（25.0 dB），本案例不使用。
4. **单跨不写跨循环**：egn.py 的 `Σ_span` 结构在本链路是单跨，代码里用注释说明该退化，
   不引入多余的循环/列表。若以后改成多跨，再按跨累加 NLI 噪信比。
5. **`x ≥ 1` 的极端情况**（NLI 单独就超过 B2B 要求，代价发散）不做特殊处理：由 `fmt()` 显示为 `'-'`。
   当前链路 `x` 仅 0.011~0.043，不会触发。
6. **不因为 `rich` 缺失去改脚本或装包**：这是用户脚本既有的第三方依赖，只在最终汇报里提示。

## Verification

1. 用内存 stub 顶掉 `rich`、并把 `savefig`/`show` 打成空操作运行脚本（只读、不落盘）：
   `.\.venv\Scripts\python.exe -c "<stub + runpy.run_path>"`，确认 exit=0、无异常。
2. 核对新增打印内容：恰好两条分波段行 + 一行表头，数值与实测基线一致
   （L96 0.106/0.082/0.048，C96 0.190/0.140/0.084）。
3. 独立复核公式：在同一进程里另算一遍 `-10*log10(1 - db2lin(24.5) * (12.5e9/baud) * si.nli/si.signal)`
   与脚本打印值逐波段比对，应完全一致；并断言 `x < 1`、`penalty > 0`。
4. 确认原有输出未受影响：三张 rich 表、SRS 转移量、OSNR 各位置打印、三张图的 `savefig` 调用次数不变。
5. `flake8 --select=F,E9 scripts/ganlin/test_gnpy/test2.py` 无告警。
6. `git status --short` 只应有 `M scripts/ganlin/test_gnpy/test2.py`（验证用的 stub 不落盘，
   不产生新文件；`temp/*.png` 已被 .gitignore 忽略）。

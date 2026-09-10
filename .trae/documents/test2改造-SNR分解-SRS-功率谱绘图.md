# test2.py：噪声分解 SNR、SRS 转移量、光纤输入/输出功率谱

## Summary

只改一个文件：[scripts/ganlin/test\_gnpy/test2.py](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/test2.py)（用户自建脚本，不属于 gnpy 原始代码）。三处改动：

1. **噪声分解 SNR**：逐波长输出 `SNR_NLI`、`SNR_ASE`、`SNR_TRX`，以及三者**噪声功率求和**后得到的 `SNR_total`，替换掉现在的 `GSNR@0.1nm / GSNR@bw / OSNR ASE / OSNR NLI` 四列。
2. **SRS**：显式打开 `SimParams.raman_params.flag`，让 `Fiber.propagate` 走 SRS 求解（而不是纯衰减），并打印**首波长与末波长**的 SRS 转移量。
3. **绘图**：新增光纤输入/输出功率谱图，弹窗显示并保存 `spectrum_c96_l96.png`。

## Current State Analysis

### 频谱与传播（现有脚本）

* 频点来自 [band\_center\_frequencies](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/bplab/utils.py#L59-L76)：L96 96 波 186.3\~191.05 THz、C96 96 波 191.3\~196.05 THz，合计 192 波。

* 传播链：`tx(EMITTER)` → `fiber.propagate(si)` → `edfa(si)` → `rx(RECEIVER)`（[test2.py#L77-L80](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/test2.py#L77-L80)）。

### 噪声分解的数据来源（`gnpy/core/info.py`）

`SpectralInformation` 每波道已有可直接用的量（[info.py#L141-L212](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/info.py#L141-L212)）：

* `signal / ase / nli`：信号、ASE、NLI 功率（W），三者之和等于 `pch`。

* `snr_lin_db` = signal/ase（**按 baud\_rate 带宽**），`snr_nli_db` = signal/nli，`gsnr_db` = signal/(ase+nli)。

* `opt_*` 系列是把噪声折算到 0.1 nm（12.5 GHz）后的值：`opt_x = x_db + 10log10(baud_rate/12.5e9)`。

* `tx_osnr`：收发机模块噪声指标（本案例 SI 里为 40 dB，来自 `eqpt_config.json` 的 `SI.tx_osnr`）。

* 现有脚本打印的 `rx.snr_01nm` 是 **ASE+NLI**（`raw_snr_01nm`），因为只有 `request.propagate` 会调 [update\_snr](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/elements.py#L330-L346) 把 `tx_osnr` 作为额外噪声并入；本脚本没调，所以不含模块噪声。

* `snr_sum(snr, bw, snr_added, bw_added=12.5e9)`（[utils.py#L219-L224](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/utils.py#L219-L224)）说明 gnpy 内部把 `tx_osnr` 当作 **0.1 nm 参考**，并先换算到通道带宽再相加。

### SRS 的开关与数据（`gnpy/core/science_utils.py`）

* `RamanParams.__init__(flag=False, method='perturbative', order=2, result_spatial_resolution=10e3, solver_spatial_resolution=10e3)`（[parameters.py#L43-L58](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/parameters.py#L43-L58)）→ **flag 默认 False，即默认不算 SRS**。

* `SimParams.set_params({...})`（[parameters.py#L94-L108](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/parameters.py#L94-L108)）是唯一入口；`science_utils.py` 里的 `sim_params = SimParams()`（[science\_utils.py#L32](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/science_utils.py#L32)）是共享单例，`set_params` 改的是类级 `_shared_dict`，所以脚本里调用后全局生效。

* [RamanSolver.calculate\_stimulated\_raman\_scattering(si, fiber)](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/science_utils.py#L104-L185)：`flag=True` 时返回 `StimulatedRamanScattering(power_profile, loss_profile, frequency, z)`，`power_profile` 形状 `(nch, nz)`，**行序与输入** **`si.frequency`** **一致**；`flag=False` 时退化为 [calculate\_attenuation\_profile](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/science_utils.py#L86-L102)（纯衰减，`z` 只有两端点）。

* `Fiber.propagate` 内部会自己再算一次 SRS 并用其 `loss_profile[:, -1]` 做衰减（[elements.py#L1230-L1260](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/core/elements.py#L1230-L1260)），但结果不落属性 → 想拿 SRS 剖面必须在脚本里单独调一次求解器。

### 绘图

仓库 [gnpy/tools/plots.py](file:///c:/g00572545/codes/github/oopt-gnpy/gnpy/tools/plots.py#L1-L74) 的风格是用 `matplotlib.pyplot` 的**扁平导入**（`from matplotlib.pyplot import show, axis, figure, ...`）并以 `show()` 结束。

### 已实测的基线（用 .venv 跑通，用于校验实现）

* 192 波、80 km、SRS 开启时整链耗时 **3.4 s**（SRS 求解本身 0.1\~1.6 s），无性能顾虑。

* SRS 转移（有 SRS 相对纯衰减）：首波长 186.3 THz **+1.991 dB**，末波长 196.05 THz **−2.453 dB**，倾斜 4.443 dB（高频向低频转移，符合物理）。

* 光纤输出首/末波道：无 SRS 均 −16.00 dBm；有 SRS 为 −14.01 / −18.45 dBm。

* 第 1 波道（0.1 nm 参考）：SNR\_ASE 36.448、SNR\_NLI 37.065、SNR\_TRX 40.000 dB；手算的合计 SNR（模块+ASE+NLI 噪声功率求和）与 gnpy `update_snr` 后的 `rx.snr_01nm` **完全一致**（32.814 dB），说明分解口径自洽。

## Proposed Changes

### 文件：scripts/ganlin/test\_gnpy/test2.py（唯一改动文件）

#### 1) 文档字符串与导入

```python
"""在 Python 代码中手工构造单跨 C+L 链路，按 C96 / L96 满波加载，输出所有波长的性能。

对照 test.py：
    transmission_main_example() 用默认参数读取 gnpy/example-data/edfa_example_network.json，
    由 cli_examples 内部完成 autodesign 和传播（Site_A -> Span1(80km) -> Edfa1 -> Site_B）。

test2.py 不依赖拓扑 JSON，直接用 gnpy 的 API 创建 Transceiver / Fiber / Edfa：
- 频点取自 gnpy.bplab.utils.band_center_frequencies（C96 / L96 各 96 波，193.1 THz 锚点的 50 GHz 栅格）
- 光纤传播开启 SRS（SimParams.raman_params.flag），并打印首/末波长的 SRS 转移量
- 逐波长输出 SNR_NLI / SNR_ASE / SNR_TRX，以及三者噪声功率求和得到的 SNR_total
- 绘制光纤输入/输出功率谱
"""

from pathlib import Path

from matplotlib.pyplot import figure, grid, legend, plot, savefig, show, title, xlabel, ylabel
from numpy import concatenate

from gnpy.bplab.utils import band_center_frequencies
from gnpy.core.elements import Edfa, Fiber, Transceiver
from gnpy.core.info import create_arbitrary_spectral_information
from gnpy.core.parameters import SimParams, TransceiverRole
from gnpy.core.science_utils import RamanSolver
from gnpy.core.utils import db2lin, dbm2watt, lin2db, watt2dbm
from gnpy.tools.json_io import DEFAULT_EQPT_CONFIG, load_equipment
```

#### 2) 打开 SRS（放在设备库之后、传播之前）

```python
# 开启 SRS：RamanParams.flag 默认 False，不打开则 Fiber.propagate 只做纯衰减
SimParams.set_params({'raman_params': {'flag': True,
                                       'solver_spatial_resolution': 50,
                                       'result_spatial_resolution': 10e3}})
```

> 只传 `raman_params`，`nli_params` 走 `NLIParams()` 默认值（`__init__.py` 里 `set_params` 用 `.get({'nli_params'}, {})` 兜底）。**不使用** `gnpy/example-data/sim_params.json`，因为它的 `nli_params.computed_channels=[1,18,37,56,75]` 是按 76 波频谱挑的，用到 192 波会大量插值。

#### 3) 传播段：额外取一次 SRS 解

把现有传播段（[test2.py#L77-L80](file:///c:/g00572545/codes/github/oopt-gnpy/scripts/ganlin/test_gnpy/test2.py#L77-L80)）改为：

```python
# ---------------------------------------------------------------- 传播
si = tx(si, role=TransceiverRole.EMITTER)
# Fiber.propagate 内部会自己算一遍 SRS 但不落属性，这里单独求解一次，
# 用于打印 SRS 转移量并画光纤输入/输出功率谱
srs = RamanSolver.calculate_stimulated_raman_scattering(si, fiber)   # SRS 开启
srs_attenuation_only = RamanSolver.calculate_attenuation_profile(si, fiber)  # 仅纯衰减，作为参照
fiber.propagate(si)  # 手工建链时 ref_pch_in_dbm 为 None，故直接调用 propagate
si = edfa(si)
si = rx(si, role=TransceiverRole.RECEIVER)

assert si.number_of_channels == len(frequency), 'EDFA 频带把部分波长滤掉了，请检查 f_min / f_max'
```

#### 4) 噪声分解 SNR（替换原来的表格列）

```python
# ---------------------------------------------------------------- 每波长的噪声受限 SNR
# 口径：SNR_ASE 按含滚降的实际信号带宽 BW*(1+Rolloff) 计算；SNR_NLI / SNR_TRX / SNR_total 为绝对量
bw_eff = si.baud_rate * (1 + si.roll_off)   # 实际信号带宽 [Hz]，本案例 32 GHz x 1.15 = 36.8 GHz
p_ase = si.ase * (1 + si.roll_off)          # ASE 噪声功率折算到 bw_eff
p_trx = si.signal / db2lin(si.tx_osnr)      # 收发机模块噪声功率
snr_ase = lin2db(si.signal / p_ase)
snr_nli = lin2db(si.signal / si.nli)
snr_trx = si.tx_osnr                        # 模块噪声受限 SNR，SI 里的 tx_osnr（本案例 40 dB）
snr_total = lin2db(si.signal / (p_ase + si.nli + p_trx))   # 三种噪声功率求和后取信噪比

print(f'\n噪声口径：SNR_ASE 用 BW*(1+Rolloff) = {bw_eff[0] * 1e-9:.2f} GHz；'
      f'SNR_NLI / SNR_TRX / SNR_total 为绝对量；'
      f'SNR_total = signal / (ASE + NLI + 模块噪声)')

print(f'\n{"Band":>4} {"Ch":>3} {"频率(THz)":>10} {"功率(dBm)":>9} {"SNR_NLI":>9} '
      f'{"SNR_ASE":>9} {"SNR_TRX":>9} {"SNR_total":>10}')
for band_name, sl in slices.items():
    for i in range(si.number_of_channels)[sl]:
        print(f'{band_name:>4} {i - sl.start + 1:>3} {si.frequency[i] * 1e-12:>10.4f} '
              f'{si.pch_dbm[i]:>9.2f} {snr_nli[i]:>9.2f} {snr_ase[i]:>9.2f} '
              f'{snr_trx[i]:>9.2f} {snr_total[i]:>10.2f}')
```

#### 5) SRS 转移量（只打首/末波长）

```python
# ---------------------------------------------------------------- SRS 转移量
transfer_db = lin2db(srs.power_profile[:, -1]) - lin2db(srs_attenuation_only.power_profile[:, -1])
print('\nSRS 转移量（有 SRS 相对纯衰减的输出功率变化）：')
print(f'  首波长 {si.frequency[0] * 1e-12:.4f} THz：{transfer_db[0]:+.3f} dB')
print(f'  末波长 {si.frequency[-1] * 1e-12:.4f} THz：{transfer_db[-1]:+.3f} dB')
```

#### 6) 分波段汇总改为汇总 `SNR_total`

```python
print('')
for band_name, sl in slices.items():
    t = snr_total[sl]
    print(f'{band_name}（{t.size} 波）SNR_total：平均 {t.mean():.2f} dB，'
          f'最差 {t.min():.2f} dB，最好 {t.max():.2f} dB')
print(f'C+L 合计（{snr_total.size} 波）SNR_total：平均 {snr_total.mean():.2f} dB，'
      f'最差 {snr_total.min():.2f} dB，最好 {snr_total.max():.2f} dB')
```

#### 7) 绘图（追加在脚本末尾）

```python
# ---------------------------------------------------------------- 光纤输入/输出功率谱
figure(figsize=(11, 5))
plot(srs.frequency * 1e-12, watt2dbm(srs.power_profile[:, 0]), label='光纤输入')
plot(srs.frequency * 1e-12, watt2dbm(srs.power_profile[:, -1]), label='光纤输出（含 SRS）')
xlabel('频率 (THz)')
ylabel('每波道功率 (dBm)')
title(f'C96 + L96 满波 {len(frequency)} 波，80 km SSMF 光纤输入/输出功率谱')
grid(True)
legend()
savefig(Path(__file__).parent / 'spectrum_c96_l96.png', dpi=150)
show()
```

> 先 `savefig` 再 `show`，保证无 GUI 环境（`MPLBACKEND=Agg`）也能落到文件。

**不变的部分**：链路参数（80 km / 0.2 dB/km / 0.5+0.5 dB）、C96+L96 频点来源、EDFA 增益 17 dB 与频带扩展、`si` 的构造、开头的链路与频谱打印、`slices` 定义、`assert` 校验。

## Assumptions & Decisions

1. **SNR 口径（按用户答复）**：

   * `SNR_ASE` 按 `BW*(1+Rolloff)` 计算 —— 脚本用 `si.ase * (1 + si.roll_off)` 把 gnpy 的 ASE（按 `baud_rate` 积分）折算到实际信号带宽；本案例 = 32 GHz × 1.15 = **36.8 GHz**。

   * `SNR_NLI = signal / nli`、`SNR_TRX = si.tx_osnr`（本案例 40 dB）、`SNR_total` 由三部分噪声功率求和，均为**绝对量**，不再做 0.1 nm / 带宽归一。

   * 因此 `SNR_total` **不等于** gnpy CLI 打印的 `GSNR (0.1 nm)`，也不等于 `update_snr` 之后的 `rx.snr_01nm`（后者把 `tx_osnr` 当 0.1 nm 参考再换算）。脚本会把这套口径打印在表头，避免误读。

   * `SNR_TRX` 对本案例是常数 40 dB，逐行输出仅为完整性。
2. **SRS**：只用 `SimParams.set_params` 打开 `raman_params.flag` 并给出空间分辨率（`solver_spatial_resolution=50`、`result_spatial_resolution=10e3`，与 `sim_params.json` 的 raman 段一致），**不**引入该文件的 `nli_params`（其 `computed_channels` 是针对 76 波的）。转移量只打印首/末波长，不做逐波道列。
3. **绘图**：只画光纤输入（`power_profile[:, 0]`）与光纤输出（`power_profile[:, -1]`，含 SRS）两条曲线，按用户要求不加"无 SRS 参照"曲线；输出文件固定为 `scripts/ganlin/test_gnpy/spectrum_c96_l96.png`。
4. 不改动 `gnpy/` 下任何文件，也不新增 `tests/bplab` 用例（本任务是脚本改造；仓库根已存在的 `.claude/rules/bplab-add-on.md` 规则也要求不动上游代码）。
5. 求解 SRS 会使整链耗时从 \~0.3 s 增至 \~3.4 s（已实测），可接受。

## Verification

1. **无 GUI 跑通**（验证脚本无异常且图能落盘）：

   ```powershell
   $env:MPLBACKEND='Agg'; .\.venv\Scripts\python.exe scripts\ganlin\test_gnpy\test2.py > $env:TEMP\t2.txt 2>$null; echo "exit=$LASTEXITCODE"
   ```

   期望 `exit=0`。
2. **核对关键数值**（与 Phase 1 实测基线一致）：

   * 表头出现 `SNR_ASE 用 BW*(1+Rolloff) = 36.80 GHz`；

   * 首波长 186.3000 THz 的 `SNR_NLI ≈ 32.98`、`SNR_ASE ≈ 31.76`、`SNR_TRX = 40.00`、`SNR_total ≈ 28.96`；

   * 末波长 196.0500 THz 的 `SNR_total` 应低于首波长（C 带 SRS 损耗更大、NLI 更强）；

   * `SRS 转移量：首波长 186.3000 THz +1.991 dB / 末波长 196.0500 THz -2.453 dB`。
3. **物理不变量**：对每个波道都应有 `SNR_total < min(SNR_NLI, SNR_ASE, SNR_TRX)`（独立噪声叠加只会降低 SNR）。用一次性命令校验，不写进脚本：

   ```powershell
   .\.venv\Scripts\python.exe -c "..."   # 复现脚本的 si/传播，断言 (snr_total < minimum(snr_nli, snr_ase, snr_trx)).all()
   ```
4. **SRS 生效证据**：同一命令下比较 `srs.power_profile[:, -1]` 与 `srs_attenuation_only.power_profile[:, -1]`，首/末波道应为 −14.01 / −18.45 dBm（有 SRS）对 −16.00 / −16.00 dBm（纯衰减）。
5. **图文件**：确认 `scripts/ganlin/test_gnpy/spectrum_c96_l96.png` 已生成且尺寸非 0。
6. **无回归**：`.\.venv\Scripts\python.exe -m pytest tests/bplab gnpy/bplab -q` 仍全绿（本次未触碰这些文件）；`git status` 确认除 `test2.py` 与该 png 外无其它改动。


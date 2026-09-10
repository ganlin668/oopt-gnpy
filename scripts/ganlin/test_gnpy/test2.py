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

from matplotlib import rcParams
from matplotlib.pyplot import figure, grid, legend, plot, savefig, show, title, xlabel, ylabel
from numpy import concatenate

from gnpy.bplab.utils import band_center_frequencies
from gnpy.core.elements import Edfa, Fiber, Transceiver
from gnpy.core.equipment import trx_mode_params
from gnpy.core.info import create_arbitrary_spectral_information
from gnpy.core.parameters import SimParams, TransceiverRole
from gnpy.core.science_utils import RamanSolver
from gnpy.core.utils import db2lin, dbm2watt, freq2wavelength, lin2db, watt2dbm
from gnpy.tools.json_io import DEFAULT_EQPT_CONFIG, load_equipment

# matplotlib 默认字体不含中文字形，必须指定中文字体，否则图中文字显示为方框
rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DengXian']
# 负号用 ASCII 减号，避免部分中文字体缺少 U+2212 字形
rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------------- 设备库 / 模块参数
equipment = load_equipment(Path(DEFAULT_EQPT_CONFIG))
si_default = equipment['SI']['default']

# SNR_TRX 是模块 B2B 无误码所需的 OSNR（设备库 Transceiver 模式的 OSNR 字段），
# 与 TX_OSNR（发射机自身 OSNR）是两个不同的量
trx_mode = trx_mode_params(equipment, 'vendorA_trx-type1', 'mode 1')
snr_trx_db = trx_mode['OSNR']     # 模块 B2B 所需 OSNR（0.1 nm 参考），本案例 11 dB
tx_osnr_db = trx_mode['tx_osnr']  # 发射机自身 OSNR，本案例 40 dB

# 开启 SRS：RamanParams.flag 默认 False，不打开则 Fiber.propagate 只做纯衰减
SimParams.set_params({'raman_params': {'flag': True,
                                       'solver_spatial_resolution': 50,
                                       'result_spatial_resolution': 10e3}})

# ---------------------------------------------------------------- 链路参数
length_km = 80.0
loss_coef = 0.2             # dB/km
con_in, con_out = 0.5, 0.5  # 连接器损耗 dB
span_loss_db = loss_coef * length_km + con_in + con_out  # 17 dB

spacing = si_default.spacing  # 50 GHz

# ---------------------------------------------------------------- C96 / L96 满波频谱
# L96 占用 186.275 ~ 191.075 THz → 96 个中心频点 186.3 ~ 191.05 THz
l96_centers = band_center_frequencies('L96', spacing)
# C96 占用 191.275 ~ 196.075 THz → 96 个中心频点 191.3 ~ 196.05 THz
c96_centers = band_center_frequencies('C96', spacing)
frequency = concatenate([l96_centers, c96_centers])  # 升序，共 192 波

si = create_arbitrary_spectral_information(
    frequency=frequency, slot_width=spacing, pch=dbm2watt(si_default.tx_power_dbm),
    baud_rate=si_default.baud_rate, roll_off=si_default.roll_off, tx_osnr=tx_osnr_db,
    tx_power=dbm2watt(si_default.tx_power_dbm), required_osnr_db_01nm=snr_trx_db)

# 色散 / 有效面积 / PMD 系数取自设备库，只覆盖与具体链路相关的参数
fiber_params = dict(equipment['Fiber']['SSMF'].__dict__)
fiber_params.update(length=length_km, length_units='km', loss_coef=loss_coef,
                    att_in=0, con_in=con_in, con_out=con_out, pmd_coef=3.0e-15)

# 示例设备库里没有 C+L 合放型号，这里沿用 std_low_gain 的模型，只把工作频带扩到 C+L 占用范围
edfa_params = dict(equipment['Edfa']['std_low_gain'].__dict__)
edfa_params.update(f_min=frequency[0] - spacing / 2, f_max=frequency[-1] + spacing / 2)

trx_params = {'system_margin': si_default.sys_margins}

# ---------------------------------------------------------------- 构造元素
tx = Transceiver(uid='Site_A', params=trx_params,
                 metadata={'location': {'city': 'Site A', 'region': '', 'latitude': 0, 'longitude': 0}})
fiber = Fiber(uid='Span1', type_variety='SSMF', params=fiber_params,
              metadata={'location': {'city': '', 'region': '', 'latitude': 1, 'longitude': 0}})
edfa = Edfa(uid='Edfa1', type_variety='std_low_gain', params=edfa_params,
            operational={'gain_target': span_loss_db, 'tilt_target': 0, 'out_voa': 0, 'in_voa': 0},
            metadata={'location': {'city': '', 'region': '', 'latitude': 2, 'longitude': 0}})
rx = Transceiver(uid='Site_B', params=trx_params,
                 metadata={'location': {'city': 'Site B', 'region': '', 'latitude': 3, 'longitude': 0}})

print(f'跨度损耗 {span_loss_db:.1f} dB，EDFA 增益 {edfa.operational.gain_target:.1f} dB，'
      f'放大频带 {edfa.params.f_min * 1e-12:.3f} ~ {edfa.params.f_max * 1e-12:.3f} THz')
print(f'频谱：{si.number_of_channels} 波（L96 {len(l96_centers)} + C96 {len(c96_centers)}），'
      f'{si.frequency[0] * 1e-12:.3f} ~ {si.frequency[-1] * 1e-12:.3f} THz，'
      f'{si_default.baud_rate * 1e-9:.0f} GBaud / {spacing * 1e-9:.0f} GHz，'
      f'输入 {si_default.tx_power_dbm:.1f} dBm/波（共 {si.ptot_dbm:.2f} dBm）')

# ---------------------------------------------------------------- 传播
si = tx(si, role=TransceiverRole.EMITTER)
# Fiber.propagate 内部会自己算一遍 SRS 但不落属性，这里单独求解一次，
# 用于打印 SRS 转移量并画光纤输入/输出功率谱
srs = RamanSolver.calculate_stimulated_raman_scattering(si, fiber)           # 含 SRS
srs_attenuation_only = RamanSolver.calculate_attenuation_profile(si, fiber)  # 仅纯衰减，作参照
fiber.propagate(si)  # 手工建链时 ref_pch_in_dbm 为 None，故直接调用 propagate
si = edfa(si)
si = rx(si, role=TransceiverRole.RECEIVER)

assert si.number_of_channels == len(frequency), 'EDFA 频带把部分波长滤掉了，请检查 f_min / f_max'

slices = {'L96': slice(0, len(l96_centers)), 'C96': slice(len(l96_centers), None)}

# ---------------------------------------------------------------- 每波长的噪声受限 SNR
# SNR_ASE 按含滚降的实际信号带宽 BW*(1+Rolloff) 计算；SNR_NLI / SNR_TRX / SNR_total 为绝对量
bw_eff = si.baud_rate * (1 + si.roll_off)   # 实际信号带宽 [Hz]，本案例 32 GHz x 1.15 = 36.8 GHz
p_ase = si.ase * (1 + si.roll_off)          # ASE 噪声功率折算到 bw_eff
p_trx = si.signal / db2lin(snr_trx_db)      # 模块噪声功率（由 SNR_TRX 等效）
snr_ase = lin2db(si.signal / p_ase)
snr_nli = lin2db(si.signal / si.nli)
snr_total = lin2db(si.signal / (p_ase + si.nli + p_trx))  # 三种噪声功率求和后取信噪比

print(f'\n噪声口径：SNR_ASE 用 BW*(1+Rolloff) = {bw_eff[0] * 1e-9:.2f} GHz；'
      f'SNR_NLI / SNR_TRX / SNR_total 为绝对量')
print(f'  SNR_TRX（模块 B2B 无误码所需 OSNR，设备库 Transceiver {trx_mode["format"]}）= {snr_trx_db:.2f} dB')
print(f'  TX_OSNR（发射机自身 OSNR，仅参考，不参与 SNR_total 求和）= {tx_osnr_db:.2f} dB')
print('  SNR_total = signal / (ASE + NLI + 模块噪声)')

print(f'\n{"Band":>4} {"Ch":>3} {"频率(THz)":>10} {"功率(dBm)":>9} {"SNR_NLI":>9} '
      f'{"SNR_ASE":>9} {"SNR_TRX":>9} {"SNR_total":>10}')
for band_name, sl in slices.items():
    for i in range(si.number_of_channels)[sl]:
        print(f'{band_name:>4} {i - sl.start + 1:>3} {si.frequency[i] * 1e-12:>10.4f} '
              f'{si.pch_dbm[i]:>9.2f} {snr_nli[i]:>9.2f} {snr_ase[i]:>9.2f} '
              f'{snr_trx_db:>9.2f} {snr_total[i]:>10.2f}')

# ---------------------------------------------------------------- SRS 转移量
transfer_db = lin2db(srs.power_profile[:, -1]) - lin2db(srs_attenuation_only.power_profile[:, -1])
print('\nSRS 转移量（有 SRS 相对纯衰减的输出功率变化）：')
print(f'  首波长 {si.frequency[0] * 1e-12:.4f} THz：{transfer_db[0]:+.3f} dB')
print(f'  末波长 {si.frequency[-1] * 1e-12:.4f} THz：{transfer_db[-1]:+.3f} dB')

# ---------------------------------------------------------------- 分波段汇总
print('')
for band_name, sl in slices.items():
    t = snr_total[sl]
    print(f'{band_name}（{t.size} 波）SNR_total：平均 {t.mean():.2f} dB，'
          f'最差 {t.min():.2f} dB，最好 {t.max():.2f} dB')
print(f'C+L 合计（{snr_total.size} 波）SNR_total：平均 {snr_total.mean():.2f} dB，'
      f'最差 {snr_total.min():.2f} dB，最好 {snr_total.max():.2f} dB')

# ---------------------------------------------------------------- 光纤输入/输出功率谱
# 横坐标用波长（nm）：lambda = c / f
wavelength_nm = freq2wavelength(srs.frequency) * 1e9

figure(figsize=(11, 5))
plot(wavelength_nm, watt2dbm(srs.power_profile[:, 0]), label='光纤输入')
plot(wavelength_nm, watt2dbm(srs.power_profile[:, -1]), label='光纤输出（含 SRS）')
xlabel('波长 (nm)')
ylabel('每波道功率 (dBm)')
title(f'C96 + L96 满波 {len(frequency)} 波，80 km SSMF 光纤输入/输出功率谱')
grid(True)
legend()
savefig(Path(__file__).parent / 'spectrum_c96_l96.png', dpi=150)
show()

"""在 Python 代码中手工构造单跨 C+L 链路，按 C96 / L96 满波加载，输出所有波长的性能。

对照 test.py：
    transmission_main_example() 用默认参数读取 gnpy/example-data/edfa_example_network.json，
    由 cli_examples 内部完成 autodesign 和传播（Site_A -> Span1(80km) -> Edfa1 -> Site_B）。

test2.py 不依赖拓扑 JSON，直接用 gnpy 的 API 创建 Transceiver / Fiber / Edfa，
频点取自 gnpy.bplab.utils.band_center_frequencies（C96 / L96 各 96 波，193.1 THz 锚点的 50 GHz 栅格），
手工完成设计（EDFA 增益 = 跨度损耗），传播满波频谱并逐波长列出性能。
"""

from pathlib import Path

from numpy import concatenate

from gnpy.bplab.utils import band_center_frequencies
from gnpy.core.elements import Edfa, Fiber, Transceiver
from gnpy.core.info import create_arbitrary_spectral_information
from gnpy.core.parameters import TransceiverRole
from gnpy.core.utils import dbm2watt
from gnpy.tools.json_io import DEFAULT_EQPT_CONFIG, load_equipment

# ---------------------------------------------------------------- 设备库
equipment = load_equipment(Path(DEFAULT_EQPT_CONFIG))
si_default = equipment['SI']['default']

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
    baud_rate=si_default.baud_rate, roll_off=si_default.roll_off, tx_osnr=si_default.tx_osnr,
    tx_power=dbm2watt(si_default.tx_power_dbm))

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
fiber.propagate(si)  # 手工建链时 ref_pch_in_dbm 为 None，故直接调用 propagate
si = edfa(si)
si = rx(si, role=TransceiverRole.RECEIVER)

assert si.number_of_channels == len(frequency), 'EDFA 频带把部分波长滤掉了，请检查 f_min / f_max'

# ---------------------------------------------------------------- 所有波长的性能
slices = {'L96': slice(0, len(l96_centers)), 'C96': slice(len(l96_centers), None)}

print(f'\n{"Band":>4} {"Ch":>3} {"频率(THz)":>10} {"功率(dBm)":>9} {"GSNR@0.1nm":>11} '
      f'{"GSNR@bw":>8} {"OSNR ASE":>9} {"OSNR NLI":>9}')
for band_name, sl in slices.items():
    for i in range(si.number_of_channels)[sl]:
        print(f'{band_name:>4} {i - sl.start + 1:>3} {si.frequency[i] * 1e-12:>10.4f} '
              f'{si.pch_dbm[i]:>9.2f} {rx.snr_01nm[i]:>11.2f} {rx.snr[i]:>8.2f} '
              f'{rx.osnr_ase_01nm[i]:>9.2f} {rx.osnr_nli[i]:>9.2f}')

# ---------------------------------------------------------------- 分波段汇总
print('')
for band_name, sl in slices.items():
    gsnr = rx.snr_01nm[sl]
    print(f'{band_name}（{gsnr.size} 波）GSNR@0.1nm：平均 {gsnr.mean():.2f} dB，'
          f'最差 {gsnr.min():.2f} dB，最好 {gsnr.max():.2f} dB')
gsnr_all = rx.snr_01nm
print(f'C+L 合计（{si.number_of_channels} 波）GSNR@0.1nm：平均 {gsnr_all.mean():.2f} dB，'
      f'最差 {gsnr_all.min():.2f} dB，最好 {gsnr_all.max():.2f} dB')

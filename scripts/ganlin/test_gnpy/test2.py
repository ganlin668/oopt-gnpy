"""在 Python 代码中手工构造链路，并估计所有波长的 GSNR。

对照 test.py：
    transmission_main_example() 用默认参数读取 gnpy/example-data/edfa_example_network.json，
    由 cli_examples 内部完成 autodesign 和传播（Site_A -> Span1(80km) -> Edfa1 -> Site_B）。

test2.py 不依赖拓扑 JSON，直接用 gnpy 的 API 创建 Transceiver / Fiber / Edfa，
手工完成设计（EDFA 增益 = 跨度损耗），传播一个满载频谱，并逐信道列出 GSNR。
"""

from pathlib import Path

from gnpy.core.elements import Edfa, Fiber, Transceiver
from gnpy.core.info import create_input_spectral_information
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

# 色散 / 有效面积 / PMD 系数取自设备库，只覆盖与具体链路相关的参数
fiber_params = dict(equipment['Fiber']['SSMF'].__dict__)
fiber_params.update(length=length_km, length_units='km', loss_coef=loss_coef,
                    att_in=0, con_in=con_in, con_out=con_out, pmd_coef=3.0e-15)

trx_params = {'system_margin': si_default.sys_margins}

# ---------------------------------------------------------------- 构造元素
tx = Transceiver(uid='Site_A', params=trx_params,
                 metadata={'location': {'city': 'Site A', 'region': '', 'latitude': 0, 'longitude': 0}})
fiber = Fiber(uid='Span1', type_variety='SSMF', params=fiber_params,
              metadata={'location': {'city': '', 'region': '', 'latitude': 1, 'longitude': 0}})
edfa = Edfa(uid='Edfa1', type_variety='std_low_gain', params=dict(equipment['Edfa']['std_low_gain'].__dict__),
            operational={'gain_target': span_loss_db, 'tilt_target': 0, 'out_voa': 0, 'in_voa': 0},
            metadata={'location': {'city': '', 'region': '', 'latitude': 2, 'longitude': 0}})
rx = Transceiver(uid='Site_B', params=trx_params,
                 metadata={'location': {'city': 'Site B', 'region': '', 'latitude': 3, 'longitude': 0}})

# ---------------------------------------------------------------- 满载频谱
si = create_input_spectral_information(
    f_min=si_default.f_min, f_max=si_default.f_max, roll_off=si_default.roll_off,
    baud_rate=si_default.baud_rate, spacing=si_default.spacing,
    tx_osnr=si_default.tx_osnr, tx_power=dbm2watt(si_default.tx_power_dbm))

print(f'跨度损耗 {span_loss_db:.1f} dB，EDFA 增益 {edfa.operational.gain_target:.1f} dB')
print(f'频谱：{si.number_of_channels} 个信道，'
      f'{si.frequency[0] * 1e-12:.3f} ~ {si.frequency[-1] * 1e-12:.3f} THz，'
      f'{si_default.baud_rate * 1e-9:.0f} GBaud，'
      f'输入 {si_default.tx_power_dbm:.1f} dBm/信道（共 {si.ptot_dbm:.2f} dBm）')

# ---------------------------------------------------------------- 传播
si = tx(si, role=TransceiverRole.EMITTER)
fiber.propagate(si)  # 手工建链时 ref_pch_in_dbm 为 None，故直接调用 propagate
si = edfa(si)
si = rx(si, role=TransceiverRole.RECEIVER)

# ---------------------------------------------------------------- 所有波长的 GSNR
print(f'\n{"Ch":>3} {"频率(THz)":>10} {"功率(dBm)":>10} {"GSNR@0.1nm":>11} {"GSNR@bw":>9} '
      f'{"OSNR ASE":>9} {"OSNR NLI":>9}')
for i in range(si.number_of_channels):
    print(f'{si.channel_number[i]:>3} {si.frequency[i] * 1e-12:>10.4f} {si.pch_dbm[i]:>10.2f} '
          f'{rx.snr_01nm[i]:>11.2f} {rx.snr[i]:>9.2f} {rx.osnr_ase_01nm[i]:>9.2f} '
          f'{rx.osnr_nli[i]:>9.2f}')

print(f'\nGSNR@0.1nm：平均 {rx.snr_01nm.mean():.2f} dB，'
      f'最差 {rx.snr_01nm.min():.2f} dB，最好 {rx.snr_01nm.max():.2f} dB')

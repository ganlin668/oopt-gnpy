"""在 Python 代码中手工构造单跨 C+L 链路，按 C96 / L96 加载，输出所有波长的性能。

对照 test.py：
    transmission_main_example() 用默认参数读取 gnpy/example-data/edfa_example_network.json，
    由 cli_examples 内部完成 autodesign 和传播（Site_A -> Span1(80km) -> Edfa1 -> Site_B）。

test2.py 不依赖拓扑 JSON，直接用 gnpy 的 API 逐个搭建完整光路：

    Tx -> Mux -> VOA -> OA1 -> FIU -> Fiber -> FIU -> OA2 -> VOA -> Demux -> Rx

- 设备库来自同目录的 eqpt_config.json，用 gnpy.bplab.trx.load_equipment_with_module_power 加载，
  以支持模块的 tx_power（最大出光功率）
- Mux / Demux / VOA / FIU 只做衰减建模，插损按 C96 / L96 波段分别配置在设备库的 Passive 段；
  因 gnpy 的 Fused 只支持标量插损，这里用 gnpy.bplab.passives.BandAttenuator
- 两级 OA 都用 C96_SGA_22dBm / L96_SGA_21dBm（C/L 双波段），各自工作在额定总输出功率
  22 / 21 dBm：增益取 p_max - 该波段输入总功率
- 频点取自 gnpy.bplab.utils.band_center_frequencies（按模块 min_spacing = 150 GHz 取 C96 / L96 栅格）
- 光纤传播开启 SRS（SimParams.raman_params.flag），并打印首/末波长的 SRS 转移量
- 逐波长输出 SNR_NLI / SNR_ASE
- 绘制光纤输入/输出、OA2 输出、接收端的功率谱
"""

from pathlib import Path

from matplotlib import rcParams
from matplotlib.pyplot import figure, grid, legend, plot, savefig, show, title, xlabel, ylabel
from numpy import array, concatenate, errstate, interp, isfinite
from rich.console import Console
from rich.table import Table

from gnpy.bplab.edfa import GainNfMultibandAmplifier
from gnpy.bplab.passives import BandAttenuator, load_passive_library
from gnpy.bplab.trx import launch_power_dbm, load_equipment_with_module_power
from gnpy.bplab.utils import band_center_frequencies
from gnpy.core.elements import Fiber, Transceiver
from gnpy.core.equipment import trx_mode_params
from gnpy.core.info import create_arbitrary_spectral_information
from gnpy.core.parameters import SimParams, TransceiverRole
from gnpy.core.science_utils import RamanSolver
from gnpy.core.utils import db2lin, dbm2watt, freq2wavelength, lin2db, watt2dbm, wavelength2freq

# matplotlib 默认字体不含中文字形，必须指定中文字体，否则图中文字显示为方框
rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DengXian']
# 负号用 ASCII 减号，避免部分中文字体缺少 U+2212 字形
rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------------- 设备库 / 模块参数
# 上游的 gnpy/example-data/eqpt_config.json 不动，模块性能写在同目录的本地副本里
EQPT_CONFIG = Path(__file__).parent / 'eqpt_config.json'
equipment = load_equipment_with_module_power(EQPT_CONFIG)
si_default = equipment['SI']['default']

trx_mode = trx_mode_params(equipment, 'Huawei', '800G ZR+ 150GHz TFLN BOL')
tx_osnr_db = trx_mode['tx_osnr']  # 发射机自身 OSNR，本案例 41 dB
# 模块最大出光功率 tx_power，作为每波道发射功率的默认值并作为上限
launch_dbm = launch_power_dbm(trx_mode)

# 开启 SRS：RamanParams.flag 默认 False，不打开则 Fiber.propagate 只做纯衰减
SimParams.set_params({'raman_params': {'flag': True,
                                       'solver_spatial_resolution': 50,
                                       'result_spatial_resolution': 10e3}})

# ---------------------------------------------------------------- 链路参数
length_km = 80.0
loss_coef = 0.275             # 1550 nm 处的衰减系数 [dB/km]，作为波长相关模型的锚点
span_loss_db_target = 28.0
con_in = 0.0
con_out = span_loss_db_target-loss_coef * length_km - con_in  # 连接器损耗 dB
span_loss_db = loss_coef * length_km + con_in + con_out  # 28.0 dB

# gnpy 没有内置的解析式衰减曲线，但 FiberParams.loss_coef 支持逐波长表：
#   {'value': [... dB/km ...], 'frequency': [... Hz ...]}，由 Fiber.loss_coef_func 在波段内线性插值。
# 下表取自仓库自带示例（docs/json.rst 与 tests/data/fiber_slope/eqpt_config_fiber_freq.json 的
# loss_coef_ripple），是"相对 1550 nm 的起伏"；使用时整体平移，使 1550 nm 处正好为 loss_coef。
# 注意 FiberParams.ref_wavelength 默认就是 1550 nm（parameters.py），故单跨损耗仍按 0.275 dB/km 计。
ATTENUATION_REF_WAVELENGTH_NM = 1550.0
LOSS_COEF_RIPPLE = (          # (频率 [THz], 相对起伏 [dB/km])
    (185.4923413566740, +0.0086276629224582),
    (186.0525164113786, +0.0058291554597716),
    (188.0131291028446, +0.0002321405343985),
    (189.9912472647702, -0.0016335311073926),
    (191.0765864332604, -0.0020999490178403),
    (192.0043763676149, -0.0016335311073926),
    (194.0175054704595, +0.0006985584448462),
    (195.9956236323851, +0.0048963196388761),
    (198.0087527352298, +0.0100269166538015),
    (200.0218818380744, +0.0160903494896223),
    (201.9649890590809, +0.0230866181463388),
)


spacing = trx_mode['min_spacing']  # 150 GHz
baud_rate = trx_mode['baud_rate']  # 131.3 GBaud
roll_off = trx_mode['roll_off']    # 0.05

# ---------------------------------------------------------------- C96 / L96 频谱（按模块栅格）
# L96 占用 186.275 ~ 191.075 THz → 150 GHz 栅格下 32 个中心频点 186.35 ~ 191.0 THz
l96_centers = band_center_frequencies('L96', spacing)
# C96 占用 191.275 ~ 196.075 THz → 150 GHz 栅格下 31 个中心频点 191.45 ~ 195.95 THz
c96_centers = band_center_frequencies('C96', spacing)
frequency = concatenate([l96_centers, c96_centers])  # 升序，共 63 波

si = create_arbitrary_spectral_information(
    frequency=frequency, slot_width=spacing, pch=dbm2watt(launch_dbm),
    baud_rate=baud_rate, roll_off=roll_off, tx_osnr=tx_osnr_db,
    tx_power=dbm2watt(launch_dbm), required_osnr_db_01nm=trx_mode['OSNR'])

# 色散 / 有效面积 / PMD 系数取自设备库，只覆盖与具体链路相关的参数
# 衰减系数用逐波长表：把示例起伏整体平移，使 1550 nm 处正好等于 loss_coef
ripple_freq = array([f * 1e12 for f, _ in LOSS_COEF_RIPPLE])     # THz -> Hz
ripple_db = array([r for _, r in LOSS_COEF_RIPPLE])              # dB/km
ref_frequency = wavelength2freq(ATTENUATION_REF_WAVELENGTH_NM * 1e-9)
static_db_per_km = loss_coef - interp(ref_frequency, ripple_freq, ripple_db)

fiber_params = dict(equipment['Fiber']['SSMF'].__dict__)
fiber_params.update(length=length_km, length_units='km', att_in=0, con_in=con_in, con_out=con_out,
                    pmd_coef=3.0e-15,
                    loss_coef={'value': (static_db_per_km + ripple_db).tolist(),
                               'frequency': ripple_freq.tolist()})

# ---------------------------------------------------------------- 无源器件插损（按波段）
# Mux / VOA / FIU / Demux 只做衰减建模，插损按 C96 / L96 分别配置在设备库的 Passive 段
library = load_passive_library(EQPT_CONFIG)

# ---------------------------------------------------------------- 两级 C/L 双波段光放
# 设备库里定义了两款 SGA：C96_SGA_22dBm（输出总功率 22 dBm）与 L96_SGA_21dBm（21 dBm）。
# gnpy 用 p_max 表示放大器的最大总输出功率（饱和门限，见 Edfa.interpol_params），
# 因此把每个波段的增益设为 "p_max - 该波段输入总功率"，使放大器正好工作在额定输出总功率上。
AMP_BANDS = (('C96', len(c96_centers), equipment['Edfa']['C96_SGA_22dBm'].p_max),
             ('L96', len(l96_centers), equipment['Edfa']['L96_SGA_21dBm'].p_max))
# 增益余量：实际输入功率会被 SRS 等效应拉偏（本例可达 0.9 dB），留出余量后由 p_max 饱和门限
# 钳位，输出才严格等于额定总输出功率
AMP_GAIN_MARGIN_DB = 2.0

amp_gain = {}   # {(光放名, 波段): 增益 [dB]}
for band, nch, p_max_dbm in AMP_BANDS:
    # 第一级（发端 VOA 之后）输入总功率 = 每波道发射功率 + 10log10(波数) - Mux 插损 - VOA 插损
    oa1_pin_dbm = launch_dbm + lin2db(nch) - library['Mux'].loss[band] - library['VOA'].loss[band]
    # 第二级（光纤之后）输入总功率 = 第一级额定输出 - FIU1 - 跨度损耗 - FIU2
    oa2_pin_dbm = p_max_dbm - library['FIU'].loss[band] - span_loss_db - library['FIU'].loss[band]
    amp_gain[('OA1', band)] = p_max_dbm - oa1_pin_dbm + AMP_GAIN_MARGIN_DB
    amp_gain[('OA2', band)] = p_max_dbm - oa2_pin_dbm + AMP_GAIN_MARGIN_DB

trx_params = {'system_margin': si_default.sys_margins}


def build_multiband_amp(uid, latitude):
    """搭建 C/L 双波段光放；amplifiers 顺序决定合波后的信道顺序，L96 在前以保证频谱升序

    用 gnpy.bplab.edfa.GainNfMultibandAmplifier：子光放的 NF 按设备库里的增益-NF 表查得
    """
    return GainNfMultibandAmplifier(
        uid=uid, type_variety='C96L96_SGA_multiband',
        params=dict(equipment['Edfa']['C96L96_SGA_multiband'].__dict__),
        amplifiers=[
            {'type_variety': 'L96_SGA_21dBm',
             'params': dict(equipment['Edfa']['L96_SGA_21dBm'].__dict__),
             'operational': {'gain_target': amp_gain[(uid, 'L96')],
                             'tilt_target': 0, 'out_voa': 0, 'in_voa': 0}},
            {'type_variety': 'C96_SGA_22dBm',
             'params': dict(equipment['Edfa']['C96_SGA_22dBm'].__dict__),
             'operational': {'gain_target': amp_gain[(uid, 'C96')],
                             'tilt_target': 0, 'out_voa': 0, 'in_voa': 0}}],
        metadata={'location': {'city': '', 'region': '', 'latitude': latitude, 'longitude': 0}})


def build_passive(uid, variety, latitude):
    """搭建纯衰减器件（Mux / VOA / FIU / Demux）"""
    return BandAttenuator(uid=uid, params={'type_variety': variety, 'loss': library[variety].loss},
                          metadata={'location': {'city': '', 'region': '', 'latitude': latitude,
                                                 'longitude': 0}})


# ---------------------------------------------------------------- 构造元素（Tx -> ... -> Rx）
tx = Transceiver(uid='Site_A', params=trx_params,
                 metadata={'location': {'city': 'Site A', 'region': '', 'latitude': 0, 'longitude': 0}})
mux = build_passive('Mux', 'Mux', 1)
mux.params.loss['C96'] = 6.0
mux.params.loss['L96'] = 6.0
voa_tx = build_passive('VOA_Tx', 'VOA', 2)
voa_tx.params.loss['C96'] = 3.5
voa_tx.params.loss['L96'] = 3.5
oa1 = build_multiband_amp('OA1', 3)
fiu1 = build_passive('FIU1', 'FIU', 4)
fiber = Fiber(uid='Span1', type_variety='SSMF', params=fiber_params,
              metadata={'location': {'city': '', 'region': '', 'latitude': 5, 'longitude': 0}})
fiu2 = build_passive('FIU2', 'FIU', 6)
oa2 = build_multiband_amp('OA2', 7)
voa_rx = build_passive('VOA_Rx', 'VOA', 8)
voa_rx.params.loss['C96'] = 1.5
voa_rx.params.loss['L96'] = 1.5
demux = build_passive('Demux', 'Demux', 9)
demux.params.loss['C96'] = 6.0
demux.params.loss['L96'] = 6.0
rx = Transceiver(uid='Site_B', params=trx_params,
                 metadata={'location': {'city': 'Site B', 'region': '', 'latitude': 10, 'longitude': 0}})

print(f'模块 {trx_mode["format"]}（{baud_rate * 1e-9:.1f} GBaud / roll_off {roll_off}），'
      f'模块最大出光功率 tx_power = {launch_dbm:.1f} dBm/波')
print(f'跨度损耗 {span_loss_db:.1f} dB；无源器件插损 [dB]：' + '，'.join(
    f'{name} C96 {library[name].loss["C96"]:g}/L96 {library[name].loss["L96"]:g}'
    for name in ('Mux', 'VOA', 'FIU', 'Demux')))
print('两级光放（C96_SGA_22dBm / L96_SGA_21dBm）增益目标 [dB]：' + '，'.join(
    f'{uid} C96 {amp_gain[(uid, "C96")]:.2f}/L96 {amp_gain[(uid, "L96")]:.2f}' for uid in ('OA1', 'OA2')))
print(f'频谱：{si.number_of_channels} 波（L96 {len(l96_centers)} + C96 {len(c96_centers)}），'
      f'{si.frequency[0] * 1e-12:.3f} ~ {si.frequency[-1] * 1e-12:.3f} THz，'
      f'{baud_rate * 1e-9:.1f} GBaud / {spacing * 1e-9:.0f} GHz，'
      f'每波道 {launch_dbm:.1f} dBm（共 {si.ptot_dbm:.2f} dBm）')

# ---------------------------------------------------------------- 传播（按光路顺序）
# Fiber.propagate 内部会自己算一遍 SRS 但不落属性，这里单独求解一次，
# 用于打印 SRS 转移量并画光纤输入/输出功率谱。必须在光纤传播前、用光纤输入谱求解
srs_holder = {}


def propagate_fiber(si):
    """SRS 单独求解 + 光纤传播；手工建链时 ref_pch_in_dbm 为 None，故直接调用 propagate"""
    srs_holder['srs'] = RamanSolver.calculate_stimulated_raman_scattering(si, fiber)         # 含 SRS
    srs_holder['attenuation_only'] = RamanSolver.calculate_attenuation_profile(si, fiber)    # 仅纯衰减，作参照
    fiber.propagate(si)
    return si


# 光路：(器件名, 类型, 传播函数)。元组顺序即器件连接顺序，传播与打印共用这一份定义
CHAIN = (
    ('Site_A', 'Transceiver', lambda si: tx(si, role=TransceiverRole.EMITTER)),
    ('Mux', 'BandAttenuator', mux),
    ('VOA_Tx', 'BandAttenuator', voa_tx),
    ('OA1', 'Multiband_amplifier', oa1),
    ('FIU1', 'BandAttenuator', fiu1),
    ('Span1', 'Fiber', propagate_fiber),
    ('FIU2', 'BandAttenuator', fiu2),
    ('OA2', 'Multiband_amplifier', oa2),
    ('VOA_Rx', 'BandAttenuator', voa_rx),
    ('Demux', 'BandAttenuator', demux),
    ('Site_B', 'Transceiver', lambda si: rx(si, role=TransceiverRole.RECEIVER)),
)

# 波段划分：frequency 为 [L96..., C96...] 升序拼接
slices = {'L96': slice(0, len(l96_centers)), 'C96': slice(len(l96_centers), None)}


def band_power_dbm(si):
    """按波段分别统计总功率 [dBm]"""
    return {band: watt2dbm(sum(si.pch[sl])) for band, sl in slices.items()}


def band_osnr_db(si):
    """按波段分别统计 OSNR 的（最差, 平均, 最好）[dB]，统计对象是波段内各波长的 OSNR

    单波长 OSNR = 累计 ASE 与发射机 tx_osnr 取倒数和，折算到 0.1 nm / 12.5 GHz。
    手工建链时 Transceiver(EMITTER) 不往光谱里注入 ASE（上游只在收端 update_snr 里合并
    tx_osnr），因此这里把 tx_osnr 一并计入，否则第一级光放之前的 OSNR 恒为无穷大。
    """
    ref_db = lin2db(12.5e9 / baud_rate)
    osnr = {}
    with errstate(divide='ignore'):     # 无 ASE 时 signal / ase 为 inf，与 tx_osnr 合并后仍为有限值
        for band, sl in slices.items():
            raw_db = lin2db(si.signal[sl] / si.ase[sl]) - ref_db
            values = -lin2db(db2lin(-raw_db) + db2lin(-tx_osnr_db))
            osnr[band] = (values.min(), values.mean(), values.max())
    return osnr


# 逐器件传播，同时记录每个器件分波段的输入/输出总功率和 OSNR
device_power = []   # [(器件名, 类型, {波段: 输入 dBm}, {波段: 输出 dBm})]
device_osnr = []    # [(器件名, 类型, {波段: 输入 dB}, {波段: 输出 dB})]
oa2_out_pch_dbm = None
for uid, dev_type, apply in CHAIN:
    pin_band, osnr_in_band = band_power_dbm(si), band_osnr_db(si)
    si = apply(si)
    device_power.append((uid, dev_type, pin_band, band_power_dbm(si)))
    device_osnr.append((uid, dev_type, osnr_in_band, band_osnr_db(si)))
    if uid == 'OA2':
        oa2_out_pch_dbm = si.pch_dbm.copy()   # OA2 输出谱，用于画图

srs = srs_holder['srs']
srs_attenuation_only = srs_holder['attenuation_only']

assert si.number_of_channels == len(frequency), '光放频带把部分波长滤掉了，请检查 f_min / f_max'

print('\n光放工作点（输入/输出为该波段总功率，输出应等于该型号的 p_max；NF 由增益-NF 表查得）：')
for uid, amplifier in (('OA1', oa1), ('OA2', oa2)):
    for band_name, sub_amp in amplifier.amplifiers.items():
        print(f'  {uid} {band_name:>6} {sub_amp.params.type_variety}：增益 {sub_amp.effective_gain:.2f} dB，'
              f'{sub_amp.pin_db:.2f} dBm → {sub_amp.pout_db:.2f} dBm，'
              f'NF {sub_amp.nf.mean():.2f} dB')

# ---------------------------------------------------------------- 每波长的噪声受限 SNR
# SNR_ASE 按含滚降的实际信号带宽 BW*(1+Rolloff) 计算；SNR_NLI 为绝对量
bw_eff = si.baud_rate * (1 + si.roll_off)   # 实际信号带宽 [Hz]，本案例 131.3 GHz x 1.05 = 137.865 GHz
p_ase = si.ase * (1 + si.roll_off)          # ASE 噪声功率折算到 bw_eff
snr_ase = lin2db(si.signal / p_ase)
snr_nli = lin2db(si.signal / si.nli)

print(f'\n噪声口径：SNR_ASE 用 BW*(1+Rolloff) = {bw_eff[0] * 1e-9:.2f} GHz；SNR_NLI 为绝对量')

print(f'\n{"Band":>4} {"Ch":>3} {"频率(THz)":>10} {"功率(dBm)":>9} {"SNR_NLI":>9} {"SNR_ASE":>9}')
for band_name, sl in slices.items():
    for i in range(si.number_of_channels)[sl]:
        print(f'{band_name:>4} {i - sl.start + 1:>3} {si.frequency[i] * 1e-12:>10.4f} '
              f'{si.pch_dbm[i]:>9.2f} {snr_nli[i]:>9.2f} {snr_ase[i]:>9.2f}')

# ---------------------------------------------------------------- SRS 转移量
transfer_db = lin2db(srs.power_profile[:, -1]) - lin2db(srs_attenuation_only.power_profile[:, -1])
print('\nSRS 转移量（有 SRS 相对纯衰减的输出功率变化）：')
print(f'  首波长 {si.frequency[0] * 1e-12:.4f} THz：{transfer_db[0]:+.3f} dB')
print(f'  末波长 {si.frequency[-1] * 1e-12:.4f} THz：{transfer_db[-1]:+.3f} dB')

# ---------------------------------------------------------------- 器件链路汇总（最终输出）
print('\n器件连接顺序（Tx -> Rx）：')
print('  ' + ' -> '.join(uid for uid, _, _ in CHAIN))

# rich 表格按显示宽度（中文字符占 2 列）对齐，避免手工 format 的对齐偏差；
# 显式指定宽度，否则输出重定向/管道时 rich 按 80 列压缩表格，把列内容截断
console = Console(width=140)
BANDS = ('C96', 'L96')


def fmt(value):
    """非有限值统一显示为 '-'"""
    return f'{value:.2f}' if isfinite(value) else '-'


def fmt_osnr(stats):
    """波段内各波长 OSNR 的统计值：最差 / 平均 / 最好"""
    return ' / '.join(fmt(value) for value in stats)


def print_device_table(title, headers, devices):
    """devices: [(器件, 类型, [各列文本])]，列顺序与 headers 一致"""
    print(f'\n{title}')
    table = Table(header_style='bold', pad_edge=False)
    table.add_column('器件', no_wrap=True)
    table.add_column('类型', no_wrap=True)
    for header in headers:
        table.add_column(header, justify='right', no_wrap=True)
    for uid, dev_type, cells in devices:
        table.add_row(uid, dev_type, *cells)
    console.print(table)


power_rows = []
for uid, dev_type, pin_band, pout_band in device_power:
    cells = []
    for band in BANDS:
        cells += [fmt(pin_band[band]), fmt(pout_band[band]),
                  fmt(pout_band[band] - pin_band[band])]
    power_rows.append((uid, dev_type, cells))
print_device_table('各器件输入/输出总功率（C96 / L96 同表并列，含噪声；增减 = 输出 - 输入）',
                   [f'{band} {item}({unit})' for band in BANDS
                    for item, unit in (('输入', 'dBm'), ('输出', 'dBm'), ('增减', 'dB'))],
                   power_rows)

osnr_rows = []
for uid, dev_type, osnr_in_band, osnr_out_band in device_osnr:
    cells = []
    for band in BANDS:
        cells += [fmt_osnr(osnr_in_band[band]), fmt_osnr(osnr_out_band[band])]
    osnr_rows.append((uid, dev_type, cells))
print_device_table(f'各器件 OSNR（单元格式为 最差 / 平均 / 最好；仅计 ASE，含发射机 tx_osnr {tx_osnr_db:.0f} dB，'
                   f'折算到 0.1 nm / 12.5 GHz）',
                   [f'{band} {side}(dB)' for band in BANDS for side in ('输入', '输出')],
                   osnr_rows)

# ---------------------------------------------------------------- 功率谱
# 横坐标用波长（nm）：lambda = c / f。四条曲线共用同一组频率（升序 63 波）
wavelength_nm = freq2wavelength(srs.frequency) * 1e9
# 图片默认保存到脚本所在目录的 temp 子文件夹（该目录已加入 .gitignore）
output_dir = Path(__file__).parent / 'temp'
output_dir.mkdir(parents=True, exist_ok=True)

figure(figsize=(11, 5))
plot(wavelength_nm, watt2dbm(srs.power_profile[:, 0]), label='光纤输入（OA1 输出经 FIU1）')
plot(wavelength_nm, watt2dbm(srs.power_profile[:, -1]), label='光纤输出（OA2 输入，含 SRS）')
plot(wavelength_nm, oa2_out_pch_dbm, label='OA2 输出（C96 22 dBm / L96 21 dBm 总功率）')
plot(wavelength_nm, si.pch_dbm, label='接收端（VOA + Demux 后）')
xlabel('波长 (nm)')
ylabel('每波道功率 (dBm)')
title(f'C96 + L96 共 {len(frequency)} 波：80 km SSMF + 两级 C/L 双波段光放功率谱')
grid(True)
legend()
savefig(output_dir / 'spectrum_c96_l96.png', dpi=150)
# show()

# ---------------------------------------------------------------- 光纤衰减系数 vs 波长
# gnpy 的 FiberParams.loss_coef 支持两种写法：
#   标量 0.275            → 波长无关
#   {'value': [...], 'frequency': [...]} → 逐波长表，Fiber.loss_coef_func 会在波段内插值（当前用法）
# 这里画本链路实际生效的衰减系数，直接反映光纤衰减是否随波长变化
loss_coef_db_per_km = fiber.loss_coef_func(frequency) * 1e3     # dB/m -> dB/km
anchor_db_per_km = fiber.loss_coef_func(ref_frequency) * 1e3    # 锚定波长（1550 nm）处
coef_span_db = loss_coef_db_per_km.max() - loss_coef_db_per_km.min()
coef_kind = '（常数，与波长无关）' if coef_span_db == 0 else '（随波长变化）'
print(f'\n光纤衰减系数（{fiber.type_variety}，{length_km:g} km）：'
      f'{loss_coef_db_per_km.min():.4f} ~ {loss_coef_db_per_km.max():.4f} dB/km，'
      f'波段内起伏 {coef_span_db:.3e} dB/km{coef_kind}')
print(f'  锚定 {ATTENUATION_REF_WAVELENGTH_NM:.0f} nm 处 {anchor_db_per_km:.4f} dB/km，'
      f'对应单跨损耗 {anchor_db_per_km * length_km + con_in + con_out:.2f} dB')

figure(figsize=(11, 5))
plot(wavelength_nm, loss_coef_db_per_km, marker='.', label='衰减系数（逐波长模型）')
plot([ATTENUATION_REF_WAVELENGTH_NM], [anchor_db_per_km], marker='o', color='crimson',
     label=f'{ATTENUATION_REF_WAVELENGTH_NM:.0f} nm 锚点：{anchor_db_per_km:.3f} dB/km')
xlabel('波长 (nm)')
ylabel('衰减系数 (dB/km)')
title(f'C96 + L96 波段的光纤衰减系数（{fiber.type_variety}，锚定 {ATTENUATION_REF_WAVELENGTH_NM:.0f} nm）')
grid(True)
legend()
savefig(output_dir / 'attenuation_coef_c96_l96.png', dpi=150)
# show()

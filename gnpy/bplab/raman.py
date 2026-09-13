# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.bplab.raman: BPLab add-on stimulated Raman scattering solver
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
gnpy.bplab.raman
================

BPLab 自建的 SRS（受激拉曼散射）求解器（纯新增，不修改 gnpy 原有代码）。

上游 gnpy 的拉曼模型把增益谱折进 ``Fiber.cr()``（用有效面积的几何近似 + ``DEFAULT_RAMAN_COEFFICIENT``），
再用扰动法（``perturbative``）或数值法解耦合方程。本模块改用 ``scipy.integrate.solve_ivp`` 直接积分
耦合功率方程，拉曼增益谱用"峰值 + 归一化谱型"给出：

    dP_k/dz = P_k * sum_m(gR_dat[k, m] * P_m) - alpha_k * P_k

其中 ``gR_dat`` 由 :func:`_get_gR_dat` 组装（泵浦对信号的增益、泵浦自身的损耗），
``gR`` 谱型取上游 ``DEFAULT_RAMAN_COEFFICIENT['gamma_raman']``，按自身最大值归一化后乘实测峰值
``gR_peak``；跨波长按 ``(f / raman_fref) ** raman_ns`` 缩放。

设备库中 Fiber 条目里的扩展字段（YANG 模型不识别，由
:func:`gnpy.bplab.trx.load_equipment_with_module_power` 在 YANG 校验前摘掉、校验后回填）：

    "raman_gain": {
      "gR_peak": 0.38e-3,
      "reference_wavelength": 1480.0e-9,
      "ns": 2.313
    }

- :func:`raman_gain_table`：由峰值生成 ``(2, N)`` 归一化增益表
- :class:`RamanParams`：拉曼参数（器件参数来自设备库，求解设置在此给默认值）
- :func:`raman_ode_foreward` / :func:`_get_gR_dat`：ODE 内核（移植自 ``scripts/ganlin/raman_file.py``，
  按 gnpy 口径改写：频率升序、功率用瓦特、不做高频在前的翻转）
- :class:`RamanSolver`：接口与上游 ``gnpy.core.science_utils.RamanSolver`` 一致
- :class:`SrsFiber`：``Fiber`` 子类，``propagate()`` 用本模块的求解器替代上游求解器
- :func:`extract_raman_gain` / :func:`attach_raman_gain`：设备库加载时的摘除与回填

限制：不支持拉曼泵浦（co/counter-propagating pumps），也不支持集总损耗（``lumped_losses``）——
后者是阶跃损耗，ODE 无法表达，遇到时直接报错而不是静默算错。
"""

from logging import getLogger
from typing import Dict, Optional

from numpy import abs, append, arange, array, asarray, exp, ones, outer, sign, sqrt, zeros
from scipy.constants import c
from scipy.integrate import solve_ivp
from scipy.interpolate import interp1d

from gnpy.core.elements import Fiber
from gnpy.core.exceptions import EquipmentConfigError
from gnpy.core.parameters import DEFAULT_RAMAN_COEFFICIENT
from gnpy.core.science_utils import NliSolver, StimulatedRamanScattering

logger = getLogger(__name__)

# 设备库 Fiber 条目里拉曼增益段的字段名（YANG 模型不接受该字段）
RAMAN_GAIN_KEY = 'raman_gain'

# G652D 拉曼参数默认值：gR 峰值 [1/(W·m)]、参考波长 [m]、波长缩放指数
DEFAULT_GR_PEAK = 0.38e-3
DEFAULT_RAMAN_REFERENCE_WAVELENGTH = 1480e-9
DEFAULT_RAMAN_NS = 2.313

# 功率下限 [W]：与参考实现一致，避免后续除法/取对数的数值问题
POWER_FLOOR_W = 1e-100


def raman_gain_table(gR_peak: float = DEFAULT_GR_PEAK):
    """生成归一化拉曼增益表，供 :func:`raman_ode_foreward` 使用

    谱型取上游 ``DEFAULT_RAMAN_COEFFICIENT['gamma_raman']``（SSMF 拉曼增益谱，90 个频差点），
    按该表自身最大值归一化后乘 ``gR_peak``，因此表中最大增益恰为 ``gR_peak``。

    :param gR_peak: 拉曼增益峰值 [1/(W·m)]
    :return: ``(2, N)`` 数组：第 0 行为频差 [Hz]，第 1 行为归一化拉曼增益 [1/(W·m)]

    >>> table = raman_gain_table(0.38e-3)
    >>> table.shape
    (2, 90)
    >>> bool(table[1].max() == 0.38e-3), table[0][0], round(table[0][table[1].argmax()] * 1e-12, 2)
    (True, 0.0, 12.75)
    """
    if gR_peak <= 0:
        raise EquipmentConfigError(f'gR_peak must be > 0, got {gR_peak}')
    gamma_raman = asarray(DEFAULT_RAMAN_COEFFICIENT['gamma_raman'], dtype=float)
    frequency_offset = asarray(DEFAULT_RAMAN_COEFFICIENT['frequency_offset'], dtype=float)
    return array([frequency_offset, gamma_raman / gamma_raman.max() * gR_peak])


class RamanParams:
    """拉曼求解参数

    器件相关的三个值（``gR_peak`` / ``reference_wavelength`` / ``ns``）来自设备库的
    ``raman_gain`` 段；``flag`` 与两个空间分辨率属仿真设置，默认值与本地参考实现一致。

    :param gR_peak: 拉曼增益峰值 [1/(W·m)]
    :param reference_wavelength: 拉曼增益的参考波长 [m]，频率由 ``c / wavelength`` 算出
    :param ns: 拉曼增益随波长的缩放指数（``(f / reference_frequency) ** ns``）
    :param flag: 是否计算 SRS；False 时退化为纯衰减（与上游同名开关语义一致）
    :param solver_spatial_resolution: ODE 求解步长 [m]
    :param result_spatial_resolution: 结果输出的空间采样间隔 [m]

    >>> params = RamanParams(gR_peak=0.5e-3)
    >>> params.gR_peak, round(params.reference_frequency * 1e-12, 4), params.ns
    (0.0005, 202.5625, 2.313)
    """

    def __init__(self, gR_peak: float = DEFAULT_GR_PEAK,
                 reference_wavelength: float = DEFAULT_RAMAN_REFERENCE_WAVELENGTH, ns: float = DEFAULT_RAMAN_NS,
                 flag: bool = True, solver_spatial_resolution: float = 50.0,
                 result_spatial_resolution: float = 10e3):
        if gR_peak <= 0:
            raise EquipmentConfigError(f'gR_peak must be > 0 [1/(W m)], got {gR_peak}')
        if not 1e-7 < reference_wavelength < 1e-5:
            raise EquipmentConfigError(f'reference_wavelength must be in (100 nm, 10 um), got {reference_wavelength}')
        if ns <= 0:
            raise EquipmentConfigError(f'ns must be > 0, got {ns}')
        if solver_spatial_resolution <= 0 or result_spatial_resolution <= 0:
            raise EquipmentConfigError('spatial resolutions must be > 0 [m], got '
                                       f'solver={solver_spatial_resolution}, result={result_spatial_resolution}')
        self.gR_peak = float(gR_peak)
        self.reference_wavelength = float(reference_wavelength)
        self.reference_frequency = c / self.reference_wavelength
        self.ns = float(ns)
        self.flag = bool(flag)
        self.solver_spatial_resolution = float(solver_spatial_resolution)
        self.result_spatial_resolution = float(result_spatial_resolution)

    def to_json(self) -> Dict:
        return {'gR_peak': self.gR_peak,
                'reference_wavelength': self.reference_wavelength,
                'ns': self.ns,
                'flag': self.flag,
                'solver_spatial_resolution': self.solver_spatial_resolution,
                'result_spatial_resolution': self.result_spatial_resolution}

    def __repr__(self):
        return (f'RamanParams(gR_peak={self.gR_peak}, '
                f'reference_wavelength={self.reference_wavelength * 1e9:.1f}nm, ns={self.ns})')


def _get_gR_dat(fch, gR, raman_fref, raman_ns):
    """由归一化拉曼增益谱生成耦合矩阵 gR_dat [1/(W·m)]

    逐波道把每个波道当作泵浦：
    - ``gR_dat[i, k]``（i 是低频信号、k 是泵浦频率 fch[k]）：泵浦对信号的增益，为正；
    - ``gR_dat[k, i]``：泵浦自身被抽走的功率，为负，并乘 ``fch[k] / fch[i]``
      （把增益系数换算到泵浦波长上，保证光子数守恒）。

    gR 的第 0 行是频差 [Hz]、第 1 行是归一化增益 [1/(W·m)]；频差对应的增益插值后再乘
    ``(fch[k] / raman_fref) ** raman_ns`` 做跨波长缩放。

    >>> fch = array([191.35e12, 191.50e12])
    >>> gR_dat = _get_gR_dat(fch, array([[0.0, 1.5e12], [0.0, 1.0e-3]]), c / 1480e-9, 2.313)
    >>> float(gR_dat[0, 0]), bool(gR_dat[0, 1] > 0), bool(gR_dat[1, 0] < 0), bool(gR_dat[1, 1] == 0)
    (0.0, True, True, True)
    """
    gR_fit = interp1d(gR[0, :], gR[1, :], kind='slinear', fill_value='extrapolate')

    def scaled_gain(delta_f, pump_frequency):
        """泵浦频率处、频差 delta_f 的拉曼增益系数 [1/(W·m)]"""
        return gR_fit(delta_f) * (pump_frequency / raman_fref) ** raman_ns

    gR_dat = zeros((fch.size, fch.size))
    for k in arange(fch.size):
        lower_than_pump = fch < fch[k]           # 只有比泵浦低的波道才从该泵浦获得增益
        delta_f = fch[k] - fch
        gain = scaled_gain(abs(delta_f), fch[k]) * sign(delta_f)
        gR_dat[lower_than_pump, k] = gain[lower_than_pump]                      # 信号获得增益
        gR_dat[k, lower_than_pump] = -gain[lower_than_pump] * fch[k] / fch[lower_than_pump]  # 泵浦被消耗
    return gR_dat


def raman_ode_foreward(alpha, fch, gR, pch_in, zdat, raman_fref, raman_ns):
    """求解无背向拉曼泵浦的耦合功率方程（移植自 ``scripts/ganlin/raman_file.py``）

    ``dP/dz = (gR_dat · P) ⊙ P − alpha ⊙ P``，用 ``scipy.integrate.solve_ivp`` 积分。

    [ref.1] Raman Amplification for Fiber Communications Systems 10.1109/JLT.2003.822828
    [ref.2] Raman gain: pump-wavelength dependence in single-mode fiber 10.1364/OL.27.001232
    [ref.3] Pump-wavelength dependence of Raman gain in single-mode optical fibers 10.1109/JLT.2003.821716

    与参考实现的差异（适配 gnpy）：不把频率翻成高频在前（gnpy 一律升序，``_get_gR_dat``
    的矩阵构造本身与顺序无关）；返回绝对功率 [W] 而不是归一化值/dBm，且直接采用 solve_ivp 的
    ``y`` 形状 ``(波道, z)``——参考实现把它转置成了 ``(z, 波道)``，而 gnpy 的
    :class:`gnpy.core.science_utils.StimulatedRamanScattering` 要的正是 ``(波道, z)``
    （``loss_profile[:, -1]`` 取每波道在光纤末端的值）。

    :param alpha: 线性衰减系数 [Np/m]，与 fch 同序
    :param fch: 频率 [Hz]，升序
    :param gR: ``(2, N)`` 归一化拉曼增益表，见 :func:`raman_gain_table`
    :param pch_in: 入纤每波道功率 [W]
    :param zdat: z 采样点 [m]，须为递增且从 0 开始
    :param raman_fref: 拉曼增益参考频率 [Hz]
    :param raman_ns: 拉曼增益波长缩放指数
    :return: ``(power_profile [N, nz] W, zdat)``，行与 fch 一一对应
    """
    alpha = asarray(alpha, dtype=float)
    fch = asarray(fch, dtype=float)
    pch_in = asarray(pch_in, dtype=float)
    zdat = asarray(zdat, dtype=float)
    gR_dat = _get_gR_dat(fch, gR, raman_fref, raman_ns)
    # rtol/atol 沿用参考实现：atol 取 1e-16 保证低功率波道的精度
    solution = solve_ivp(lambda z, power: (gR_dat.dot(power)) * power - alpha * power,
                         [zdat[0], zdat[-1]], pch_in, t_eval=zdat, rtol=1e-6, atol=1e-16)
    power_profile = solution.y    # solve_ivp 的 y 形状 (状态维, 时间点) = (波道, z)，与 gnpy 一致
    power_profile[power_profile < POWER_FLOOR_W] = POWER_FLOOR_W
    return power_profile, zdat


class RamanSolver:
    """用 :func:`raman_ode_foreward` 计算 SRS 的求解器

    方法签名与上游 :class:`gnpy.core.science_utils.RamanSolver` 一致，可直接替换。
    """

    @staticmethod
    def calculate_attenuation_profile(spectral_info, fiber) -> StimulatedRamanScattering:
        """只算纯衰减（不含 SRS），与上游同名方法一致

        :param spectral_info: 光谱信息
        :param fiber: 光纤实例
        :return: SRS 容器（power_profile 为纯衰减结果）
        """
        z = array([0, fiber.params.length])
        frequency = spectral_info.frequency
        loss_profile = exp(-outer(fiber.alpha(frequency), z))
        power_profile = outer(spectral_info.pch, ones(z.size)) * loss_profile
        return StimulatedRamanScattering(power_profile, loss_profile, frequency, z)

    @staticmethod
    def calculate_stimulated_raman_scattering(spectral_info, fiber) -> StimulatedRamanScattering:
        """沿 z 积分耦合功率方程，得到各波道在光纤内的功率/损耗剖面

        :param spectral_info: 光纤入纤处的光谱信息（功率单位 W）
        :param fiber: 光纤实例，须带 :class:`RamanParams`（``SrsFiber`` 会自动注入）
        :return: :class:`gnpy.core.science_utils.StimulatedRamanScattering`
        """
        logger.debug('Start computing fiber Stimulated Raman Scattering (bplab ODE solver)')
        raman_params = getattr(fiber, 'raman_params', None) or RamanParams()
        if not raman_params.flag:
            return RamanSolver.calculate_attenuation_profile(spectral_info, fiber)
        if fiber.lumped_losses.size:
            raise EquipmentConfigError(
                f'{fiber.uid}: the bplab SRS solver does not support lumped losses '
                f'({fiber.lumped_losses.size} splice(s) defined)')

        length = fiber.params.length
        z = append(arange(0, length, raman_params.solver_spatial_resolution), length)
        z_final = append(arange(0, length, raman_params.result_spatial_resolution), length)
        frequency = spectral_info.frequency
        gR = raman_gain_table(raman_params.gR_peak)
        power_profile, z = raman_ode_foreward(fiber.alpha(frequency), frequency, gR, spectral_info.pch, z,
                                             raman_params.reference_frequency, raman_params.ns)
        # 结果与上游一致地重采样到 z_final，消费者拿到的 z 采样不变
        power_profile = interp1d(z, power_profile, axis=1)(z_final)
        loss_profile = power_profile / outer(spectral_info.pch, ones(z_final.size))
        return StimulatedRamanScattering(power_profile, loss_profile, frequency, z_final)


class SrsFiber(Fiber):
    """用 bplab 的 SRS 求解器（:class:`RamanSolver` + solve_ivp）替代上游求解器的光纤

    构造时若 ``params`` 里带设备库的 ``raman_gain`` 段（由
    :func:`gnpy.bplab.trx.load_equipment_with_module_power` 回填），则据此建 :class:`RamanParams`；
    也可用 ``raman_params=`` 显式传入；两者都没有时用 G652D 默认值。
    """

    def __init__(self, *args, params=None, raman_params: Optional[RamanParams] = None, **kwargs):
        params = dict(params or {})
        settings = params.pop(RAMAN_GAIN_KEY, None)     # FiberParams 不认识该键，构造前取出
        super().__init__(*args, params=params, **kwargs)
        self.raman_params = raman_params if raman_params is not None else RamanParams(**(settings or {}))

    def propagate(self, spectral_info):
        """与上游 :meth:`gnpy.core.elements.Fiber.propagate` 同流程，只把 SRS 求解器换成 bplab 的

        :param spectral_info: The spectral information object.
        :type spectral_info: SpectralInformation
        """
        # apply the attenuation due to the input connector loss
        attenuation_in_db = self.params.con_in + self.params.att_in
        spectral_info.apply_attenuation_db(attenuation_in_db)

        # inter channels Raman effect
        stimulated_raman_scattering = RamanSolver.calculate_stimulated_raman_scattering(spectral_info, self)

        # NLI noise evaluated at the fiber input
        nli = NliSolver.compute_nli(spectral_info, stimulated_raman_scattering, self)
        spectral_info.add_nli(nli)

        # chromatic dispersion and pmd variations
        spectral_info.chromatic_dispersion += self.chromatic_dispersion(spectral_info.frequency)
        spectral_info.pmd = sqrt(spectral_info.pmd ** 2 + self.pmd ** 2)

        # latency
        spectral_info.latency += self.params.latency

        # apply the attenuation due to the fiber losses
        attenuation_fiber = stimulated_raman_scattering.loss_profile[:, -1]
        spectral_info.apply_attenuation_lin(attenuation_fiber)

        # apply the attenuation due to the output connector loss
        attenuation_out_db = self.params.con_out
        spectral_info.apply_attenuation_db(attenuation_out_db)
        self.pch_out_dbm = spectral_info.pch_dbm
        self.propagated_labels = spectral_info.label


def extract_raman_gain(json_data: Dict) -> Dict[str, Dict]:
    """从原始设备库 json 的 Fiber 条目里摘出 ``raman_gain``

    该字段不在 YANG 模型中，必须在 :func:`gnpy.tools.convert_legacy_yang.yang_to_legacy`
    校验之前摘掉。

    :param json_data: 原始设备库 json（会被就地修改，去掉 raman_gain 字段）
    :return: {光纤型号: 拉曼增益段}
    """
    extracted = {}
    for entry in json_data.get('Fiber', []):
        settings = entry.pop(RAMAN_GAIN_KEY, None)
        if settings:
            extracted[entry['type_variety']] = settings
    return extracted


def attach_raman_gain(equipment: Dict, extracted: Dict[str, Dict]) -> None:
    """把摘出的 ``raman_gain`` 挂到设备库的 Fiber 条目上

    上游 ``json_io.Fiber`` 只认自己登记的键，未知键会被静默丢弃，
    因此必须在 :func:`gnpy.tools.json_io._equipment_from_json` 之后显式挂上。

    :param equipment: 设备库字典
    :param extracted: :func:`extract_raman_gain` 的返回值
    """
    for variety, settings in extracted.items():
        fiber = equipment.get('Fiber', {}).get(variety)
        if fiber is None:
            logger.warning(f'{RAMAN_GAIN_KEY} defined for unknown Fiber type_variety {variety}: ignored')
            continue
        setattr(fiber, RAMAN_GAIN_KEY, settings)

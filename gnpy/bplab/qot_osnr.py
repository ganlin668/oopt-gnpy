# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.bplab.qot_osnr: BPLab add-on QoT OSNR penalties
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""
gnpy.bplab.qot_osnr
===================

BPLab 自建的 QoT（传输质量）OSNR 代价计算函数（纯新增，不修改 gnpy 原有代码）。

非线性 OSNR 代价（NLI penalty）：

    代价[dB] = -10*log10(1 - OSNR_b2b / OSNR_NLI)

其中 ``OSNR_b2b`` 是模块 B2B 无误码所需的 OSNR，``OSNR_NLI`` 是由模型算出的非线性受限 OSNR
（NLI 噪信比按 0.1 nm / 12.5 GHz 参考带宽折算后取倒数）。物理含义：为了让 ASE 噪声与非线性
噪声合计仍满足 B2B 要求，ASE 侧需要额外付出的 OSNR。
"""

from numpy import log10

from gnpy.core.utils import db2lin

# OSNR 参考带宽 [Hz]：0.1 nm（1550 nm 附近）
OSNR_REFERENCE_BANDWIDTH_HZ = 12.5e9


def nli_osnr_penalty_db(spectral_info, osnr_b2b_db):
    """逐波道的非线性 OSNR 代价 [dB]

    代价 = -10*log10(1 - OSNR_b2b / OSNR_NLI)，其中

    - ``OSNR_b2b``：模块 B2B 无误码所需的 OSNR（0.1 nm 参考带宽）；
    - ``OSNR_NLI``：非线性受限 OSNR。NLI 噪信比（信号带宽内）乘 ``12.5e9 / baud_rate``
      即折算到 0.1 nm 参考带宽，其倒数就是 ``OSNR_NLI``。

    代价随非线性恶化而增大：``OSNR_NLI`` 远高于 ``OSNR_b2b`` 时趋近 0，逼近 ``OSNR_b2b`` 时发散
    （此时非线性单独就吃掉了全部 OSNR 预算，返回 inf / nan）。

    多跨链路无需在此按跨累加：gnpy 的 ``SpectralInformation.nli`` 由 ``add_nli`` 逐跨累加，
    且噪信比随 pch 同步缩放，因此沿无源器件与光放保持不变——传链路末端或任一位置的
    ``spectral_info``，结果相同。

    :param spectral_info: 光谱信息，需含 nli / signal / baud_rate
    :param osnr_b2b_db: 模块 B2B required OSNR [dB]（0.1 nm 参考）
    :return: 逐波道的非线性 OSNR 代价 [dB]
    """
    nsr_nli = spectral_info.nli / spectral_info.signal
    nsr2osnr_conv = OSNR_REFERENCE_BANDWIDTH_HZ / spectral_info.baud_rate
    return -10 * log10(1 - db2lin(osnr_b2b_db) * nsr2osnr_conv * nsr_nli)

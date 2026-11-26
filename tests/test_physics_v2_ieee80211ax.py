"""
Bộ kiểm thử hồi quy Vật lý Chuẩn IEEE 802.11ax (Lõi Digital Twin v2):
  1. Khóa rò rỉ kênh kề Pha 1 trong đúng băng tần U-NII-1 [0..3] / U-NII-3 [4..8] (cấm nhảy 505 MHz từ CH48 -> CH149).
  2. Bất đối xứng CCA Kênh chính (Primary 20 MHz) vs Kênh phụ (Secondary 20 MHz).
  3. Lão hóa kênh CSI liên tục Clarke-Jakes J0, mốc đẳng hướng BF=OFF (0.0 dB), tách Doppler với mật độ ngồi yên.
  4. Đồng cư Legacy 802.11a/n/ac toàn diện (Heusse Harmonic Drag, ATF vs FIFO, Dup RTS/CTS, Non-TWT Jitter).
  5. Kiểm định độ khớp tối ưu toàn cục compute_optimal_targets (45,360 cấu hình) & khả năng đạt SLA >= 99% của Oracle.
"""
import itertools
import numpy as np

from src.envs.digital_twin_env import (
    ACTION_NVEC,
    StatefulChannelGuardWrapper,
    HardwareAwareWrapper,
    UltimateDigitalTwinEnv,
    QOE_LATENCY_MS,
)


def test_phase1_band_isolation_and_primary_secondary_cca():
    env = UltimateDigitalTwinEnv(seed=2026)
    phase1_cnt = 0
    for _ in range(5000):
        env._sample_markov_regime()
        if env.scenario_mode == 1:
            phase1_cnt += 1
            assert not (
                float(env.target_ch_noise[3]) > -90.0 and float(env.target_ch_noise[4]) > -90.0
            ), f"Lỗi rò rỉ xuyên dải DFS giữa CH48 và CH149: {env.target_ch_noise[3:5]}"
    assert phase1_cnt >= 1000, f"Số lần roll Pha 1 quá thấp: {phase1_cnt}"

    # Bất đối xứng Primary vs Secondary 20 MHz trên cụm HE40 (CH36, CH40)
    obs = np.array([-56.0, -73.0, 24.0, 0.25, 0.28, 0.45, 0.65, 0.0], dtype=np.float32)
    noise_ch36_jam = np.array([-73.0, -95.0, -95.0, -95.0, -95.0, -95.0, -95.0, -95.0, -95.0], dtype=np.float32)
    m_pri = UltimateDigitalTwinEnv._original_evaluate_network_physics(
        obs, [0, 1, 3, 7, 1, 2, 1], aqm_enabled=True, sub_noise_dbm=noise_ch36_jam
    )
    m_sec = UltimateDigitalTwinEnv._original_evaluate_network_physics(
        obs, [1, 1, 3, 7, 1, 2, 1], aqm_enabled=True, sub_noise_dbm=noise_ch36_jam
    )
    assert m_pri["p_c"] > m_sec["p_c"], f"Primary CCA stall phải tạo p_c cao hơn Secondary ({m_pri['p_c']} vs {m_sec['p_c']})"

    # Guardrail không cách ly cả khối 4 kênh UNII-1 khi gặp nhiễu hẹp mức -76 dBm trên CH36
    guard = StatefulChannelGuardWrapper(HardwareAwareWrapper(UltimateDigitalTwinEnv()))
    guard.current_ch = 0
    obs_narrow = np.array([-56.0, -76.0, 24.0, 0.15, 0.20, 0.35, 0.65, 0.0], dtype=np.float32)
    guard.filter_channel_action(obs_narrow, np.array([0, 0, 3, 7, 1, 2, 1]))
    assert 0 in guard.quarantine_until and 2 not in guard.quarantine_until and 3 not in guard.quarantine_until, (
        f"Nhiễu hẹp -76 dBm không được khóa nhầm CH44/CH48: {guard.quarantine_until}"
    )
    print("[PASS 1] Cách ly ranh giới phổ Pha 1, bất đối xứng Primary/Secondary CCA & Guardrail hợp lệ.")


def test_phase3_jakes_csi_aging_continuity():
    # Kiểm tra tính trơn tại ranh giới sta=95
    _, g_lo, _, _ = UltimateDigitalTwinEnv._compute_jakes_bf_physics(4, 94.9, 0.15, 0.45, 0.0)
    _, g_hi, _, _ = UltimateDigitalTwinEnv._compute_jakes_bf_physics(4, 95.1, 0.15, 0.45, 0.0)
    assert abs(g_hi - g_lo) < 0.05, f"Độ lợi Jakes BF bị nhảy bước giả tạo: {g_lo} vs {g_hi}"

    # Giảng đường ngồi yên (90 STA, is_break=0, retry=0.10) KHÔNG kích hoạt CSI aging;
    # Bão chuyển vùng giờ ra chơi (125 STA, is_break=1, retry=0.25) CÓ kích hoạt CSI aging
    _, _, _, aging_stationary = UltimateDigitalTwinEnv._compute_jakes_bf_physics(4, 90.0, 0.10, 0.45, 0.0)
    _, _, _, aging_roam = UltimateDigitalTwinEnv._compute_jakes_bf_physics(4, 125.0, 0.25, 0.65, 1.0)
    assert not aging_stationary, "Giảng đường tĩnh 90 STA không nên bị đánh cờ CSI aging"
    assert aging_roam, "Bão chuyển vùng 125 STA giờ giải lao phải kích hoạt CSI aging"

    # Mốc đẳng hướng BF=OFF (bf_idx=0) có bf_gain_db == 0.0 (kiểm chứng qua mức độ công bằng trong reward)
    obs_roam = np.array([-66.0, -95.0, 125.0, 0.25, 0.35, 0.65, 0.24, 1.0], dtype=np.float32)
    m_off = UltimateDigitalTwinEnv._original_evaluate_network_physics(
        obs_roam, [4, 0, 5, 6, 0, 1, 0], aqm_enabled=True
    )
    m_on = UltimateDigitalTwinEnv._original_evaluate_network_physics(
        obs_roam, [4, 0, 5, 6, 0, 1, 1], aqm_enabled=True
    )
    assert m_off["reward"] > m_on["reward"], "Khi bão chuyển vùng, tắt Beamforming (BF=OFF) phải đạt reward cao hơn BF=ON"
    assert m_on["is_csi_aging_regime"] == 1.0 and m_on["bf_corr_rho"] < 0.2
    print("[PASS 2] Mô hình lão hóa kênh CSI Clarke-Jakes liên tục trơn & phân biệt di động vs tĩnh.")


def test_phase4_legacy_heusse_and_protection_overhead():
    obs_leg = np.array([-55.0, -95.0, 30.0, 0.35, 0.42, 0.80, 0.14, 0.0], dtype=np.float32)
    m_atf_20 = UltimateDigitalTwinEnv._original_evaluate_network_physics(
        obs_leg, [4, 0, 3, 8, 1, 0, 1], aqm_enabled=True
    )
    m_fifo_20 = UltimateDigitalTwinEnv._original_evaluate_network_physics(
        obs_leg, [4, 0, 3, 8, 1, 0, 1], aqm_enabled=False
    )
    m_atf_80 = UltimateDigitalTwinEnv._original_evaluate_network_physics(
        obs_leg, [4, 2, 3, 8, 1, 0, 1], aqm_enabled=True
    )
    assert m_atf_20["legacy_share"] > 0.80
    assert m_atf_20["throughput"] > m_fifo_20["throughput"], "OpenWrt ATF phải bảo vệ thông lượng trước Heusse drag tốt hơn FIFO"
    assert m_atf_80["legacy_prot_overhead"] > m_atf_20["legacy_prot_overhead"], "Dup RTS/CTS ở HE80 phải tốn overhead cao hơn HE20"
    print(
        f"[PASS 3] Legacy Phase 4: share={m_atf_20['legacy_share']:.2f}, "
        f"ATF={m_atf_20['throughput']:.1f} > FIFO={m_fifo_20['throughput']:.1f} Mbps, "
        f"prot_HE80={m_atf_80['legacy_prot_overhead']:.3f} > prot_HE20={m_atf_20['legacy_prot_overhead']:.3f}."
    )


def test_oracle_optimality_and_sla_feasibility():
    env = UltimateDigitalTwinEnv(seed=777)
    max_gap = 0.0
    for idx in range(50):
        obs, sub_n, of_b, dm_s = env.sample_snapshot_scenario()
        opt_act, opt_m = UltimateDigitalTwinEnv.compute_optimal_targets(
            obs, aqm_enabled=True, sub_noise_dbm=sub_n, base_ofdma_capacity=of_b, demand_scale=dm_s
        )
        best_r = -1e9
        for cand in itertools.product(*(range(n) for n in ACTION_NVEC)):
            m = UltimateDigitalTwinEnv._original_evaluate_network_physics(
                obs, cand, aqm_enabled=True, sub_noise_dbm=sub_n, base_ofdma_capacity=of_b, demand_scale=dm_s
            )
            if m["reward"] > best_r:
                best_r = m["reward"]
        gap = abs(best_r - opt_m["reward"])
        max_gap = max(max_gap, gap)
        assert gap < 1e-4, f"Snapshot {idx}: compute_optimal_targets lệch nghiệm vét cạn 45,360 cấu hình: gap={gap}"

    sla_hits = 0
    for _ in range(1000):
        obs, sub_n, of_b, dm_s = env.sample_snapshot_scenario()
        _, opt_m = UltimateDigitalTwinEnv.compute_optimal_targets(
            obs, aqm_enabled=True, sub_noise_dbm=sub_n, base_ofdma_capacity=of_b, demand_scale=dm_s
        )
        if opt_m["p99_latency_ms"] <= QOE_LATENCY_MS and opt_m["packet_loss_pct"] < 1.0:
            sla_hits += 1
    sla_rate = 100.0 * sla_hits / 1000.0
    assert sla_rate >= 99.0, f"Oracle SLA khả thi quá thấp: {sla_rate:.2f}% (< 99.0%)"
    print(f"[PASS 4] compute_optimal_targets khớp 45,360 cấu hình (max_gap={max_gap:.2e}) | Oracle SLA={sla_rate:.2f}%.")


if __name__ == "__main__":
    test_phase1_band_isolation_and_primary_secondary_cca()
    test_phase3_jakes_csi_aging_continuity()
    test_phase4_legacy_heusse_and_protection_overhead()
    test_oracle_optimality_and_sla_feasibility()
    print("=== TẤT CẢ KIỂM THỬ LÕI VẬT LÝ V2 ĐẠT CHUẨN IEEE 802.11ax ===")

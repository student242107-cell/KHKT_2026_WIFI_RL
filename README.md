# HỆ THỐNG TỐI ƯU HÓA TÀI NGUYÊN VÔ TUYẾN COGNITIVE WI-FI ĐẦU CUỐI (KHKT 2026)
### *Quy hoạch Topology Vật lý (Ray-Tracing/FDTD) & Điều phối RRM Liên tầng 8D-State / 7D-Action bằng Học Tăng Cường Sâu PPO trên Phần cứng OpenWrt*

---

## MỤC LỤC
1. [Tổng quan Hệ thống, Triết lý Thượng Tôn Người Dùng & Mô hình Toán học $M \gg N$](#1-tổng-quan-hệ-thống-triết-lý-thượng-tôn-người-dùng--mô-hình-toán-học-m-gg-n)
2. [Bài toán 1: Quy hoạch Cấu trúc Không gian & Tự động Khởi tạo Hạ tầng (Zero-Touch Provisioning)](#2-bài-toán-1-quy-hoạch-cấu-trúc-không-gian--tự-động-khởi-tạo-hạ-tầng-zero-touch-provisioning)
3. [Bài toán 2: Mô hình Hóa Toán học POMDP, Công thức Vật lý 4 Tầng & Các Tối ưu Kỹ thuật trong Huấn luyện](#3-bài-toán-2-mô-hình-hóa-toán-học-pomdp-công-thức-vật-lý-4-tầng--các-tối-ưu-kỹ-thuật-trong-huấn-luyện)
4. [Các Điểm nhấn Công nghệ Phần cứng Thực tế Tích hợp trong Mã nguồn](#4-các-điểm-nhấn-công-nghệ-phần-cứng-thực-tế-tích-hợp-trong-mã-nguồn)
5. [Kết quả Kiểm chứng Thực nghiệm Đa Seed (5 Seeds × 360,000 Bước), Dữ liệu Thật Kaggle & Stress Test](#5-kết-quả-kiểm-chứng-thực-nghiệm-đa-seed-5-seeds--360000-bước-dữ-liệu-thật-kaggle--stress-test)
6. [Cấu trúc Thư mục Dự án](#6-cấu-trúc-thư-mục-dự-án)
7. [Hướng dẫn Triển khai Thực tiễn Từng Bước trên Phần cứng (Step-by-Step Deployment Guide)](#7-hướng-dẫn-triển-khai-thực-tiễn-từng-bước-trên-phần-cứng-step-by-step-deployment-guide)
   - [7.1. Chuẩn bị Môi trường & Cấu hình File `.env`](#71-chuẩn-bị-môi-trường--cấu-hình-file-env)
   - [7.2. Bước 0: Khảo sát Hiện trạng Mạng bằng USB Wi-Fi Monitor Mode (Chưa dùng AI) & Xuất Bản đồ Nhiệt `collision_heatmap.png`](#72-bước-0-khảo-sát-hiện-trạng-mạng-bằng-usb-wi-fi-monitor-mode-chưa-dùng-ai--xuất-bản-đồ-nhiệt-collision_heatmappng)
   - [7.3. Bước 1: Tự động Quét Topo (`auto_discover_aps.py`) & Thiết lập Hạ tầng (`setup_network.py`)](#73-bước-1-tự-động-quét-topo-auto_discover_apspy--thiết-lập-hạ-tầng-setup_networkpy)
   - [7.4. Bước 2: Khởi chạy Telemetry Dual-Band, Vệ sĩ An toàn (`failsafe_watchdog.py`) & Tác tử AI (`deploy_hil_agent.py`)](#74-bước-2-khởi-chạy-telemetry-dual-band-vệ-sĩ-an-toàn-failsafe_watchdogpy--tác-tử-ai-deploy_hil_agentpy)
   - [7.5. Bước 3: Triển khai `beacon_bait_daemon.py`, Packet Injection 25s & Chiến thuật Zero-Disruption VAP Pruning Mạng Giáo viên](#75-bước-3-triển-khai-beacon_bait_daemonpy-packet-injection-25s--chiến-thuật-zero-disruption-vap-pruning-mạng-giáo-viên)
   - [7.6. Bước 4: Cơ chế Vận hành Dual Fail-Safe (`Soft Rescue` & `Hard Reboot`) và Kết nối Ổ cắm Thông minh Sonoff S26 R2](#76-bước-4-cơ-chế-vận-hành-dual-fail-safe-soft-rescue--hard-reboot-và-kết-nối-ổ-cắm-thông-minh-sonoff-s26-r2)
   - [7.7. Bước 5: Ghi nhận & Xuất Báo cáo Đánh giá Định kỳ Hiệu năng QoE (`qoe_recorder.py` & `visualize_qoe.py`)](#77-bước-5-ghi-nhận--xuất-báo-cáo-đánh-giá-định-kỳ-hiệu-năng-qoe-qoe_recorderpy--visualize_qoepy)
   - [7.8. Bước 6: Hai Phương pháp Huấn luyện Lại và Tinh chỉnh Trọng số Tối ưu (`best_model.zip`)](#78-bước-6-hai-phương-pháp-huấn-luyện-lại-và-tinh-chỉnh-trọng-số-tối-ưu-best_modelzip)

---

## 1. TỔNG QUAN HỆ THỐNG, TRIẾT LÝ THƯỢNG TÔN NGƯỜI DÙNG & MÔ HÌNH TOÁN HỌC $M \gg N$

### 1.1. Bối cảnh và Bài toán Thực tiễn
Trong môi trường mật độ người dùng tập trung cao và biến động mạnh theo thời gian như trường học, giảng đường và hội trường lớn, mô hình mạng Wi-Fi truyền thống cấu hình tĩnh (**Hệ thống $N$**) bộc lộ 4 điểm nghẽn nghiêm trọng:
1. **Nhiễu đồng kênh (CCI) và nhiễu kênh phụ gộp (Secondary-Channel Jamming):** Cố định kênh và gộp băng thông `80 MHz` mù quáng khiến toàn bộ cụm kênh sụp đổ khi một kênh con `20 MHz` bị nhiễu.
2. **Bão va chạm CSMA/CA & Thăm dò tốc độ mù quáng (Minstrel Blind Probing):** Khi hàng trăm thiết bị di chuyển trong giờ giải lao, thuật toán Minstrel-HT tự do đẩy lên `MCS 10–11` ở vùng phai mờ (fading), làm tỷ lệ truyền lại (`Retry Rate`) và xác suất va chạm ($p_c$) vọt lên trên `50%`.
3. **Hiện tượng Phình bộ đệm (Bufferbloat) & Lão hóa kênh (CSI Aging):** Hàng đợi FIFO tích tụ hàng nghìn gói tin đẩy độ trễ đuôi (`P99 Latency`) lên `200 – 500 ms`; đồng thời các gói thăm dò *NDP Sounding* của Beamforming bị lỗi thời khi học sinh di chuyển nhanh làm lãng phí Airtime.
4. **Lãng phí Airtime do phát quảng bá SSID/Beacon dư thừa:** Các mạng chuyên biệt (như mạng Giáo viên `School_Teacher`) phát Beacon liên tục 24/7 ngay cả khi phòng học không có giáo viên.

Dự án **Cognitive Wi-Fi (KHKT 2026)** chuyển đổi hạ tầng mạng tĩnh ($N$) sang **Hệ sinh thái Nhận thức Thích ứng Thời gian thực Vận hành bằng Học Tăng Cường Sâu PPO ($M$)**, kết hợp quy hoạch vật lý trước triển khai với điều khiển vòng kín **Hardware-In-The-Loop (HIL)** trên Router thương mại chạy OpenWrt.

```text
       HẠ TẦNG TRUYỀN THỐNG (N - Tĩnh)                     HỆ THỐNG COGNITIVE WI-FI (M - Thích ứng)
+-------------------------------------------+      +----------------------------------------------------------+
| - Quy hoạch cảm tính, lắp dư thừa AP      |      | - Fast Profiling, Ray-Tracing & Phân vùng Zone Role      |
| - Kênh 36 / HE80 / Tx 20dBm / MCS 11 tĩnh | ---> | - PPO 8D-State -> 7D-Action (45,360 cấu hình RRM)        |
| - Bufferbloat hàng đợi FIFO (P99 > 200ms) |      | - Bộ ba MAC Guard: ATF + Dynamic AQL + FQ-CoDel (<15ms)  |
| - Phát Beacon Teacher 24/7 tốn Airtime    |      | - Zero-Disruption VAP Pruning + 802.11i RSN Baiting      |
| - Treo Router phải rút nguồn thủ công     |      | - Dual Fail-Safe: Soft Rescue & Hard Reboot Sonoff 220V  |
+-------------------------------------------+      +----------------------------------------------------------+
```

### 1.2. Triết lý Cốt lõi: “Thượng Tôn Chất Lượng Người Dùng” (User Quality Supremacy)
Mọi quyết định điều phối của AI và các mô-đun tiết kiệm tài nguyên (Green AI) đều phải tuân thủ tuyệt đối các **ràng buộc vận hành cứng (Hard Constraints)**:
- **Độ trễ đuôi phân vị thứ 99 ($\text{Latency}_{\text{P99}}$):** $\le 15.0\,\text{ms}$.
- **Tỷ lệ mất gói đường truyền ($\text{Packet Loss}$):** $< 1.0\%$.
- **Không gián đoạn kết nối hiện hữu:** Tuyệt đối không tắt giao diện mạng Giáo viên khi còn $\ge 1$ thiết bị giáo viên kết nối, và việc bật/tắt mạng Giáo viên không được gây rớt sóng dù chỉ `1 ms` cho mạng Học sinh.

### 1.3. Mô hình Toán học Chứng minh Ưu thế Hệ thống ($M \gg N$)

#### A. Chỉ số Hiệu quả Tổng thể ($\text{Index}_{VN}$)
$$\text{Index}_{VN} = \frac{\text{Throughput} \times (1 - \text{PER})}{\text{Latency}_{\text{P99}} \times (\text{CapEx} \times \text{OpEx}_{\text{Setup}})}$$

#### B. Hàm Tiện ích Tuyến tính Tổng hợp $U(X)$
$$U(X) = \alpha \cdot \text{Goodput} - \beta \cdot \text{Latency}_{\text{P99}} - \gamma \cdot \text{CapEx} - \delta \cdot \text{OpEx}, \quad (\beta \gg \alpha > 0)$$

Khi mật độ thiết bị và nhiễu tăng cao, $\text{Latency}_{\text{P99}}$ của hệ thống tĩnh $N$ bùng nổ do Bufferbloat làm $U(N)$ rơi xuống miền âm, trong khi hệ thống $M$ giữ vững $\text{Latency}_{\text{P99}} \le 15\,\text{ms}$ và đạt $U(M) \gg U(N)$.

---

## 2. BÀI TOÁN 1: QUY HOẠCH CẤU TRÚC KHÔNG GIAN & TỰ ĐỘNG KHỞI TẠO HẠ TẦNG (ZERO-TOUCH PROVISIONING)

### 2.1. Mô phỏng Truyền sóng Vật lý & Phân tầng Phần cứng ([`notebooks/01_topology_raytracing.ipynb`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/01_topology_raytracing.ipynb) & [`notebooks/meep_wifi_sim.ipynb`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/meep_wifi_sim.ipynb))
- Trích xuất cấu trúc tường, vật liệu cản sóng và mô phỏng trường điện từ **Ray-Tracing / FDTD** để định vị vị trí lắp đặt AP tối ưu, xóa bỏ vùng lõm sóng và tiết kiệm `> 20%` chi phí phần cứng (`CapEx`).
- Quy hoạch từng thiết bị OpenWrt trong [`configs/network_config.yaml`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/configs/network_config.yaml) theo **Phân vùng Chức năng (`zone_type`)**:
  1. **Vùng Phòng học (`classroom`):** Bật mạng Học sinh 5GHz (`default_radio1`) và 2.4GHz (`default_radio0`); bật mạng mở định danh `School_Onboarding` (`onboarding_2g`); **mặc định tắt giao diện VAP Giáo viên (`teacher_5g`)** chờ kích hoạt động khi giáo viên xuất hiện.
  2. **Vùng Văn phòng Giáo viên (`privileged_office`):** Luôn bật mạng Giáo viên (`School_Teacher` **Always-On**), tắt mạng Học sinh, và tự động kẹp công suất phát 5GHz ở mức **`12 dBm`** để chống tràn sóng (RF bleeding) ra hành lang.

### 2.2. Cổng Định danh Captive Portal Cô lập (Isolated Subnet DNS Hijacking) & Chuyển vùng 802.11r
Khác với kỹ thuật bắt cóc DNS toàn cục cũ dễ làm ảnh hưởng đến truy vấn DNS của mạng Học sinh, [`scripts/setup_network.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/scripts/setup_network.py) thiết lập một **kiến trúc mạng Onboarding cô lập hoàn toàn**:
- Tạo cầu nối riêng `br-onboard` (`onboard_net`, IP `192.168.199.1/24`, DHCP tầm ngắn `5m`).
- Khởi chạy một tiến trình `dnsmasq` độc lập lắng nghe riêng tại `192.168.199.1:5353` (`--address=/#/192.168.199.1`) và dùng luật Firewall `DNAT` chuyển hướng truy vấn DNS (`cổng 53 -> 5353`) và HTTP (`cổng 80 -> EDGE_SERVER_IP:80`) **chỉ dành riêng cho vùng `onboard`**.
- Dịch vụ Web [`src/onboarding/app.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/onboarding/app.py) bắt toàn bộ gói thăm dò Captive Portal của **Android** (`/generate_204`), **iOS/macOS** (`/hotspot-detect.html`), **Windows** (`/connecttest.txt`, `/ncsi.txt`) và **Firefox** (`/canonical.html`), tự động bật cửa sổ đăng nhập, tra cứu MAC thực qua bảng ARP/DHCP của Router Master, lưu vào [`data/processed/mac_vip_list.json`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/mac_vip_list.json), và sau 6 giây tự động đánh thức VAP `School_Teacher` đồng thời ngắt thiết bị khỏi mạng Onboarding để điện thoại chuyển vùng mượt sang mạng VIP.
- Đồng bộ chuẩn **IEEE 802.11r Fast BSS Transition (`mobility_domain='abcd'`, `ft_psk_generate_local='1'`)** trên toàn bộ các AP giúp thời gian chuyển vùng giữa các phòng học `< 20 ms`.

---

## 3. BÀI TOÁN 2: MÔ HÌNH HÓA TOÁN HỌC POMDP, CÔNG THỨC VẬT LÝ 4 TẦNG & CÁC TỐI ƯU KỸ THUẬT TRONG HUẤN LUYỆN

Toàn bộ lõi mô phỏng [`src/envs/digital_twin_env.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/envs/digital_twin_env.py), môi trường phần cứng [`src/envs/real_router_env.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/envs/real_router_env.py), bộ điều phối thực tế [`src/rl_agent/deploy_hil_agent.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/rl_agent/deploy_hil_agent.py) và pipeline huấn luyện [`notebooks/training_digital_twin.ipynb`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/training_digital_twin.ipynb) được đồng bộ hóa **1-1** về không gian trạng thái, không gian hành động, kiến trúc mạng trích xuất đặc trưng và hàm phần thưởng.

### 3.1. Không gian Trạng thái Cảm biến Kép 8 Chiều ($\mathcal{S}_t \in \mathbb{R}^8$) & Cửa sổ Thời gian `VecFrameStack(4)` ($\mathbb{R}^{32}$)

Tại mỗi bước thời gian $t$, vector quan sát cơ sở 8 chiều kết hợp **6 chỉ số tầng MAC/Ngữ cảnh** và **2 chỉ số tầng Vật lý (PHY Sensing)**:

$$\mathcal{S}_t = \Big[\,\text{avg\_rssi},\ \text{noise\_floor},\ \text{sta\_count},\ \text{retry\_rate},\ p_c,\ \text{airtime\_util},\ \text{ofdma\_ratio},\ \text{is\_break\_time}\,\Big]^\top$$

| Chiều | Ký hiệu trong Code | Miền giá trị `[OBS_LOW, OBS_HIGH]` | Tầng cảm biến | Công thức & Ý nghĩa Vật lý |
|:---:|---|:---:|:---:|---|
| `obs[0]` | `avg_rssi` | `[-90.0, -30.0]` dBm | MAC / PHY | Cường độ tín hiệu thu trung bình của các trạm đang kết nối (`iw station dump`). |
| `obs[1]` | `noise_floor` | `[-105.0, -50.0]` dBm | **PHY Sensing** | **Subchannel-Aware Noise Floor**: Mức nhiễu nền cực đại trên toàn cụm kênh con 20 MHz đang gộp (`occupied_subs` của `HE20/40/80`): $\text{Noise}_{\text{obs}}(t) = \max_{c \in \text{OccupiedSubs}} N_{\text{true}}(c, t)$. |
| `obs[2]` | `sta_count` | `[0.0, 300.0]` | MAC | Số lượng thiết bị Client đang kết nối thực tế tới AP. |
| `obs[3]` | `retry_rate` | `[0.0, 1.0]` | MAC | Tỷ lệ khung MPDU phải phát lại: $\frac{\Delta \text{tx\_retries}}{\Delta \text{tx\_packets} + \Delta \text{tx\_retries}}$. |
| `obs[4]` | `p_c` | `[0.0, 1.0]` | MAC | Xác suất va chạm gói tin CSMA/CA tổng hợp từ tỷ lệ lỗi phát và mật độ nghẽn Airtime. |
| `obs[5]` | `airtime_util` | `[0.0, 1.0]` | MAC | Tỷ lệ chiếm dụng kênh vô tuyến: $\max\!\left(\frac{\Delta t_{\text{busy}}}{\Delta t_{\text{active}}},\, \text{clip}\!\left(\frac{\text{PPS}}{7500}, 0, 0.98\right)\right)$. |
| `obs[6]` | `ofdma_ratio` | `[0.0, 1.0]` | **PHY Sensing** | Hiệu suất ghép khung đa truy cập phân chia tần số trực giao IEEE 802.11ax (`HE-MU / RU`). |
| `obs[7]` | `is_break_time` | `{0.0, 1.0}` | Context | Cờ ngữ cảnh lịch học (bằng `1.0` trong khung giờ giải lao/chuyển tiết cao điểm roaming, `0.0` trong giờ học). |

Để giải quyết tính quan sát một phần (POMDP) và nắm bắt tốc độ biến thiên của môi trường, 4 khung trạng thái liên tiếp được xếp chồng qua `VecFrameStack(n_stack=4)` tạo thành vector đầu vào **32 chiều**:
$$X_t = \big[\,\mathcal{S}_{t-3}^\top,\ \mathcal{S}_{t-2}^\top,\ \mathcal{S}_{t-1}^\top,\ \mathcal{S}_t^\top\,\big]^\top \in \mathbb{R}^{32}$$

---

### 3.2. Không gian Hành động Đa chiều 7 Vũ khí RRM ($\mathcal{A}_t \in \text{MultiDiscrete}([9, 3, 7, 12, 2, 5, 2])$)

Tác tử PPO điều khiển đồng thời **7 chiều hành động rời rạc** với tổng cộng **$9 \times 3 \times 7 \times 12 \times 2 \times 5 \times 2 = 45,360$ cấu hình RRM**:

$$\mathcal{A}_t = \big[\,a_{\text{ch}},\ a_{\text{bw}},\ a_{\text{tx}},\ a_{\text{mcs}},\ a_{\text{bcn}},\ a_{\text{obss}},\ a_{\text{bf}}\,\big]$$

| Chiều | Vũ khí RRM | Số nấc | Tập giá trị Vật lý ánh xạ | Cơ chế Thực thi trên Phần cứng OpenWrt (`mt76` / `nl80211`) |
|:---:|---|:---:|---|---|
| `act[0]` | **Channel** | `9` | `5GHz Non-DFS`: `[36, 40, 44, 48, 149, 153, 157, 161, 165]`<br>*(2.4GHz map sang `[1, 6, 11]`)* | `uci set wireless.radio1.channel='<CH>'` (Gộp nhóm `RELOAD_DIMS`). |
| `act[1]` | **Bandwidth** | `3` | `["HE20", "HE40", "HE80"]` (`20 / 40 / 80 MHz`) | `uci set wireless.radio1.htmode='<BW>'` (Tự động hạ về `HE20` nếu `CH == 165`). |
| `act[2]` | **Tx Power** | `7` | `[14, 15, 16, 17, 18, 19, 20]` dBm ($P_{\text{tx}} = 14 + a_{\text{tx}}$) | **Áp nóng (Zero-Downtime):** `iw dev phy1-ap0 set txpower fixed <mBm>`. |
| `act[3]` | **MCS Ceiling** | `12` | `HE-MCS 0 .. 11` | **Áp nóng (Zero-Downtime):** `iw dev phy1-ap0 set bitrates he-mcs-5 1:0-<MCS> 2:0-<MCS>` (nếu `MCS=11` gỡ giới hạn). |
| `act[4]` | **Beacon Interval** | `2` | `[80, 300]` ms | `uci set wireless.radio1.beacon_int='<80|300>'`. |
| `act[5]` | **OBSS-PD Reuse** | `5` | Offset `[0, 5, 10, 15, 20]` dB $\leftrightarrow$ Ngưỡng `[-82, -77, -72, -67, -62]` dBm | `uci set wireless.radio1.he_spr_sr_control='4'` & `he_spr_non_srg_obss_pd_max_offset='<off>'`. |
| `act[6]` | **SU-Beamforming** | `2` | `[0 (OFF), 1 (ON)]` | `uci set wireless.radio1.he_su_beamformer='<0|1>'`. |

---

### 3.3. Kiến trúc Mạng Trích xuất Đặc trưng `NormalizedWifiFeaturesExtractor` (Gaussian RBF + Temporal-Delta)

Một phát hiện kỹ thuật quan trọng trong quá trình tối ưu hóa huấn luyện là: nếu đưa thẳng toàn bộ vector `32D` của `VecFrameStack(4)` qua một lớp `Linear(32, 64) + LayerNorm` phẳng, các khung quá khứ $\mathcal{S}_{t-3..\,t-1}$ sẽ làm nhòe (frame smearing) trạng thái hiện tại $\mathcal{S}_t$, đồng thời hiện tượng thiên lệch phổ (spectral bias) của mạng MLP khiến chính sách khó phân giải chính xác 12 nấc bậc thang `HE-MCS` và 7 nấc `Tx Power`.

Hệ thống khắc phục triệt để bằng kiến trúc **`NormalizedWifiFeaturesExtractor`** ([`src/envs/digital_twin_env.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/envs/digital_twin_env.py#L122-L168)):
1. **Chuẩn hóa Min-Max về $[-1.0, 1.0]$:**
   $$\tilde{\mathcal{S}}_t = \text{clip}\!\left(2.0 \cdot \frac{\mathcal{S}_t - \text{OBS\_LOW}}{\text{OBS\_HIGH} - \text{OBS\_LOW} + 10^{-6}} - 1.0,\ -1.0,\ 1.0\right) \in \mathbb{R}^8$$
2. **Mở rộng Cơ sở Xuyên tâm Gaussian RBF (8 tâm đều $\mu_k \in \text{linspace}(-1, 1, 8)$ theo Sutton & Barto Ch. 9.5):**
   $$\phi_{i,k}\big(\tilde{\mathcal{S}}_{t,i}\big) = \exp\!\Big(-10.0\,\big(\tilde{\mathcal{S}}_{t,i} - \mu_k\big)^2\Big) \in \mathbb{R}^{8 \times 8} \implies z_t = \Big[\,\tilde{\mathcal{S}}_t \;\Big\|\; \text{vec}\big(\Phi(\tilde{\mathcal{S}}_t)\big)\,\Big] \in \mathbb{R}^{72}$$
3. **Tổng hợp Nhánh Hiện tại (Residual Skip + 3-Layer SiLU MLP) và Nhánh Vi phân Thời gian ($\Delta \tilde{\mathcal{S}}_t = \tilde{\mathcal{S}}_t - \tilde{\mathcal{S}}_{t-1}$):**
   $$h_t = W_{\text{skip}}\,z_t + \text{MLP}_{\text{cur}}(z_t) + 0.15 \cdot \text{MLP}_{\Delta}\big(\tilde{\mathcal{S}}_t - \tilde{\mathcal{S}}_{t-1}\big) \in \mathbb{R}^{64}$$
   Vector đặc trưng $h_t \in \mathbb{R}^{64}$ sau đó đi vào 2 mạng Actor-Critic độc lập `pi = [256, 256]` và `vf = [256, 256]`.

---

### 3.4. Hệ thống Công thức Vật lý Viễn thông 4 Tầng (`_original_evaluate_network_physics`)

Lõi vật lý nhân quả 4 tầng được sử dụng thống nhất để tính toán trạng thái kế tiếp và phần thưởng trong cả mô phỏng lẫn Fine-Tuning thực tế:

#### Tầng 1: Phổ tần 9 Kênh Non-DFS Đối xứng & Gộp kênh (Channel Bonding)
- Hàm `resolve_valid_5g_channel_and_bw(ch_idx, bw_idx)` xác định tập kênh con 20 MHz bị chiếm dụng $\mathcal{C}_{\text{occ}}$ (1 kênh cho `HE20`, 2 kênh cho `HE40`, 4 kênh cho `HE80`; riêng Kênh 165 chỉ cho phép `HE20`).
- Công suất nhiễu gộp trên toàn bộ các kênh con trong $\mathcal{C}_{\text{occ}}$ được cộng trên thang tuyến tính mW kèm hệ số mở rộng băng thông ($\Delta_{\text{bw}} = 0\,\text{dB}$ cho `HE20`, $+3.0\,\text{dB}$ cho `HE40`, $+6.0\,\text{dB}$ cho `HE80`):
  $$N_{\text{bonded, dBm}} = 10 \log_{10}\!\left(\frac{1}{|\mathcal{C}_{\text{occ}}|}\sum_{c \in \mathcal{C}_{\text{occ}}} 10^{N(c)/10}\right) + \Delta_{\text{bw}}, \qquad N_{\text{max, occ}} = \max_{c \in \mathcal{C}_{\text{occ}}} N(c)$$

#### Tầng 2: Quỹ Đường truyền (Link Budget), Lão hóa Kênh CSI Aging (Beamforming) & Tái sử dụng Không gian OBSS-PD
- **Chế độ Lão hóa Kênh (CSI Aging Regime):** Xảy ra khi mật độ thiết bị và độ di động vượt ngưỡng làm ma trận phản hồi CSI lỗi thời trước khi khung dữ liệu phát đi:
  $$\text{IsCSIAging} = (\text{STA} > 95) \;\lor\; (\text{IsBreak} = 1 \land \text{STA} > 58) \;\lor\; (\text{STA} > 70 \land \text{Retry}_{\text{obs}} > 0.30)$$
  Độ lợi hữu ích của SU-Beamforming ($G_{\text{BF}}$):
  $$G_{\text{BF}} = \begin{cases} -2.5\,\text{dB} & \text{nếu } \text{IsCSIAging} = \text{True} \land a_{\text{bf}} = 1 \text{ (Phạt lỗi pha CSI \& tốn NDP Sounding)} \\ +1.4\,\text{dB} & \text{nếu } \text{IsCSIAging} = \text{True} \land a_{\text{bf}} = 0 \text{ (Thưởng cắt bỏ Sounding Overhead)} \\ +2.8\,\text{dB} & \text{nếu } \text{IsCSIAging} = \text{False} \land a_{\text{bf}} = 1 \text{ (Độ lợi tập trung búp sóng SU-BF)} \\ -1.2\,\text{dB} & \text{nếu } \text{IsCSIAging} = \text{False} \land a_{\text{bf}} = 0 \end{cases}$$
- **Tỷ số Tín hiệu trên Nhiễu và Can nhiễu Hiệu dụng ($\text{SINR}_{\text{eff}}$):**
  $$\text{SINR}_{\text{eff}} = \text{RSSI}_{\text{comp}} + G_{\text{BF}} - N_{\text{eff, dBm}} - M_{\text{fading}} - L_{\text{OBSS\_leak}} - 0.35\,P_{\text{CCA\_deferral}}$$

#### Tầng 3: Điều chế Thích ứng Minstrel-HT (`mcs_ceiling`) & Va chạm CSMA/CA Bianchi
- Mức điều chế an toàn tối đa ($\text{MCS}_{\text{safe}} \in \{0..11\}$) được tra từ bảng ngưỡng $\text{SINR}_{\text{req}}$ chuẩn IEEE 802.11ax với biên dự trữ $6.5\,\text{dB}$.
- Khi kênh có rủi ro phai mờ động, bộ điều khiển Minstrel-HT tự nhiên có xu hướng thử nghiệm vọt lố $\text{MCS}_{\text{natural}} = \min(11,\, \text{MCS}_{\text{safe}} + \Delta_{\text{overshoot}})$. Vũ khí `mcs_ceiling` ($a_{\text{mcs}}$) khóa chặn trên:
  $$\text{MCS}_{\text{op}} = \min\!\big(a_{\text{mcs}},\ \text{MCS}_{\text{natural}}\big)$$
- Tỷ lệ lỗi gói tầng vật lý ($\text{PER}_{\text{phy}}$) được neo vào biên SINR $\Delta_{\text{SINR}} = \text{SINR}_{\text{eff}} - \text{SINR}_{\text{req}}(\max(\text{MCS}_{\text{op}}, \text{MCS}_{\text{safe}}))$ kèm phạt thăm dò quá trần và phạt kéo giãn airtime khi đặt trần quá thấp:
  $$\text{PER}_{\text{phy}} = \text{clip}\!\left(\frac{1}{1 + \exp(0.95(\Delta_{\text{SINR}} - 2.1))} + 0.032\,\Delta_{\text{overreach}}^2 + 0.022\max(0, a_{\text{mcs}} - \text{MCS}_{\text{safe}}) + 0.014\max(0, \text{MCS}_{\text{safe}} - a_{\text{mcs}}),\ 0.002,\ 0.96\right)$$

#### Tầng 4: Thông lượng Hữu ích Goodput (Chuẩn IETF RFC 2647), Hàng đợi M/G/1 AQM & Độ trễ Đuôi Gamma Wilson-Hilferty P99
- **Nguyên lý Bảo toàn Lưu lượng (Flow Conservation Bound) & Goodput chuẩn IETF RFC 2647:**
  $$\text{Goodput}_{\text{raw}} = \min\!\big(\text{OfferedLoad}_{\text{Mbps}},\ \text{ServiceCapacity}_{\text{Mbps}}\big)$$
  $$\text{Goodput}_{\text{Mbps}} = \text{Goodput}_{\text{raw}} \times \max\!\left(0.15,\ (1 - \text{LossRatio})\left(1 - 0.16\,\min\!\left(1.0,\ \frac{\max(0.0,\, \text{Latency}_{\text{P99}} - 15.0)}{85.0}\right)\right)\right)$$
- **Độ trễ Phân vị thứ 99 ($\text{Latency}_{\text{P99}}$) theo Phân phối Gamma Dịch chuyển $X = d_{\min} + \text{Gamma}(k, \theta)$ (Phép biến đổi Wilson-Hilferty):**
  Với độ trễ sàn phần cứng $d_{\min} = 1.50\,\text{ms}$, kỳ vọng độ trễ $\mu_L$ (từ mô hình hàng đợi M/G/1 có kiểm soát AQM `FQ-CoDel + AQL` so với FIFO Bufferbloat) và độ lệch chuẩn $\sigma_{\text{jitter}}$:
  $$k = \max\!\left(0.25,\ \left(\frac{\mu_L - d_{\min}}{\sigma_{\text{jitter}}}\right)^2\right), \qquad \theta = \frac{\mu_L - d_{\min}}{k}$$
  $$\text{Latency}_{\text{P99}} = d_{\min} + k\theta \cdot \max\!\left(0.22,\ 1 - \frac{1}{9k} + \frac{2.32635}{3\sqrt{k}}\right)^3$$

---

### 3.5. Hàm Phần thưởng Đa Mục tiêu & Phạt Quá độ Chuyển mạch Phần cứng (`evaluate_network_physics_with_downtime`)

Phần thưởng tức thời $R_t$ kết hợp 8 thành phần vật lý nhân quả và chi phí chuyển mạch phần cứng:

$$R_t = R_{\text{tput}} + R_{\text{AMC}} + R_{\text{link}} - P_{\text{collision}} - P_{\text{latency}} - P_{\text{loss}} + B_{\text{SLA}} + P_{\text{downtime/flapping}}$$

Trong đó:
- **Điểm thông lượng & Hiệu suất phổ ($R_{\text{tput}}$):**
  $$R_{\text{tput}} = 2.15 \cdot \frac{\text{Goodput}}{\max(\text{OfferedLoad}, 1.0)} + 0.72 \cdot \frac{\text{Goodput}}{100.0} + \min\!\left(0.95,\ 0.55 \cdot \frac{\text{ServiceCapacity}}{\max(\text{OfferedLoad}, 20.0)}\right)$$
- **Thưởng ổn định điều chế AMC ($R_{\text{AMC}}$):**
  $$R_{\text{AMC}} = 0.92\,\exp\!\big(-0.80\,(a_{\text{mcs}} - \text{MCS}_{\text{safe}})^2\big)$$
- **Thưởng đồng bộ Liên tầng 6 Vũ khí Phụ trợ ($R_{\text{link}}$):** Phạt sai lệch công suất phát, rò rỉ OBSS-PD, sai lệch băng thông tối ưu $|a_{\text{bw, actual}} - a_{\text{bw, opt}}|$, và thưởng $+0.42$ cho lựa chọn đúng chu kỳ Beacon theo `is_break_time` cũng như bật/tắt đúng Beamforming theo chế độ `CSI Aging`.
- **Phạt Va chạm, Độ trễ P99 & Mất gói ($P_{\text{collision}}, P_{\text{latency}}, P_{\text{loss}}$) + Thưởng đạt chuẩn SLA ($B_{\text{SLA}}$):**
  $$P_{\text{collision}} = 3.9\,p_c^2 + 1.8\,\text{PER}_{\text{phy}}, \qquad P_{\text{latency}} = 0.038\,\min(\text{Latency}_{\text{P99}}, 60) + 1.20\,\tanh\!\left(\frac{\max(0, \text{Latency}_{\text{P99}} - 15.0)}{16.0}\right)$$
  $$P_{\text{loss}} = 3.2\,\text{LossRatio} + 0.60\,\mathbb{I}(\text{LossPct} \ge 1.0\%), \qquad B_{\text{SLA}} = \begin{cases} +0.60 & \text{nếu } \text{Latency}_{\text{P99}} \le 15.0\,\text{ms} \land \text{LossPct} < 1.0\% \\ -0.40 & \text{ngược lại} \end{cases}$$
- **Phạt Chuyển mạch Phần cứng (`wifi reload` Downtime & Action Flapping):**
  Khi thay đổi kênh ($a_{\text{ch}} \ne a_{\text{ch, prev}}$) hoặc băng thông ($a_{\text{bw}} \ne a_{\text{bw, prev}}$), lệnh `wifi reload` trên phần cứng gây ngắt quãng truyền dẫn ngắn. Nếu độ lợi vật lý thuần túy $\Delta R_{\text{phys}} \le 0.35$ (đổi kênh/băng thông vô ích khi kênh đang sạch), hệ thống áp phạt $P_{\text{flapping}} = -0.85$ (đổi kênh) và $-0.55$ (đổi băng thông), đồng thời mô phỏng chính xác mức sụt giảm Goodput tạm thời và độ trễ quá độ.

<<<<<<< HEAD
### 3.6. Các Tối ưu Kỹ thuật Đột phá trong Huấn luyện PPO (`notebooks/training_digital_twin.ipynb`)
1. **Loại bỏ Biến Ẩn Rò rỉ trong Mục tiêu Vũ khí:** Toàn bộ các ngưỡng mục tiêu của `Bandwidth`, `Tx Power`, `MCS Ceiling`, `OBSS-PD` được định nghĩa hoàn toàn dựa trên vector quan sát được $\mathcal{S}_t$, giúp hàm chính sách $\pi_\theta(a|s)$ đạt tính nhất quán Bayes 100%.
2. **Cố định Kịch bản Đánh giá Common Random Numbers (CRN) trong `EvalCallback`:** Khi `EvalCallback` bắt đầu mỗi kỳ chấm điểm mới ngay sau auto-reset (`current_step == 0`), bộ sinh số ngẫu nhiên `self.rng` của môi trường đánh giá tự động quay về `self.base_seed`. Nhờ đó, cả 10 kỳ đánh giá dọc theo `360,000` bước đều thi đấu trên **cùng bộ 10 episodes chuẩn**, ngăn chặn triệt để hiện tượng một checkpoint non ở đầu quá trình train vô tình gặp chuỗi episode dễ rồi chiếm giữ `best_model.zip`.
3. **Bộ Siêu tham số PPO Hội tụ Nhanh:** Huấn luyện **`360,000` bước/seed** trên 5 Seeds độc lập (`[11, 22, 33, 44, 55]`), `n_steps = 1024` (`4` luồng = `4,096` bước/rollout), `batch_size = 256`, `n_epochs = 8`, hệ số chiết khấu tầm ngắn-trung `gamma = 0.55`, `gae_lambda = 0.80`, `ent_coef = 0.001` và lịch giảm tốc độ học `piecewise_lr_schedule(4.0e-4 -> 1.5e-5)`.
=======
Ở hệ thống tĩnh ($N$), để chống nghẽn, người quản trị thường lắp dày đặc các AP đắt tiền một cách dư thừa, làm $\text{CapEx}$ và $\text{OpEx}_{\text{Setup}}$ tăng phi mã, nhưng lại làm trầm trọng thêm nhiễu đồng kênh, đẩy $\text{Latency}$ lên cao và hạ thấp $\text{Reliability}$. Ngược lại, Hệ thống $M$ phân hạng AP tối ưu theo mật độ thực, dẹp bỏ Bufferbloat bằng AQL/FQ-CoDel và điều phối phổ bằng AI, dẫn đến:

$$
\text{Index}_{VN}(M) \gg \text{Index}_{VN}(N)
$$

#### 2. Hàm Tiện ích Tuyến tính Tổng hợp $U(X)$ và Động lực Phạt Phi Lồi

Hàm tiện ích hệ thống $U(X)$ đánh giá sự đánh đổi toàn diện giữa các tham số trên cấu hình $X \in \{M, N\}$:

$$
Score = (w_1 \times Spatial\_Throughput) + (w_2 \times Coverage\_Ratio) - (w_3 \times CapEx) + (w_4 \times UQS)
$$

Với $w_4$ sẽ được gán với giá trị lớn nhất trong 4 trọng số (phản ánh đúng triết lý Thượng Tôn Trải Nghiệm Người Dùng).

```text
   Tiện ích U(X)
        ^
        |          Hệ thống M (Cognitive AI + AQL + Dynamic Pruning)
   +U_M |..........===============================> Giữ vững Latency_99 <= 15 ms
        |         /
        |        /
      0 +-------/------------------------------------> Mật độ thiết bị / Tải mạng
        |      /
        |     /     Hệ thống N (Hạ tầng tĩnh truyền thống)
   -U_N |..../......-----------------------------\  Độ trễ đuôi bùng nổ (Bufferbloat)
        |   /                                     \ (Latency_99 >> 15 ms)
        v  /                                       v Sụp đổ tiện ích phi lồi (Collapse)
```

Khi mật độ người dùng tăng vọt (ví dụ giờ ra chơi), CSMA/CA truyền thống ở hệ thống $N$ bị nghẽn mạch, hàng đợi ngập tràn gây Bufferbloat khiến $\text{Latency}_{99} \gg 15\,\text{ms}$, kéo $U(N)$ rơi thẳng đứng xuống miền âm (sụp đổ tiện ích). Hệ thống $M$ kích hoạt AQL giới hạn thời gian giữ sóng, điều chỉnh băng thông (HE20/HE40/HE80) và chuyển kênh tự động bằng PPO, duy trì $\text{Latency}_{99} \le 15\,\text{ms}$ trong mọi điều kiện tải.
>>>>>>> 75a9f2f (Tạo graph cho các AP trong bài toán 1 và thuật toán tham lam)

---

## 4. CÁC ĐIỂM NHẤN CÔNG NGHỆ PHẦN CỨNG THỰC TẾ TÍCH HỢP TRONG MÃ NGUỒN

1. **`StatefulChannelGuardWrapper` — Cách ly Cụm Kênh Gộp 40/80 MHz & Thoát Nhiễu 1 Bước trên 9 Kênh Non-DFS:**
   - Khi phát hiện nhiễu trên dải subchannel đang dùng (`noise_floor > -84.0 dBm`), Guardrail lập tức cách ly cả cặp kênh `40 MHz` (`ch_idx ^ 1`) hoặc toàn bộ khối 4 kênh `80 MHz UNII-1 / UNII-3` (khi `noise_floor > -75.0 dBm`), giúp tác tử thoát khỏi đám nhiễu băng rộng chỉ trong **1 bước duy nhất**.
   - Sử dụng bộ xoay vòng toàn cục `_ESCAPE_ROTATION = (4, 0, 6, 2, 5, 1, 7, 3, 8)` kích hoạt đồng đều cả **9/9 kênh 5GHz Non-DFS** (`36..165`) và khóa giữ kênh khi kênh hiện tại vẫn sạch (`<= -84.0 dBm`), triệt tiêu hoàn toàn hiện tượng nhảy kênh Ping-Pong (`PingPong = 0.23%`).
2. **`HardwareAwareWrapper` — Đa hình Phần cứng Tự Nhận thức 3 Thế hệ Router:**
   - Tự động kiểm tra năng lực phần cứng và gọt hành động an toàn:
     - **`WIFI_6` (802.11ax — Xiaomi AX3000T MediaTek MT7981B):** Mở toàn bộ **7/7 vũ khí** (`HE80`, `MCS 0..11`, `OBSS-PD`, `SU-Beamforming`).
     - **`WIFI_5_AC` (802.11ac):** Mở **6/7 vũ khí** (khóa `MCS <= 9`, đưa `OBSS-PD` về mặc định `0`).
     - **`WIFI_4_LEGACY` (802.11n):** Mở **5/7 vũ khí** (gọt băng thông `<= HE40`, khóa `MCS <= 7`, tắt `OBSS-PD` và tắt `Beamforming`).
3. **Thực thi Nguyên tử `apply_ai_profile_atomic()` & Tách biệt Lệnh Áp Nóng (Zero-Downtime) vs. Lệnh Reload:**
   - Trong [`src/modules/wifi_actuators.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/modules/wifi_actuators.py), lệnh `wifi reload` **chỉ được gọi khi ít nhất một trong 5 thông số UCI (`channel`, `htmode`, `beacon_int`, `he_su_beamformer`, `he_spr_non_srg_obss_pd_max_offset`) thực sự thay đổi** so với giá trị đang chạy trên Router.
   - Hai vũ khí tinh chỉnh liên tục là **`Tx Power`** (`iw dev phy1-ap0 set txpower fixed <mBm>`) và **`HE-MCS Ceiling`** (`iw dev phy1-ap0 set bitrates he-mcs-5 1:0-<MCS> 2:0-<MCS>`) được **áp nóng trực tiếp xuống kernel `nl80211` trong 0 ms** mà không làm ngắt kết nối.
4. **Bộ Ba Vũ khí Tầng MAC Chống Bufferbloat (`ATF + Dynamic Adaptive AQL + FQ-CoDel`):**
   - **Airtime Fairness (ATF):** Bật trên cả UCI và `/sys/kernel/debug/ieee80211/phy1/airtime_fairness`.
   - **Dynamic Adaptive AQL (`compute_adaptive_aql`):** Tự động siết giới hạn hàng đợi phần cứng `mt76` (`aql_txq_limit` & `aql_threshold`) xuống **`1200 us`** ngay khi phát hiện nghẽn (`p_c > 0.20` hoặc `airtime_util > 0.50` hoặc `sta_count >= 80`) để cắt đứt Bufferbloat; mở rộng lên `1600 / 2400 / 3200 us` tương ứng với `HE20 / HE40 / HE80` khi kênh thoáng để tối đa hóa ghép khung A-MPDU.
   - **FQ-CoDel AQM:** Áp đặt hàng đợi công bằng `tc qdisc replace dev <iface> root fq_codel limit 2048 target 5ms interval 50ms ecn` trên mọi giao diện `phy1-ap*` và `br-lan`.
5. **Bộ lọc Trễ Vùng Chết Đa Vũ khí (Multi-Weapon Schmitt-Trigger Deadband Hysteresis) & Chống CCI Đa AP:**
   - Trong [`src/rl_agent/deploy_hil_agent.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/rl_agent/deploy_hil_agent.py), `HILCoordinator` phân bổ kênh trực giao giữa nhiều AP (`occupied_channels`), cách ly kênh nhiễu trong `30 phút` (`CHANNEL_QUARANTINE_SEC = 1800`), và yêu cầu xác nhận `DEBOUNCE_CYCLES = 2` cùng thời gian neo tối thiểu `MIN_DWELL_CYCLES = 3` trước khi cho phép `wifi reload` (tự động bỏ qua trễ khi xảy ra tình huống khẩn cấp `p_c > 0.60` hoặc `noise_floor > -70 dBm`).

---

## 5. KẾT QUẢ KIỂM CHỨNG THỰC NGHIỆM ĐA SEED (5 SEEDS × 360,000 BƯỚC), DỮ LIỆU THẬT KAGGLE & STRESS TEST

Toàn bộ kết quả và 5 biểu đồ chuẩn 300 DPI dưới đây được xuất tự động từ lần chạy hoàn chỉnh (**Run All**) của [`notebooks/training_digital_twin.ipynb`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/training_digital_twin.ipynb) và lưu trữ tại [`notebooks/KHKT_training_digital_twin_8DState_7DAct/`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct).

### 5.1. Bảng 1: Đối chứng Closed-Loop Optimality, Cam kết QoE SLA, Độ sạch kênh & Tần suất chuyển mạch (`5 Seeds ± 95% CI`)
> File CSV: [`table1_optimality_sla_multiseed_ci95.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct/summary_reports/table1_optimality_sla_multiseed_ci95.csv) | Biểu đồ: [`learning_curve_plot.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct/wifi_rl_logs/learning_curve_plot.png)

| Seed | Optimality Dual (M) | Optimality MAC-Only | Optimality Heuristic | Optimality Static (N) | QoE SLA Oracle | QoE SLA Dual (M) | QoE SLA MAC-Only | QoE SLA Heuristic | QoE SLA Static (N) | CleanCh Dual (%) | CleanCh MAC-Only (%) | Sw/Ep Dual | Sw/Ep Oracle |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `seed 11` | 98.26% | 94.68% | 58.35% | 0.00% | 100.00% | 98.83% | 96.73% | 58.00% | 1.27% | 99.50% | 97.13% | 14.10 | 33.20 |
| `seed 22` | 98.17% | 95.24% | 61.79% | 0.00% | 100.00% | 99.03% | 97.77% | 63.00% | 0.23% | 99.70% | 98.63% | 14.50 | 34.00 |
| `seed 33` | 97.56% | 94.16% | 59.57% | 0.00% | 100.00% | 97.77% | 95.70% | 58.57% | 0.00% | 98.70% | 96.50% | 20.00 | 38.70 |
| `seed 44` | 98.28% | 94.55% | 61.59% | 0.00% | 100.00% | 99.10% | 97.73% | 66.67% | 0.23% | 99.60% | 98.20% | 12.90 | 30.90 |
| `seed 55` | 98.38% | 94.27% | 58.96% | 0.00% | 100.00% | 99.10% | 95.27% | 57.10% | 0.27% | 99.63% | 95.30% | 15.60 | 39.90 |
| **Trung bình ± 95% CI** | **98.13 ± 0.40%** | **94.58 ± 0.52%** | **60.05 ± 1.93%** | **0.00 ± 0.00%** | **100.00 ± 0.00%** | **98.77 ± 0.71%** | **96.64 ± 1.42%** | **60.67 ± 5.03%** | **0.40 ± 0.62%** | **99.43 ± 0.51%** | **97.15 ± 1.66%** | **15.42 ± 3.40** | **35.34 ± 4.74** |

---

### 5.2. Bảng 2A & 2B: Độ trễ `Pooled System P99`, Packet Loss, Goodput & Hiệu suất Đạt Trần Lý thuyết Oracle (`%Ora`)
> File CSV: [`table2_p99_loss_goodput_multiseed_ci95.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct/summary_reports/table2_p99_loss_goodput_multiseed_ci95.csv) | Biểu đồ 4 khung: [`p99_histogram_ecdf_and_multiseed_ci95.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct/summary_reports/p99_histogram_ecdf_and_multiseed_ci95.png)

| Seed | Pooled P99 Dual (M) | Pooled P99 MAC-Only | Pooled P99 Heuristic | Pooled P99 Static (N) | Step Mean-P99 Dual (M) | Loss Dual (M) | Loss MAC-Only | Loss Heuristic | Loss Static (N) | Goodput Dual (M) | Attainment Dual (%Ora) | Attainment MAC (%Ora) | Attainment Heur (%Ora) | Attainment Static (%Ora) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `seed 11` | **6.67 ms** | 18.64 ms | 25.70 ms | 185.13 ms | 3.57 ms | **0.34%** | 1.33% | 10.42% | 73.84% | 39.55 Mbps | **97.44%** | 95.27% | 79.04% | 21.15% |
| `seed 22` | **5.58 ms** | 16.12 ms | 26.67 ms | 179.10 ms | 3.60 ms | **0.27%** | 1.00% | 9.94% | 76.21% | 42.22 Mbps | **97.53%** | 95.76% | 81.07% | 16.49% |
| `seed 33` | **17.48 ms** | 21.81 ms | 27.08 ms | 242.80 ms | 3.86 ms | **0.84%** | 1.79% | 11.70% | 77.92% | 44.49 Mbps | **95.85%** | 93.55% | 77.10% | 15.71% |
| `seed 44` | **5.42 ms** | 18.20 ms | 26.62 ms | 247.80 ms | 3.50 ms | **0.24%** | 1.14% | 10.13% | 71.05% | 45.60 Mbps | **97.62%** | 95.85% | 81.20% | 21.09% |
| `seed 55` | **5.76 ms** | 19.32 ms | 25.78 ms | 252.51 ms | 3.48 ms | **0.25%** | 1.32% | 10.14% | 74.10% | 41.10 Mbps | **98.64%** | 95.65% | 83.97% | 21.76% |
| **TB ± 95% CI** | **8.18 ± 6.49 ms** | **18.82 ± 2.55 ms** | **26.37 ± 0.75 ms** | **221.47 ± 44.89 ms** | **3.60 ± 0.19 ms** | **0.39 ± 0.32%** | **1.32 ± 0.37%** | **10.47 ± 0.88%** | **74.62 ± 3.23%** | **42.59 ± 3.05 Mbps** | **97.42 ± 1.24%** | **95.22 ± 1.19%** | **80.48 ± 3.20%** | **19.24 ± 3.59%** |

*(Trần Lý thuyết Toàn tri Simulator Oracle theo nguyên lý bảo toàn lưu lượng IEEE/IETF Flow Conservation Bound = **`43.73 Mbps`** ứng với `100.0% Attainment`, `Pooled P99 Oracle = 4.76 ms`, `Loss Oracle = 0.01%`).*

---

### 5.3. Bảng 3: Kiểm định Độ phủ & Độ chính xác có Điều kiện trên 7 Vũ khí RRM (`3,000` Bước Closed-Loop)

| STT | Vũ khí Điều phối RRM | Số nấc kích hoạt | Tỷ lệ Khớp chuẩn (`Exact Match`) | Tỷ lệ Khớp trong Dung sai 1 nấc (`Tol ±1`) | Chỉ số Bổ trợ |
|:---:|---|:---:|:---:|:---:|---|
| 1 | **Channel (9 kênh 5GHz Non-DFS)** | **`9 / 9`** | **`99.5%`** | **`99.6%`** | `CleanCh = 99.5%`, `PingPong = 0.23%` |
| 2 | **Bandwidth (`HE20 / HE40 / HE80`)** | **`3 / 3`** | **`86.9%`** | **`99.5%`** | Tự hạ về `HE20` trên Kênh 165 |
| 3 | **Tx Power (`14 .. 20 dBm`)** | **`7 / 7`** | **`96.1%`** | **`100.0%`** | Áp nóng `iw set txpower fixed` |
| 4 | **MCS Ceiling (`HE-MCS 0 .. 11`)** | **`11 / 12`** | **`73.2%`** | **`88.9%`** | Chặn Minstrel blind probing |
| 5 | **Beacon Interval (`80 / 300 ms`)** | **`2 / 2`** | **`100.0%`** | **`100.0%`** | Thích ứng lịch `is_break_time` |
| 6 | **OBSS-PD Spatial Reuse (`-82 .. -62 dBm`)** | **`4 / 5`** | **`98.2%`** | **`100.0%`** | Mở rộng tái sử dụng không gian |
| 7 | **SU-Beamforming (`OFF / ON`)** | **`2 / 2`** | **`99.7%`** | **`100.0%`** | Chủ động tắt khi `CSI Aging` |

---

### 5.4. Kết quả Kiểm định Thực chiến trên Chuỗi Dữ liệu Thực tế Kaggle (`REAL_KAGGLE_CSV`) & Stress Test 600 Bước
> Biểu đồ: [`kaggle_real_trace_replay.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct/wifi_rl_logs/kaggle_real_trace_replay.png), [`pulse_chaos_test.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct/wifi_rl_logs/pulse_chaos_test.png), [`auxiliary_5_actions_audit.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct/wifi_rl_logs/auxiliary_5_actions_audit.png) | CSV: [`step46_real_and_chaos_audit.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/KHKT_training_digital_twin_8DState_7DAct/wifi_rl_logs/step46_real_and_chaos_audit.csv)

- **Chế độ 1 — Phát lại Vòng kín trên Cửa sổ 600 Bước Thời gian thực Liên tiếp (`REAL_KAGGLE_CSV`: *UjiIndoorLoc* + *RF Telemetry*):**
  - **PPO Dual-Sensing (M):** `Goodput = 52.50 Mbps` | `P99 TB = 3.69 ms` | `PooledStep-P99 = 10.47 ms` | **`Loss = 0.49% (< 0.5%)`** | **`QoE SLA = 98.2%`**.
  - **Static Legacy (N):** `Goodput = 1.20 Mbps` | `P99 TB = 121.45 ms` | `PooledStep-P99 = 385.73 ms` | `Loss = 99.67%` | `QoE SLA = 0.0%`.
- **Chế độ 2 — Stress Test Xung Nhiễu 600 Bước Thuần PPO (Kèm Bộ lọc Trễ Vùng Chết Schmitt-Trigger):**
  - `Step 50 (Bình yên)`: `Kênh 36 | 80 MHz | Tx=16dBm | MCS=9 | Bcn=300ms | OBSS=-62dBm | BF=ON` $\rightarrow$ `Goodput = 67.7 Mbps, Lat99 = 3.18 ms, Loss = 0.00%`.
  - `Step 150 (Đợt 1 - Nhiễu CH36-40)`: `Kênh 149 | 20 MHz | Tx=17dBm | MCS=8 | Bcn=300ms | OBSS=-67dBm | BF=ON` $\rightarrow$ `Goodput = 45.3 Mbps, Lat99 = 6.25 ms, Loss = 1.00%`.
  - `Step 350 (Đợt 2 - Bão Roaming 125 STA)`: `Kênh 149 | 20 MHz | Tx=18dBm | MCS=7 | Bcn=80ms | OBSS=-72dBm | BF=OFF` $\rightarrow$ `Goodput = 11.6 Mbps, Lat99 = 3.20 ms, Loss = 0.00%`.
  - `Step 450 (Hồi phục Bình yên)`: `Kênh 149 | 80 MHz | Tx=16dBm | MCS=9 | Bcn=300ms | OBSS=-67dBm | BF=ON` $\rightarrow$ `Goodput = 74.2 Mbps, Lat99 = 3.34 ms, Loss = 0.00%`.
  - `Step 550 (Đợt 3 - Sập cả dải UNII-1)`: `Kênh 149 | 80 MHz | Tx=19dBm | MCS=3 | Bcn=80ms | OBSS=-72dBm | BF=ON` $\rightarrow$ `Goodput = 29.3 Mbps, Lat99 = 3.55 ms, Loss = 0.00%`.
  - **Trung bình toàn chuỗi 600 bước:** `PPO Dual-Sensing` đạt **`Goodput = 48.6 Mbps | Trễ P99 = 3.83 ms | Loss = 0.17%`** (chỉ chuyển mạch đúng **5 lần**), vượt trội hoàn toàn so với `Static Legacy` (`Goodput = 19.1 Mbps | Trễ P99 = 67.40 ms | Loss = 62.09%`).

---

## 6. CẤU TRÚC THƯ MỤC DỰ ÁN

```text
KHKT_2026_WIFI_RL/
├── configs/
│   ├── network_config.yaml                  # Bản đồ Topo mạng, IP, BSSID 2.4G/5G, Role & Zone Type
│   ├── rl_params.yaml                       # Cấu hình tham số RL
│   └── teacher_db.json                      # Cơ sở dữ liệu tài khoản Giáo viên cho Captive Portal
├── data/
│   └── processed/
│       ├── per_ap_state.json                # Trạng thái 8D Dual-Sensing thời gian thực của từng AP
│       ├── telemetry_history.csv            # Lịch sử thu thập Telemetry phục vụ Dashboard & Heatmap
│       ├── ai_actions_log.csv               # Hộp đen ghi lại mọi quyết định 7D của AI (AILogger)
│       ├── qoe_metrics.csv                  # Dữ liệu đo kiểm P50/P95/P99 Latency, Jitter & Packet Loss
│       ├── mac_vip_list.json                # Danh sách địa chỉ MAC đặc quyền của Giáo viên
│       └── failsafe_events.log              # Nhật ký sự kiện cứu hộ Soft Rescue & Hard Reboot
├── notebooks/
│   ├── 01_topology_raytracing.ipynb         # Bài toán 1: Quy hoạch vị trí AP bằng Ray-Tracing
│   ├── meep_wifi_sim.ipynb                  # Bài toán 1: Mô phỏng trường điện từ FDTD (MEEP)
│   ├── data_visualization.ipynb             # Xuất Bản đồ nhiệt va chạm (collision_heatmap.png) & Audit
│   ├── training_digital_twin.ipynb          # Bài toán 2: Pipeline huấn luyện & kiểm định 5 Seeds × 360k bước
│   └── KHKT_training_digital_twin_8DState_7DAct/
│       ├── saved_models/best_model.zip      # Trọng số PPO chuẩn xuất từ Bước 3
│       ├── seed_11..55/                     # Trọng số best_model.zip & 3,000 mẫu QoE/seed (Dual-Sensing)
│       ├── seed_11..55_mac_only/            # Trọng số best_model.zip của 5 seeds PPO MAC-Only độc lập
│       ├── summary_reports/                 # Bảng CSV đối chứng 5 Seeds & Biểu đồ 4 khung P99/ECDF/Goodput
│       └── wifi_rl_logs/                    # Biểu đồ Learning Curve, Kaggle Replay & Stress Test (A-F)
├── saved_models/
│   ├── best_model.zip                       # Trọng số PPO chính thức dùng cho deploy_hil_agent.py & train.py
│   └── best_model_real_world.zip            # Trọng số PPO sau khi Fine-Tune trực tiếp trên Router thật
├── scripts/
│   ├── auto_discover_aps.py                 # Quét đa luồng 50 workers tự động phát hiện Router OpenWrt & BSSID
│   ├── setup_network.py                     # Zero-Touch Provisioning (Master, Dumb AP, Onboard Subnet, 802.11r)
│   ├── enable_monitor_mode.sh               # Chuyển card USB Wi-Fi sang chế độ Monitor Mode (wlan0mon)
│   └── setup_env.sh                         # Cài đặt môi trường ảo và thư viện phụ thuộc
├── src/
│   ├── envs/
│   │   ├── digital_twin_env.py              # Lõi Bản sao số Continuous-Markov 4 tầng, RBF Extractor & Guardrails
│   │   └── real_router_env.py               # Môi trường Gym kết nối trực tiếp Router thật để Online Fine-Tuning
│   ├── modules/
│   │   ├── telemetry.py                     # Cảm biến Dual-Mode (OpenWrt SSH Dual-Band & Scapy USB Sniffer)
│   │   ├── wifi_actuators.py                # Thực thi nguyên tử 7 vũ khí RRM, ATF+AQL+FQ-CoDel & VAP Pruning
│   │   ├── beacon_bait_daemon.py            # Bơm Beacon mồi 802.11i RSN 25s & Đánh thức/Tỉa VAP 0ms gián đoạn
│   │   ├── failsafe_watchdog.py             # Vệ sĩ an toàn kép (Soft Rescue SSH & Hard Reboot 220V/PoE)
│   │   ├── sonoff_controller.py             # Điều khiển ổ cắm thông minh Sonoff S26 R2 DIY Mode (Port 8081)
│   │   └── qoe_recorder.py                  # Daemon đo kiểm ngầm Latency P99, Jitter & Packet Loss
│   ├── onboarding/
│   │   └── app.py                           # Cổng Captive Portal định danh Giáo viên & Handover tự động
│   ├── rl_agent/
│   │   ├── deploy_hil_agent.py              # Tác tử AI điều phối mạng vòng kín thời gian thực (HIL Controller)
│   │   └── train.py                         # Kịch bản Online Fine-Tuning trọng số PPO trực tiếp trên Router thật
│   └── utils/
│       └── logger.py                        # Lớp AILogger ghi hộp đen quyết định AI ra CSV
├── tests/
│   ├── test_sonoff_api.py                   # Bộ công cụ kiểm thử kết nối & Power Cycle ổ cắm Sonoff S26 R2
│   └── test_pc_formula.py                   # Kiểm thử đơn vị công thức xác suất va chạm tầng MAC
├── dashboard.py                             # Giao diện trung tâm chỉ huy thời gian thực (Streamlit Port 8501)
├── visualize_qoe.py                         # Công cụ xuất biểu đồ đánh giá định kỳ cam kết QoE SLA
├── .env                                     # File cấu hình biến môi trường & thông tin xác thực phần cứng
└── requirements.txt                         # Danh sách gói thư viện Python
```

---

## 7. HƯỚNG DẪN TRIỂN KHAI THỰC TIỄN TỪNG BƯỚC TRÊN PHẦN CỨNG (STEP-BY-STEP DEPLOYMENT GUIDE)

### 7.1. Chuẩn bị Môi trường & Cấu hình File `.env`

1. **Kích hoạt môi trường Python:**
   ```bash
   source .venv/bin/activate
   ```
2. **Thiết lập thông số phần cứng trong file [`.env`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/.env) tại thư mục gốc:**
   ```ini
   # 1. Thông tin đăng nhập SSH cho hệ thống Router OpenWrt (Xiaomi AX3000T)
   ROUTER_IP=192.168.2.1
   MASTER_ROUTER_IP=192.168.2.1
   ROUTER_USER=root
   ROUTER_PASS=mat_khau_ssh_cua_ban
   ROUTER_SSH_PORT=22

   # 2. Địa chỉ IP của Laptop Edge Server (nơi chạy Captive Portal cổng 80)
   EDGE_SERVER_IP=192.168.2.229

   # 3. Cấu hình tên mạng Wi-Fi (SSID) & Chuyển vùng nhanh 802.11r
   WIFI_SSID_5G="KHKT_Testbed 5GHz"
   WIFI_SSID_2G="KHKT_Testbed 2.4 GHz"
   WIFI_PASSWORD=0776642209
   WIFI_ENCRYPTION=psk2
   TEACHER_SSID=School_Teacher
   TEACHER_PASSWORD=TeacherPassword456
   ONBOARD_SSID=School_Onboarding
   MOBILITY_DOMAIN=abcd

   # 4. Cấu hình USB Wi-Fi Monitor Mode & Chu kỳ Beacon Baiting
   MONITOR_IFACE=wlan0mon
   BAIT_IFACE=wlan0mon
   BAIT_INJECT_INTERVAL=25
   TEACHER_WAKE_DURATION=60    # Đặt 60s khi chấm thi demo, 600s (10 phút) khi vận hành thực tế

   # 5. Cấu hình Cứu hộ Cứng (Hard Reboot) qua Ổ cắm Thông minh Sonoff S26 R2 (DIY Mode)
   HARD_FAILSAFE_MODE=SONOFF   # Chọn SONOFF hoặc POE_SWITCH
   SONOFF_IP=192.168.137.105   # IP của Sonoff trên mạng Mobile Hotspot (hoặc để mDNS tự tìm)
   SONOFF_PORT=8081
   SONOFF_DEVICE_ID=1000abcdef
   SONOFF_POWER_CYCLE_DELAY=5
   ```

---

### 7.2. Bước 0: Khảo sát Hiện trạng Mạng bằng USB Wi-Fi Monitor Mode (Chưa dùng AI) & Xuất Bản đồ Nhiệt `collision_heatmap.png`

Trước khi bật AI can thiệp, người triển khai sử dụng card USB Wi-Fi ở chế độ **Monitor Mode** để thu thập dữ liệu thụ động qua không khí trên các kênh đang có người dùng và xuất ra biểu đồ **Bản đồ nhiệt va chạm (`collision_heatmap.png`)**, giúp hội đồng hoặc quản trị viên thấy rõ hiện trạng nghẽn mạng truyền thống.

#### 1. Bật chế độ Monitor Mode trên card USB Wi-Fi
Cắm card USB Wi-Fi (chip Realtek RTL8812AU / RTL8821AU hoặc tương đương) vào Laptop Edge Server và chạy:
```bash
sudo bash scripts/enable_monitor_mode.sh
```
- **Tác dụng:** Ngắt các tiến trình chiếm dụng card (`airmon-ng check kill`), chuyển giao diện `wlan0` sang `type monitor`, đổi tên thành `wlan0mon` và kích hoạt giao diện.

#### 2. Chạy Cảm biến Telemetry ở chế độ Quét Không khí (`--mode scapy`)
```bash
sudo .venv/bin/python -m src.modules.telemetry \
    --mode scapy \
    --zone CangTin \
    --iface wlan0mon \
    --scan-time 30
```
- **Cơ chế hoạt động 2 pha của `SmartChannelHopper` trong chế độ `scapy`:**
  - **Pha 1 (Khám phá kênh — `Discovery Phase` trong `--scan-time` giây):** Card `wlan0mon` nhảy qua toàn bộ 12 kênh (`1, 6, 11, 36, 40, 44, 48, 149, 153, 157, 161, 165`), dừng `0.5s` ở mỗi kênh để đếm mật độ khung Beacon và xác định những kênh thực sự đang có AP và người dùng hoạt động.
  - **Pha 2 (Khóa kênh trọng điểm — `Channel Pinning Phase`):** Tự động khóa vào các kênh sôi động nhất để bóc tách cờ `Retry` trong `FCfield` (`bit 0x08`) của các khung Data (`type == 2`), đo `RSSI`, `Noise Floor` từ `RadioTap` header và giải mã `PHY Preamble` (`HE-SU`, `HE-MU OFDMA`, `VHT`, `HT`, `Legacy`).
- **Giải thích chi tiết toàn bộ các tham số (`argparse`) của [`src/modules/telemetry.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/modules/telemetry.py):**

| Tham số | Lựa chọn / Mặc định | Giải thích & Khi nào sử dụng |
|---|---|---|
| `--mode` | `openwrt` *(mặc định)* hoặc `scapy` | • Chọn **`scapy`** ở **Bước 0** (khi khảo sát hiện trạng mạng bằng USB Wi-Fi Monitor Mode mà chưa cần SSH vào Router, hoặc khi quét thụ động môi trường xung quanh).<br>• Chọn **`openwrt`** ở **Bước 2** (khi triển khai AI điều khiển chính thức: đọc trực tiếp bộ đếm phần cứng `iw station dump` & `survey dump` của cả 2 băng tần 2.4GHz/5GHz qua SSH với độ chính xác 100%). |
| `--zone` | Chuỗi ký tự<br>*(Mặc định: `KHKT_Testbed`)* | Nhãn định danh khu vực vật lý đang đo đạc. Ví dụ:<br>• `--zone CangTin`: Khi đặt trạm khảo sát tại Căng tin.<br>• `--zone ThuVien`: Khi khảo sát tại Thư viện.<br>• `--zone PhongHoc_A101`: Khi khảo sát tại Phòng học.<br>• `--zone KHKT_Testbed`: Khi chạy thực nghiệm trên bệ thử.<br>Giá trị này được ghi trực tiếp vào cột `Zone` của `telemetry_history.csv` và trường `"zone"` của `per_ap_state.json` để phân tách dữ liệu theo không gian. |
| `--iface` | Mặc định: `wlan0mon` | Tên giao diện card mạng USB Wi-Fi ở chế độ Monitor Mode (chỉ dùng khi `--mode scapy`). |
| `--interval` | Số nguyên (giây)<br>*(Mặc định: `3`)* | Chu kỳ lấy mẫu qua SSH (tính bằng giây) khi chạy `--mode openwrt`. |
| `--scan-time` | Số nguyên (giây)<br>*(Mặc định: `30`)* | Thời lượng (giây) dành cho Pha 1 quét khám phá toàn bộ 12 kênh trước khi khóa kênh (`Channel Pinning`) ở `--mode scapy`. |

- **Kết quả của tác vụ Telemetry được lưu ở đâu?**
  1. [`data/processed/per_ap_state.json`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/per_ap_state.json): File JSON lưu trạng thái 8D mới nhất kèm khối `phy_sensing` của từng BSSID (cập nhật liên tục để AI đọc).
  2. [`data/processed/telemetry_history.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/telemetry_history.csv): File CSV lưu chuỗi lịch sử theo thời gian gồm 10 cột: `Timestamp, Hour, Minute, AP_MAC, avg_rssi, p_c, retry_rate, airtime_util, sta_count, Zone`.

#### 3. Xuất Bản đồ Nhiệt Hiện trạng Đụng độ Mạng (`collision_heatmap.png`)
Sau khi thu thập dữ liệu vào `data/processed/telemetry_history.csv`, mở và chạy Cell 1 của notebook [`notebooks/data_visualization.ipynb`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/data_visualization.ipynb):
- Notebook tổng hợp ma trận Pivot Table của xác suất va chạm tầng MAC ($p_c$) theo **Khu vực phát sóng (AP / Zone)** và **Khung giờ trong ngày (Hour)**, sau đó kết xuất bản đồ nhiệt 300 DPI tại:
  👉 **[`wifi_rl_logs/collision_heatmap.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/wifi_rl_logs/collision_heatmap.png)** (các ô màu cam/đỏ đậm chỉ rõ những khung giờ và khu vực đang bị nghẽn va chạm $p_c > 0.40$ khi chưa có AI).

---

### 7.3. Bước 1: Tự động Quét Topo (`auto_discover_aps.py`) & Thiết lập Hạ tầng (`setup_network.py`)

#### 1. Tự động phát hiện mọi Router OpenWrt trong mạng LAN (`auto_discover_aps.py`)
```bash
.venv/bin/python scripts/auto_discover_aps.py \
    --subnet 192.168.2 \
    --master-ip 192.168.2.1 \
    --start 1 \
    --end 20
```
- **Giải thích các tham số (`argparse`) của [`scripts/auto_discover_aps.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/scripts/auto_discover_aps.py):**
  - `--subnet`: 3 octet đầu của dải mạng LAN (mặc định: `192.168.2`).
  - `--master-ip`: Địa chỉ IP của Router đóng vai trò Gateway/Master (mặc định: `192.168.2.1`). Thiết bị có IP trùng với `--master-ip` được gán `role: master`; mọi thiết bị OpenWrt khác tìm thấy được gán `role: ap` (`Router_AP1`, `Router_AP2`...).
  - `--start` và `--end`: Octet cuối bắt đầu và kết thúc quét (mặc định quét nhanh `1..20`; đặt `--end 254` nếu muốn quét toàn bộ dải `/24`). Script phóng 50 luồng song song kiểm tra cổng SSH và đọc `iw dev` để lấy chính xác cặp địa chỉ MAC phần cứng `bssid_5g` (`phy1-ap0`) và `bssid_2g` (`phy0-ap0`).
- **Kết quả đầu ra:** Tự động sinh file [`configs/network_config.yaml`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/configs/network_config.yaml). *(Mẹo: Nếu muốn chỉ định một AP cụ thể làm phòng giáo viên Always-On, chỉ cần thêm dòng `zone_type: privileged_office` vào node đó trong `configs/network_config.yaml`; mặc định mọi node là `zone_type: classroom`).*

#### 2. Tự động quy hoạch và nạp cấu hình toàn mạng (`setup_network.py`)
```bash
.venv/bin/python scripts/setup_network.py
```
- **Tác dụng:** Đọc [`configs/network_config.yaml`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/configs/network_config.yaml) và tự động SSH vào từng thiết bị:
  - Trên **Router Master**: Cấu hình DHCP LAN, tạo mạng Onboarding cô lập (`br-onboard` `192.168.199.1/24`), bật `dnsmasq` riêng trên cổng `5353` + Firewall DNAT Captive Portal cho riêng mạng `School_Onboarding`, khởi tạo các VAP `default_radio0`, `onboarding_2g`, `default_radio1`, `teacher_5g` (kèm chuẩn chuyển vùng nhanh `802.11r` và `airtime_fairness='1'`).
  - Trên các **Dumb AP**: Chuyển cổng WAN nhập vào `br-lan`, trỏ Gateway/DNS về Master, tắt DHCP/Firewall cục bộ để trở thành điểm phát sóng thuần túy đồng bộ漫游 `802.11r`.

---

### 7.4. Bước 2: Khởi chạy Telemetry Dual-Band, Vệ sĩ An toàn (`failsafe_watchdog.py`) & Tác tử AI (`deploy_hil_agent.py`)

Mở **4 cửa sổ Terminal** riêng biệt (hoặc chạy dưới dạng dịch vụ nền) theo đúng thứ tự sau:

#### Terminal 1 — Khởi chạy Cảm biến Telemetry Chế độ OpenWrt Dual-Band
```bash
.venv/bin/python -m src.modules.telemetry \
    --mode openwrt \
    --zone KHKT_Testbed \
    --interval 3
```
- Cứ mỗi `3 giây`, hệ thống SSH đọc đồng thời cả 2 băng tần `2.4GHz (phy0-ap*)` và `5GHz (phy1-ap*)` trên toàn bộ các AP, tính toán 8 chỉ số Dual-Sensing (`avg_rssi`, `noise_floor`, `sta_count`, `retry_rate`, `p_c`, `airtime_util`, `ofdma_ratio`, `is_break_time`) và ghi ra [`data/processed/per_ap_state.json`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/per_ap_state.json) cùng [`data/processed/telemetry_history.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/telemetry_history.csv).

#### Terminal 2 — Khởi chạy Vệ sĩ Giám sát An toàn Kép (`failsafe_watchdog.py`)
```bash
.venv/bin/python src/modules/failsafe_watchdog.py
```
- Giám sát liên tục độ trễ ICMP và xác suất va chạm $p_c$ của mọi AP mỗi `5 giây`, sẵn sàng kích hoạt `Soft Rescue` hoặc `Hard Reboot` nếu phát hiện sự cố kéo dài.

#### Terminal 3 — Khởi chạy Tác tử AI Điều phối Vòng kín (`deploy_hil_agent.py`)
```bash
.venv/bin/python -m src.rl_agent.deploy_hil_agent \
    --band 5G \
    --interval 10 \
    --hardware WIFI_6 \
    --model saved_models/best_model.zip
```
- **Giải thích chi tiết các tham số (`argparse`) của [`src/rl_agent/deploy_hil_agent.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/rl_agent/deploy_hil_agent.py):**

| Tham số | Lựa chọn / Mặc định | Giải thích & Khi nào sử dụng |
|---|---|---|
| `--band` | `5G` *(mặc định)* hoặc `2.4G` | Chọn băng tần mục tiêu để AI điều phối (`5G` ứng với `radio1 / phy1-ap0` trên 9 kênh Non-DFS `36..165`; `2.4G` ứng với `radio0 / phy0-ap0` trên 3 kênh `1, 6, 11`). Có thể chạy 2 tiến trình song song cho cả 2 băng tần. |
| `--interval` | Số nguyên (giây)<br>*(Mặc định: `10`)* | Chu kỳ suy luận và thực thi hành động của AI (mặc định `10 giây/lần`). |
| `--hardware` | `WIFI_6` *(mặc định)*,<br>`WIFI_5_AC`,<br>`WIFI_4_LEGACY` | Khai báo thế hệ phần cứng của Router để `HardwareAwareWrapper` tự động mở hoặc khóa các vũ khí tương ứng (`7/7` cho Wi-Fi 6, `6/7` cho Wi-Fi 5, `5/7` cho Wi-Fi 4). |
| `--model` | Đường dẫn file `.zip`<br>*(Mặc định: `saved_models/best_model.zip`)* | Đường dẫn tới file trọng số PPO. Có thể đổi sang `saved_models/best_model_real_world.zip` sau khi đã chạy Online Fine-Tuning trên phần cứng thực. |
| `--dry-run` | Cờ boolean *(Tùy chọn)* | Khi thêm `--dry-run`, AI vẫn đọc Telemetry thật, chạy mạng nơ-ron `VecFrameStack(4)`, lọc qua `StatefulChannelGuardWrapper` và ghi log quyết định, nhưng **không gửi lệnh SSH** xuống Router (dùng khi muốn kiểm thử an toàn trước khi can thiệp sóng thật). |

- **Các công cụ tự động kích hoạt và nơi xem kết quả khi `deploy_hil_agent.py` chạy:**
  1. **Hộp đen Quyết định AI (`AILogger` — [`src/utils/logger.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/utils/logger.py)):** Tự động ghi mọi hành động 7 chiều, trạng thái mạng và điểm thưởng kỳ vọng vào **[`data/processed/ai_actions_log.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/ai_actions_log.csv)**. Bạn có thể chạy Cell 2 của [`notebooks/data_visualization.ipynb`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/data_visualization.ipynb) để xuất biểu đồ kiểm toán hành động AI tại `wifi_rl_logs/ai_actions_audit.png`.
  2. **Bộ ghi Chất lượng Trải nghiệm (`QoE Recorder` — [`src/modules/qoe_recorder.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/modules/qoe_recorder.py)):** Tự động khởi chạy trong một daemon thread nền, đo `P50/P95/P99 Latency`, `Jitter` và `Packet Loss` mỗi chu kỳ và ghi vào **[`data/processed/qoe_metrics.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/qoe_metrics.csv)**.

#### Terminal 4 — Mở Trạm Điều hành Thời gian thực (`dashboard.py`)
```bash
.venv/bin/streamlit run dashboard.py
```
- Truy cập **`http://localhost:8501`** trên trình duyệt để theo dõi trực quan theo thời gian thực (tự động làm tươi mỗi 5 giây) số lượng Clients, tỷ lệ va chạm $p_c$, độ bận Airtime, RSSI của từng AP và trạng thái can thiệp của AI.

---

### 7.5. Bước 3: Triển khai `beacon_bait_daemon.py`, Packet Injection 25s & Chiến thuật Zero-Disruption VAP Pruning Mạng Giáo viên

#### 1. Bản chất kỹ thuật: Tại sao phải tắt hẳn giao diện VAP thay vì chỉ ẩn SSID (`hidden=1`)?
Nếu chỉ cấu hình ẩn tên mạng (`uci set wireless.teacher_5g.hidden='1'`), Router vẫn phát khung quản lý Beacon đều đặn mỗi `100 ms` với trường SSID rỗng, tiếp tục tiêu tốn Airtime vô ích. Để tiết kiệm **100% Beacon Airtime** khi không có giáo viên mà **không làm gián đoạn mạng Học sinh**, hàm `prune_unused_ssid()` trong [`src/modules/wifi_actuators.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/modules/wifi_actuators.py#L219-L288) tìm đúng giao diện ảo của mạng Giáo viên (ví dụ `VAP_IF=phy1-ap1`) và dùng trực tiếp giao thức điều khiển socket của `hostapd`:
- **Khi tỉa (tắt) mạng Giáo viên:** `hostapd_cli -i "$VAP_IF" disable` (tắt hoàn toàn BSS `teacher_5g` trong **0 ms**, tuyệt đối không gọi `wifi reload` toàn cục nên mạng Học sinh `phy1-ap0` không bị rớt sóng dù chỉ 1 gói tin).
- **Khi đánh thức (bật) mạng Giáo viên:** `hostapd_cli -i "$VAP_IF" enable` (hoặc fallback `ubus call network reload` / `wifi up radio1` nếu BSS chưa từng được khởi tạo).

#### 2. Quản lý Danh sách Đặc quyền `mac_vip_list.json` & Cổng Onboarding (`src/onboarding/app.py`)
Danh sách địa chỉ MAC thiết bị của Giáo viên được lưu tại [`data/processed/mac_vip_list.json`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/mac_vip_list.json) và tài khoản đăng nhập lưu tại [`configs/teacher_db.json`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/configs/teacher_db.json) (`{"GV001": "pass123", "GV002": "pass123"}`). Có 2 cách cập nhật danh sách này:
1. **Cách 1 — Tự động hoàn toàn qua Cổng Captive Portal (Zero-Touch Onboarding):**
   Khởi chạy Web Server định danh trên cổng 80 của Laptop Edge Server:
   ```bash
   sudo .venv/bin/python src/onboarding/app.py
   ```
   - Khi một giáo viên mới đến trường lần đầu (khi mạng `School_Teacher` đang tắt), giáo viên bấm kết nối vào mạng mở **`School_Onboarding`**.
   - Điện thoại tự động bật pop-up **Cổng Định Danh Giáo Viên**. Giáo viên nhập mã `GV001` và mật khẩu `pass123`.
   - Hệ thống tự động trích xuất địa chỉ MAC của điện thoại, ghi thêm vào [`data/processed/mac_vip_list.json`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/mac_vip_list.json), hiển thị mật khẩu mạng `School_Teacher` cho giáo viên bấm **"Sao chép"**, và sau **6 giây** tự động gọi `prune_unused_ssid(..., disable=False)` bật sóng `School_Teacher` đồng thời gửi lệnh `iw dev <onboard_if> station del <MAC>` để điện thoại tự chuyển sang mạng `School_Teacher`.
2. **Cách 2 — Khai báo trực tiếp vào file JSON:**
   Quản trị viên có thể thêm trực tiếp địa chỉ MAC (viết hoa) vào [`data/processed/mac_vip_list.json`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/mac_vip_list.json):
   ```json
   [
     "D4:53:2A:11:22:33",
     "A8:5C:2C:44:55:66"
   ]
   ```

#### 3. Vận hành Vệ sĩ Mồi nhử & Bơm Gói tin `beacon_bait_daemon.py` mỗi 25 Giây
Từ lần thứ hai trở đi, điện thoại của Giáo viên đã lưu sẵn hồ sơ mạng `School_Teacher`. Để tự động phát hiện Giáo viên bước vào lớp học ngay cả khi VAP `School_Teacher` đang tắt, hãy chạy:
```bash
sudo .venv/bin/python -m src.modules.beacon_bait_daemon
```
Mô-đun [`src/modules/beacon_bait_daemon.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/modules/beacon_bait_daemon.py) khởi chạy **3 luồng song song**:
1. **Luồng 1 — Bơm Khung Beacon Mồi Chuẩn IEEE 802.11i RSN Đa Kênh mỗi `25 giây` (`inject_fake_beacon`):**
   - Hàm `build_ieee80211_bait_beacon()` đóng gói khung `Dot11Beacon` mang SSID `School_Teacher`, BSSID thật của AP (đọc từ `network_config.yaml`), gắn đầy đủ các thẻ `Supported Rates (ID=1)`, `DS Parameter Set (ID=3)`, `Extended Supported Rates (ID=50)` và đặc biệt là **20-byte thẻ bảo mật chuẩn IEEE 802.11i `RSN Information Element (ID=48)` (`WPA2-PSK + AES-CCMP-128`)**. Nhờ có thẻ RSN chuẩn, các hệ điều hành hiện đại (iOS, Android, Windows) nhận diện đây là mạng WPA2 đã lưu hợp lệ và lập tức phản hồi.
   - Cứ mỗi `BAIT_INJECT_INTERVAL = 25` giây, card `wlan0mon` tự động đọc kênh 5GHz mới nhất mà AI vừa chọn từ [`data/processed/ai_actions_log.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/ai_actions_log.csv) kết hợp cùng các kênh 2.4GHz/5GHz (`[36, 44, 149, 1, 6, 11]`), chuyển kênh và bơm 5 khung Beacon mồi liên tiếp (`sendp(..., count=5, inter=0.05)`).
2. **Luồng 2 — Bắt Gói tin Phản hồi & Đánh thức Tức thì (`process_packet` & `is_teacher_trigger_packet`):**
   - Ngay khi điện thoại Giáo viên bước vào vùng phủ sóng và nghe thấy Beacon mồi (hoặc tự quét mạng nền), điện thoại phát ra khung `Dot11ProbeReq`, `Dot11Auth` hoặc `Dot11AssoReq`.
   - Daemon nhận diện Giáo viên trong **cả 2 trường hợp**:
     - Địa chỉ MAC nguồn (`addr2`) nằm trong [`data/processed/mac_vip_list.json`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/mac_vip_list.json), **HOẶC**
     - Khung `Directed Probe Request` hỏi trực tiếp tên SSID `School_Teacher` (giúp đánh thức mạng thành công ngay cả khi điện thoại Giáo viên bật tính năng **Random MAC / Private Wi-Fi Address** lúc quét mạng!).
   - Lập tức gọi `wake_up_router()` để bật VAP `teacher_5g` (`disable=False`) trên AP phòng học đó và khởi động bộ đếm lùi `TEACHER_WAKE_DURATION` (`60s` hoặc `600s`).
3. **Luồng 3 — Giám sát Dự phòng Không cần USB Monitor (`active_presence_monitor_loop`) & Rào chắn Thượng Tôn Người Dùng (`try_turn_off_teacher_network`):**
   - Mỗi `20 giây`, daemon quét qua SSH danh sách trạm trên mọi giao diện AP: nếu phát hiện MAC VIP của giáo viên xuất hiện trên bất kỳ mạng nào của AP, hệ thống cũng tự động đánh thức VAP `teacher_5g`.
   - Khi hết thời gian đếm ngược `TEACHER_WAKE_DURATION`, hàm `get_teacher_active_clients()` kiểm tra trực tiếp `iw dev <vap> station dump`:
     - Nếu còn **$\ge 1$ thiết bị** đang kết nối vào `School_Teacher`: **Tuyệt đối không tắt!** Tự động gia hạn thêm một chu kỳ `TEACHER_WAKE_DURATION` nữa.
     - Chỉ khi chắc chắn **`0` thiết bị** còn kết nối (Giáo viên đã rời phòng học) và AP đó thuộc vùng `classroom` (không phải `privileged_office`), hệ thống mới tắt VAP `teacher_5g` để thu hồi Airtime.

---

### 7.6. Bước 4: Cơ chế Vận hành Dual Fail-Safe (`Soft Rescue` & `Hard Reboot`) và Kết nối Ổ cắm Thông minh Sonoff S26 R2

Mô-đun [`src/modules/failsafe_watchdog.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/modules/failsafe_watchdog.py) bảo vệ hệ thống 24/7 trước mọi rủi ro treo phần cứng hoặc nghẽn cấu hình:

#### 1. Cơ chế Giám sát Kép & Kích hoạt 2 Tầng (`Soft Rescue` $\rightarrow$ `Hard Reboot`)
- Cứ mỗi **`5 giây`**, Watchdog kiểm tra từng AP trong [`configs/network_config.yaml`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/configs/network_config.yaml) qua 2 chỉ số:
  1. **Độ trễ ICMP Burst (`check_ap_latency`):** Bắn 3 gói ping liên tiếp (`ping -c 3 -W 2 -q <AP_IP>`). Bất thường nếu `avg_latency > 500.0 ms` hoặc `packet_loss == 100%`.
  2. **Va chạm Tầng MAC Nguy kịch (`check_mac_layer_anomaly`):** Đọc `p_c` của cả `bssid_2g` và `bssid_5g` từ `per_ap_state.json`. Bất thường nếu $\max(p_{c,2\text{G}}, p_{c,5\text{G}}) > 0.85$.
- Nếu một AP bị bất thường **`6` lần liên tiếp (`~30 giây`)**, Watchdog kích hoạt quy trình giải cứu 2 tầng:
  - **Tầng 1 — Cứu hộ Mềm (`soft_reset_ap`):**
    Kết nối SSH vào AP (timeout `5s`) và nạp cấu hình **Safe-Fallback Profile** cho cả 2 băng tần:
    - `radio0 (2.4GHz)`: Kênh `1`, băng thông `HE20`, công suất `14 dBm`, Beacon `100 ms`.
    - `radio1 (5GHz)`: Kênh `36`, băng thông `HE20` (chống nghẽn), công suất `16 dBm`, Beacon `100 ms`.
    - Mở khóa toàn bộ giới hạn MCS (`iw dev phy0-ap0 set bitrates` & `iw dev phy1-ap0 set bitrates`) và ghi sự kiện `SOFT` vào [`data/processed/failsafe_events.log`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/failsafe_events.log).
  - **Tầng 2 — Cứu hộ Cứng (`hard_reboot_ap`):**
    Nếu AP bị *Kernel Panic*, treo driver hoặc mất hoàn toàn kết nối SSH khiến `soft_reset_ap()` trả về `False`, Watchdog lập tức chuyển sang **Cứu hộ Cứng**: gửi lệnh cắt nguồn điện vật lý 220V qua ổ cắm thông minh **Sonoff S26 R2 / S26AI** (hoặc ngắt nguồn cổng PoE trên Switch Cisco qua Netmiko nếu đặt `HARD_FAILSAFE_MODE=POE_SWITCH`), ghi sự kiện `HARD` vào [`data/processed/failsafe_events.log`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/failsafe_events.log).

#### 2. Hướng dẫn Kết nối & Điều khiển Ổ cắm Thông minh Sonoff S26 R2 (DIY Mode REST API)
Để đảm bảo Laptop Edge Server **luôn gửi được lệnh cắt điện ngay cả khi Router OpenWrt đã bị treo hoàn toàn**, ổ cắm Sonoff S26 R2 được kết nối theo kiến trúc **Out-of-Band (Kênh truyền độc lập)** thông qua **Mobile Hotspot** (hoặc mạng Wi-Fi phụ phát từ điện thoại/Laptop):

```text
+--------------------+      Wi-Fi Mobile Hotspot      +-----------------------+
| LAPTOP EDGE SERVER | <============================> | Ổ CẮM SONOFF S26 R2   |
| (sonoff_controller)|     (Dải IP: 192.168.137.x)    | (DIY Mode - Port 8081)|
+--------------------+      HTTP POST /zeroconf/*     +-----------+-----------+
                                                                  | Nguồn điện 220V AC
                                                                  v
                                                      +-----------------------+
                                                      | ROUTER XIAOMI AX3000T |
                                                      +-----------------------+
```

- **Các bước thiết lập phần cứng Sonoff S26 R2:**
  1. Kích hoạt chế độ **DIY Mode** trên ổ cắm Sonoff S26 R2 (theo hướng dẫn jumper/nút nhấn 5 giây của ITEAD) và kết nối Sonoff vào mạng Wi-Fi **Mobile Hotspot** (cùng mạng không dây với Laptop Edge Server).
  2. Khi ở DIY Mode, Sonoff tự động quảng bá dịch vụ mDNS `_ewelink._tcp.local.` tại cổng **`8081`**. Mô-đun [`src/modules/sonoff_controller.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/modules/sonoff_controller.py) sẽ **tự động quét mDNS (`discover_sonoff()`)** để tìm IP, Port và `device_id` của ổ cắm, hoặc bạn có thể điền trực tiếp `SONOFF_IP` vào file `.env`.
  3. Cắm dây nguồn 220V của Router Xiaomi AX3000T vào ổ cắm Sonoff S26 R2.
- **Kiểm thử kết nối và cắt nguồn Sonoff trước khi vận hành:**
  Sử dụng công cụ [`tests/test_sonoff_api.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/tests/test_sonoff_api.py):
  ```bash
  # 1. Kiểm tra thông tin trạng thái, firmware và cường độ sóng của ổ cắm Sonoff
  .venv/bin/python tests/test_sonoff_api.py --test info

  # 2. Kiểm thử chu trình Power Cycle thực tế (Tắt điện 220V -> chờ 5 giây -> Bật lại điện 220V)
  .venv/bin/python tests/test_sonoff_api.py --test cycle

  # 3. Chạy toàn bộ 10 bài kiểm thử tự động (mDNS, Ping, Info, Switch, Pulse, Watchdog Integration)
  .venv/bin/python tests/test_sonoff_api.py
  ```

---

### 7.7. Bước 5: Ghi nhận & Xuất Báo cáo Đánh giá Định kỳ Hiệu năng QoE (`qoe_recorder.py` & `visualize_qoe.py`)

Khi [`src/rl_agent/deploy_hil_agent.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/rl_agent/deploy_hil_agent.py) hoạt động, mô-đun [`src/modules/qoe_recorder.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/modules/qoe_recorder.py) tự động chạy ngầm, bắn 10 gói ICMP ping mỗi chu kỳ (`interval = 0.2s/gói`), tính toán chính xác `latency_min_ms`, `latency_avg_ms`, `latency_max_ms`, `latency_p50_ms`, `latency_p95_ms`, **`latency_p99_ms`**, **`jitter_ms` (chuẩn RFC 3550)** và **`packet_loss_pct`**, rồi ghi liên tục vào file [`data/processed/qoe_metrics.csv`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/qoe_metrics.csv).

Người quản trị hệ thống có thể xuất báo cáo kiểm toán chất lượng mạng bất cứ lúc nào (ngay cả khi HIL Agent vẫn đang chạy) bằng công cụ [`visualize_qoe.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/visualize_qoe.py):

```bash
# 1. Xem toàn bộ lịch sử QoE trên cửa sổ đồ họa trực tiếp
.venv/bin/python visualize_qoe.py

# 2. Chỉ đánh giá 60 phút vận hành gần nhất và lưu xuất ra 3 file ảnh PNG báo cáo
.venv/bin/python visualize_qoe.py --last 60 --save
```

- **Giải thích các tham số (`argparse`) của [`visualize_qoe.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/visualize_qoe.py):**
  - `--last <N_PHÚT>`: Chỉ trích xuất dữ liệu trong $N$ phút gần nhất (ví dụ `--last 30` cho 1 tiết học, `--last 1440` cho 24 giờ qua; bỏ trống để lấy toàn bộ lịch sử).
  - `--save`: Chuyển sang chế độ kết xuất file ảnh tĩnh (không cần màn hình GUI), tự động tính **Tỷ lệ tuân thủ cam kết SLA (`% thời gian P99 < 15ms` và `% thời gian Packet Loss < 1%`)** và lưu ra **3 file biểu đồ** tại thư mục `data/processed/`:
    1. **[`data/processed/qoe_combined.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/qoe_combined.png):** Biểu đồ tổng hợp 2 tầng (Độ trễ `P99 + Avg + Jitter` và `Packet Loss %` kèm vùng xanh an toàn / vùng đỏ vi phạm SLA).
    2. **[`data/processed/qoe_latency.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/qoe_latency.png):** Biểu đồ chuyên sâu về độ trễ `P99` và `Jitter`.
    3. **[`data/processed/qoe_packet_loss.png`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/data/processed/qoe_packet_loss.png):** Biểu đồ chuyên sâu về tỷ lệ rớt gói và đánh dấu tọa độ các điểm vi phạm SLA (nếu có).
  - `--csv <ĐƯỜNG_DẪN>`: Tùy chọn chỉ định một file CSV QoE khác (mặc định: `data/processed/qoe_metrics.csv`).

---

### 7.8. Bước 6: Hai Phương pháp Huấn luyện Lại và Tinh chỉnh Trọng số Tối ưu (`best_model.zip`)

Hệ thống hỗ trợ đầy đủ **2 cơ chế huấn luyện bổ trợ lẫn nhau** để nâng cấp bộ não PPO (`best_model.zip`):

#### Phương pháp 1: Huấn luyện Offline trên Bản sao số Vật lý 4 Tầng (`notebooks/training_digital_twin.ipynb`)
Sử dụng khi bạn tinh chỉnh công thức vật lý, thay đổi cấu trúc hàm thưởng hoặc muốn huấn luyện mới toàn bộ 5 Seeds độc lập (`360,000` bước/seed):
1. Mở [`notebooks/training_digital_twin.ipynb`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/notebooks/training_digital_twin.ipynb):
   - Tại **Cell 6 (Bước 3)**: Đặt `FORCE_RETRAIN_STEP3 = True`.
   - Tại **Cell 8 (Bước 3.1)**: Đặt `FORCE_RETRAIN_MAC_ALL_SEEDS = True` (nếu muốn train lại cả nhóm đối chứng MAC-Only).
   - Tại **Cell 12 (Bước 4.5)**: Đặt `FORCE_RETRAIN_ALL_SEEDS = True`.
2. Chạy toàn bộ các cell (**Run All**). Sau khi hoàn tất, trọng số tốt nhất do `EvalCallback` (với cơ chế cố định kịch bản CRN) tuyển chọn sẽ được xuất ra tại:
   - `notebooks/KHKT_training_digital_twin_8DState_7DAct/saved_models/best_model.zip`
   - `notebooks/KHKT_training_digital_twin_8DState_7DAct/seed_{11..55}/best_model.zip`
3. Đồng bộ trọng số mới sang thư mục triển khai thực tế:
   ```bash
   cp notebooks/KHKT_training_digital_twin_8DState_7DAct/saved_models/best_model.zip saved_models/best_model.zip
   ```

#### Phương pháp 2: Huấn luyện Tinh chỉnh Trực tuyến trên Phần cứng Thực tế (Online HIL Fine-Tuning qua `src/rl_agent/train.py`)
Do mô phỏng không thể bao quát 100% đặc tính suy hao đa đường (multipath fading) riêng biệt của từng kiến trúc phòng học thực tế, dự án cung cấp kịch bản [`src/rl_agent/train.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/rl_agent/train.py) kết nối với môi trường [`src/envs/real_router_env.py`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/src/envs/real_router_env.py) để **vừa điều khiển Router thật, vừa tiếp tục học thử-và-sai (Trial-and-Error) để tinh chỉnh trọng số PPO (Đợt huấn luyện thứ 2 — Sim-to-Real Continual Fine-Tuning)**:

1. **Điều kiện tiên quyết (Ràng buộc kỹ thuật bắt buộc):**
   - Phải bật sẵn `python -m src.modules.telemetry --mode openwrt --interval 3` ở một Terminal khác để `RealRouterEnv._get_real_state()` luôn đọc được vector trạng thái 8D thực tế mới nhất từ `data/processed/per_ap_state.json`.
   - Khuyến nghị bật sẵn `python src/modules/failsafe_watchdog.py` để bảo vệ phần cứng trong suốt quá trình AI khám phá hành động mới.
   - Không chạy `deploy_hil_agent.py` cùng lúc với `train.py` trên cùng một băng tần để tránh xung đột lệnh SSH.
2. **Khởi chạy Online Fine-Tuning trên Router thật:**
   ```bash
   .venv/bin/python -m src.rl_agent.train
   ```
   - **Cơ chế hoạt động bên trong `train.py` & `RealRouterEnv`:**
     - Nạp trọng số đã tối ưu từ mô phỏng `saved_models/best_model.zip` vào môi trường `RealRouterEnv` (đã bọc `HardwareAwareWrapper` và `VecFrameStack(n_stack=4)` khớp 100% kiến trúc `32D -> 7D`).
     - Ở mỗi bước tương tác (`step_interval = 10.0` giây), AI gửi cấu hình 7 vũ khí xuống Router thật qua `apply_ai_profile_atomic()` và `apply_mcs_ceiling()`, chờ `10 giây` để mạng thực tế phản hồi, đọc trạng thái 8D mới từ Telemetry và tính phần thưởng thực tế $R_t$.
     - Gọi `model.learn(total_timesteps=..., reset_num_timesteps=False)` để tiếp tục cập nhật gradient mạng Actor-Critic từ dữ liệu tương tác thật và xuất trọng số thực chiến ra:
       👉 **[`saved_models/best_model_real_world.zip`](file:///home/chigguaymeovang300475/KHKT_2026_WIFI_RL/saved_models/best_model_real_world.zip)**
3. **Đưa trọng số đã Fine-Tune trên phần cứng thực vào vận hành chính thức:**
   ```bash
   .venv/bin/python -m src.rl_agent.deploy_hil_agent \
       --band 5G \
       --interval 10 \
       --hardware WIFI_6 \
       --model saved_models/best_model_real_world.zip
   ```

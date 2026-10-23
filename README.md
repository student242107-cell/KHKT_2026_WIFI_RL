# BÁO CÁO NGHIÊN CỨU TỔNG THỂ: HỆ THỐNG TỐI ƯU HÓA COGNITIVE WI-FI ĐẦU CUỐI (KHKT 2026)

## 1. TỔNG QUAN HỆ THỐNG CẤP ĐIỀU HÀNH VÀ MÔ HÌNH TỐI ƯU HÓA \(M \gg N\)

### Bối cảnh và tầm quan trọng chiến lược

Sự gia tăng theo cấp số nhân của lưu lượng di động và mật độ tập trung cao của các thiết bị đầu cuối đã bộc lộ những điểm yếu căn bản của các mô hình triển khai Wi-Fi tĩnh truyền thống (N). Trong môi trường vận hành mật độ cao — chẳng hạn trụ sở doanh nghiệp, hội trường đại học, trung tâm triển lãm và khu dân cư — việc gán kênh tĩnh, cố định mức công suất phát và sử dụng các cơ chế tranh chấp ở lớp MAC thiếu phối hợp dẫn đến nhiễu giữa các ô phủ sóng ở tần số vô tuyến (RF) nghiêm trọng, va chạm khung dữ liệu ở mức nghiêm trọng và sự gia tăng đột biến của độ trễ đuôi. Dự án Cognitive Wi-Fi dành cho KHKT 2026 thực hiện sự chuyển đổi mô hình căn bản từ hạ tầng tĩnh kiểu cũ (N) sang một hệ sinh thái nhận thức thích ứng theo thời gian thực, vận hành bằng AI (M). Không thể giải quyết môi trường không dây mật độ cao chỉ bằng tinh chỉnh sau triển khai hoặc bổ sung phần cứng tĩnh. Thay vào đó, môi trường này đòi hỏi một khuôn khổ đầu cuối hợp nhất kỹ thuật cấu trúc liên kết không gian trước triển khai với cơ chế điều khiển vòng kín trong thời gian chạy, theo thời gian thực, dựa trên Học tăng cường (RL). Bằng cách kết nối trực tiếp việc lập hồ sơ không gian vật lý với một bộ máy thực thi tự chủ, hệ thống nhận thức được đề xuất chuyển phổ vô tuyến chưa được quản lý thành môi trường truyền dẫn không dây có tính xác định và độ tin cậy cao.

### Triết lý cốt lõi: “Thượng Tôn Chất Lượng Người Dùng” (User Quality Supremacy)

Nền tảng kiến trúc của hệ thống nhận thức này được chi phối bởi nguyên tắc vận hành tuyệt đối “Thượng Tôn Chất Lượng Người Dùng” (User Quality Supremacy). Triết lý này yêu cầu các chỉ số Chất lượng dịch vụ (QoS) và Chất lượng trải nghiệm (QoE) trong thực tế phải là những ràng buộc vận hành cứng, không được phép thỏa hiệp. Cụ thể, hệ thống duy trì các giới hạn hiệu năng sau trong tất cả trạng thái vận hành đang hoạt động:

- Độ trễ (`Latency_99`): $\le 15\,\text{ms}$ (giới hạn độ trễ ở phân vị thứ 99).
- Tỷ lệ lỗi gói tin (`PER`): $<1\%$.
- Chỉ báo cường độ tín hiệu thu (`RSSI`): $\ge -65\,\text{dBm}$.

Trong mọi trường hợp, không được phép làm suy giảm các giới hạn trải nghiệm người dùng này để đáp ứng các mô hình toán học trừu tượng trên lý thuyết, tối đa hóa thông lượng tổng hợp hoặc thực hiện các biện pháp tắt nguồn tiết kiệm năng lượng quyết liệt theo hướng AI xanh (Green AI). Việc tiết kiệm năng lượng và tối ưu hiệu quả sử dụng phổ tần chỉ được thực hiện trong miền khả thi hợp lệ được xác định bởi những ràng buộc QoE không thể thỏa hiệp này.

### Chứng minh toán học về ưu thế của hệ thống \((M >> N)\)

Để chứng minh bằng toán học rằng hệ sinh thái nhận thức (M) vượt trội rõ rệt so với các mô hình triển khai tĩnh truyền thống (N), chúng tôi xây dựng hai mô hình toán học bổ trợ cho nhau: **Chỉ số hiệu quả nhân** (`Index_VN`) và **Hàm tiện ích tuyến tính tổng hợp** (`U(X)`).

#### 1. Chỉ số hiệu quả nhân (`Index_VN`)

Chỉ số hiệu năng tổng thể của hệ thống được định nghĩa là tỷ số vô hướng giữa tiện ích vận hành và phần tài nguyên phần cứng, triển khai phát sinh thêm:

$$
\text{Index}_{VN}=\frac{\text{Throughput}\times\text{Reliability}}{\text{Latency}\times(\text{CapEx}\times\text{OpEx}_{\text{Setup}})}
$$

Trong đó:

- `Throughput` đo thông lượng tải dữ liệu không gian tổng hợp được truyền trong khu vực triển khai ($\text{Mbps}/\text{m}^2$).
- `Reliability` được định nghĩa là $(1-\text{PER})$, biểu thị tỷ lệ các đơn vị dữ liệu giao thức MAC (MPDU) được xác nhận thành công.
- `Latency` đo độ trễ đầu cuối của khung, từ lúc được đưa vào hàng đợi MAC đến khi nhận được xác nhận ACK ở lớp vật lý.
- `CapEx` biểu thị tổng chi phí tiền tệ dành cho các điểm truy cập phần cứng (AP) và hạ tầng vô tuyến.
- `OpEx_Setup` biểu thị chi phí vận hành liên quan đến khảo sát hiện trường vật lý, nhân công lắp đặt và cấu hình RF thủ công.

Trong các hệ thống cũ (N), quy hoạch địa điểm tĩnh dựa vào kiểm tra thực địa bằng cách đi bộ và triển khai dư thừa đồng loạt phần cứng đắt tiền để bao phủ các đợt tăng mật độ trong trường hợp xấu nhất, khiến `CapEx` và `OpEx_Setup` bị đẩy lên cao. Đồng thời, nhiễu đồng kênh (CCI) không được quản lý trong các hệ thống cũ gây va chạm khung khi tải tăng, làm tăng `Latency` trung bình và giảm `Reliability`. Hệ thống M tối ưu phân hạng phần cứng thông qua quét không gian dựa trên LiDAR (giảm `OpEx_Setup`) và quản lý động việc cấp phát sóng mang con bằng Đa truy nhập phân chia theo tần số trực giao (OFDMA) theo thời gian thực (giảm `Latency` và tối đa hóa `Reliability`). Do đó, $\text{Index}_{VN}(M)\gg\text{Index}_{VN}(N)$.

#### 2. Hàm tiện ích tuyến tính tổng hợp \(U(X)\) và động lực của mức phạt phi lồi

Hàm tiện ích hệ thống $U(X)$ đánh giá sự cân bằng giữa thông lượng, độ trễ đuôi, chi phí vốn và chi phí vận hành trên các cấu hình hệ thống $X\in\{M,N\}$:

$$
U(X)=\alpha\cdot\text{Spatial\_Throughput}-\beta\cdot\text{Latency}_{99}-\gamma\cdot\text{CapEx}-\delta\cdot\text{OpEx}
$$

Trong đó, $\alpha,\beta,\gamma,\delta>0$ là các hệ số trọng số. Để thực thi triết lý cốt lõi “Thượng Tôn Chất Lượng Người Dùng”, vectơ trọng số được cấu hình sao cho trọng số phạt độ trễ lớn hơn hẳn trọng số thông lượng ($\beta\gg\alpha$).

```text
   Tiện ích U(X)
        ^
        |          Hệ thống M (Thực thi thích ứng nhận thức)
   +U_M |..........===============================> Độ trễ được giới hạn <= 15 ms
        |         /
        |        /
      0 +-------/------------------------------------> Mật độ / tải thiết bị đầu cuối
        |      /
        |     /     Hệ thống N (Hạ tầng tĩnh truyền thống)
   -U_N |..../......-----------------------------\  Độ trễ đuôi tăng vọt
        |   /                                     \ (Latency_99 > 15 ms)
        v  /                                       v Sụp đổ tiện ích phi lồi
```

Trong các mô hình triển khai tĩnh cũ (N), khi mật độ thiết bị đầu cuối tăng lên, cơ chế tranh chấp Đa truy nhập cảm nhận sóng mang, tránh va chạm (CSMA/CA) suy giảm do thuật toán lùi thời gian theo cấp số nhân. Khi mật độ người dùng làm các hàng đợi gói tin đầy lên, độ trễ đuôi ở phân vị thứ 99 vượt ngưỡng $15\,\text{ms}$ ($\text{Latency}_{99}\gg15\,\text{ms}$). Vì $\beta\gg\alpha$, số hạng $-\beta\cdot\text{Latency}_{99}$ chi phối $U(N)$, khiến đường cong tiện ích của mô hình triển khai cũ giảm sâu xuống các giá trị âm trong điều kiện mật độ cao.

Ngược lại, Hệ thống M sử dụng thuật toán học tăng cường Proximal Policy Optimization (PPO) để điều chỉnh băng thông kênh, mức công suất phát, thời điểm thức dậy mục tiêu (TWT) và tham số Truy nhập kênh phân tán nâng cao (EDCA) trước khi hàng đợi bị bão hòa. Bằng cách duy trì $\text{Latency}_{99}\le15\,\text{ms}$ trong mọi chế độ mật độ vận hành, Hệ thống M tránh được sự sụp đổ tiện ích phi lồi, qua đó thể hiện ưu thế toán học: $U(M)\gg U(N)$.

### Các mục tiêu định lượng của hệ thống

Khuôn khổ Cognitive Wi-Fi được thiết kế để đáp ứng ba chuẩn vận hành định lượng cốt lõi so với các hệ thống tĩnh truyền thống:

| Chỉ số mục tiêu hệ thống | Chuẩn vận hành mục tiêu | Cơ chế tối ưu hóa kỹ thuật |
|---|---:|---|
| Giảm chi phí đầu tư (CapEx) | $\ge20\%$ | Phân hạng phần cứng không đồng nhất theo mật độ và ghép với hồ sơ không gian. |
| Giảm chi phí vận hành thiết lập (`OpEx_Setup`) | 30–50% | Quét kiến trúc đa đặc trưng tự động bằng LiDAR và cảm biến quang học. |
| Cải thiện tiện ích và chất lượng Wi-Fi tổng thể ($U(X)$) | $\ge60\%$ | Điều khiển tham số vô tuyến động bằng PPO theo vòng kín và tinh chỉnh lớp MAC. |

Sau khi xác lập ưu thế toán học nền tảng và các mục tiêu vận hành của khuôn khổ nhận thức, báo cáo chuyển sang kỹ thuật cấu trúc liên kết vô tuyến trước triển khai cần thiết để đạt được những cải thiện này.

---

## 2. BÀI TOÁN 1: TỐI ƯU HÓA CẤU TRÚC LIÊN KẾT HẠ TẦNG VÔ TUYẾN (QUY HOẠCH VÀ TRIỂN KHAI)

### Bối cảnh và tầm quan trọng chiến lược

Việc loại bỏ vùng chết phủ sóng, giảm thiểu nhiễu giữa các ô và bảo đảm phân bổ vốn hiệu quả đòi hỏi độ chính xác cao trong thiết kế cấu trúc liên kết trước khi lắp đặt phần cứng vật lý. Lập hồ sơ không gian từ giai đoạn đầu, trước triển khai, tạo ra môi trường RF vật lý tối ưu, bảo đảm rằng lớp tối ưu hóa AI thời gian thực ở các giai đoạn sau hoạt động trong một cấu trúc liên kết hệ thống cân bằng tốt, thay vì phải cố bù đắp các sai sót căn bản trong thiết kế địa điểm vật lý.

### Bài toán 1.1: Thu thập nhanh đặc trưng hồ sơ bằng quét không gian

Để giảm chi phí khảo sát hiện trường (`OpEx_Setup`) từ 30% đến 50%, hệ thống thay các phép thử thực địa thủ công bằng phương pháp quét không gian tự động. Sử dụng cảm biến đo và định vị bằng ánh sáng (LiDAR) kết hợp camera độ sâu quang học, thiết bị quét thu thập ma trận đặc trưng kiến trúc 5 chiều từ các cơ sở mục tiêu:

1. **Hình học thể tích và diện tích bề mặt (V, A):** trích xuất ranh giới thể tích 3D để tính kích thước phòng chính xác và hồ sơ lan truyền tín hiệu RF.
2. **Kết cấu tường và độ dày kết cấu ($d_w$):** đo kích thước vách ngăn nhằm xác định tường chịu lực, lõi bê tông cốt thép và vách thạch cao.
3. **Hệ số hấp thụ và suy hao vật liệu ($\alpha_{\text{mat}}$):** tính toán hệ số suy hao vật liệu (biểu diễn bằng $\text{dB/m}$) trên cả hai băng tần $2.4\,\text{GHz}$ và $5\,\text{GHz}$ đối với bê tông, kính trong, kính đã xử lý, gỗ và vách kim loại tấm.
4. **Đường truyền nhìn thẳng (LOS) và không nhìn thẳng (NLOS):** lập bản đồ các vectơ phản xạ gương chủ yếu, cạnh nhiễu xạ và vùng khuất tương ứng với các vị trí lắp AP tiềm năng.
5. **Luồng di chuyển của con người ($F_{\text{human}}$):** theo dõi các hành lang lưu thông, cửa ra vào, lối vào cầu thang và cách bố trí chỗ ngồi để xây dựng bản đồ phân bố mật độ người thay đổi theo thời gian.

### Lập hồ sơ phần cứng không đồng nhất và phân hạng theo mật độ

Triển khai đồng loạt các AP có chi phí cao và cấu hình cao trên toàn bộ doanh nghiệp gây ra chi phí vốn không cần thiết. Kiến trúc nhận thức áp dụng chiến lược triển khai không đồng nhất, ánh xạ các không gian vật lý vào ba phân hạng phần cứng dựa trên mật độ trạm (STA) và yêu cầu lưu lượng:

| Phân hạng phần cứng | Môi trường vật lý mục tiêu và mật độ STA | Đặc tả PHY/MAC IEEE 802.11ax | Độ chi tiết RU và tham số vận hành RF |
|---|---|---|---|
| **Tier 1 (mật độ cực cao)** | Khu A, căng-tin, hội trường, sân vận động (100–200+ STA/AP) | AP cấu hình cao; MU-MIMO 4×4 hoặc 8×8; băng thông 80/160 MHz; 1024-QAM; khoảng cách sóng mang con 78.125 kHz | Chia động thành RU 26, 52, 106 và 242 tone; bật BSS Coloring; phân bổ CPU/RAM cao. |
| **Tier 2 (mật độ trung bình)** | Văn phòng doanh nghiệp, phòng hội nghị (30–100 STA/AP) | AP tầm trung; MU-MIMO 2×2 hoặc 4×4; băng thông 20/40/80 MHz; 1024-QAM | Tạo chùm tia (beamforming); phân bổ RU 52 và 106 tone động; tham số hàng đợi EDCA thích ứng. |
| **Tier 3 (mật độ thấp / chuyển tiếp)** | Hành lang, cầu thang, kho lưu trữ (dưới 30 STA/AP) | AP chi phí thấp; MIMO 2×2; băng thông kênh cố định 20 MHz; 256-QAM | Chủ động giới hạn công suất phát ở 10 dBm để loại bỏ hiện tượng tràn RF sang các ô Tier 1/2; sử dụng một RU 242 tone. |

### Cơ chế kỹ thuật của sóng mang con và đơn vị tài nguyên (RU) trong AP Tier 1

Trong các môi trường triển khai Tier 1 có mật độ cực cao, khoảng cách sóng mang con truyền thống của IEEE 802.11a/g/n/ac (312.5 kHz với 64 sóng mang con trên mỗi 20 MHz) gây kém hiệu quả về thời gian phát khi truyền các tải dữ liệu nhỏ của người dùng (ví dụ: VoIP, tin nhắn tức thời, telemetry) do chi phí MAC cố định. AP Tier 1 triển khai cấu trúc sóng mang con IEEE 802.11ax, chia kênh 20 MHz thành 256 sóng mang con (mật độ sóng mang con tăng gấp 4 lần). Nhờ đó đạt khoảng cách sóng mang con chi tiết 78.125 kHz ($20{,}000\,\text{kHz}/256$).

```text
Kênh 20 MHz (tổng cộng 256 sóng mang con, khoảng cách 78.125 kHz)
+----+----+----+----+----+----+----+----+----+-------------------+
|RU1 |RU2 |RU3 |RU4 |RU5 |RU6 |RU7 |RU8 |RU9 | Các RU 26 tone    |
|26T |26T |26T |26T |26T |26T |26T |26T |26T | (9 STA song song) |
+----+----+----+----+----+----+----+----+----+-------------------+
|     RU 52 tone 1 | RU 52 tone 2 | RU 52 tone 3 | RU 52 tone 4    |
+------------------+--------------+--------------+------------------+
|            RU 106 tone 1        |         RU 106 tone 2         |
+---------------------------------+--------------------------------+
|                    RU 242 tone 1 (một SU)                        |
+------------------------------------------------------------------+
```

Theo đặc tả lớp vật lý IEEE 802.11ax, một kênh 20 MHz chứa 242 sóng mang con được lấp đầy, có thể được phân chia động thành các Đơn vị tài nguyên (RU) nhỏ hơn:

- **RU 26 tone:** gồm 24 sóng mang con dữ liệu và 2 sóng mang con pilot (độ rộng khoảng 2 MHz). Hỗ trợ tối đa 9 người dùng song song trên 20 MHz.
- **RU 52 tone:** gồm 48 sóng mang con dữ liệu và 4 sóng mang con pilot (độ rộng khoảng 4 MHz). Hỗ trợ tối đa 4 người dùng song song trên 20 MHz.
- **RU 106 tone:** gồm 102 sóng mang con dữ liệu và 4 sóng mang con pilot (độ rộng khoảng 8 MHz). Hỗ trợ tối đa 2 người dùng song song trên 20 MHz.
- **RU 242 tone:** chiếm toàn bộ phổ 20 MHz cho các đợt truyền thông lượng cao của một người dùng đơn (SU).

AP Tier 1 chia động các kênh 20 MHz hoặc 80 MHz thành những RU 26 tone trong các tình huống mật độ cao. Cấu trúc sóng mang con này kéo dài thời lượng ký hiệu OFDM tiêu chuẩn từ $3.2\,\mu\text{s}$ lên $12.8\,\mu\text{s}$. Khi kết hợp với các khoảng bảo vệ (GI) có thể chọn ($0.8\,\mu\text{s}$, $1.6\,\mu\text{s}$, $3.2\,\mu\text{s}$), hiệu suất sử dụng phổ ở lớp vật lý tăng từ 80% ($3.2/(3.2+0.8)$) trong hệ thống cũ lên 94% ($12.8/(12.8+0.8)$) tại các nút Tier 1 dùng 802.11ax.

### Xác thực bằng mô phỏng bản sao số truy vết tia 3D

Để xác thực vị trí AP và thiết lập công suất trước khi lắp đặt thực tế, ma trận đặc trưng không gian 5D đã thu thập được xử lý bởi bộ máy Bản sao số truy vết tia 3D. Bộ máy truy vết tia tính toán các vectơ truyền lan đa đường, đánh giá phản xạ gương, nhiễu xạ cạnh dao và suy hao truyền qua vật liệu trên bố cục không gian mục tiêu.

Ngoài việc xác thực vùng phủ, Bản sao số truy vết tia 3D xuất ra các ma trận suy hao không gian vật lý $H_{\text{spatial}}(f)$ và các hàm truyền suy hao đường truyền. Các ma trận không gian tính toán này được xuất trực tiếp để khởi tạo không gian quan sát trạng thái ban đầu và các hàm truyền kênh của môi trường Gymnasium PPO động được mô tả ở Phần 3. Sự tích hợp này kết nối việc lập hồ sơ không gian vật lý trước triển khai với học tăng cường trong thời gian chạy, loại bỏ độ trễ hội tụ do khởi động lạnh khi kích hoạt hệ thống trong thực tế.

Sau khi thiết lập cấu trúc liên kết vật lý được tối ưu và khởi tạo môi trường mô phỏng, báo cáo chuyển sang lớp vận hành động trong thời gian chạy.

---

## 3. BÀI TOÁN 2: TỐI ƯU HÓA VẬN HÀNH ĐỘNG THEO THỜI GIAN THỰC

### Bối cảnh và tầm quan trọng chiến lược

Kỹ thuật vật lý trước triển khai tạo nền tảng RF tối ưu; tuy nhiên, môi trường vận hành thực tế có nhiều biến động động. Sự gia tăng đột ngột và tạm thời của thiết bị đầu cuối, vật cản vật lý di chuyển, lưu lượng ứng dụng bùng phát và nhiễu đồng kênh không được quản lý đòi hỏi hệ thống phải liên tục thích ứng trong thời gian chạy. Tối ưu hóa vận hành động theo thời gian thực triển khai điều khiển phản hồi vòng kín bằng học tăng cường để duy trì các giới hạn QoE khi điều kiện mạng thay đổi.

### Kiến trúc bệ thử phần cứng và Sim-to-Real

Khuôn khổ vận hành nhận thức được xác thực trên một bệ thử phần cứng được thiết kế cho điện toán biên:

```text
+-----------------------------------------------------------------------------------+
|                              MÁY CHỦ BIÊN (EDGE SERVER)                            |
|                                                                                   |
|   +---------------------------------+     +-----------------------------------+   |
|   | Giao diện bảng điều khiển       |     | Tác tử PPO (môi trường Gymnasium) |   |
|   | Streamlit                       |     |                                   |   |
|   +---------------------------------+     +-----------------------------------+   |
|                    ^                                 ^         |                  |
+--------------------|---------------------------------|---------|------------------+
                     | Telemetry                      | Telemetry | Hành động
                     | (JSON)                         | (gRPC)    | (JSON-RPC)
                     v                                |           v
+----------------------------------------+    +-------------------------------------+
| THIẾT BỊ BẮT GÓI WI-FI USB             |    | BỘ ĐỊNH TUYẾN XIAOMI AX3000T OPENWRT|
| (Realtek RTL8812AU - Monitor Mode)     |    | (MediaTek MT7981B Filogic 820)      |
| - Bắt Dot11Beacon / ProbeReq           |    | - Thu thập chỉ số MAC (p_c, Retry)  |
| - Kiểm tra tiền tố PHY (RL-SIG)        |    | - Thực thi TxPower, Channel, TWT    |
+----------------------------------------+    +-------------------------------------+
                                                                 | Đặt lại nguồn cứng
                                                                 v
                                               +-------------------------------------+
                                               | Ổ CẮM THÔNG MINH SONOFF S26 R2      |
                                               | (Watchdog qua REST API tự xây dựng) |
                                               +-------------------------------------+
```

- **Phần cứng truy cập cấp doanh nghiệp:** bộ định tuyến Xiaomi AX3000T sử dụng bộ xử lý ARM Cortex-A53 hai lõi MediaTek MT7981B Filogic 820 (1.3 GHz), chạy firmware Linux OpenWrt tùy chỉnh.
- **Thiết bị bắt telemetry chuyên dụng:** bộ điều hợp Wi-Fi USB ngoài (Realtek RTL8812AU hoạt động ở chế độ giám sát RF — Monitor Mode) được bố trí để bắt các khung quản lý chiếm dụng thời gian phát thô.
- **Nút tính toán biên trung tâm:** máy chủ xử lý biên cục bộ chạy bộ máy chính sách RL và tổng hợp telemetry tốc độ cao.
- **Giao thức truyền thông liên hệ thống hai kênh:**
  - **Kênh telemetry đường lên:** socket gRPC Streaming thông lượng cao, truyền theo thời gian thực tỷ lệ va chạm MAC, tiêu đề khung PHY và chỉ số nhiễu từ các daemon OpenWrt đến máy chủ biên với chi phí phụ trội dưới một mili giây.
  - **Kênh điều khiển đường xuống:** JSON-RPC độ trễ thấp qua WebSockets, gửi các lệnh tối ưu hóa (chuyển kênh, điều chỉnh công suất phát, thay đổi tham số EDCA) xuống các daemon bộ định tuyến riêng lẻ.

### Lõi học tăng cường và bản sao số Chaos Engineering

Trí tuệ trung tâm của bộ điều khiển động dựa trên thuật toán PPO đơn tác tử (Single-Agent Proximal Policy Optimization), hoạt động trong môi trường Bản sao số Gymnasium tùy chỉnh. Môi trường quan sát được khởi tạo bằng các ma trận suy hao không gian xuất từ Bản sao số truy vết tia 3D (Phần 2).

Để bảo đảm tính vững chắc của chính sách trước khi triển khai phần cứng thực tế, bản sao số tích hợp các nguyên tắc Chaos Engineering cùng các bộ dữ liệu chuỗi thời gian về hiệu năng Wi-Fi thực tế. Trong quá trình huấn luyện ngoại tuyến, mô phỏng đưa vào các sự kiện gây căng thẳng môi trường nghiêm trọng:

- Chèn nhiễu xung tức thời $+20\,\text{dB}$ để mô phỏng nhiễu từ lò vi sóng bên ngoài hoặc các nguồn không phải Wi-Fi.
- Chuỗi rơi gói ngẫu nhiên theo đợt ở mức 15–50% để mô phỏng kênh suy hao đột ngột.
- Các đỉnh độ trễ đột ngột vượt quá 300 ms để mô phỏng tắc nghẽn hàng đợi nghiêm trọng.
- Đưa vào các AP giả mạo không được quản lý, hoạt động với chu kỳ công tác cao trên các tần số kênh chính.

Huấn luyện dưới các nhiễu loạn môi trường hỗn loạn giúp ngăn chính sách khớp quá mức, bảo đảm hành vi điều khiển ổn định khi triển khai trên phần cứng biên thực tế.

### Cơ chế cảm biến kép (không cần CSI, 0% tự gây nhiễu, không có lỗi OOM)

Các phương pháp quản lý phổ tần động truyền thống thường dựa vào việc trích xuất ma trận Thông tin trạng thái kênh (CSI) thô. Tuy nhiên, thu thập và xử lý các ma trận CSI thô nhiều chiều đòi hỏi cấp phát bộ nhớ lớn, có thể dẫn đến lỗi kernel panic do hết bộ nhớ (OOM) trên bộ định tuyến nhúng (giới hạn RAM 256–512 MB). Ngoài ra, các khung dò CSI tường minh tạo thêm chi phí tự gây nhiễu trong thời gian phát sóng.

Để khắc phục các giới hạn này, hệ thống triển khai **Cơ chế cảm biến kép không cần CSI, có trọng lượng xử lý thấp**:

```text
                     LUỒNG THỜI GIAN PHÁT RF THÔ
                                      |
                  +-------------------+-------------------+
                  |                                       |
                  v                                       v
       [LỚP 1: CẢM BIẾN MAC]                    [LỚP 2: TIỀN TỐ PHY]
       - Xác suất va chạm (p_c)                 - Kiểm tra RL-SIG (4 us)
       - Tỷ lệ truyền lại (RTS/CTS & ACK)         sau L-SIG (4 us)
       - Bắt khung bằng Scapy                   - Phép quay BPSK: chòm sao 90 độ
         (Dot11Beacon / ProbeReq)               - Phân biệt HE SU / HE MU / HE TB
                                                  - Phân tích L_Rate -> giải mã HE-SIG-B
                  |                                       |
                  +-------------------+-------------------+
                                      |
                                      v
                      TELEMETRY TỔNG HỢP NHẸ
                  (0% tự gây nhiễu, không có nguy cơ OOM)
```

#### 1. Cảm biến lớp MAC

Sử dụng các daemon phân tách khung nhẹ (tệp thực thi bọc Scapy) chạy trên các nút bắt gói RTL8812AU chuyên dụng. Các daemon suy ra chỉ số tranh chấp lớp MAC bằng cách theo dõi:

- Xác suất va chạm khung ở lớp MAC ($p_c$), tính từ tỷ lệ khung unicast không được xác nhận.
- Tỷ lệ truyền lại khung (`Retry_Rate`), suy ra bằng cách kiểm tra bit retry trong tiêu đề MAC.
- Theo dõi mật độ trạm đang hoạt động thông qua trích xuất khung quản lý Dot11Beacon và ProbeReq.

#### 2. Cảm biến tiền tố PHY (theo nguyên lý Bằng sáng chế Hoa Kỳ 10,637,706 B2)

Để thu thập trạng thái kênh ở lớp vật lý mà không cần ma trận CSI thô, thiết bị bắt gói kiểm tra cấu trúc tiền tố vật lý của các PPDU IEEE 802.11ax. Cụ thể, thiết bị kiểm tra trường Repeated Legacy Signal (RL-SIG) có thời lượng $4\,\mu\text{s}$, nằm liền kề ngay sau trường Legacy Signal (L-SIG) tiêu chuẩn có thời lượng $4\,\mu\text{s}$.

**Kiến trúc tiền tố PPDU IEEE 802.11ax:**

```text
+---------+---------+---------+---------+------------+---------+---------+
|  L-STF  |  L-LTF  |  L-SIG  | RL-SIG  | HE-SIG-A   | HE-STF  | HE-LTF  |
|  8 us   |  8 us   |  4 us   |  4 us   | 8 hoặc 16us|  4 us   | 4-16 us |
+---------+---------+---------+---------+------------+---------+---------+
                                   ^
                                   | Phân tích phép quay chòm sao BPSK
                                   +-> Nhận diện tức thời định dạng HE
```

Trong 802.11ax, trường RL-SIG truyền một bản sao chính xác từng bit của trường L-SIG, nhưng áp dụng ánh xạ chòm sao khóa dịch pha nhị phân (BPSK) đã quay pha $90^\circ$ trên các sóng mang con. Bằng cách kiểm tra phép quay chòm sao bằng xử lý tín hiệu số (DSP) theo thời gian thực trên ký hiệu RL-SIG dài $4\,\mu\text{s}$, thiết bị bắt gói phân biệt các PPDU Hiệu suất cao (HE) với khung HT/VHT đời cũ mà không cần xử lý tải dữ liệu.

Ngoài ra, phân tích trường tốc độ legacy (`L_Rate`) cho phép phần cứng giải mã cấu trúc High Efficiency Signal-B (HE-SIG-B) trong các PPDU HE MU. Quá trình này trích xuất sơ đồ điều chế và mã hóa (MCS), phân bổ luồng không gian và bản đồ gán RU trực tiếp từ tiêu đề PHY. Bằng cách chỉ phân tích tiêu đề tiền tố, cơ chế cảm biến kép tạo ra mức tự gây nhiễu 0% và sử dụng dưới 2 MB bộ nhớ, loại bỏ lỗi kernel OOM trên bộ định tuyến.

### Kiến trúc an toàn kép (Dual Fail-Safe)

Để bảo đảm độ sẵn sàng vận hành cao (thời gian hoạt động 99.99%), kiến trúc tích hợp cấu trúc phục hồi an toàn nhiều tầng:

1. **Daemon Soft Watchdog (`cognitive-agent`):** daemon nền nhẹ chạy trên Linux OpenWrt, theo dõi các vòng socket gRPC/JSON-RPC đang hoạt động. Nếu tiến trình điều khiển gặp lỗi hoặc mất kết nối với máy chủ biên trong hơn 30 giây, watchdog khởi động lại ngăn xếp định tuyến và khôi phục các hồ sơ vận hành dự phòng tĩnh.
2. **Đặt lại nguồn cứng bằng ổ cắm thông minh:** nếu AP gặp lỗi kernel hoặc treo phần cứng không phản hồi tín hiệu phần mềm, watchdog biên bên ngoài kích hoạt đặt lại nguồn vật lý bằng ổ cắm thông minh Sonoff S26 R2 thông qua REST API cục bộ.
3. **Khôi phục bootloader tự động (UART/TFTP):** nếu bộ nhớ flash bị hỏng trong quá trình cập nhật cấu hình khi hệ thống đang chạy, các tập lệnh khôi phục tự động gửi lệnh bootloader qua cổng nối tiếp UART, khôi phục ảnh firmware sạch qua TFTP mà không cần can thiệp vật lý thủ công.

### AI xanh so với nguyên tắc Thượng Tôn Chất Lượng Người Dùng

Mặc dù giảm thiểu năng lượng là tham số vận hành quan trọng, mục tiêu này vẫn hoàn toàn đứng sau nguyên tắc Thượng Tôn Chất Lượng Người Dùng. Cấm tắt hoàn toàn nguồn của AP trong giờ vận hành; tắt radio sẽ tạo ra lỗ hổng vùng phủ sóng, làm giảm chất lượng liên kết và gây ra các đợt thiết bị đầu cuối kết nối lại ồ ạt.

Thay vào đó, việc giảm công suất phát (`Tx Power`) chỉ được giới hạn trong giờ nghỉ, khi vectơ thời gian theo thời gian thực xác nhận tỷ lệ sử dụng phòng là 0%. Trong các khoảng thời gian này, AP quản lý lịch ngủ của thiết bị bằng Target Wake Time (TWT) theo IEEE 802.11ax và điều chỉnh tham số EDCA. TWT cấp lịch thức dậy rời rạc cho các thiết bị IoT và thiết bị đầu cuối đã kết nối, cho phép radio của chúng duy trì trạng thái ngủ cho đến khi đến các cửa sổ truyền dẫn được chỉ định.

### Bảng điều khiển có con người tham gia vòng lặp (HITL)

Giao diện Bảng điều khiển Streamlit theo thời gian thực cung cấp cho quản trị viên khả năng quan sát mạng và ghi đè điều khiển thủ công:

- Hiển thị trực tiếp mức sử dụng kênh theo không gian, phân bố PER và các chỉ số độ trễ ở phân vị thứ 99.
- Trực quan hóa các điều chỉnh tham số của tác tử PPO trên các ô phủ sóng được quản lý.
- Cung cấp điều khiển ghi đè thủ công để quản trị viên khóa tham số kênh cụ thể, đóng băng không gian hành động của tác tử hoặc kích hoạt ngay chế độ dự phòng tĩnh trên toàn địa điểm trong các sự kiện bảo trì.

### Kiến trúc triển khai thực tế

Hệ thống hỗ trợ ba mô hình triển khai phần cứng nhằm đáp ứng quy mô doanh nghiệp và giới hạn ngân sách khác nhau:

| Phương án triển khai | Kiến trúc thực thi tại biên | Môi trường mục tiêu | Chi phí phần cứng phát sinh | Khả năng mở rộng và chịu lỗi |
|---|---|---|---|---|
| **Phương án 1: Bộ điều khiển chính trên bộ định tuyến** | Tệp nhị phân PPO C++/ONNX Runtime (dấu chân bộ nhớ dưới 2 MB) được nhúng trực tiếp vào RAM bộ định tuyến | Văn phòng chi nhánh nhỏ, cửa hàng bán lẻ (1–5 AP) | 0 USD (chạy trên phần cứng bộ định tuyến sẵn có) | Tự chủ hoàn toàn tại chỗ; thực thi nhẹ với số lượng ô nhỏ. |
| **Phương án 2: Mini PC / Raspberry Pi biên** | Cổng biên cục bộ chạy Gymnasium Python và môi trường thực thi tác tử PPO | Trường học, văn phòng doanh nghiệp cỡ vừa (10–50 AP) | 50–100 USD cho mỗi cổng tại địa điểm | Phối hợp nhiều ô cục bộ hiệu quả; vòng điều khiển dưới một mili giây. |
| **Phương án 3: Quản lý qua đám mây, có dự phòng cục bộ** | Điều phối tập trung trên đám mây, kết hợp các tác tử dự phòng cục bộ tự chủ trên phần cứng biên | Khuôn viên doanh nghiệp nhiều tòa nhà (trên 50 AP) | Phí đăng ký dịch vụ đám mây | Tối ưu hóa chính sách toàn cục trên hàng nghìn ô; dự phòng tại chỗ bảo đảm hoạt động khi WAN mất kết nối. |

Sau khi mô tả kiến trúc vận hành và cơ chế cảm biến động, báo cáo chuyển sang hình thức hóa toán học quá trình ra quyết định và các chiến thuật thực thi ở lớp MAC.

---

## 4. TỔNG HỢP MÔ HÌNH MDP VÀ CÁC CHIẾN THUẬT VẬN HÀNH LỚP MAC

### Bối cảnh và tầm quan trọng chiến lược

Để đưa các vòng điều khiển động vào vận hành, những mục tiêu hiệu năng QoE trừu tượng phải được chuyển đổi bằng toán học thành không gian trạng thái-hành động của học tăng cường và thực thi thông qua cơ chế lớp MAC mức thấp của IEEE 802.11ax. Phần này trình bày hình thức hóa Quá trình quyết định Markov (MDP), phân tích hội tụ gradient của hàm thưởng và phác thảo các chiến thuật vận hành MAC tương ứng.

### Hình thức hóa quá trình quyết định Markov (MDP)

Bộ máy điều khiển mô hình hóa việc quản lý mạng động thành một Quá trình quyết định Markov rời rạc theo thời gian, được xác định bởi bộ $ (\mathcal{S},\mathcal{A},\mathcal{P},\mathcal{R},\gamma) $.

```text
+---------------------------------------------------------------------------------+
|                                  MÔI TRƯỜNG                                     |
|                                                                                 |
|  Quan sát trạng thái S_t thuộc Box(7)                                           |
|  [Norm_RSSI, Noise_Floor, STA_Count, Retry_Rate, p_c, Airtime_Util, Time_Vec]   |
+---------------------------------------------------------------------------------+
        |                                                                    ^
        |                                                                    | Thực thi hành động A_t
        v                                                                    | - Phân bổ kênh
+-----------------------+                                         +-----------------------+
|                       |        Tạo ra phần thưởng vô hướng R_t  |                       |
|  CHÍNH SÁCH TÁC TỬ PPO|---------------------------------------->| BỘ MÁY THỰC THI CHIẾN |
|                       |  R_t = w1*(Tput/Tput_max)               | THUẬT LỚP MAC          |
|                       |      - w2*max(0, Latency-15ms)^2        |                       |
+-----------------------+      - w3*(PER + mu*RET) - Penalty      +-----------------------+
```

#### 1. Không gian trạng thái \((\mathcal{S}\in\text{Box}(7))\)

Vectơ trạng thái mà tác tử PPO quan sát tại bước thời gian $t$ gồm 7 chiều liên tục đã chuẩn hóa:

$$
S_t=\Big[\text{Norm\_RSSI},\ \text{Noise\_Floor},\ \text{STA\_Count},\ \text{Retry\_Rate},\ p_c,\ \text{Airtime\_Util},\ \text{Time\_Context\_Vector}\Big]
$$

Trong đó:

- `Norm_RSSI` $\in[0,1]$: chỉ báo cường độ tín hiệu thu trung bình của các thiết bị đã kết nối, chuẩn hóa trên khoảng $[-90\,\text{dBm},-30\,\text{dBm}]$.
- `Noise_Floor` $\in[0,1]$: mức nhiễu RF nền, chuẩn hóa trên khoảng $[-110\,\text{dBm},-60\,\text{dBm}]$.
- `STA_Count` $\in[0,1]$: số trạm đang kết nối, tính tương đối so với giới hạn dung lượng của ô phủ sóng.
- `Retry_Rate` $\in[0,1]$: tỷ lệ khung được truyền lại tại lớp MAC, đo được.
- $p_c\in[0,1]$: xác suất va chạm MAC ước lượng từ tỷ lệ thất bại RTS/CTS.
- `Airtime_Util` $\in[0,1]$: tỷ lệ thời gian kênh bị chiếm bởi truyền dẫn đồng kênh và hoạt động OBSS.
- `Time_Context_Vector`: mảng thời gian `[Hour, Minute, Is_Break_Time]`, mã hóa các biến động sử dụng theo chu kỳ.

#### 2. Không gian hành động \((\mathcal{A}\in\text{MultiDiscrete})\)

Tác tử lựa chọn các vectơ hành động rời rạc điều khiển 4 tham số vật lý và lớp liên kết:

$$
\mathcal{A}=\Big\{\text{Channel\_Allocation},\ \text{Tx\_Power\_Matching},\ \text{Beacon\_Interval},\ \text{Bandwidth\_Elasticity}\Big\}
$$

- **Phân bổ kênh:** chọn các kênh vận hành không chồng lấn: $\{1,6,11\}$ ở băng tần 2.4 GHz và $\{36,44,149,157\}$ ở băng tần 5 GHz.
- **Khớp công suất phát:** chọn các mức công suất phát $\{10\,\text{dBm},14\,\text{dBm},20\,\text{dBm}\}$.
- **Điều chỉnh khoảng thời gian Beacon:** chọn các cấu hình thời gian Beacon: $\{100\,\text{ms (lưu lượng dày đặc/giờ nghỉ)},300\,\text{ms (lưu lượng thấp/giờ học)}\}$.
- **Tính linh hoạt băng thông và đục lỗ tiền tố:** chọn độ rộng kênh vận hành $\{20\,\text{MHz},40\,\text{MHz},80\,\text{MHz}\}$. Khi vận hành ở 80 MHz hoặc 160 MHz, tác tử có thể bật ghép kênh không liền kề IEEE 802.11ax (các chế độ đục lỗ tiền tố 4–7) nếu các kênh 20 MHz phụ bị nhiễu OBSS cản trở:
  - **Chế độ 4:** tổng băng thông 80 MHz, đục lỗ kênh 20 MHz phụ (còn 60 MHz băng thông sử dụng được).
  - **Chế độ 5:** tổng băng thông 80 MHz, đục lỗ một kênh 20 MHz nằm trong khối 40 MHz phụ.
  - **Chế độ 6/7:** băng thông 160 MHz (80+80 MHz), đục lỗ các kênh 20 MHz phụ trong khối 80 MHz chính hoặc phụ.

#### 3. Hàm thưởng phi tuyến \((R_t)\)

Giá trị thưởng vô hướng tại bước $t$ được xác định như sau:

$$
R_t=w_1\cdot\left(\frac{\text{Throughput}}{\text{Throughput}_{\max}}\right)-w_2\cdot\max(0,\text{Latency}-15\text{ms})^2-w_3\cdot(\text{PER}+\mu\cdot\text{Retry\_Rate})-\text{Penalty}_{\text{FailSafe}}
$$

Trong đó $w_1,w_2,w_3,\mu>0$ là các siêu tham số điều chỉnh tỷ lệ.

### Phân tích gradient toán học và hội tụ chính sách

Để đánh giá hành vi hội tụ của chính sách, chúng ta xem xét các đạo hàm riêng của $R_t$ theo trọng số tham số và thành phần trạng thái, phân tích tác động của chúng lên các cập nhật gradient chính sách dưới hàm mục tiêu PPO $\mathcal{L}^{\text{CLIP}}(\theta)$:

1. **Số hạng thông lượng tuyến tính có chặn** ($w_1\cdot\frac{\text{Throughput}}{\text{Throughput}_{\max}}$): đạo hàm
   $$\frac{\partial R_t}{\partial\text{Throughput}}=\frac{w_1}{\text{Throughput}_{\max}}$$
   tạo ra vectơ gradient dương không đổi trong trạng thái vận hành bình thường ($\text{Latency}\le15\,\text{ms}$). Điều này hướng chính sách đến lựa chọn tham số có thông lượng cao khi điều kiện liên kết ổn định.
2. **Mức phạt độ trễ bậc hai** ($-w_2\cdot\max(0,\text{Latency}-15\text{ms})^2$): đặt độ vi phạm độ trễ là $\Delta_L=\max(0,\text{Latency}-15\text{ms})$, hành vi gradient có dạng từng đoạn:
   $$
   \frac{\partial R_t}{\partial\text{Latency}}=\begin{cases}0 & \text{nếu }\text{Latency}\le15\text{ms}\-2w_2\cdot\Delta_L & \text{nếu }\text{Latency}>15\text{ms}\end{cases}
   $$
   Khi $\text{Latency}\le15\,\text{ms}$, gradient bằng 0, ngăn việc tối thiểu hóa độ trễ làm triệt tiêu tối ưu hóa thông lượng. Tuy nhiên, khi $\text{Latency}>15\,\text{ms}$, độ lớn gradient tăng tuyến tính theo $\Delta_L$ (do đó mức phạt là bậc hai). Điều này loại bỏ hiện tượng bão hòa gradient khi tắc nghẽn nghiêm trọng, tạo ra các cập nhật lợi thế $\hat{A}_t$ lớn, buộc phân phối chính sách $\pi_\theta(a|s)$ tránh xa các hành động có độ trễ cao.
3. **Mức phạt mất gói và truyền lại** ($-w_3\cdot(\text{PER}+\mu\cdot\text{Retry\_Rate})$): mất khung không thể khôi phục (`PER`) làm suy giảm cửa sổ TCP ở tầng trên, trong khi truyền lại (`Retry_Rate`) tiêu tốn thêm thời gian phát mà không nhất thiết làm rơi gói tin. Siêu tham số $\mu\in(0,1)$ cân bằng hai thành phần này. Vectơ gradient $\nabla_\theta R_t$ phân bổ trọng số cho giảm mất gói tương quan với truyền lại chiếm thời gian phát:
   $$\frac{\partial R_t}{\partial\text{PER}}=-w_3,\qquad\frac{\partial R_t}{\partial\text{Retry\_Rate}}=-w_3\cdot\mu$$
   Đặt $\mu\approx0.25$ giúp ngăn tác tử loại bỏ kênh một cách quá quyết liệt khi tranh chấp tăng đột biến trong thời gian ngắn, duy trì độ ổn định gradient và ưu tiên giảm mất khung không thể khôi phục.
4. **Mức phạt bước an toàn dự phòng** (`Penalty_FailSafe`): nếu lựa chọn hành động dẫn đến sự cố hệ thống, tràn bộ đệm hoặc kích hoạt watchdog, một mức phạt hằng số lớn ($\text{Penalty}_{\text{FailSafe}}\gg100$) sẽ được đưa vào. Trong phương trình kỳ vọng Bellman:
   $$V^\pi(s_t)=\mathbb{E}_{\pi}\left[R_t+\gamma V^\pi(s_{t+1})\right]$$
   việc đưa `Penalty_FailSafe` vào làm giảm mạnh ước lượng giá trị trạng thái $V(s_t)$, tạo ra ước lượng lợi thế âm $\hat{A}_t=R_t+\gamma V(s_{t+1})-V(s_t)$. Với cơ chế cắt tỷ số chính sách PPO:
   $$
   \mathcal{L}^{\text{CLIP}}(\theta)=\hat{\mathbb{E}}_t\left[\min\left(r_t(\theta)\hat{A}_t,\ \text{clip}(r_t(\theta),1-\epsilon,1+\epsilon)\hat{A}_t\right)\right]
   $$
   cập nhật lợi thế âm làm giảm tỷ số xác suất $r_t(\theta)=\frac{\pi_\theta(a_t|s_t)}{\pi_{\theta_{\text{old}}}(a_t|s_t)}$, hướng phân phối tìm kiếm tránh khỏi các vùng hành động dễ gây sự cố.

### Ma trận chiến thuật vận hành lớp MAC

Khi tác tử PPO chọn một vectơ hành động, quyết định sẽ được chuyển thành các cập nhật tham số cụ thể ở cấp nhân Linux, `hostapd` và trình điều khiển, rồi thực thi trên AP mục tiêu:

| Chiến thuật vận hành MAC | Cờ cấu hình trình điều khiển/Linux | Giá trị tham số tiêu chuẩn | Tác động giao thức và RF |
|---|---|---|---|
| **Thực thi tốc độ cơ bản** | `hostapd.conf`<br>`basic_rates=120 240` | Tắt các tốc độ legacy 1, 2, 5.5, 11 Mbps; bắt buộc tốc độ cơ bản 12 Mbps hoặc 24 Mbps | Tắt các tốc độ quản lý legacy chậm, giảm thời gian phát tiêu tốn cho khung beacon và probe, giải phóng dung lượng sóng mang con cho luồng dữ liệu. |
| **Tinh giản SSID theo không gian địa lý** | `hostapd.conf`<br>`ignore_broadcast_ssid=1` | Hạn chế phát quảng bá; điều khiển công suất phát động theo từng ô không gian | Giảm chi phí beacon của các SSID không thiết yếu trong ô mật độ cao, bảo toàn dung lượng kênh beacon. |
| **Danh sách VIP và Beacon Baiting** | Chèn khung Scapy tùy chỉnh qua thiết bị bắt gói | Chèn các khung `Dot11Beacon` / `ProbeReq` được định hướng | Đánh thức thiết bị đầu cuối đã kết nối khỏi trạng thái ngủ sâu trước các hàng đợi đường xuống đã lập lịch, giảm các đỉnh độ trễ đuôi. |
| **Tinh chỉnh EDCA cho vùng ưu tiên luôn hoạt động** | Registry trình điều khiển / `iw`<br>`wme_ac_vi_aifs=2`<br>`wme_ac_vi_cwmin=7`<br>`wme_ac_vi_cwmax=15` | Giảm khoảng cách giữa các khung phân xử (AIFS) và giới hạn cửa sổ tranh chấp (CW) cho hàng đợi Video/Voice | Tạo quyền ưu tiên tranh chấp cho luồng nhạy với độ trễ, bảo đảm truyền trong dưới 15 ms khi tải thiết bị đầu cuối cao. |
| **Tái sử dụng không gian (OBSS_PD và BSS Color)** | `hostapd.conf`<br>`he_bss_color=1..63`<br>`iw dev wlan0 set obss_pd -62` | ID BSS Color 6 bit; điều chỉnh ngưỡng OBSS_PD từ -82 dBm lên -62 dBm | Cho phép trạm phân biệt khung Intra-BSS với Inter-BSS; nâng ngưỡng CCA để cho phép truyền dẫn chồng lấn đồng thời. |
| **Tham số hóa UORA và điều khiển tranh chấp** | `hostapd.conf`<br>`uora_ocw_min=3`<br>`uora_ocw_max=7` | Điều chỉnh cửa sổ tranh chấp OFDMA (OCWmin, OCWmax) và cơ chế lùi OFDMA (OBO) | Điều chỉnh động cửa sổ tranh chấp truy nhập ngẫu nhiên trên các RU truy nhập ngẫu nhiên dựa trên trigger, theo xác suất va chạm đo được $p_c$. |

### Cơ chế kỹ thuật của tái sử dụng không gian và điều khiển UORA

#### 1. Tái sử dụng không gian thông qua BSS Coloring và OBSS_PD

Trong CSMA/CA truyền thống của IEEE 802.11, một trạm trì hoãn truyền dẫn bất cứ khi nào phát hiện năng lượng đồng kênh vượt ngưỡng Đánh giá kênh rỗi (CCA) tiêu chuẩn là $-82\,\text{dBm}$. Trong môi trường triển khai mật độ cao, điều này gây ra việc trì hoãn không cần thiết do các ô chồng lấn ở xa (OBSS).

```text
      Ô A (BSS Color 12)                        Ô B (BSS Color 45)
+-------------------------+                 +-------------------------+
| [AP A]                  |                 | [AP B]                  |
|   |                     |                 |   |                     |
|   v (Đang truyền)       |                 |   v (Muốn truyền)       |
| [STA A1]                |                 | [STA B1]                |
+-------------------------+                 +-------------------------+
             \                                   /
              \--- Nhận được khung liên BSS ---/
                   Cường độ tín hiệu: -72 dBm

CSMA/CA cũ: -72 dBm > ngưỡng CCA -82 dBm -> TRÌ HOÃN TRUYỀN (bị chặn)
802.11ax SR: BSS Color 12 != 45 (liên BSS)
            -72 dBm < ngưỡng OBSS_PD -62 dBm -> TRUYỀN SONG SONG
```

Trong chiến thuật tái sử dụng không gian, mỗi khung IEEE 802.11ax có trường BSS Color 6 bit trong tiêu đề PHY (giá trị 1–63). Khi trạm nhận phát hiện khung đến, nó kiểm tra BSS Color:

- **Khung Intra-BSS (màu khớp):** trạm áp dụng ngưỡng CCA tiêu chuẩn ($-82\,\text{dBm}$).
- **Khung Inter-BSS (màu khác):** trạm áp dụng ngưỡng OBSS_PD cao hơn (ví dụ $-62\,\text{dBm}$).

Nếu cường độ tín hiệu nhận của khung Inter-BSS thấp hơn ngưỡng OBSS_PD đã nâng (ví dụ $-72\,\text{dBm}<-62\,\text{dBm}$), trạm xem kênh là rỗi và bắt đầu truyền song song trên cùng kênh, làm tăng dung lượng không gian tổng hợp.

#### 2. Điều khiển truy nhập ngẫu nhiên dựa trên UL OFDMA (UORA)

Khi AP lập lịch truyền đường lên bằng các khung Trigger (TF), một số RU được chỉ định cho truy nhập ngẫu nhiên bằng cách gán trường con Association ID là $\text{AID12}=0$. Các trạm đầu cuối tranh chấp những RU truy nhập ngẫu nhiên này thực thi giao thức UORA:

```text
[AP gửi khung Trigger] -> Cho biết N_RU RU truy nhập ngẫu nhiên (AID12 = 0)
        |
[STA 1 thực thi UORA]
  - Duy trì bộ đếm lùi OFDMA (OBO) ~ Uniform(0, OCW)
  - Kiểm tra số RU khả dụng:
      Nếu OBO < N_RU:
          OBO = 0 -> Chọn RU ngẫu nhiên -> Truyền khung dữ liệu đường lên
      Ngược lại:
          OBO = OBO - N_RU -> Chờ khung Trigger tiếp theo
```

Mỗi trạm duy trì bộ đếm lùi OFDMA (`OBO`) được lấy theo phân bố đều trên $[0,\text{OCW}]$, trong đó `OCW` là Cửa sổ tranh chấp OFDMA ($\text{OCW}_{\min}\le\text{OCW}\le\text{OCW}_{\max}$). Các tham số $\text{OCW}_{\min}$ và $\text{OCW}_{\max}$ được quảng bá trong khung Beacon. Khi khung Trigger đến với $N_{\text{RU}}$ RU truy nhập ngẫu nhiên:

- Nếu $\text{OBO}<N_{\text{RU}}$, trạm đặt $\text{OBO}=0$, chọn ngẫu nhiên một RU trong số các RU ứng viên và truyền gói đường lên.
- Nếu $\text{OBO}\ge N_{\text{RU}}$, trạm giảm bộ đếm ($\text{OBO}\leftarrow\text{OBO}-N_{\text{RU}}$) và hoãn truyền đến khung Trigger tiếp theo.

Nếu xảy ra va chạm trên RU truy nhập ngẫu nhiên (thể hiện qua việc thiếu xác nhận khối đa người dùng Multi-User Block ACK), trạm tăng gấp đôi cửa sổ tranh chấp ($\text{OCW}\leftarrow\min(2\cdot\text{OCW}+1,\text{OCW}_{\max})$). Tác tử PPO động theo dõi xác suất va chạm MAC $p_c$ qua telemetry gRPC. Khi $p_c$ tăng trong các giai đoạn mật độ cao, tác tử điều chỉnh $\text{OCW}_{\min}$ và $\text{OCW}_{\max}$ trong các khung Beacon, duy trì tính ổn định của UORA và ngăn chuỗi va chạm truy nhập ngẫu nhiên lan rộng.

### Tổng hợp hệ thống và kết luận

Hệ thống Tối ưu hóa Cognitive Wi-Fi đầu cuối (KHKT 2026) hợp nhất kỹ thuật hạ tầng vô tuyến vật lý với các vòng điều khiển học tăng cường động. Bằng cách kết nối thu thập đặc trưng không gian (quét LiDAR, mô phỏng truy vết tia 3D, phân hạng phần cứng theo mật độ) với lớp vận hành tự chủ (cảm biến kép không cần CSI, tác tử PPO được huấn luyện với Chaos Engineering và cơ chế an toàn dự phòng nhiều tầng), khuôn khổ này giải quyết các giới hạn mang tính cấu trúc của hạ tầng Wi-Fi tĩnh. Các tham số tiện ích trừu tượng được ánh xạ trực tiếp thành những chiến thuật MAC IEEE 802.11ax mức thấp, bao gồm BSS Coloring, thiết lập ngưỡng tái sử dụng không gian, đục lỗ tiền tố, chia RU theo sóng mang con và quản lý tranh chấp UORA. Dưới sự dẫn dắt của triết lý Thượng Tôn Chất Lượng Người Dùng, khuôn khổ tích hợp hướng tới đáp ứng các chuẩn hiệu năng định lượng: giảm chi phí đầu tư ít nhất 20%, giảm chi phí vận hành thiết lập 30–50% và cải thiện tổng thể tiện ích, chất lượng Wi-Fi trên 60% so với mô hình triển khai tĩnh truyền thống.

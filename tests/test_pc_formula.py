# ==============================================================================
# File: /src/test_pc_formula.py
# Mô tả: Kiểm chứng toán học công thức tính Xác suất va chạm tầng MAC (p_c)
# ==============================================================================

def calculate_pc(rts_sent, data_sent, cts_recv, ack_recv):
    """
    Hàm tính toán Xác suất va chạm tầng MAC (p_c)
    dựa trên tỷ lệ khung phản hồi thành công (CTS, ACK) so với khung gửi đi (RTS, DATA).
    """
    # Hằng số làm trơn (epsilon) để tránh lỗi chia cho 0 khi không có lưu lượng mạng
    epsilon = 1e-6 
    
    # Mẫu số: Tổng số khung tin yêu cầu băng thông và khung dữ liệu phát đi
    denominator = rts_sent + data_sent + epsilon
    
    # Tử số: Tổng số khung tin xác nhận đã nhận thành công
    numerator = cts_recv + ack_recv
    
    # Tính toán xác suất va chạm (p_c = 1 - Tỷ lệ thành công)
    p_c = 1.0 - (numerator / denominator)
    
    # Đảm bảo p_c không bị âm do nhiễu hoặc sai số dấu phẩy động (float)
    return max(0.0, p_c)

def run_tests():
    print("=== BẮT ĐẦU KIỂM CHỨNG TOÁN HỌC CÔNG THỨC p_c ===\n")
    
    # ---------------------------------------------------------
    # KỊCH BẢN 1: MẠNG LÝ TƯỞNG (Không đụng độ)
    # Gửi đi 100 RTS và 400 DATA (Tổng = 500). 
    # Nhận lại đủ 100 CTS và 400 ACK (Tổng = 500).
    # ---------------------------------------------------------
    rts_s_1, data_s_1 = 100, 400
    cts_r_1, ack_r_1 = 100, 400
    pc_ideal = calculate_pc(rts_s_1, data_s_1, cts_r_1, ack_r_1)
    
    print("[Kịch bản 1] Mạng thông thoáng lý tưởng (Nhận đủ 100%):")
    print(f"  - Gửi: RTS={rts_s_1}, DATA={data_s_1} | Nhận: CTS={cts_r_1}, ACK={ack_r_1}")
    print(f"  -> p_c tính được: {pc_ideal:.2f} (Kỳ vọng: 0.00)\n")
    
    # ---------------------------------------------------------
    # KỊCH BẢN 2: MẠNG BỊ NGHẼN (Mất 20% phản hồi do va chạm)
    # Gửi đi 100 RTS và 400 DATA (Tổng = 500).
    # Bị đụng độ mất 20%, tức là chỉ nhận được 80% (Tổng nhận = 400).
    # Giả sử nhận được 80 CTS và 320 ACK.
    # ---------------------------------------------------------
    rts_s_2, data_s_2 = 100, 400
    cts_r_2, ack_r_2 = 80, 320
    pc_congested = calculate_pc(rts_s_2, data_s_2, cts_r_2, ack_r_2)
    
    print("[Kịch bản 2] Mạng bị đụng độ (Mất 20% gói phản hồi):")
    print(f"  - Gửi: RTS={rts_s_2}, DATA={data_s_2} | Nhận: CTS={cts_r_2}, ACK={ack_r_2}")
    print(f"  -> p_c tính được: {pc_congested:.2f} (Kỳ vọng: 0.20)\n")

    # ---------------------------------------------------------
    # KỊCH BẢN 3: LỚP HỌC TRỐNG (Zero Traffic)
    # Kiểm tra tính toàn vẹn của mã khi mẫu số bằng 0.
    # ---------------------------------------------------------
    rts_s_3, data_s_3 = 0, 0
    cts_r_3, ack_r_3 = 0, 0
    pc_empty = calculate_pc(rts_s_3, data_s_3, cts_r_3, ack_r_3)
    
    print("[Kịch bản 3] Không có lưu lượng mạng (Zero Traffic):")
    print(f"  - Gửi: 0 | Nhận: 0")
    print(f"  -> p_c tính được: {pc_empty:.6f} (Không bị lỗi ZeroDivisionError)")

if __name__ == "__main__":
    run_tests()
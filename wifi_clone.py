import os
import re
import platform
import subprocess
from collections import Counter
from flask import Flask, jsonify, render_template

app = Flask(__name__)

def parse_windows_wifi():
    """Quét và phân tích mạng Wi-Fi trên Windows thông qua netsh"""
    networks = []
    try:
        # Chạy trực tiếp list lệnh (không cần shell=True để tránh xung đột tham số)
        cmd = ["netsh", "wlan", "show", "networks", "mode=bssid"]
        raw_output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        
        try:
            output = raw_output.decode("utf-8")
        except UnicodeDecodeError:
            output = raw_output.decode("cp437", errors="ignore")

        # Chuẩn hóa ngắt dòng CRLF thành LF
        output = output.replace("\r\n", "\n")

        # Phân tách theo từng SSID
        ssid_sections = re.split(r"\nSSID \d+\s*:", output)
        
        for section in ssid_sections[1:]:
            lines = section.strip().splitlines()
            if not lines:
                continue
            
            # Lọc đúng dòng tên SSID, loại bỏ các dòng Network type hoặc rác
            ssid = lines[0].strip()
            if not ssid or "Network type" in ssid or "Loại mạng" in ssid:
                ssid = "[Mạng ẩn / Hidden SSID]"

            # Nhận diện chuẩn mạng và mã hóa
            net_type = "802.11"
            auth = "Mở"
            for line in lines:
                l = line.strip()
                if l.startswith("Network type") or l.startswith("Loại mạng"):
                    net_type = l.split(":", 1)[-1].strip()
                elif l.startswith("Authentication") or l.startswith("Xác thực"):
                    auth = l.split(":", 1)[-1].strip()

            # Tách các BSSID (Access Point) trong cùng 1 SSID
            bssid_sections = re.split(r"\n\s+BSSID \d+\s*:\s*", section)
            for b_sec in bssid_sections[1:]:
                b_lines = [b.strip() for b in b_sec.strip().splitlines() if b.strip()]
                bssid = b_lines[0].strip() if b_lines else "N/A"
                
                signal_pct = 0
                channel = 0
                radio_type = net_type

                for b_line in b_lines:
                    if b_line.startswith("Signal") or b_line.startswith("Tín hiệu"):
                        match = re.search(r"(\d+)%", b_line)
                        if match:
                            signal_pct = int(match.group(1))
                    elif b_line.startswith("Channel") or b_line.startswith("Kênh"):
                        match = re.search(r"(\d+)", b_line)
                        if match:
                            channel = int(match.group(1))
                    elif b_line.startswith("Radio type") or b_line.startswith("Loại radio"):
                        radio_type = b_line.split(":", 1)[-1].strip()

                # Công thức chuẩn chuyển đổi % sang dBm: dBm = (Signal / 2) - 100
                rssi = int((signal_pct / 2.0) - 100) if signal_pct > 0 else -100

                if channel > 0:
                    networks.append({
                        "ssid": ssid,
                        "bssid": bssid,
                        "rssi": rssi,
                        "percent": signal_pct,
                        "channel": channel,
                        "band": "5 GHz" if channel > 14 else "2.4 GHz",
                        "auth": auth,
                        "radio": radio_type
                    })
    except Exception as ex:
        print(f"[ERROR] Quét Windows thất bại: {ex}")
    
    return networks

def parse_linux_wifi():
    """Quét và phân tích mạng Wi-Fi trên Linux thông qua nmcli"""
    networks = []
    try:
        cmd = ["nmcli", "-t", "-f", "SSID,BSSID,CHAN,SIGNAL,SECURITY,FREQ", "dev", "wifi", "list", "--rescan", "yes"]
        output = subprocess.check_output(cmd, encoding="utf-8", errors="ignore")

        for line in output.strip().splitlines():
            if not line:
                continue
            
            parts = line.split(":")
            if len(parts) >= 6:
                ssid = parts[0] or "[Mạng ẩn / Hidden SSID]"
                # Lọc bỏ các dòng không phải SSID
                if "Network type" in ssid or "Loại mạng" in ssid:
                    ssid = "[Mạng ẩn / Hidden SSID]"
                bssid = ":".join(parts[1:7]) if len(parts) >= 7 else parts[1]
                chan_str = parts[-4] if len(parts) >= 7 else parts[2]
                sig_str = parts[-3] if len(parts) >= 7 else parts[3]
                sec_str = parts[-2] if len(parts) >= 7 else parts[4]
                
                channel = int(chan_str) if chan_str.isdigit() else 1
                signal_pct = int(sig_str) if sig_str.isdigit() else 0
                rssi = int((signal_pct / 2.0) - 100)

                networks.append({
                    "ssid": ssid,
                    "bssid": bssid,
                    "rssi": rssi,
                    "percent": signal_pct,
                    "channel": channel,
                    "band": "5 GHz" if channel > 14 else "2.4 GHz",
                    "auth": sec_str or "Mở",
                    "radio": "802.11"
                })
    except Exception as ex:
        print(f"[ERROR] Quét Linux thất bại: {ex}")

    return networks

def analyze_channels(networks):
    """Tính toán độ nghẽn kênh 2.4 GHz và đề xuất kênh tối ưu (Tính năng WiFi Analyzer)"""
    ch_24ghz = [n["channel"] for n in networks if n.get("band") == "2.4 GHz"]
    counts = Counter(ch_24ghz)
    
    # 3 kênh 2.4 GHz độc lập không chồng lấn chuẩn
    standard_channels = [1, 6, 11]
    recommended = min(standard_channels, key=lambda x: counts.get(x, 0))
    
    return {
        "channel_density": dict(counts),
        "recommended_channel": recommended
    }

@app.route("/")
def home():
    try:
        return render_template("index.html")
    except Exception:
        return (
            "<h2>WiFi Analyzer Backend đang hoạt động!</h2>"
            "<p>Dữ liệu JSON quét mạng: <a href='/api/scan'>/api/scan</a></p>"
        )

@app.route("/api/scan")
def api_scan():
    os_name = platform.system()
    if os_name == "Windows":
        data = parse_windows_wifi()
    elif os_name == "Linux":
        data = parse_linux_wifi()
    else:
        data = []
    
    # Sắp xếp theo cường độ sóng giảm dần
    data.sort(key=lambda item: item["rssi"], reverse=True)
    
    analysis = analyze_channels(data)
    
    return jsonify({
        "total": len(data),
        "networks": data,
        "analysis": analysis
    })

if __name__ == "__main__":
    print("[INFO] Khởi động WiFi Analyzer tại: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
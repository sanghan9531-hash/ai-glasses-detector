"""
AI 안경 탐지 시스템 (시험 감독관용)
===========================================================
시험 중 AI 스마트 안경 사용을 점검하기 위한 감독 보조 프로그램.

  [1] 기기 스캔   : 주변 블루투스 기기 중 AI 안경 의심 기기 식별
  [2] 위치 추적   : 선택한 기기의 신호 세기로 근접도 측정
  [3] 렌즈 탐지   : 웹캠 영상에서 카메라 렌즈 반사광 검출
  [4] 육안 점검   : 감독관 확인용 점검 항목
  [5] 점검 기록   : 점검 결과 저장 및 CSV 내보내기

  * 시뮬레이션 모드: 블루투스·카메라 없이 가상 데이터로 동작 확인

설치 (최초 1회):
    pip install bleak opencv-python pillow
실행:
    python ai_glasses_detector_app.py

본 프로그램은 감독 보조 도구이며, 판정 결과만으로 부정행위를 단정할 수 없습니다.
최종 판단은 반드시 감독관의 직접 확인을 거쳐야 합니다.
"""

import asyncio
import csv
import datetime
import json
import math
import os
import queue
import random
import sys
import threading
import time
from collections import deque

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    from bleak import BleakScanner
    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageTk
    HAS_CAMERA_LIBS = True
except ImportError:
    HAS_CAMERA_LIBS = False

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False


# ═══════════════════════════════════════════════════════════
# 1. 설정 및 기기 시그니처
# ═══════════════════════════════════════════════════════════
APP_TITLE = "AI 안경 탐지 시스템"
VERSION = "1.1"
SCAN_SECONDS = 10
DEMO_SCAN_SECONDS = 3
def app_dir():
    """기록·로그를 저장할 폴더 (exe로 실행하면 exe가 있는 폴더)"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


RECORD_FILE = os.path.join(app_dir(), "ai_glasses_records.json")

GLASSES_NAMES = [
    "ray-ban", "rayban", "meta", "oakley", "xreal", "rokid",
    "even g1", "inmo", "solos", "vuzix", "halliday", "glass",
]

# 블루투스 SIG 제조사 ID - 실기기 스캔으로 검증 필요
GLASSES_COMPANY_IDS = {
    0x01AB: "Meta Platforms",
    0x058E: "Meta Platforms Technologies",
}

NORMAL_COMPANY_IDS = {
    0x004C: "Apple",
    0x0075: "Samsung",
    0x0006: "Microsoft",
}

# 시뮬레이션용 가상 기기: (주소, 이름, 제조사 ID, 신호 세기)
FAKE_DEVICES = [
    ("SIM-01", "Ray-Ban Meta 7F2A", [0x01AB], -58),
    ("SIM-02", "(이름 없음)", [], -52),
    ("SIM-03", "AirPods Pro", [0x004C], -64),
    ("SIM-04", "Galaxy Buds2 Pro", [0x0075], -71),
    ("SIM-05", "Oakley Meta HSTN", [], -80),
    ("SIM-06", "LG TV", [], -86),
    ("SIM-07", "(이름 없음)", [0x0075], -77),
]

CHECKLIST = [
    "안경테(특히 다리 부분)가 일반 안경보다 두껍거나 무겁다",
    "안경 전면 모서리에 소형 렌즈 또는 구멍이 있다",
    "LED 표시등이 있거나 점등된다",
    "안경 다리 안쪽에 충전 단자가 있다",
    "안경 다리에 스피커 구멍이 있다",
    "안경 다리를 반복적으로 만지거나 누른다",
    "작은 소리로 혼잣말을 한다 (음성 명령 의심)",
    "렌즈 안쪽에 화면이나 글자가 비친다",
    "스마트워치, 이어폰 등 다른 전자기기를 착용하고 있다",
]


# ═══════════════════════════════════════════════════════════
# 2. 판별 로직
# ═══════════════════════════════════════════════════════════
def distance_hint(rssi):
    if rssi >= -55:
        return "근거리 (약 1~2m)"
    elif rssi >= -70:
        return "중거리 (교실 내)"
    return "원거리"


def check_device(name, company_ids, rssi):
    """기기 정보를 분석하여 (점수, 등급, 판정 근거)를 반환한다."""
    score = 0
    reasons = []
    name_lower = name.lower()

    for word in GLASSES_NAMES:
        if word in name_lower:
            score += 50
            reasons.append(f"기기명에 '{word}' 포함")
            break

    for company_id in company_ids:
        if company_id in GLASSES_COMPANY_IDS:
            score += 50
            reasons.append(f"제조사 ID 일치: {GLASSES_COMPANY_IDS[company_id]} "
                           f"(0x{company_id:04X})")
        elif company_id in NORMAL_COMPANY_IDS:
            reasons.append(f"일반 기기 제조사: {NORMAL_COMPANY_IDS[company_id]}")

    if score >= 50:
        level = "danger"
    elif name == "(이름 없음)" and not reasons and rssi >= -60:
        level = "check"
        reasons.append("식별 불가 기기가 근거리에서 감지됨")
    else:
        level = "normal"
    return score, level, reasons


def make_result(address, name, company_ids, rssi):
    score, level, reasons = check_device(name, company_ids, rssi)
    return {"address": address, "name": name, "rssi": rssi,
            "score": score, "level": level, "reasons": reasons}


def sort_results(results):
    return sorted(results, key=lambda r: (r["score"], r["rssi"]), reverse=True)


async def real_scan():
    found = await BleakScanner.discover(timeout=SCAN_SECONDS, return_adv=True)
    results = []
    for device, adv in found.values():
        name = adv.local_name or device.name or "(이름 없음)"
        results.append(make_result(device.address, name,
                                   list(adv.manufacturer_data.keys()), adv.rssi))
    return sort_results(results)


def fake_scan():
    time.sleep(DEMO_SCAN_SECONDS)
    return sort_results([make_result(a, n, c, r + random.randint(-4, 4))
                         for a, n, c, r in FAKE_DEVICES])


# ── 위치 추적 ─────────────────────────────────────────────
def rssi_to_proximity(rssi):
    """신호 세기(-95 ~ -35 dBm)를 근접도(0 ~ 100%)로 변환한다."""
    return max(0, min(100, (rssi + 95) * 100 / 60))


def proximity_info(p):
    if p >= 75:
        return "매우 가까움 - 반경 1~2m 이내", "#B91C1C"
    elif p >= 50:
        return "가까움 - 접근 중", "#C2410C"
    elif p >= 25:
        return "보통", "#A16207"
    return "멂", "#1D4ED8"


def beep_interval_ms(p):
    return int(1500 - p * 13)


# ── 렌즈 탐지 ─────────────────────────────────────────────
class GlintDetector:
    """
    카메라 렌즈 반사광 검출기.

    오탐지를 줄이기 위해 세 단계로 걸러낸다.
      1) 국소 대비: 주변보다 확연히 밝은 '작은 점'만 후보로 삼는다.
         (모니터·창문·흰 종이처럼 넓게 밝은 영역과 그 모서리는 제외)
      2) 지속성: 최근 12프레임 중 8프레임 이상 같은 위치에 나타나야 확정한다.
         (카메라 노이즈, 순간적인 광택은 제외)
      3) 기준 화면: 손전등을 켜기 전부터 있던 반사(눈동자 반사, 조명 등)는 제외한다.
    """
    HISTORY = 12          # 지속성 판단에 쓰는 최근 프레임 수
    PERSIST = 8           # 이 중 몇 번 이상 나타나야 확정할지
    NEAR = 12             # 같은 위치로 볼 거리(픽셀)
    BASELINE_FRAMES = 25  # 기준 화면 등록에 쓰는 프레임 수 (약 1초)
    MAX_CANDIDATES = 15   # 후보가 이보다 많으면 '반사 과다 환경'으로 본다
    # 감도(1~5) → 주변 대비 밝기 차이 기준. 감도가 높을수록 약한 반사도 잡는다.
    CONTRAST = {1: 130, 2: 105, 3: 85, 4: 70, 5: 55}
    MIN_PEAK = 200        # 후보 지점의 최소 밝기(0~255)
    RING_IN, RING_OUT = 5, 10

    def __init__(self):
        self.history = deque(maxlen=self.HISTORY)
        self.ignored = []
        self.baseline_buffer = None
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        # 주변 고리(ring) 영역 마스크 미리 계산
        size = self.RING_OUT * 2 + 1
        yy, xx = np.mgrid[:size, :size] - self.RING_OUT
        dist = np.sqrt(xx ** 2 + yy ** 2)
        self.ring_mask = (dist >= self.RING_IN) & (dist <= self.RING_OUT)

    def reset(self):
        self.history.clear()

    def clear_baseline(self):
        self.ignored = []
        self.baseline_buffer = None

    @property
    def capturing_baseline(self):
        return self.baseline_buffer is not None

    def start_baseline(self):
        self.baseline_buffer = []

    def find_candidates(self, frame_bgr, level):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        contrast = self.CONTRAST.get(level, 105)

        tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, self.kernel)
        mask = ((tophat >= contrast) & (gray >= self.MIN_PEAK)).astype(np.uint8)
        count, _, stats, centers = cv2.connectedComponentsWithStats(mask, connectivity=8)

        h_img, w_img = gray.shape
        spots = []
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            if area < 2 or area > 150 or w > 16 or h > 16:
                continue
            if max(w, h) > 2.5 * min(w, h):          # 길쭉한 빛(모서리·선) 제외
                continue
            if area / float(w * h) < 0.4:            # 속이 빈 모양 제외
                continue
            cx, cy = int(round(centers[i][0])), int(round(centers[i][1]))
            r = self.RING_OUT
            if cx - r < 0 or cy - r < 0 or cx + r >= w_img or cy + r >= h_img:
                continue                              # 화면 가장자리 제외
            patch = gray[cy - r:cy + r + 1, cx - r:cx + r + 1]
            ring = patch[self.ring_mask]
            peak = int(patch[r - 2:r + 3, r - 2:r + 3].max())
            # 주변이 어둡고, 중심만 확연히 밝아야 한다
            if peak - float(ring.mean()) < contrast:
                continue
            if (ring >= 170).mean() > 0.15:          # 밝은 물체 바로 옆이면 제외
                continue
            spots.append((cx, cy, int(max(w, h)) // 2 + 1))
        return spots

    def _near(self, a, b):
        return abs(a[0] - b[0]) <= self.NEAR and abs(a[1] - b[1]) <= self.NEAR

    def update(self, spots):
        """후보를 받아 (확정 지점 목록, 기준 등록 완료 여부)를 반환한다."""
        baseline_done = False
        if self.baseline_buffer is not None:
            self.baseline_buffer.append(spots)
            if len(self.baseline_buffer) >= self.BASELINE_FRAMES:
                self._finish_baseline()
                baseline_done = True

        self.history.append(spots)
        confirmed = []
        if len(self.history) >= self.PERSIST and not self.capturing_baseline:
            for s in spots:
                if any(self._near(s, z) for z in self.ignored):
                    continue
                hits = sum(1 for past in self.history
                           if any(self._near(s, p) for p in past))
                if hits >= self.PERSIST:
                    confirmed.append(s)
        return confirmed, baseline_done

    def _finish_baseline(self):
        zones = []
        for frame_spots in self.baseline_buffer:
            for s in frame_spots:
                for z in zones:
                    if self._near(s, z["pos"]):
                        z["hits"] += 1
                        break
                else:
                    zones.append({"pos": s, "hits": 1})
        # 기준 화면에서 3번 이상 보인 반사는 '원래 있던 것'으로 보고 제외
        self.ignored = [z["pos"] for z in zones if z["hits"] >= 3]
        self.baseline_buffer = None


def make_fake_frame(t, rng=random):
    """
    시뮬레이션용 가상 영상.
    실제 웹캠처럼 노이즈·모니터 빛·눈동자 반사·순간 광택을 넣고,
    3초 후 '손전등 켜짐' 상태가 되면 안경의 카메라 렌즈가 반사광을 낸다.
    """
    frame = np.full((300, 400, 3), 55, dtype=np.uint8)
    cv2.rectangle(frame, (295, 20), (390, 110), (235, 235, 235), -1)            # 모니터/창문
    cv2.ellipse(frame, (190, 170), (80, 100), 0, 0, 360, (110, 130, 160), -1)   # 얼굴
    cv2.rectangle(frame, (125, 140), (180, 175), (30, 30, 30), 3)               # 안경
    cv2.rectangle(frame, (200, 140), (255, 175), (30, 30, 30), 3)
    cv2.line(frame, (180, 150), (200, 150), (30, 30, 30), 3)
    cv2.circle(frame, (228, 160), 2, (245, 245, 245), -1)                       # 눈동자 반사(원래 있음)
    for _ in range(2):                                                          # 순간 광택
        cv2.circle(frame, (rng.randint(20, 380), rng.randint(200, 290)), 2,
                   (250, 250, 250), -1)
    light_on = t >= 3
    if light_on:
        cv2.circle(frame, (130, 145), 2, (255, 255, 255), -1)                   # 렌즈 반사광
    noise = np.random.normal(0, 10, frame.shape)
    frame = np.clip(frame + noise, 0, 255).astype(np.uint8)
    cv2.putText(frame, "SIMULATION  LIGHT " + ("ON" if light_on else "OFF"),
                (10, 290), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1)
    return frame


# ═══════════════════════════════════════════════════════════
# 3. 디자인
# ═══════════════════════════════════════════════════════════
BG = "#F1F5F9"
WHITE = "#FFFFFF"
BORDER = "#CBD5E1"
TEXT = "#0F172A"
SUB = "#64748B"
NAVY = "#1E293B"
ACCENT = "#334155"
PRIMARY = "#1E40AF"
GREEN = "#15803D"
RED = "#B91C1C"
GRAY = "#475569"
FONT = "맑은 고딕"

LEVELS = {
    "danger": {"label": "위험", "color": RED, "bg": "#FEF2F2"},
    "check":  {"label": "주의", "color": "#A16207", "bg": "#FEFCE8"},
    "normal": {"label": "일반", "color": SUB, "bg": WHITE},
}
LEVEL_CHOICES = ["위험", "주의", "일반", "이상 없음"]


def make_button(parent, text, command, color=PRIMARY, size=10):
    return tk.Button(parent, text=text, command=command, bg=color, fg=WHITE,
                     activebackground=color, activeforeground=WHITE,
                     font=(FONT, size, "bold"), relief="flat", cursor="hand2",
                     padx=12, pady=6)


def section_title(parent, text):
    tk.Label(parent, text=text, bg=BG, fg=TEXT, font=(FONT, 11, "bold")).pack(
        anchor="w", padx=14, pady=(12, 2))


def make_scroll_area(parent):
    outer = tk.Frame(parent, bg=BG)
    canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
    bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=BG)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(window_id, width=e.width))
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")

    def on_wheel(e):
        canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", on_wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    return outer, canvas, inner


# ═══════════════════════════════════════════════════════════
# 4. 애플리케이션
# ═══════════════════════════════════════════════════════════
class DetectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_TITLE} v{VERSION}")
        self.root.geometry("520x820")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.demo = tk.BooleanVar(value=False)

        self.results = []
        self.device_options = []
        self.scan_queue = queue.Queue()
        self.scanning = False
        self.seconds_left = 0
        self.show_normal = tk.BooleanVar(value=False)

        self.radar_queue = queue.Queue()
        self.radar_stop = threading.Event()
        self.radar_running = False
        self.radar_target = None
        self.radar_values = deque(maxlen=5)
        self.radar_last_seen = 0
        self.proximity = 0
        self.sound_on = tk.BooleanVar(value=True)

        self.camera = None
        self.camera_running = False
        self.camera_photo = None
        self.camera_start_time = 0
        self.glint_count = 0
        self.camera_fail_count = 0
        self.glint_detector = None
        self.sensitivity = tk.IntVar(value=2)
        self.show_candidates = tk.BooleanVar(value=False)

        self.room = tk.StringVar()
        self.proctor = tk.StringVar()
        self.records = []
        self.load_records()

        self.setup_style()
        self.build_header()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.scan_tab = self.add_tab("기기 스캔")
        self.radar_tab = self.add_tab("위치 추적")
        self.lens_tab = self.add_tab("렌즈 탐지")
        self.check_tab = self.add_tab("육안 점검")
        self.record_tab = self.add_tab("점검 기록")

        self.build_scan_tab()
        self.build_radar_tab()
        self.build_lens_tab()
        self.build_check_tab()
        self.build_record_tab()
        self.build_statusbar()

    # ── 공통 ────────────────────────────────────────────
    def setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=(FONT, 10, "bold"), padding=(10, 6),
                        background="#E2E8F0", foreground=ACCENT)
        style.map("TNotebook.Tab", background=[("selected", WHITE)],
                  foreground=[("selected", NAVY)])
        style.configure("Treeview", font=(FONT, 9), rowheight=26)
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"))

    def add_tab(self, title):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text=title)
        return frame

    def build_header(self):
        header = tk.Frame(self.root, bg=NAVY)
        header.pack(fill="x")
        left = tk.Frame(header, bg=NAVY)
        left.pack(side="left", padx=16, pady=12)
        tk.Label(left, text=APP_TITLE, bg=NAVY, fg=WHITE,
                 font=(FONT, 16, "bold")).pack(anchor="w")
        tk.Label(left, text="시험 감독 보조 프로그램", bg=NAVY, fg="#94A3B8",
                 font=(FONT, 9)).pack(anchor="w")

        right = tk.Frame(header, bg=NAVY)
        right.pack(side="right", padx=16)
        self.mode_badge = tk.Label(right, text="실제 모드", bg="#166534", fg=WHITE,
                                   font=(FONT, 9, "bold"), padx=8, pady=2)
        self.mode_badge.pack(anchor="e")
        tk.Checkbutton(right, text="시뮬레이션 모드", variable=self.demo,
                       command=self.on_demo_change, bg=NAVY, fg="#CBD5E1",
                       selectcolor=ACCENT, activebackground=NAVY,
                       activeforeground=WHITE, font=(FONT, 9)).pack(anchor="e", pady=(4, 0))

    def build_statusbar(self):
        bar = tk.Frame(self.root, bg="#E2E8F0")
        bar.pack(fill="x", side="bottom")
        self.status_label = tk.Label(bar, text="준비", bg="#E2E8F0", fg=ACCENT,
                                     font=(FONT, 9), anchor="w")
        self.status_label.pack(side="left", padx=10, pady=4)
        tk.Label(bar, text="판정 결과는 참고용이며, 최종 판단은 감독관이 직접 확인 후 내립니다.",
                 bg="#E2E8F0", fg=SUB, font=(FONT, 8)).pack(side="right", padx=10)

    def set_status(self, text):
        self.status_label.config(text=text)

    def on_demo_change(self):
        self.stop_radar()
        self.stop_camera()
        if hasattr(self, "baseline_label"):
            self.clear_baseline()
        self.results = []
        self.update_device_choices()
        self.reset_counts()
        if self.demo.get():
            self.mode_badge.config(text="시뮬레이션", bg="#A16207")
            self.set_status("시뮬레이션 모드: 가상 기기 및 가상 영상을 사용합니다.")
        else:
            self.mode_badge.config(text="실제 모드", bg="#166534")
            self.set_status("실제 모드: 블루투스 및 카메라를 사용합니다.")
        self.show_scan_message("[스캔 시작]을 눌러 시험실 점검을 시작하십시오.")

    def on_close(self):
        self.stop_radar()
        self.stop_camera()
        self.save_records()
        self.root.destroy()

    # ═══════════════════════════════════════════════════
    # [1] 기기 스캔
    # ═══════════════════════════════════════════════════
    def build_scan_tab(self):
        tab = self.scan_tab

        summary = tk.Frame(tab, bg=BG)
        summary.pack(fill="x", padx=10, pady=(12, 6))
        self.count_labels = {}
        for key, title, color in [("all", "감지 기기", TEXT),
                                  ("danger", "위험", RED),
                                  ("check", "주의", LEVELS["check"]["color"])]:
            card = tk.Frame(summary, bg=WHITE, highlightbackground=BORDER,
                            highlightthickness=1)
            card.pack(side="left", expand=True, fill="x", padx=4)
            num = tk.Label(card, text="-", bg=WHITE, fg=color, font=(FONT, 20, "bold"))
            num.pack(pady=(6, 0))
            tk.Label(card, text=title, bg=WHITE, fg=SUB, font=(FONT, 9)).pack(pady=(0, 6))
            self.count_labels[key] = num

        controls = tk.Frame(tab, bg=BG)
        controls.pack(fill="x", padx=14, pady=4)
        self.scan_button = make_button(controls, "스캔 시작", self.start_scan, size=13)
        self.scan_button.pack(fill="x")
        self.progress = ttk.Progressbar(controls, mode="determinate")
        self.progress.pack(fill="x", pady=(6, 0))
        row = tk.Frame(controls, bg=BG)
        row.pack(fill="x")
        self.scan_status = tk.Label(row, text="대기", bg=BG, fg=SUB, font=(FONT, 9))
        self.scan_status.pack(side="left", pady=4)
        tk.Checkbutton(row, text="일반 기기 표시", variable=self.show_normal,
                       command=self.draw_results, bg=BG, fg=SUB,
                       activebackground=BG, font=(FONT, 9)).pack(side="right")

        area, self.scan_canvas, self.scan_list = make_scroll_area(tab)
        area.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.show_scan_message("[스캔 시작]을 눌러 시험실 점검을 시작하십시오.")

    def reset_counts(self):
        for label in self.count_labels.values():
            label.config(text="-")
        self.progress["value"] = 0

    def start_scan(self):
        if self.scanning:
            return
        self.stop_radar()
        self.scanning = True
        self.scan_button.config(state="disabled", text="스캔 진행 중", bg="#64748B")
        total = DEMO_SCAN_SECONDS if self.demo.get() else SCAN_SECONDS
        self.seconds_left = total
        self.progress.config(maximum=total, value=0)
        self.show_scan_message("주변 블루투스 신호를 수신하고 있습니다.")
        self.set_status("기기 스캔 진행 중")
        self.scan_tick()
        threading.Thread(target=self.scan_worker, args=(self.demo.get(),),
                         daemon=True).start()
        self.root.after(200, self.check_scan_queue)

    def scan_worker(self, demo):
        try:
            if demo:
                results = fake_scan()
            elif not HAS_BLEAK:
                raise RuntimeError("bleak 라이브러리가 설치되어 있지 않습니다. "
                                   "(pip install bleak)")
            else:
                results = asyncio.run(real_scan())
            self.scan_queue.put(("ok", results))
        except Exception as error:
            self.scan_queue.put(("error", error))

    def scan_tick(self):
        if not self.scanning:
            return
        if self.seconds_left > 0:
            self.scan_status.config(text=f"스캔 중 - {self.seconds_left}초 남음", fg=PRIMARY)
            self.progress["value"] = self.progress["maximum"] - self.seconds_left
            self.seconds_left -= 1
        else:
            self.progress["value"] = self.progress["maximum"]
            self.scan_status.config(text="결과 분석 중", fg=PRIMARY)
        self.root.after(1000, self.scan_tick)

    def check_scan_queue(self):
        try:
            kind, data = self.scan_queue.get_nowait()
        except queue.Empty:
            self.root.after(200, self.check_scan_queue)
            return

        self.scanning = False
        self.scan_button.config(state="normal", text="다시 스캔", bg=PRIMARY)

        if kind == "error":
            self.progress["value"] = 0
            self.scan_status.config(text="스캔 실패", fg=RED)
            self.set_status("스캔 실패")
            self.show_scan_message("스캔을 수행할 수 없습니다.\n블루투스 상태를 확인하십시오.")
            if messagebox.askyesno(
                    "스캔 실패",
                    f"블루투스를 사용할 수 없습니다.\n\n{data}\n\n"
                    "시뮬레이션 모드로 전환하시겠습니까?"):
                self.demo.set(True)
                self.on_demo_change()
                self.start_scan()
            return

        self.results = data
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.scan_status.config(text=f"스캔 완료 ({now}) - {len(data)}개 기기 감지", fg=SUB)
        self.set_status(f"스캔 완료: {len(data)}개 기기 감지")
        self.update_device_choices()
        self.draw_results()

    def show_scan_message(self, text):
        for child in self.scan_list.winfo_children():
            child.destroy()
        tk.Label(self.scan_list, text=text, bg=BG, fg=SUB, font=(FONT, 10),
                 justify="center").pack(pady=60)

    def draw_results(self):
        if not self.results:
            if self.scan_status.cget("text").startswith("스캔 완료"):
                self.show_scan_message("감지된 블루투스 기기가 없습니다.")
            return

        danger = sum(1 for r in self.results if r["level"] == "danger")
        check = sum(1 for r in self.results if r["level"] == "check")
        self.count_labels["all"].config(text=len(self.results))
        self.count_labels["danger"].config(text=danger)
        self.count_labels["check"].config(text=check)

        for child in self.scan_list.winfo_children():
            child.destroy()

        if danger == 0 and check == 0:
            tk.Label(self.scan_list, text="의심 기기가 감지되지 않았습니다.", bg="#F0FDF4",
                     fg=GREEN, font=(FONT, 11, "bold"), pady=12).pack(fill="x", pady=(4, 8))

        for r in self.results:
            if r["level"] == "normal" and not self.show_normal.get():
                continue
            self.draw_card(r)
        self.scan_canvas.yview_moveto(0)

    def draw_card(self, r):
        style = LEVELS[r["level"]]
        card = tk.Frame(self.scan_list, bg=style["bg"],
                        highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=4)
        tk.Frame(card, bg=style["color"], width=5).pack(side="left", fill="y")

        body = tk.Frame(card, bg=style["bg"])
        body.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        top = tk.Frame(body, bg=style["bg"])
        top.pack(fill="x")
        tk.Label(top, text=r["name"], bg=style["bg"], fg=TEXT,
                 font=(FONT, 11, "bold")).pack(side="left")
        tk.Label(top, text=f" {style['label']} ", bg=style["color"], fg=WHITE,
                 font=(FONT, 9, "bold")).pack(side="right")

        tk.Label(body, text=f"신호 세기 {r['rssi']} dBm  |  {distance_hint(r['rssi'])}",
                 bg=style["bg"], fg=SUB, font=(FONT, 9)).pack(anchor="w", pady=(3, 0))
        for reason in r["reasons"]:
            tk.Label(body, text=f"- {reason}", bg=style["bg"], fg=TEXT,
                     font=(FONT, 9)).pack(anchor="w")

        buttons = tk.Frame(body, bg=style["bg"])
        buttons.pack(anchor="e", pady=(6, 0))
        make_button(buttons, "위치 추적", lambda: self.track_device(r),
                    color=ACCENT, size=9).pack(side="left", padx=3)
        make_button(buttons, "기록", lambda: self.open_record_dialog(
            f"[기기 스캔] {r['name']} ({r['rssi']} dBm) - " + "; ".join(r["reasons"]),
            style["label"]), color=GREEN, size=9).pack(side="left", padx=3)

    # ═══════════════════════════════════════════════════
    # [2] 위치 추적
    # ═══════════════════════════════════════════════════
    def build_radar_tab(self):
        tab = self.radar_tab
        section_title(tab, "대상 기기 위치 추적")
        tk.Label(tab, text="대상 기기를 선택한 후 장비를 들고 시험실 내를 이동하십시오.\n"
                           "대상에 가까워질수록 근접도가 높아지고 신호음 간격이 짧아집니다.",
                 bg=BG, fg=SUB, font=(FONT, 9), justify="left").pack(anchor="w", padx=14)

        chooser = tk.Frame(tab, bg=BG)
        chooser.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(chooser, text="대상 기기", bg=BG, fg=TEXT,
                 font=(FONT, 10, "bold")).pack(side="left")
        self.device_choice = ttk.Combobox(chooser, state="readonly", font=(FONT, 10))
        self.device_choice.pack(side="left", fill="x", expand=True, padx=8)
        self.update_device_choices()

        self.gauge = tk.Canvas(tab, width=360, height=200, bg=BG, highlightthickness=0)
        self.gauge.pack(pady=(14, 0))

        self.proximity_label = tk.Label(tab, text="대기", bg=BG, fg=SUB,
                                        font=(FONT, 13, "bold"))
        self.proximity_label.pack()
        self.rssi_label = tk.Label(tab, text="", bg=BG, fg=SUB, font=(FONT, 9))
        self.rssi_label.pack()

        buttons = tk.Frame(tab, bg=BG)
        buttons.pack(pady=12)
        self.radar_button = make_button(buttons, "추적 시작", self.toggle_radar,
                                        color=PRIMARY, size=12)
        self.radar_button.pack(side="left", padx=4)
        make_button(buttons, "기록", self.record_radar, color=GREEN,
                    size=12).pack(side="left", padx=4)

        tk.Checkbutton(tab, text="신호음 사용", variable=self.sound_on, bg=BG,
                       fg=TEXT, activebackground=BG, font=(FONT, 9)).pack()
        self.draw_gauge(0, BORDER)

    def update_device_choices(self):
        self.device_options = list(self.results)
        names = [f"[{LEVELS[r['level']]['label']}] {r['name']}  ({r['rssi']} dBm)"
                 for r in self.device_options]
        if not names:
            names = ["(기기 스캔을 먼저 실행하십시오)"]
        if hasattr(self, "device_choice"):
            self.device_choice.config(values=names)
            self.device_choice.current(0)

    def track_device(self, r):
        if r in self.device_options:
            self.device_choice.current(self.device_options.index(r))
        self.notebook.select(self.radar_tab)
        self.stop_radar()
        self.start_radar()

    def draw_gauge(self, p, color):
        g = self.gauge
        g.delete("all")
        box = (50, 30, 310, 290)
        g.create_arc(box, start=180, extent=-180, style="arc", width=22, outline="#E2E8F0")
        if p > 0:
            g.create_arc(box, start=180, extent=-180 * p / 100, style="arc",
                         width=22, outline=color)
        for i in range(0, 101, 25):  # 눈금
            angle = math.radians(180 - i * 1.8)
            x1, y1 = 180 + 112 * math.cos(angle), 160 - 112 * math.sin(angle)
            g.create_text(x1, y1, text=str(i), font=(FONT, 8), fill=SUB)
        g.create_text(180, 135, text=f"{int(p)}%", font=(FONT, 34, "bold"),
                      fill=color if p > 0 else SUB)
        g.create_text(180, 178, text="근접도", font=(FONT, 9), fill=SUB)

    def toggle_radar(self):
        if self.radar_running:
            self.stop_radar()
        else:
            self.start_radar()

    def start_radar(self):
        if not self.device_options:
            messagebox.showinfo("위치 추적", "기기 스캔을 먼저 실행하십시오.")
            return
        if self.scanning:
            messagebox.showinfo("위치 추적", "스캔이 완료된 후 시작하십시오.")
            return

        index = self.device_choice.current()
        if index < 0 or index >= len(self.device_options):
            index = 0
        self.radar_target = self.device_options[index]
        self.radar_values.clear()
        self.radar_last_seen = 0
        self.proximity = 0
        while not self.radar_queue.empty():
            self.radar_queue.get_nowait()
        self.radar_stop = threading.Event()
        self.radar_running = True
        self.radar_button.config(text="추적 중지", bg=RED, activebackground=RED)
        self.proximity_label.config(text="신호 수신 대기", fg=SUB)
        self.set_status(f"위치 추적 중: {self.radar_target['name']}")

        threading.Thread(target=self.radar_worker,
                         args=(self.radar_target["address"], self.demo.get(),
                               self.radar_stop),
                         daemon=True).start()
        self.root.after(150, self.radar_update)
        self.root.after(500, self.radar_beep)

    def stop_radar(self):
        self.radar_stop.set()
        if self.radar_running:
            self.radar_running = False
            self.radar_button.config(text="추적 시작", bg=PRIMARY, activebackground=PRIMARY)
            self.proximity_label.config(text="중지됨", fg=SUB)
            self.set_status("위치 추적 중지")

    def radar_worker(self, address, demo, stop):
        if demo:
            rssi = -85.0
            while not stop.is_set():
                rssi += random.uniform(-3, 4.5)
                rssi = max(-95, min(-38, rssi))
                if rssi >= -40:
                    rssi = -90
                self.radar_queue.put(("rssi", int(rssi)))
                time.sleep(0.3)
            return

        async def run():
            def on_found(device, adv):
                if device.address == address:
                    self.radar_queue.put(("rssi", adv.rssi))
            scanner = BleakScanner(detection_callback=on_found)
            await scanner.start()
            try:
                while not stop.is_set():
                    await asyncio.sleep(0.2)
            finally:
                await scanner.stop()

        try:
            asyncio.run(run())
        except Exception as error:
            self.radar_queue.put(("error", str(error)))

    def radar_update(self):
        if not self.radar_running:
            return
        while True:
            try:
                kind, value = self.radar_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                self.stop_radar()
                messagebox.showerror("위치 추적 실패", f"블루투스를 사용할 수 없습니다.\n\n{value}")
                return
            self.radar_values.append(value)
            self.radar_last_seen = time.time()

        if self.radar_values and time.time() - self.radar_last_seen < 5:
            avg = sum(self.radar_values) / len(self.radar_values)
            self.proximity = rssi_to_proximity(avg)
            text, color = proximity_info(self.proximity)
            self.draw_gauge(self.proximity, color)
            self.proximity_label.config(text=text, fg=color)
            self.rssi_label.config(text=f"평균 신호 세기 {avg:.0f} dBm  |  {distance_hint(avg)}")
        else:
            self.proximity = 0
            self.draw_gauge(0, BORDER)
            self.proximity_label.config(text="신호 없음 - 위치를 이동하십시오", fg=SUB)
            self.rssi_label.config(text="")

        self.root.after(150, self.radar_update)

    def radar_beep(self):
        if not self.radar_running:
            return
        if self.sound_on.get() and self.proximity > 0:
            if HAS_WINSOUND:
                freq = int(600 + self.proximity * 8)
                threading.Thread(target=winsound.Beep, args=(freq, 70), daemon=True).start()
            else:
                self.root.bell()
        self.root.after(beep_interval_ms(self.proximity), self.radar_beep)

    def record_radar(self):
        if not self.radar_target:
            messagebox.showinfo("기록", "추적 대상 기기가 없습니다.")
            return
        r = self.radar_target
        self.open_record_dialog(
            f"[위치 추적] {r['name']} - 근접도 {int(self.proximity)}%",
            LEVELS[r["level"]]["label"])

    # ═══════════════════════════════════════════════════
    # [3] 렌즈 탐지
    # ═══════════════════════════════════════════════════
    def build_lens_tab(self):
        tab = self.lens_tab
        section_title(tab, "카메라 렌즈 반사광 탐지")

        steps = tk.Frame(tab, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        steps.pack(fill="x", padx=14, pady=(2, 6))
        for text in ["1. 손전등을 끈 상태에서 [기준 화면 등록]을 누릅니다. (기존 반사광 제외)",
                     "2. 웹캠 옆에서 손전등을 켜고 수험생 방향을 비춥니다.",
                     "3. 일정 시간 같은 위치에 지속되는 반사광만 의심 지점으로 표시됩니다."]:
            tk.Label(steps, text=text, bg=WHITE, fg=TEXT, font=(FONT, 9),
                     anchor="w", justify="left").pack(fill="x", padx=10, pady=1)

        top = tk.Frame(tab, bg=BG)
        top.pack(fill="x", padx=14, pady=(2, 4))
        tk.Label(top, text="카메라", bg=BG, fg=TEXT, font=(FONT, 9, "bold")).pack(side="left")
        self.camera_choice = ttk.Combobox(top, state="readonly", width=10, font=(FONT, 9),
                                          values=["카메라 1", "카메라 2", "카메라 3"])
        self.camera_choice.current(0)
        self.camera_choice.pack(side="left", padx=6)
        self.camera_button = make_button(top, "카메라 켜기", self.toggle_camera,
                                         color=PRIMARY, size=9)
        self.camera_button.pack(side="right")

        self.video_label = tk.Label(tab, bg="#0F172A", width=52, height=14,
                                    text="카메라 꺼짐", fg="#94A3B8", font=(FONT, 11))
        self.video_label.pack(padx=10)

        self.glint_label = tk.Label(tab, text="", bg=BG, fg=TEXT, font=(FONT, 11, "bold"),
                                    wraplength=470)
        self.glint_label.pack(pady=(6, 0))
        self.baseline_label = tk.Label(tab, text="기준 화면: 미등록", bg=BG, fg=SUB,
                                       font=(FONT, 9))
        self.baseline_label.pack()

        slider = tk.Frame(tab, bg=BG)
        slider.pack(fill="x", padx=24, pady=2)
        tk.Label(slider, text="감도", bg=BG, fg=TEXT, font=(FONT, 9, "bold")).pack(side="left")
        tk.Label(slider, text="낮음", bg=BG, fg=SUB, font=(FONT, 8)).pack(side="left", padx=(8, 0))
        tk.Scale(slider, from_=1, to=5, orient="horizontal", variable=self.sensitivity,
                 bg=BG, highlightthickness=0, font=(FONT, 8), length=200,
                 command=lambda v: self.glint_detector and self.glint_detector.reset()
                 ).pack(side="left", padx=4)
        tk.Label(slider, text="높음", bg=BG, fg=SUB, font=(FONT, 8)).pack(side="left")
        tk.Checkbutton(slider, text="후보 지점 표시", variable=self.show_candidates, bg=BG,
                       fg=SUB, activebackground=BG, font=(FONT, 8)).pack(side="right")

        buttons = tk.Frame(tab, bg=BG)
        buttons.pack(pady=6)
        make_button(buttons, "기준 화면 등록", self.register_baseline, color=ACCENT,
                    size=9).pack(side="left", padx=3)
        make_button(buttons, "기준 초기화", self.clear_baseline, color=GRAY,
                    size=9).pack(side="left", padx=3)
        make_button(buttons, "기록", self.record_lens, color=GREEN,
                    size=9).pack(side="left", padx=3)

        if not HAS_CAMERA_LIBS:
            self.glint_label.config(
                text="렌즈 탐지 기능을 사용하려면 다음을 설치하십시오.\n"
                     "pip install opencv-python pillow", fg=RED, font=(FONT, 9, "bold"))

    def toggle_camera(self):
        if self.camera_running:
            self.stop_camera()
        else:
            self.start_camera()

    def start_camera(self):
        if not HAS_CAMERA_LIBS:
            messagebox.showinfo("렌즈 탐지", "다음 명령으로 라이브러리를 먼저 설치하십시오.\n\n"
                                         "pip install opencv-python pillow")
            return
        if self.glint_detector is None:
            self.glint_detector = GlintDetector()
        self.glint_detector.reset()

        if not self.demo.get():
            index = max(0, self.camera_choice.current())
            backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
            self.camera = cv2.VideoCapture(index, backend)
            if not self.camera.isOpened():
                self.camera.release()
                self.camera = None
                if messagebox.askyesno("렌즈 탐지",
                                       f"카메라 {index + 1}을(를) 열 수 없습니다.\n"
                                       "다른 카메라를 선택하거나, 다른 프로그램이 카메라를 "
                                       "사용 중인지 확인하십시오.\n\n"
                                       "시뮬레이션 모드로 전환하시겠습니까?"):
                    self.demo.set(True)
                    self.on_demo_change()
                    self.start_camera()
                return
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.camera_running = True
        self.camera_fail_count = 0
        self.camera_start_time = time.time()
        self.camera_button.config(text="카메라 끄기", bg=RED, activebackground=RED)
        self.set_status("렌즈 탐지 실행 중" + (" (시뮬레이션 영상)" if self.demo.get() else ""))
        self.camera_loop()

    def stop_camera(self):
        self.camera_running = False
        if self.camera is not None:
            try:
                self.camera.release()
            except Exception:
                pass
            self.camera = None
        if self.glint_detector is not None:
            self.glint_detector.reset()
        if hasattr(self, "camera_button"):
            self.camera_button.config(text="카메라 켜기", bg=PRIMARY, activebackground=PRIMARY)
            self.video_label.config(image="", text="카메라 꺼짐", width=52, height=14)
            self.camera_photo = None
            self.glint_count = 0

    def register_baseline(self):
        if not self.camera_running:
            messagebox.showinfo("기준 화면 등록", "카메라를 먼저 켜십시오.")
            return
        self.glint_detector.start_baseline()
        self.baseline_label.config(text="기준 화면 등록 중... 카메라를 움직이지 마십시오.",
                                   fg=PRIMARY)

    def clear_baseline(self):
        if self.glint_detector is not None:
            self.glint_detector.clear_baseline()
        self.baseline_label.config(text="기준 화면: 미등록", fg=SUB)

    def camera_loop(self):
        if not self.camera_running:
            return
        if self.camera is not None:
            ok, frame = self.camera.read()
            if not ok or frame is None:
                self.camera_fail_count += 1
                if self.camera_fail_count >= 25:
                    self.stop_camera()
                    messagebox.showerror("렌즈 탐지", "카메라 영상을 받을 수 없습니다.\n"
                                                   "카메라 연결 상태를 확인하십시오.")
                    return
                self.root.after(100, self.camera_loop)
                return
            self.camera_fail_count = 0
        else:
            frame = make_fake_frame(time.time() - self.camera_start_time)

        frame = cv2.resize(frame, (460, int(frame.shape[0] * 460 / frame.shape[1])))
        detector = self.glint_detector
        candidates = detector.find_candidates(frame, self.sensitivity.get())
        confirmed, baseline_done = detector.update(candidates)

        if baseline_done:
            n = len(detector.ignored)
            self.baseline_label.config(
                text=f"기준 화면: 등록됨 (기존 반사광 {n}곳 제외)", fg=GREEN)
            self.set_status("기준 화면이 등록되었습니다. 이제 손전등을 켜고 비추십시오.")

        # 화면 표시
        for x, y, *_ in detector.ignored:
            cv2.drawMarker(frame, (x, y), (150, 150, 150), cv2.MARKER_TILTED_CROSS, 10, 1)
        if self.show_candidates.get():
            for x, y, r in candidates:
                cv2.circle(frame, (x, y), r + 5, (0, 200, 255), 1)
        for i, (x, y, r) in enumerate(confirmed, 1):
            cv2.circle(frame, (x, y), r + 12, (0, 0, 230), 2)
            cv2.line(frame, (x - r - 20, y), (x - r - 7, y), (0, 0, 230), 2)
            cv2.line(frame, (x + r + 7, y), (x + r + 20, y), (0, 0, 230), 2)
            cv2.putText(frame, str(i), (x + r + 12, y - r - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 230), 2)

        self.glint_count = len(confirmed)
        prefix = "[시뮬레이션] " if self.demo.get() else ""
        if detector.capturing_baseline:
            self.glint_label.config(text=prefix + "기준 화면 등록 중", fg=PRIMARY)
        elif len(candidates) > GlintDetector.MAX_CANDIDATES:
            self.glint_label.config(
                text=prefix + "반사 요소가 많아 판별이 어렵습니다. "
                              "감도를 낮추거나 카메라 각도·조명을 조정하십시오.",
                fg=LEVELS["check"]["color"])
        elif confirmed:
            self.glint_label.config(
                text=prefix + f"반사광 의심 지점 {len(confirmed)}곳 검출 - 근접하여 직접 확인하십시오",
                fg=RED)
        else:
            self.glint_label.config(text=prefix + "검출된 반사광 없음", fg=GREEN)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self.camera_photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.video_label.config(image=self.camera_photo, text="", width=460,
                                height=frame.shape[0])
        self.root.after(40, self.camera_loop)

    def record_lens(self):
        baseline = "기준 등록" if (self.glint_detector and self.glint_detector.ignored) else "기준 미등록"
        self.open_record_dialog(
            f"[렌즈 탐지] 반사광 의심 지점 {self.glint_count}곳 (감도 {self.sensitivity.get()}, {baseline})",
            "위험" if self.glint_count else "이상 없음")

    # ═══════════════════════════════════════════════════
    # [4] 육안 점검
    # ═══════════════════════════════════════════════════
    def build_check_tab(self):
        tab = self.check_tab
        section_title(tab, "육안 점검 항목")
        tk.Label(tab, text="점검 대상 수험생의 안경 및 행동을 확인하고 해당 항목에 표시하십시오.",
                 bg=BG, fg=SUB, font=(FONT, 9)).pack(anchor="w", padx=14, pady=(0, 8))

        box = tk.Frame(tab, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="x", padx=14)
        self.check_vars = []
        for i, item in enumerate(CHECKLIST, 1):
            var = tk.BooleanVar(value=False)
            tk.Checkbutton(box, text=f"{i}. {item}", variable=var,
                           command=self.update_check_result, bg=WHITE, fg=TEXT,
                           activebackground=WHITE, anchor="w", font=(FONT, 10),
                           wraplength=440, justify="left").pack(fill="x", padx=10, pady=3)
            self.check_vars.append((item, var))

        self.check_result = tk.Label(tab, text="", bg=BG, font=(FONT, 12, "bold"),
                                     wraplength=460)
        self.check_result.pack(pady=14)

        buttons = tk.Frame(tab, bg=BG)
        buttons.pack()
        make_button(buttons, "초기화", self.reset_checklist, color=GRAY).pack(side="left", padx=4)
        make_button(buttons, "기록에 추가", self.record_checklist, color=GREEN).pack(side="left", padx=4)
        self.update_check_result()

    def checked_items(self):
        return [item for item, var in self.check_vars if var.get()]

    def check_level(self):
        n = len(self.checked_items())
        if n >= 3:
            return "위험", "해당 안경을 직접 확인하십시오.", RED
        elif n >= 1:
            return "주의", "지속적으로 관찰하십시오.", LEVELS["check"]["color"]
        return "이상 없음", "", SUB

    def update_check_result(self):
        n = len(self.checked_items())
        level, advice, color = self.check_level()
        text = f"해당 항목 {n}개  |  판정: {level}" + (f"\n{advice}" if advice else "")
        self.check_result.config(text=text, fg=color)

    def reset_checklist(self):
        for _, var in self.check_vars:
            var.set(False)
        self.update_check_result()

    def record_checklist(self):
        items = self.checked_items()
        level, _, _ = self.check_level()
        what = "[육안 점검] " + ("; ".join(items) if items else "해당 항목 없음")
        self.open_record_dialog(what, level)

    # ═══════════════════════════════════════════════════
    # [5] 점검 기록
    # ═══════════════════════════════════════════════════
    def build_record_tab(self):
        tab = self.record_tab
        section_title(tab, "시험 정보")

        info = tk.Frame(tab, bg=BG)
        info.pack(fill="x", padx=14, pady=(0, 6))
        tk.Label(info, text="시험실", bg=BG, fg=TEXT, font=(FONT, 10)).grid(row=0, column=0, sticky="w")
        tk.Entry(info, textvariable=self.room, font=(FONT, 10), width=14).grid(row=0, column=1, padx=6)
        tk.Label(info, text="감독관", bg=BG, fg=TEXT, font=(FONT, 10)).grid(row=0, column=2, sticky="w")
        tk.Entry(info, textvariable=self.proctor, font=(FONT, 10), width=14).grid(row=0, column=3, padx=6)

        section_title(tab, "점검 내역")
        table = tk.Frame(tab, bg=BG)
        table.pack(fill="both", expand=True, padx=14, pady=4)
        columns = ("time", "seat", "level", "what", "memo")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=12)
        for col, title, width in [("time", "시각", 60), ("seat", "좌석", 50),
                                  ("level", "판정", 60), ("what", "내용", 220),
                                  ("memo", "비고", 120)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.tag_configure("odd", background="#F8FAFC")
        self.tree.tag_configure("even", background=WHITE)
        self.tree.tag_configure("위험", foreground=RED)
        self.tree.tag_configure("주의", foreground=LEVELS["check"]["color"])
        self.tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        buttons = tk.Frame(tab, bg=BG)
        buttons.pack(pady=8)
        make_button(buttons, "직접 입력", lambda: self.open_record_dialog("", ""),
                    color=PRIMARY, size=9).pack(side="left", padx=3)
        make_button(buttons, "선택 삭제", self.delete_selected_record,
                    color=GRAY, size=9).pack(side="left", padx=3)
        make_button(buttons, "CSV 내보내기", self.export_records,
                    color=GREEN, size=9).pack(side="left", padx=3)
        make_button(buttons, "전체 삭제", self.clear_records,
                    color=RED, size=9).pack(side="left", padx=3)

        tk.Label(tab, text="개인정보 보호: 기기 고유 주소(MAC)는 저장하지 않습니다. "
                           "시험 종료 후 기록을 삭제하십시오.",
                 bg=BG, fg=SUB, font=(FONT, 8)).pack(pady=(0, 6))
        self.refresh_records()

    def open_record_dialog(self, what, level):
        win = tk.Toplevel(self.root)
        win.title("점검 기록 작성")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="점검 내용", bg=BG, fg=TEXT, font=(FONT, 10, "bold")).pack(
            anchor="w", padx=16, pady=(14, 2))
        what_box = tk.Text(win, width=46, height=3, font=(FONT, 10), wrap="word")
        what_box.insert("1.0", what)
        what_box.pack(padx=16)

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=16, pady=8)
        tk.Label(row, text="좌석 번호", bg=BG, fg=TEXT, font=(FONT, 10, "bold")).pack(side="left")
        seat = tk.Entry(row, font=(FONT, 10), width=8)
        seat.pack(side="left", padx=6)
        tk.Label(row, text="판정", bg=BG, fg=TEXT, font=(FONT, 10, "bold")).pack(side="left")
        level_box = ttk.Combobox(row, values=LEVEL_CHOICES, font=(FONT, 10), width=10)
        level_box.set(level)
        level_box.pack(side="left", padx=6)

        tk.Label(win, text="비고 (확인 결과, 조치 사항 등)", bg=BG, fg=TEXT,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=16)
        memo = tk.Text(win, width=46, height=4, font=(FONT, 10), wrap="word")
        memo.pack(padx=16)

        def save():
            if not seat.get().strip() and not messagebox.askyesno(
                    "확인", "좌석 번호가 입력되지 않았습니다. 그대로 저장하시겠습니까?",
                    parent=win):
                return
            self.records.append({
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "seat": seat.get().strip(),
                "level": level_box.get().strip(),
                "what": what_box.get("1.0", "end").strip(),
                "memo": memo.get("1.0", "end").strip(),
            })
            self.save_records()
            self.refresh_records()
            win.destroy()
            self.set_status("점검 기록이 저장되었습니다.")

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(pady=12)
        make_button(buttons, "저장", save, color=GREEN).pack(side="left", padx=4)
        make_button(buttons, "취소", win.destroy, color=GRAY).pack(side="left", padx=4)
        seat.focus_set()

    def refresh_records(self):
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        for i, rec in enumerate(self.records):
            level = rec.get("level", "")
            tags = ["even" if i % 2 == 0 else "odd"]
            if level in ("위험", "주의"):
                tags.append(level)
            self.tree.insert("", "end", iid=str(i), tags=tags, values=(
                str(rec.get("time", ""))[11:16], rec.get("seat", ""), level,
                rec.get("what", ""), rec.get("memo", "")))

    def delete_selected_record(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("삭제", "삭제할 기록을 선택하십시오.")
            return
        if not messagebox.askyesno("삭제", f"선택한 기록 {len(selected)}건을 삭제하시겠습니까?"):
            return
        for iid in sorted((int(s) for s in selected), reverse=True):
            del self.records[iid]
        self.save_records()
        self.refresh_records()
        self.set_status("선택한 기록을 삭제했습니다.")

    def clear_records(self):
        if self.records and messagebox.askyesno(
                "전체 삭제", "모든 점검 기록을 삭제하시겠습니까?\n삭제 후에는 복구할 수 없습니다."):
            self.records = []
            self.save_records()
            self.refresh_records()
            self.set_status("모든 점검 기록을 삭제했습니다.")

    def export_records(self):
        if not self.records:
            messagebox.showinfo("내보내기", "저장된 점검 기록이 없습니다.")
            return
        today = datetime.datetime.now().strftime("%Y%m%d")
        room = self.room.get().strip() or "시험실"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=f"AI안경_점검기록_{room}_{today}.csv",
            filetypes=[("CSV 파일 (Excel)", "*.csv")])
        if not path:
            return
        try:
            f = open(path, "w", newline="", encoding="utf-8-sig")
        except OSError as error:
            messagebox.showerror("내보내기 실패",
                                 f"파일을 저장할 수 없습니다.\n"
                                 f"같은 파일이 엑셀에서 열려 있다면 닫은 후 다시 시도하십시오.\n\n{error}")
            return
        with f:
            writer = csv.writer(f)
            writer.writerow(["AI 안경 점검 기록"])
            writer.writerow(["시험실", self.room.get()])
            writer.writerow(["감독관", self.proctor.get()])
            writer.writerow(["작성 일시", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")])
            writer.writerow(["모드", "시뮬레이션" if self.demo.get() else "실제"])
            writer.writerow([])
            writer.writerow(["번호", "시각", "좌석", "판정", "점검 내용", "비고"])
            for i, rec in enumerate(self.records, 1):
                writer.writerow([i, rec.get("time", ""), rec.get("seat", ""),
                                 rec.get("level", ""), rec.get("what", ""),
                                 rec.get("memo", "")])
            writer.writerow([])
            writer.writerow(["※ 본 기록은 감독 보조 프로그램의 참고 자료이며, "
                             "최종 판단은 감독관의 직접 확인에 따릅니다."])
        self.set_status(f"CSV 파일로 저장했습니다: {os.path.basename(path)}")

    def load_records(self):
        try:
            with open(RECORD_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self.records = [r for r in data.get("records", []) if isinstance(r, dict)]
            self.room.set(data.get("room", ""))
            self.proctor.set(data.get("proctor", ""))
        except (OSError, ValueError, AttributeError):
            self.records = []

    def save_records(self):
        try:
            with open(RECORD_FILE, "w", encoding="utf-8") as f:
                json.dump({"room": self.room.get(), "proctor": self.proctor.get(),
                           "records": self.records}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


def pick_font(root):
    """운영체제에 설치된 한글 글꼴을 골라 쓴다."""
    import tkinter.font as tkfont
    installed = set(tkfont.families(root))
    for name in ["맑은 고딕", "Malgun Gothic", "Apple SD Gothic Neo",
                 "NanumGothic", "나눔고딕", "Noto Sans CJK KR", "Noto Sans KR"]:
        if name in installed:
            return name
    return "TkDefaultFont"


def install_error_handler(root):
    """예상치 못한 오류가 나도 프로그램이 멈추지 않도록 기록하고 알린다."""
    import traceback
    log_path = os.path.join(app_dir(), "error_log.txt")
    last_shown = [0.0]

    def handler(exc_type, exc_value, exc_tb):
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]\n{detail}")
        except OSError:
            pass
        if time.time() - last_shown[0] > 5:   # 같은 오류 창이 계속 뜨지 않도록
            last_shown[0] = time.time()
            messagebox.showerror("오류", f"작업 중 오류가 발생했습니다.\n\n{exc_value}\n\n"
                                       f"자세한 내용은 error_log.txt에 저장되었습니다.")
    root.report_callback_exception = handler


if __name__ == "__main__":
    root = tk.Tk()
    FONT = pick_font(root)
    install_error_handler(root)
    DetectorApp(root)
    root.mainloop()

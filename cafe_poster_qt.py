"""N통합 발행기 — PySide6 (Qt6) UI"""

import os
import sys
import threading
import configparser
from datetime import datetime

# 콘솔 숨기기 (Windows) — FreeConsole은 Playwright 서브프로세스 pipe를 깨뜨리므로 사용 금지
if sys.platform == 'win32':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        user32.ShowWindow(hwnd, 0)  # SW_HIDE

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QComboBox, QFileDialog,
    QSlider, QGroupBox, QFormLayout, QRadioButton, QButtonGroup, QCheckBox,
    QSplitter, QFrame, QScrollArea, QMessageBox, QSizePolicy, QSpinBox,
    QListWidget, QListWidgetItem, QAbstractItemView, QDialog, QDialogButtonBox,
    QProgressBar
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer, Slot, QMetaObject, Q_ARG, Qt as QtConst
from PySide6.QtGui import QPixmap, QFont, QIcon, QIntValidator
from qt_material import apply_stylesheet

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from core.browser import NaverBrowser
from core.poster import post_to_cafe
from core.tethering import toggle_tethering, get_current_ip
from core.template_parser import list_postings
from core import seo_generator
from core.cafe_editor import fetch_my_articles, edit_article_replace

CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'config.ini')
POSTINGS_DIR = os.path.join(BASE_DIR, 'postings_cafe')

VERSION = 'ver.2026.04.17'

# 원고 템플릿 — 업종별 원고 구조 분기
# - 청소: 작업 전/작업 후 사진 분리 + '인용구: 청소 전/후' 구조
# - 포장이사: 단일 사진 폴더 (별도 사진/포장이사 폴더), 작업 전/후 분리 없음, 정보·후기 혼합 톤
TEMPLATE_LIST = ['청소', '포장이사']


class CafeFolderDialog(QDialog):
    """카페 원고생성기 사진 폴더 설정 팝업"""
    def __init__(self, parent, thumb_val, before_vals, after_vals):
        super().__init__(parent)
        self.setWindowTitle("사진 폴더 설정")
        self.setMinimumWidth(600)
        self.setModal(True)

        layout = QVBoxLayout(self)

        form_group = QGroupBox("사진 폴더 — 원고 저장 시 이미지 자동 복사")
        form = QFormLayout(form_group)
        form.setLabelAlignment(Qt.AlignRight)

        def make_row(val, placeholder):
            row = QHBoxLayout()
            entry = QLineEdit(val)
            entry.setPlaceholderText(placeholder)
            row.addWidget(entry)
            btn = QPushButton("폴더")
            btn.setFixedWidth(48)
            btn.clicked.connect(lambda _=False, e=entry: self._pick_folder(e))
            row.addWidget(btn)
            return row, entry

        thumb_row, self.thumb_entry = make_row(thumb_val, '썸네일 폴더')
        form.addRow("썸네일", thumb_row)

        self.before_entries = []
        for i, val in enumerate(before_vals, 1):
            row, entry = make_row(val, f'작업 전 사진 폴더 ({i}순위)')
            form.addRow(f"작업 전 {i}순위", row)
            self.before_entries.append(entry)

        self.after_entries = []
        for i, val in enumerate(after_vals, 1):
            row, entry = make_row(val, f'작업 후 사진 폴더 ({i}순위)')
            form.addRow(f"작업 후 {i}순위", row)
            self.after_entries.append(entry)

        layout.addWidget(form_group)

        btn_box = QDialogButtonBox()
        btn_save = btn_box.addButton("💾  저장", QDialogButtonBox.AcceptRole)
        btn_cancel = btn_box.addButton("취소", QDialogButtonBox.RejectRole)
        btn_save.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; padding: 6px 18px; border-radius: 4px; border: none;")
        btn_cancel.setStyleSheet("padding: 6px 18px;")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _pick_folder(self, entry):
        path = QFileDialog.getExistingDirectory(self, "폴더 선택", entry.text() or "")
        if path:
            entry.setText(path)

    def get_values(self):
        return (
            self.thumb_entry.text().strip(),
            [e.text().strip() for e in self.before_entries],
            [e.text().strip() for e in self.after_entries],
        )


class UIBridge(QObject):
    """쓰레드 → UI 안전 통신"""
    append_log = Signal(object, str)  # (widget, html)
    set_text = Signal(object, str)    # (widget, text)
    set_plain = Signal(object, str)   # (widget, text)
    set_enabled = Signal(object, bool)  # (widget, enabled)
    show_msgbox = Signal(str, str)    # (title, message)
    set_progress = Signal(object, int, int, str)  # (bar, value, max, label_text)


class CafePosterQt(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("N통합 발행기")
        self.setMinimumSize(1000, 750)
        self.resize(1250, 850)

        self.browser = NaverBrowser()
        self.is_running = False
        self.stop_flag = False
        self.config = configparser.ConfigParser()
        self._load_config()

        # 쓰레드→UI 브릿지
        self._bridge = UIBridge()
        self._bridge.append_log.connect(self._do_append_log)
        self._bridge.set_text.connect(self._on_set_text)
        self._bridge.set_plain.connect(lambda w, t: w.setPlainText(t))
        self._bridge.set_enabled.connect(lambda w, e: w.setEnabled(e))
        self._bridge.show_msgbox.connect(lambda t, m: QMessageBox.information(self, t, m))
        self._bridge.set_progress.connect(self._on_set_progress)

        self._build_ui()
        self._refresh_postings()

    def _save_all_settings(self):
        """현재 UI의 주요 값들을 config.ini에 일괄 저장 + 현재 템플릿 섹션에도 저장."""
        saved = 0
        try:
            # 0) 현재 템플릿
            cur_template = self._cfg('GENERATOR', 'template', '청소')
            for attr in ('cafe_template', 'blog_template', 'one_template', 'oneb_template', 'onec_template'):
                cb = getattr(self, attr, None)
                if cb is not None:
                    cur_template = cb.currentText()
                    break
            self._set_cfg('GENERATOR', 'template', cur_template)

            # 1) 카페 자동댓글
            if hasattr(self, 'ac_accounts'):
                self._on_ac_save()
                saved += 1

            # 2) 카페 발행 — 계정·카페 목록·딜레이
            if hasattr(self, 'pub_accounts'):
                raw = self.pub_accounts.toPlainText().strip()
                self._set_cfg('CAFE', 'account_list', raw.replace('\n', '\\n'))
            if hasattr(self, 'pub_cafes'):
                raw = self.pub_cafes.toPlainText().strip()
                self._set_cfg('CAFE', 'cafe_list', raw.replace('\n', '\\n'))
            if hasattr(self, 'pub_delay'):
                self._set_cfg('POSTING', 'delay', str(self.pub_delay.value()))
            if hasattr(self, 'cafe_brand'):
                self._set_cfg('CAFE', 'brand', self.cafe_brand.currentText())

            # 3) 카페 원고생성기 + 카드뉴스 옵션
            if hasattr(self, 'cafegen_brand'):
                self._set_cfg('CAFE', 'cafegen_brand', self.cafegen_brand.currentText())

            # 4) 원큐 자체 옵션
            for key, attr in [
                ('one_delay', 'one_delay'), ('oneb_delay', 'oneb_delay'),
                ('onec_delay', 'onec_delay'),
                ('one_img_count', 'one_img_count'),
                ('oneb_img_count', 'oneb_img_count'),
            ]:
                w = getattr(self, attr, None)
                if w is not None:
                    self._set_cfg('POSTING', key, str(w.value()))
            if hasattr(self, 'onec_brand'):
                self._set_cfg('CAFE', 'onec_brand', self.onec_brand.currentText())

            # 5) 템플릿별 이미지 수 저장
            try:
                self._save_template_settings(cur_template)
            except Exception:
                pass

            # 6) 사진 폴더 — 현재 선택된 이미지 템플릿 섹션에 저장
            try:
                _tpl_img = (self.img_template.currentText()
                            if hasattr(self, 'img_template') else '새집느낌')
                self._save_photos_to_section(_tpl_img)
            except Exception:
                pass

            # 7) 자동댓글 상태 등은 각 핸들러가 자체 저장
            self._save_config_file()
            QMessageBox.information(self, "저장 완료",
                f"모든 설정 저장 완료\n현재 템플릿: {cur_template}\n다음 실행에서 자동 로드됩니다.")
        except Exception as e:
            QMessageBox.warning(self, "저장 실패", f"설정 저장 중 오류:\n{e}")

    def _show_secondary_tab(self, title: str):
        """보조 탭 보이게 + 그 탭으로 전환."""
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == title:
                self.tabs.setTabVisible(i, True)
                self.tabs.setCurrentIndex(i)
                return

    def _hide_all_secondary_tabs(self):
        """보조 탭 모두 다시 숨김 — 원큐·자동댓글·이미지재가공만 남김."""
        for title in getattr(self, '_secondary_tab_titles', []):
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == title:
                    self.tabs.setTabVisible(i, False)
                    break

    def _on_set_progress(self, bar, value, total, label):
        """progress bar 업데이트 — 메인 쓰레드에서 안전하게.
        total=0 → idle 상태 (0% + 라벨 리셋)
        total>0 → 카운트다운 진행"""
        if bar is None:
            return
        try:
            bar.setVisible(True)  # 항상 보이게 유지
            if total <= 0:
                bar.setMaximum(100)
                bar.setValue(0)
                bar.setFormat("대기 없음 (다음 키워드 사이 카운트다운 표시)")
                return
            bar.setMaximum(total)
            bar.setValue(value)
            if label:
                bar.setFormat(label)
        except Exception:
            pass

    def _on_set_text(self, widget, text):
        """set_text Signal 처리 — QPixmap 특수 처리 포함"""
        if text.startswith('__PIXMAP__'):
            path = text[10:]
            if os.path.isfile(path):
                pix = QPixmap(path).scaled(420, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                widget.setPixmap(pix)
            else:
                widget.setText("이미지 로드 실패")
        else:
            widget.setText(text)

    def _do_append_log(self, widget, html):
        widget.append(html)
        sb = widget.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Config ──
    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            self.config.read(CONFIG_PATH, encoding='utf-8')

    def _save_config_file(self):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            self.config.write(f)

    def _cfg(self, section, key, fallback=''):
        return self.config.get(section, key, fallback=fallback)

    def _set_cfg(self, section, key, val):
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = str(val)

    # ── UI ──
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 헤더
        header = QHBoxLayout()
        title = QLabel("N통합 발행기")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #e6edf3;")
        header.addWidget(title)
        ver = QLabel(VERSION)
        ver.setStyleSheet("color: #636e72; font-size: 11px; padding-top: 10px;")
        header.addWidget(ver)
        header.addStretch()
        layout.addLayout(header)

        # 탭 (원큐는 분석/생성/패키징 UI가 먼저 만들어진 뒤에 빌드)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_image_tab(), "썸네일 생성")
        self.tabs.addTab(self._build_analyzer_tab(), "포스팅 분석")
        self.tabs.addTab(self._build_blog_gen_tab(), "블로그 원고생성기")
        self.tabs.addTab(self._build_blog_pack_tab(), "블로그발행(패키징)")
        self.tabs.addTab(self._build_blog_auto_tab(), "블로그발행(자동)")
        self.tabs.addTab(self._build_cafe_gen_tab(), "카페 원고생성기")
        self.tabs.addTab(self._build_publish_tab(), "카페 발행(자동)")
        self.tabs.addTab(self._build_cafe_edit_tab(), "카페 글수정(SEO)")
        self.tabs.addTab(self._build_auto_comment_tab(), "카페 자동댓글")
        self.tabs.addTab(self._build_image_reprocess_tab(), "이미지 재가공")
        # 원큐 3종은 맨 앞으로 삽입 (패키징 → 블로그 → 카페)
        self.tabs.insertTab(0, self._build_one_shot_tab(), "🚀 원큐(패키징)")
        self.tabs.insertTab(1, self._build_one_shot_blog_tab(), "🚀 원큐(블로그)")
        self.tabs.insertTab(2, self._build_one_shot_cafe_tab(), "🚀 원큐(카페)")
        self.tabs.setCurrentIndex(0)

        # 보조 탭 — 자주 안 쓰는 것 숨김 (메뉴바 '기타 도구'에서 토글)
        self._secondary_tab_titles = [
            "썸네일 생성", "포스팅 분석",
            "블로그 원고생성기", "블로그발행(패키징)", "블로그발행(자동)",
            "카페 원고생성기", "카페 발행(자동)",
        ]
        for title in self._secondary_tab_titles:
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == title:
                    self.tabs.setTabVisible(i, False)
                    break

        # 메뉴바: 기타 도구 → 클릭하면 해당 탭 표시 + 전환
        try:
            mb = self.menuBar()
            # 글로벌 설정 저장 메뉴
            save_menu = mb.addMenu("💾 설정")
            save_act = save_menu.addAction("💾 모든 설정 저장 (현재 UI 값 → config.ini)")
            save_act.triggered.connect(self._save_all_settings)
            # 기타 도구 메뉴
            tools_menu = mb.addMenu("🔧 기타 도구")
            for title in self._secondary_tab_titles:
                act = tools_menu.addAction(title)
                act.triggered.connect(lambda checked=False, t=title: self._show_secondary_tab(t))
            tools_menu.addSeparator()
            hide_act = tools_menu.addAction("✕ 모든 보조 탭 숨기기")
            hide_act.triggered.connect(self._hide_all_secondary_tabs)
        except Exception:
            pass
        layout.addWidget(self.tabs)

        # ── 초기 동기화: 모든 탭이 빌드된 후, 현재 이미지 템플릿값으로
        #    업체명 드롭다운들을 한 번 강제 맞춤 (저장값이 어긋나 있어도 통일) ──
        try:
            init_tpl = getattr(self, '_current_img_template', None) \
                       or self._cfg('IMAGE', 'template', '새집느낌')
            self._sync_template_dropdowns(init_tpl)
        except Exception:
            pass

        # ── 패키징 템플릿(청소/포장이사) ↔ img_template(새집느낌/가족사랑클린/포장이사) 정합성 체크
        #    저장된 상태가 어긋나 있으면 (예: 패키징=청소 + img_template=포장이사) 자동 정정 ──
        try:
            pack_tpl = self._cfg('GENERATOR', 'template', '청소')
            img_tpl = getattr(self, '_current_img_template', None) \
                       or self._cfg('IMAGE', 'template', '새집느낌')
            need_fix = False
            target_img = None
            if pack_tpl == '포장이사' and img_tpl != '포장이사':
                need_fix, target_img = True, '포장이사'
            elif pack_tpl == '청소' and img_tpl == '포장이사':
                need_fix, target_img = True, '새집느낌'
            if need_fix and hasattr(self, 'img_template'):
                idx = self.img_template.findText(target_img)
                if idx >= 0:
                    # signal 살려서 _on_img_template_changed → bg_path/사진폴더 재로드까지 트리거
                    self.img_template.setCurrentIndex(idx)
        except Exception:
            pass

    # ═══════ 이미지 생성 탭 ═══════
    def _build_image_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        # 좌측
        left = QWidget()
        lv = QVBoxLayout(left)

        # 배경
        g1 = QGroupBox("썸네일 생성")
        f1 = QFormLayout(g1); f1.setLabelAlignment(Qt.AlignRight)

        # 템플릿 선택 드롭다운
        self.img_template = QComboBox()
        self.img_template.addItems(['새집느낌', '가족사랑클린', '포장이사'])
        _initial_tpl = self._cfg('IMAGE', 'template', '새집느낌')
        self.img_template.setCurrentText(_initial_tpl)
        self._current_img_template = _initial_tpl  # 드롭다운 전환 시 이전값 추적용
        self.img_template.currentTextChanged.connect(self._on_img_template_changed)
        f1.addRow("템플릿", self.img_template)

        bg_row = QHBoxLayout()
        self.img_bg = QLineEdit(self._load_img_field('bg_path', '', _initial_tpl))
        self.img_bg.setPlaceholderText("사진 파일 또는 폴더")
        bg_row.addWidget(self.img_bg)
        btn_file = QPushButton("파일")
        btn_file.setMinimumWidth(45)
        btn_file.clicked.connect(self._pick_bg_file)
        bg_row.addWidget(btn_file)
        btn_folder = QPushButton("폴더")
        btn_folder.setMinimumWidth(45)
        btn_folder.clicked.connect(self._pick_bg_folder)
        bg_row.addWidget(btn_folder)
        f1.addRow("배경사진", bg_row)

        # 배경 네비게이션
        self._bg_files = []
        self._bg_index = 0
        nav_row = QHBoxLayout()
        btn_prev = QPushButton("<")
        btn_prev.setFixedWidth(30)
        btn_prev.clicked.connect(lambda: self._bg_nav(-1))
        nav_row.addWidget(btn_prev)
        self.bg_nav_label = QLabel("사진을 선택하세요")
        self.bg_nav_label.setAlignment(Qt.AlignCenter)
        nav_row.addWidget(self.bg_nav_label, 1)
        btn_next = QPushButton(">")
        btn_next.setFixedWidth(30)
        btn_next.clicked.connect(lambda: self._bg_nav(1))
        nav_row.addWidget(btn_next)
        f1.addRow("", nav_row)

        out_row = QHBoxLayout()
        self.img_out = QLineEdit(self._load_img_field('output_path', '', _initial_tpl))
        self.img_out.setPlaceholderText("저장 폴더")
        out_row.addWidget(self.img_out)
        btn_out = QPushButton("찾기")
        btn_out.setMinimumWidth(45)
        btn_out.clicked.connect(lambda: self._pick_folder(self.img_out))
        out_row.addWidget(btn_out)
        f1.addRow("저장폴더", out_row)

        img_save_row = QHBoxLayout()
        btn_save_img_dirs = QPushButton("💾 폴더 설정 저장")
        btn_save_img_dirs.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px; border: none;")
        btn_save_img_dirs.clicked.connect(self._on_save_img_folders)
        img_save_row.addWidget(btn_save_img_dirs)
        self.img_folder_status = QLabel("")
        img_save_row.addWidget(self.img_folder_status, 1)
        f1.addRow("", img_save_row)

        lv.addWidget(g1)

        # 텍스트
        g2 = QGroupBox("텍스트 설정")
        f2 = QFormLayout(g2); f2.setLabelAlignment(Qt.AlignRight)
        # 템플릿별 기본값
        _def = self._img_defaults(_initial_tpl)

        self.img_t1 = QTextEdit()
        self.img_t1.setMaximumHeight(50)
        self.img_t1.setPlainText(self._load_img_field('text1', _def['text1'], _initial_tpl))
        f2.addRow("서비스명", self.img_t1)
        self.img_t2 = QLineEdit(self._load_img_field('text2', _def['text2'], _initial_tpl))
        f2.addRow("전화번호", self.img_t2)

        font_row = QHBoxLayout()
        self.img_font = QComboBox()
        self.img_font.addItems(['맑은 고딕 Bold', '맑은 고딕', '굴림', '바탕', 'Impact'])
        self.img_font.setCurrentText(self._load_img_field('font_name', _def['font_name'], _initial_tpl))
        font_row.addWidget(self.img_font)
        self.img_color = QLineEdit(self._load_img_field('font_color', _def['font_color'], _initial_tpl))
        self.img_color.setFixedWidth(80)
        font_row.addWidget(self.img_color)
        self.img_outline = QComboBox()
        self.img_outline.addItems(['없음', '얇게', '보통', '두껍게', '매우 두껍게'])
        self.img_outline.setCurrentText(self._load_img_field('outline', _def['outline'], _initial_tpl))
        font_row.addWidget(self.img_outline)
        f2.addRow("폰트/색상", font_row)

        size_row = QHBoxLayout()
        self.img_fs1 = QSpinBox(); self.img_fs1.setRange(30, 200); self.img_fs1.setValue(int(self._load_img_field('font_size1', str(_def['font_size1']), _initial_tpl)))
        size_row.addWidget(QLabel("크기1")); size_row.addWidget(self.img_fs1)
        self.img_fs2 = QSpinBox(); self.img_fs2.setRange(30, 200); self.img_fs2.setValue(int(self._load_img_field('font_size2', str(_def['font_size2']), _initial_tpl)))
        size_row.addWidget(QLabel("크기2")); size_row.addWidget(self.img_fs2)
        f2.addRow("", size_row)

        pos_row = QHBoxLayout()
        self.img_t1y = QSpinBox(); self.img_t1y.setRange(0, 1080); self.img_t1y.setValue(int(self._load_img_field('text1_y', str(_def['text1_y']), _initial_tpl)))
        pos_row.addWidget(QLabel("위치1")); pos_row.addWidget(self.img_t1y)
        self.img_t2y = QSpinBox(); self.img_t2y.setRange(0, 1080); self.img_t2y.setValue(int(self._load_img_field('text2_y', str(_def['text2_y']), _initial_tpl)))
        pos_row.addWidget(QLabel("위치2")); pos_row.addWidget(self.img_t2y)
        f2.addRow("", pos_row)

        adj_row = QHBoxLayout()
        self.img_ox = QSpinBox(); self.img_ox.setRange(-200, 200); self.img_ox.setValue(int(self._load_img_field('offset_x', str(_def.get('offset_x', 0)), _initial_tpl)))
        adj_row.addWidget(QLabel("좌우")); adj_row.addWidget(self.img_ox)
        self.img_oy = QSpinBox(); self.img_oy.setRange(-200, 200); self.img_oy.setValue(int(self._load_img_field('offset_y', str(_def.get('offset_y', 0)), _initial_tpl)))
        adj_row.addWidget(QLabel("상하")); adj_row.addWidget(self.img_oy)
        self.img_bh = QSpinBox(); self.img_bh.setRange(400, 800); self.img_bh.setValue(int(self._load_img_field('bg_height', str(_def.get('bg_height', 650)), _initial_tpl)))
        adj_row.addWidget(QLabel("높이")); adj_row.addWidget(self.img_bh)
        f2.addRow("", adj_row)

        lv.addWidget(g2)

        # 버튼
        btn_row = QHBoxLayout()
        btn_save = QPushButton("설정 저장")
        btn_save.clicked.connect(self._save_image_config)
        btn_row.addWidget(btn_save)
        btn_preview = QPushButton("미리보기")
        btn_preview.clicked.connect(self._preview_thumbnail)
        btn_row.addWidget(btn_preview)
        btn_gen = QPushButton("생성")
        btn_gen.setStyleSheet("background-color: #e94560; color: white; font-weight: bold; border: none; border-radius: 8px;")
        btn_gen.clicked.connect(self._create_thumbnail)
        btn_row.addWidget(btn_gen)
        lv.addLayout(btn_row)
        lv.addStretch()

        # 우측 — 미리보기
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("미리보기"))
        self.img_preview = QLabel("이미지를 선택하세요")
        self.img_preview.setAlignment(Qt.AlignCenter)
        self.img_preview.setMinimumHeight(300)
        rv.addWidget(self.img_preview, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([380, 420])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)

        # 포장이사가 초기 템플릿이면 기본 폴더 자동 로드 → 좌우 네비 즉시 동작
        if _initial_tpl == '포장이사':
            _default_pkg = os.path.join(BASE_DIR, '사진', '포장이사 썸네일 가공 전')
            if os.path.isdir(_default_pkg):
                # img_bg 가 비어있으면 채워주기
                if not self.img_bg.text().strip():
                    self.img_bg.setText(_default_pkg)
                # 폴더 한번 로드 (qt 이벤트 루프 시작 후 실행)
                QTimer.singleShot(100, lambda: self._load_bg_folder(_default_pkg))

        return tab

    def _make_priority_dir_rows(self, form, label, attr_list_name,
                                cfg_section, cfg_key_base, placeholder):
        """1~5순위 폴더 입력행을 form에 추가하고 self.{attr_list_name}에 [QLineEdit×5] 저장.
        1순위 저장값이 비었으면 cfg_key_base(구형 키) 로 폴백."""
        entries = []
        for i in range(1, 6):
            row = QHBoxLayout()
            if i == 1:
                val = self._cfg(cfg_section, f'{cfg_key_base}_1', '') \
                      or self._cfg(cfg_section, cfg_key_base, '')
            else:
                val = self._cfg(cfg_section, f'{cfg_key_base}_{i}', '')
            entry = QLineEdit(val)
            entry.setPlaceholderText(f'{placeholder} ({i}순위)')
            row.addWidget(entry)
            btn = QPushButton("폴더")
            btn.setFixedWidth(40)
            btn.clicked.connect(lambda _checked=False, e=entry: self._pick_folder(e))
            row.addWidget(btn)
            form.addRow(f"{label} {i}순위", row)
            entries.append(entry)
        setattr(self, attr_list_name, entries)

    @staticmethod
    def _collect_priority_paths(entry_list):
        """[QLineEdit×N] → [비어있지 않은 경로, ...] (순위 유지). N은 현재 1~5."""
        return [e.text().strip() for e in (entry_list or []) if e.text().strip()]

    # ═══════ 카페 원고생성기 탭 ═══════
    def _build_cafe_gen_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        # 사진 폴더 설정 — 값은 hidden QLineEdit에 보관, 팝업으로 편집 (템플릿별 분리 저장)
        _tpl = getattr(self, '_current_img_template', None) or self._cfg('IMAGE', 'template', '새집느낌')
        self.cafe_thumb_dir = QLineEdit(self._load_photo_field('cafe_thumb_dir', 'GENERATOR', _tpl))
        self.cafe_before_dirs = []
        self.cafe_after_dirs = []
        for i in range(1, 6):
            val = self._load_photo_field(f'cafe_before_dir_{i}', 'GENERATOR', _tpl)
            if not val and i == 1 and _tpl == '새집느낌':
                val = self._cfg('GENERATOR', 'cafe_before_dir', '')
            self.cafe_before_dirs.append(QLineEdit(val))
        for i in range(1, 6):
            val = self._load_photo_field(f'cafe_after_dir_{i}', 'GENERATOR', _tpl)
            if not val and i == 1 and _tpl == '새집느낌':
                val = self._cfg('GENERATOR', 'cafe_after_dir', '')
            self.cafe_after_dirs.append(QLineEdit(val))

        # 포장이사 전용 사진 폴더 (단일 폴더 — 1.png, 2.png ... 순서대로)
        _packing_default = os.path.join(BASE_DIR, '사진', '포장이사')
        self.cafe_packing_dir = QLineEdit(self._cfg('PACKING', 'photo_dir', _packing_default))

        folder_btn_row = QHBoxLayout()
        btn_open_folder_dlg = QPushButton("📁  사진 폴더 설정(청소)")
        btn_open_folder_dlg.setStyleSheet(
            "background-color: #2d3436; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: 1px solid #6c5ce7;")
        btn_open_folder_dlg.clicked.connect(self._open_cafe_folder_dialog)
        folder_btn_row.addWidget(btn_open_folder_dlg)
        btn_open_packing_dlg = QPushButton("📦  사진 폴더 설정(포장이사)")
        btn_open_packing_dlg.setStyleSheet(
            "background-color: #2d3436; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: 1px solid #f59e0b;")
        btn_open_packing_dlg.clicked.connect(self._open_packing_folder_dialog)
        folder_btn_row.addWidget(btn_open_packing_dlg)
        self.cafe_folder_status = QLabel("")
        folder_btn_row.addWidget(self.cafe_folder_status, 1)
        lv.addLayout(folder_btn_row)

        g = QGroupBox("AI 설정")
        f = QFormLayout(g); f.setLabelAlignment(Qt.AlignRight)

        self.cafe_template = QComboBox()
        self.cafe_template.addItems(TEMPLATE_LIST)
        _tpl = self._cfg('GENERATOR', 'template', '청소')
        _ti = self.cafe_template.findText(_tpl)
        if _ti >= 0:
            self.cafe_template.setCurrentIndex(_ti)
        self.cafe_template.currentTextChanged.connect(self._on_template_changed)
        f.addRow("템플릿", self.cafe_template)

        # 발행 업체 — 카페 원고 생성 시 본문에 들어갈 회사명/전화번호 결정
        self.cafegen_brand = QComboBox()
        self.cafegen_brand.addItems(['새집느낌', '가족사랑클린', '카드뉴스(정보성)'])
        _cgb = self._cfg('CAFE', 'cafegen_brand', '새집느낌') or '새집느낌'
        _cgi = self.cafegen_brand.findText(_cgb)
        self.cafegen_brand.setCurrentIndex(_cgi if _cgi >= 0 else 0)
        self.cafegen_brand.currentTextChanged.connect(self._on_cafegen_brand_changed)
        f.addRow("업체명", self.cafegen_brand)

        # 작성 관점 — 고객 후기 vs 업체 소개 톤 분기 (블로그 패턴 동일)
        cafegen_vp_row = QHBoxLayout()
        self.cafegen_vp_group = QButtonGroup()
        _cur_vp = (self._cfg('CAFE', 'cafegen_viewpoint', '고객') or '고객').strip()
        _vp_rb1 = QRadioButton("고객 관점")
        _vp_rb2 = QRadioButton("업체 관점")
        if _cur_vp == '업체':
            _vp_rb2.setChecked(True)
        else:
            _vp_rb1.setChecked(True)
        self.cafegen_vp_group.addButton(_vp_rb1, 0)
        self.cafegen_vp_group.addButton(_vp_rb2, 1)
        cafegen_vp_row.addWidget(_vp_rb1); cafegen_vp_row.addWidget(_vp_rb2)
        self.cafegen_vp_group.buttonClicked.connect(
            lambda btn: self._on_cafegen_viewpoint_changed('업체' if btn is _vp_rb2 else '고객'))
        f.addRow("작성 관점", cafegen_vp_row)

        self.cafe_model = QComboBox()
        self.cafe_model.addItems(['gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo', 'claude-sonnet-4-6', 'claude-haiku-4-5'])
        saved_cafe_model = self._cfg('GENERATOR', 'cafe_model', 'gpt-4o-mini')
        c_idx = self.cafe_model.findText(saved_cafe_model)
        if c_idx >= 0:
            self.cafe_model.setCurrentIndex(c_idx)
        f.addRow("모델", self.cafe_model)
        self.cafe_oai_key = QLineEdit(self._cfg('GENERATOR', 'openai_api_key'))
        self.cafe_oai_key.setPlaceholderText("GPT 사용 시만")
        f.addRow("OpenAI 키", self.cafe_oai_key)
        self.cafe_claude_key = QLineEdit(self._cfg('GENERATOR', 'claude_api_key'))
        self.cafe_claude_key.setPlaceholderText("Claude 사용 시만")
        f.addRow("Claude 키", self.cafe_claude_key)

        cafe_key_save_row = QHBoxLayout()
        btn_save_cafe_keys = QPushButton("💾 API 키 저장")
        btn_save_cafe_keys.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px; border: none;")
        btn_save_cafe_keys.clicked.connect(self._on_save_cafe_api_keys)
        cafe_key_save_row.addWidget(btn_save_cafe_keys)
        self.cafe_key_status = QLabel("")
        cafe_key_save_row.addWidget(self.cafe_key_status, 1)
        f.addRow("", cafe_key_save_row)

        lv.addWidget(g)

        g2 = QGroupBox("키워드")
        v2 = QVBoxLayout(g2)
        self.cafe_keywords = QTextEdit()
        self.cafe_keywords.setMaximumHeight(80)
        self.cafe_keywords.setPlainText("천안입주청소\n인천이사청소\n부산이사청소")
        v2.addWidget(self.cafe_keywords)
        lv.addWidget(g2)

        g3 = QGroupBox("생성 설정")
        f3 = QFormLayout(g3); f3.setLabelAlignment(Qt.AlignRight)
        self.cafe_img_count = QSpinBox(); self.cafe_img_count.setRange(0, 30); self.cafe_img_count.setValue(9)
        f3.addRow("이미지 수", self.cafe_img_count)
        lv.addWidget(g3)

        cafe_btn_row = QHBoxLayout()
        self.cafe_gen_btn = QPushButton("카페 원고 생성")
        self.cafe_gen_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 12px; font-weight: bold; padding: 8px; border-radius: 6px; border: none;")
        self.cafe_gen_btn.clicked.connect(self._on_cafe_generate)
        cafe_btn_row.addWidget(self.cafe_gen_btn)
        btn_cafe_prompt = QPushButton("프롬프트 보기/수정")
        btn_cafe_prompt.clicked.connect(self._show_cafe_prompt)
        cafe_btn_row.addWidget(btn_cafe_prompt)
        lv.addLayout(cafe_btn_row)
        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("카페 원고 생성 로그"))
        self.cafe_log = QTextEdit()
        self.cafe_log.setReadOnly(True)
        self.cafe_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.cafe_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([350, 450])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    # ═══════ 카페 발행 탭 ═══════
    def _build_publish_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        # 계정 목록 (다중 저장 + 순차 가동)
        g_acc = QGroupBox("계정 목록")
        v_acc = QVBoxLayout(g_acc)
        self.pub_accounts = QTextEdit()
        self.pub_accounts.setMaximumHeight(80)
        saved_accounts = self._cfg('CAFE', 'account_list')
        if saved_accounts:
            self.pub_accounts.setPlainText(saved_accounts.replace('\\n', '\n'))
        self.pub_accounts.setPlaceholderText("아이디 | 비밀번호 (한 줄에 하나)")
        v_acc.addWidget(self.pub_accounts)
        v_acc.addWidget(QLabel("한 줄에: 아이디 | 비밀번호  (1개면 1개만, 여러 개면 순환)"))
        save_acc_row = QHBoxLayout()
        btn_save_acc = QPushButton("저장")
        btn_save_acc.setFixedWidth(60)
        btn_save_acc.setStyleSheet("background-color: #6c5ce7; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_save_acc.clicked.connect(self._on_save_account_list)
        save_acc_row.addWidget(btn_save_acc)
        # 발행 설정 (다이얼로그)
        btn_open_settings = QPushButton("⚙ 발행 설정")
        btn_open_settings.setFixedWidth(100)
        btn_open_settings.setStyleSheet("background-color: #3498db; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_open_settings.clicked.connect(self._on_open_cafe_settings_dialog)
        save_acc_row.addWidget(btn_open_settings)
        self.acc_list_status = QLabel("")
        save_acc_row.addWidget(self.acc_list_status, 1)
        v_acc.addLayout(save_acc_row)

        lv.addWidget(g_acc)

        # 카페 목록
        g2 = QGroupBox("카페 목록")
        v2 = QVBoxLayout(g2)
        self.pub_cafes = QTextEdit()
        self.pub_cafes.setMaximumHeight(60)
        saved_list = self._cfg('CAFE', 'cafe_list')
        if saved_list:
            self.pub_cafes.setPlainText(saved_list.replace('\\n', '\n'))
        self.pub_cafes.setPlaceholderText("게시판URL | 카페URL (한 줄에 하나)")
        v2.addWidget(self.pub_cafes)
        v2.addWidget(QLabel("한 줄에: 게시판URL | 카페URL  (여러 줄 = 순환 배분)"))
        save_cafe_row = QHBoxLayout()
        btn_save_cafes = QPushButton("저장")
        btn_save_cafes.setFixedWidth(60)
        btn_save_cafes.setStyleSheet("background-color: #6c5ce7; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_save_cafes.clicked.connect(self._on_save_cafe_list)
        save_cafe_row.addWidget(btn_save_cafes)
        btn_expand_cafes = QPushButton("전체보기")
        btn_expand_cafes.setFixedWidth(80)
        btn_expand_cafes.setStyleSheet("background-color: #3498db; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_expand_cafes.clicked.connect(self._on_expand_cafe_list)
        save_cafe_row.addWidget(btn_expand_cafes)
        self.cafe_list_status = QLabel("")
        save_cafe_row.addWidget(self.cafe_list_status, 1)
        v2.addLayout(save_cafe_row)
        lv.addWidget(g2)

        # 설정
        g3 = QGroupBox("발행 설정")
        f3 = QFormLayout(g3); f3.setLabelAlignment(Qt.AlignRight)
        # 발행 업체 — 발행 직전 본문에서 다른 브랜드 토큰을 이 값으로 치환
        self.cafe_brand = QComboBox()
        self.cafe_brand.addItems(['새집느낌', '가족사랑클린', '카드뉴스(정보성)'])
        _cb = self._cfg('CAFE', 'brand', '새집느낌') or '새집느낌'
        _ci = self.cafe_brand.findText(_cb)
        self.cafe_brand.setCurrentIndex(_ci if _ci >= 0 else 0)
        self.cafe_brand.setFixedWidth(160)
        self.cafe_brand.currentTextChanged.connect(self._on_cafe_brand_changed)
        f3.addRow("발행 업체", self.cafe_brand)
        self.pub_delay = QSpinBox(); self.pub_delay.setRange(5, 86400); self.pub_delay.setValue(int(self._cfg('POSTING', 'delay', '30')))
        self.pub_delay.setSuffix("초"); self.pub_delay.setFixedWidth(120)
        f3.addRow("딜레이", self.pub_delay)
        lv.addWidget(g3)

        # 원고 목록
        g4 = QGroupBox("원고 목록")
        v4 = QVBoxLayout(g4)

        # 원고 폴더 선택
        pub_dir_row = QHBoxLayout()
        pub_dir_row.addWidget(QLabel("원고 폴더"))
        self.pub_postings_dir = QLineEdit(self._cfg('POSTING', 'cafe_postings_dir', POSTINGS_DIR))
        self.pub_postings_dir.setPlaceholderText("원고가 있는 폴더 경로")
        pub_dir_row.addWidget(self.pub_postings_dir, 1)
        btn_pub_dir_pick = QPushButton("폴더")
        btn_pub_dir_pick.setFixedWidth(50)
        btn_pub_dir_pick.clicked.connect(lambda: (self._pick_folder(self.pub_postings_dir), self._refresh_postings()))
        pub_dir_row.addWidget(btn_pub_dir_pick)
        btn_pub_dir_save = QPushButton("💾")
        btn_pub_dir_save.setFixedWidth(40)
        btn_pub_dir_save.setToolTip("폴더 경로 저장")
        btn_pub_dir_save.setStyleSheet("background-color: #6c5ce7; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_pub_dir_save.clicked.connect(self._on_save_cafe_postings_dir)
        pub_dir_row.addWidget(btn_pub_dir_save)
        v4.addLayout(pub_dir_row)

        self.pub_postings = QListWidget()
        self.pub_postings.setMaximumHeight(140)
        self.pub_postings.setSelectionMode(QAbstractItemView.NoSelection)
        self.pub_postings.setStyleSheet(self._posting_list_style())
        v4.addWidget(self.pub_postings)
        sel_row = QHBoxLayout()
        btn_sel_all = QPushButton("전체 선택")
        btn_sel_all.setFixedWidth(80)
        btn_sel_all.setStyleSheet("font-size: 11px; padding: 4px;")
        btn_sel_all.clicked.connect(lambda: self._toggle_postings(self.pub_postings, True))
        sel_row.addWidget(btn_sel_all)
        btn_sel_none = QPushButton("전체 해제")
        btn_sel_none.setFixedWidth(80)
        btn_sel_none.setStyleSheet("font-size: 11px; padding: 4px;")
        btn_sel_none.clicked.connect(lambda: self._toggle_postings(self.pub_postings, False))
        sel_row.addWidget(btn_sel_none)
        sel_row.addStretch()
        btn_refresh = QPushButton("새로고침")
        btn_refresh.setFixedWidth(80)
        btn_refresh.setStyleSheet("font-size: 11px; padding: 4px;")
        btn_refresh.clicked.connect(self._refresh_postings)
        sel_row.addWidget(btn_refresh)
        v4.addLayout(sel_row)
        lv.addWidget(g4)

        # 시작/중지
        btn_row = QHBoxLayout()
        self.pub_start_btn = QPushButton("작업 시작")
        self.pub_start_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 12px; font-weight: bold; padding: 8px; border-radius: 6px; border: none;")
        self.pub_start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.pub_start_btn)
        self.pub_stop_btn = QPushButton("중지")
        self.pub_stop_btn.setEnabled(False)
        self.pub_stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.pub_stop_btn)
        lv.addLayout(btn_row)
        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("실행 로그"))
        self.pub_log = QTextEdit()
        self.pub_log.setReadOnly(True)
        self.pub_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.pub_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([350, 450])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    # ═══════ 카페 자동댓글 탭 ═══════
    def _build_auto_comment_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        # 자동댓글 전용 계정 (카페 발행 탭과 분리)
        g_acc = QGroupBox("자동댓글 전용 계정 — 한 줄에 하나 (아이디 | 비밀번호)")
        v_acc = QVBoxLayout(g_acc)
        self.ac_accounts = QTextEdit()
        self.ac_accounts.setMaximumHeight(70)
        self.ac_accounts.setPlaceholderText("아이디 | 비밀번호 (한 줄에 하나, 첫 번째 계정만 사용)")
        saved_ac_accounts = self._cfg('AUTO_COMMENT', 'account_list', '')
        if saved_ac_accounts:
            self.ac_accounts.setPlainText(saved_ac_accounts.replace('\\n', '\n'))
        v_acc.addWidget(self.ac_accounts)
        _acc_lbl = QLabel("• 카페 발행(자동) 탭의 계정과 분리 운영 — 댓글 전용 부계정 권장")
        _acc_lbl.setStyleSheet("color: #8b949e; padding: 6px; font-size: 11px;")
        _acc_lbl.setWordWrap(True); _acc_lbl.setMinimumHeight(28)
        v_acc.addWidget(_acc_lbl)
        lv.addWidget(g_acc)

        # 카페·게시판 URL 목록
        g1 = QGroupBox("모니터링 게시판 URL — 한 줄에 하나")
        v1 = QVBoxLayout(g1)
        self.ac_urls = QTextEdit()
        self.ac_urls.setMaximumHeight(80)
        self.ac_urls.setPlaceholderText(
            "예시:\nhttps://cafe.naver.com/ca-fe/cafes/10174516/menus/664\nhttps://cafe.naver.com/ca-fe/cafes/12345678/menus/100"
        )
        saved_urls = self._cfg('AUTO_COMMENT', 'urls', '')
        if saved_urls:
            self.ac_urls.setPlainText(saved_urls.replace('\\n', '\n'))
        v1.addWidget(self.ac_urls)
        _url_lbl = QLabel("게시판 URL 형식: cafes/{cafe_id}/menus/{menu_id}")
        _url_lbl.setStyleSheet("color: #8b949e; padding: 6px; font-size: 11px;")
        _url_lbl.setWordWrap(True); _url_lbl.setMinimumHeight(28)
        v1.addWidget(_url_lbl)
        lv.addWidget(g1)

        # 메인 키워드 풀 (댓글마다 랜덤 1개 삽입)
        g_kw = QGroupBox("메인 키워드 풀 — 한 줄에 하나, 댓글마다 랜덤 1개 자동 삽입")
        v_kw = QVBoxLayout(g_kw)
        self.ac_keywords = QTextEdit()
        self.ac_keywords.setMaximumHeight(60)
        self.ac_keywords.setPlaceholderText("입주청소\n이사청소\n청소업체 추천")
        saved_kws = self._cfg('AUTO_COMMENT', 'keywords', '')
        if saved_kws:
            self.ac_keywords.setPlainText(saved_kws.replace('\\n', '\n'))
        v_kw.addWidget(self.ac_keywords)
        kw_help = QLabel(
            "• 댓글 풀 항목에 {키워드} 라고 적으면 그 자리에 키워드 자동 삽입\n"
            "• 마커 없는 댓글엔 키워드 미포함 (자유 톤 유지)\n"
            "• AI 모드도 키워드 풀에서 랜덤 1개를 자연스럽게 포함하도록 요청"
        )
        kw_help.setStyleSheet("color: #8b949e; padding: 8px; font-size: 11px; line-height: 1.6;")
        kw_help.setWordWrap(True)
        kw_help.setMinimumHeight(70)
        v_kw.addWidget(kw_help)
        lv.addWidget(g_kw)

        # 댓글 풀
        g2 = QGroupBox("댓글 풀 — 한 줄에 하나 (비어있고 AI 옵션 켜져있으면 AI 자동 생성)")
        v2 = QVBoxLayout(g2)
        self.ac_pool = QTextEdit()
        self.ac_pool.setMaximumHeight(120)
        self.ac_pool.setPlaceholderText(
            "예시 (마커 사용):\n{키워드} 정보 감사합니다 :)\n저도 {키워드} 알아보던 중인데 도움 됐어요\n좋은 후기 잘 봤어요~"
        )
        saved_pool = self._cfg('AUTO_COMMENT', 'pool', '')
        if saved_pool:
            self.ac_pool.setPlainText(saved_pool.replace('\\n', '\n'))
        v2.addWidget(self.ac_pool)
        self.ac_use_ai = QCheckBox("풀이 비어있으면 AI 자동 생성 사용 (Claude 키 필요)")
        self.ac_use_ai.setChecked(self._cfg('AUTO_COMMENT', 'use_ai', '1') == '1')
        self.ac_use_ai.setStyleSheet("color: #fdcb6e;")
        v2.addWidget(self.ac_use_ai)
        lv.addWidget(g2)

        # 주기·딜레이 설정 — 버튼 1개 → 팝업 다이얼로그 (UI 짤림 회피)
        self._ac_interval_val = int(self._cfg('AUTO_COMMENT', 'interval_min', '5'))
        self._ac_delay_min_val = int(self._cfg('AUTO_COMMENT', 'delay_min', '5'))
        self._ac_delay_max_val = int(self._cfg('AUTO_COMMENT', 'delay_max', '15'))

        self.ac_settings_btn = QPushButton()
        self.ac_settings_btn.setStyleSheet(
            "QPushButton { background: #1b1f24; color: #e6edf3; border: 1px solid #6c5ce7; "
            "border-radius: 6px; padding: 12px 16px; font-size: 13px; text-align: left; }"
            "QPushButton:hover { background: #252b32; }"
        )
        self.ac_settings_btn.setMinimumHeight(50)
        self.ac_settings_btn.clicked.connect(self._ac_open_settings_dialog)
        self._update_ac_settings_btn_text()
        lv.addWidget(self.ac_settings_btn)

        info = QLabel(
            "• 자기 글 / 이미 댓글 단 글은 자동 스킵\n"
            "• 안전 권장: 체크 5분 / 딜레이 5~15분 / 한 시간 5건 이내"
        )
        info.setStyleSheet("color: #8b949e; padding: 6px; background: #161b22; border-radius: 4px;")
        info.setWordWrap(True)
        lv.addWidget(info)

        # 저장 + 시작/중지
        save_row = QHBoxLayout()
        btn_save = QPushButton("💾 설정 저장")
        btn_save.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; padding: 8px 16px; border-radius: 4px; border: none;")
        btn_save.clicked.connect(self._on_ac_save)
        save_row.addWidget(btn_save)
        self.ac_save_status = QLabel("")
        save_row.addWidget(self.ac_save_status, 1)
        lv.addLayout(save_row)

        btn_row = QHBoxLayout()
        self.ac_start_btn = QPushButton("▶ 자동댓글 시작")
        self.ac_start_btn.setStyleSheet("background-color: #00b894; color: white; font-size: 11px; font-weight: bold; padding: 6px; border-radius: 4px; border: none;")
        self.ac_start_btn.setFixedHeight(32)
        self.ac_start_btn.clicked.connect(self._on_ac_start)
        btn_row.addWidget(self.ac_start_btn, 3)
        self.ac_stop_btn = QPushButton("■ 중지")
        self.ac_stop_btn.setEnabled(False)
        self.ac_stop_btn.setStyleSheet("background-color: #636e72; color: white; font-size: 11px; font-weight: bold; padding: 6px; border-radius: 4px; border: none;")
        self.ac_stop_btn.setFixedHeight(32)
        self.ac_stop_btn.clicked.connect(self._on_ac_stop)
        btn_row.addWidget(self.ac_stop_btn, 1)
        lv.addLayout(btn_row)

        self.ac_status = QLabel("대기 중")
        self.ac_status.setStyleSheet("color: #8b949e; padding: 8px; font-size: 12px;")
        lv.addWidget(self.ac_status)
        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("실행 로그"))
        self.ac_log = QTextEdit()
        self.ac_log.setReadOnly(True)
        self.ac_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.ac_log)

        splitter.addWidget(left); splitter.addWidget(right)
        splitter.setSizes([460, 720])  # 다른 탭과 동일 비율

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)

        self._ac_stop_flag = False
        return tab

    # ═══════ 이미지 재가공 탭 ═══════
    def _build_image_reprocess_tab(self):
        """이미지 재가공 — EXIF 제거 + 미세 변형으로 네이버 SEO 중복 검출 회피."""
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        # 입력 파일/폴더
        g_in = QGroupBox("입력 — 여러 이미지 (파일 또는 폴더)")
        v_in = QVBoxLayout(g_in)
        self.imgrp_files = QListWidget()
        self.imgrp_files.setMaximumHeight(180)
        self.imgrp_files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.imgrp_files.setStyleSheet(self._posting_list_style())
        v_in.addWidget(self.imgrp_files)

        in_btn_row = QHBoxLayout()
        btn_add_files = QPushButton("📄 파일 추가")
        btn_add_files.clicked.connect(self._on_imgrp_add_files)
        in_btn_row.addWidget(btn_add_files)
        btn_add_folder = QPushButton("📁 폴더 통째 추가")
        btn_add_folder.clicked.connect(self._on_imgrp_add_folder)
        in_btn_row.addWidget(btn_add_folder)
        btn_clear = QPushButton("🧹 비우기")
        btn_clear.clicked.connect(lambda: self.imgrp_files.clear())
        in_btn_row.addWidget(btn_clear)
        in_btn_row.addStretch()
        v_in.addLayout(in_btn_row)
        lv.addWidget(g_in)

        # 출력 폴더
        g_out = QGroupBox("출력 폴더")
        v_out = QVBoxLayout(g_out)
        out_row = QHBoxLayout()
        self.imgrp_output = QLineEdit(self._cfg('IMGRP', 'output_dir',
                                                os.path.join(BASE_DIR, '사진', '재가공')))
        out_row.addWidget(self.imgrp_output, 1)
        btn_pick_out = QPushButton("폴더…")
        btn_pick_out.clicked.connect(lambda: self._pick_folder(self.imgrp_output))
        out_row.addWidget(btn_pick_out)
        v_out.addLayout(out_row)
        v_out.addWidget(QLabel("• 같은 이름으로 저장 (입력=출력 폴더면 _new 접미사 자동 추가)"))
        lv.addWidget(g_out)

        # 옵션 — 슬라이더로 강도 조정. 슬라이더 변경 시 자동 미리보기 갱신.
        g_opt = QGroupBox("재가공 옵션 — 슬라이더로 강도 조정")
        v_opt = QVBoxLayout(g_opt)

        def _make_slider(lo: int, hi: int, val: int, fmt=lambda v: str(v)):
            """수평 슬라이더 + 값 라벨. (slider, value_label, getter) 반환."""
            from PySide6.QtWidgets import QSlider
            s = QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            s.setValue(val)
            s.setFixedWidth(180); s.setMinimumHeight(24)
            lbl = QLabel(fmt(val))
            lbl.setStyleSheet("color: #f59e0b; font-weight: bold; min-width: 50px;")
            s.valueChanged.connect(lambda v: lbl.setText(fmt(v)))
            # 슬라이더 놓을 때마다 자동 미리보기 시뮬레이션 트리거
            s.sliderReleased.connect(self._on_imgrp_simulate)
            return s, lbl

        def _row(check_text: str, default_check: bool, *widgets):
            row = QHBoxLayout()
            chk = QCheckBox(check_text); chk.setChecked(default_check)
            chk.stateChanged.connect(lambda _: self._on_imgrp_simulate())
            row.addWidget(chk)
            for w in widgets:
                row.addWidget(w)
            row.addStretch()
            v_opt.addLayout(row)
            return chk

        # 색상 미세 조정 — ±1~30%
        self.imgrp_color_slider, _l1 = _make_slider(1, 30, 3, lambda v: f"±{v}%")
        self.imgrp_color = _row("색상", True, QLabel("강도"), self.imgrp_color_slider, _l1, QLabel("(밝기·대비·채도)"))

        # 미세 회전 — slider 0~50, 표시 v/10 도
        self.imgrp_rotate_slider, _l2 = _make_slider(0, 50, 5, lambda v: f"±{v/10:.1f}도")
        self.imgrp_rotate = _row("회전", True, QLabel("강도"), self.imgrp_rotate_slider, _l2)

        # 미세 리사이즈 — 슬라이더 1개로 ±N% (예: 2 → 98~102)
        self.imgrp_resize_slider, _l3 = _make_slider(1, 30, 2, lambda v: f"{100-v}~{100+v}%")
        self.imgrp_resize = _row("리사이즈", True, QLabel("강도"), self.imgrp_resize_slider, _l3)

        # 노이즈 — ±1~30
        self.imgrp_noise_slider, _l4 = _make_slider(1, 30, 3, lambda v: f"±{v}")
        self.imgrp_noise = _row("노이즈", True, QLabel("강도"), self.imgrp_noise_slider, _l4)

        # 샤프닝 — 0~150%
        self.imgrp_sharpen_slider, _l5 = _make_slider(0, 150, 60, lambda v: f"{v}%")
        self.imgrp_sharpen = _row("샤프닝", True, QLabel("강도"), self.imgrp_sharpen_slider, _l5)

        # 테두리 — 0~200px (단일값 max, min은 max의 절반)
        self.imgrp_border_slider, _l6 = _make_slider(0, 200, 0, lambda v: f"{max(0, v//2)}~{v}px")
        self.imgrp_border_style = QComboBox()
        from core.image_reprocess import BORDER_STYLES as _BS
        self.imgrp_border_style.addItems(_BS)
        self.imgrp_border_style.setMinimumHeight(28)
        self.imgrp_border_style.setFixedWidth(180)
        self.imgrp_border_style.currentTextChanged.connect(lambda _: self._on_imgrp_simulate())
        self.imgrp_border = _row("테두리", False, QLabel("최대"), self.imgrp_border_slider, _l6,
                                 QLabel("스타일"), self.imgrp_border_style)

        # 미리보기 버튼 — 다시 갱신 (랜덤이라 결과 매번 다름)
        prev_row = QHBoxLayout()
        btn_preview = QPushButton("🔄 미리보기 다시 (저장 X · 매번 다른 결과)")
        btn_preview.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; padding: 8px 14px; border-radius: 4px; border: none;")
        btn_preview.clicked.connect(self._on_imgrp_simulate)
        prev_row.addWidget(btn_preview)
        prev_row.addStretch()
        v_opt.addLayout(prev_row)

        info = QLabel(
            "• EXIF·메타데이터는 항상 완전 제거\n"
            "• 매 이미지마다 위 범위에서 랜덤 값 적용 → 같은 원본도 매번 다른 결과\n"
            "• 🔍 미리보기 버튼: 현재 옵션·강도로 즉석 처리해 우측에 표시 (저장 안 됨, 매번 결과 다름)\n"
            "• 한 이미지를 N번 돌려쓰려면 강도를 좀 키우세요 (색상 5~10%, 노이즈 5~10)"
        )
        info.setStyleSheet("color: #8b949e; padding: 8px; background: #161b22; border-radius: 4px; font-size: 11px;")
        info.setWordWrap(True); info.setMinimumHeight(90)
        v_opt.addWidget(info)

        lv.addWidget(g_opt)

        # 시작/중지
        btn_row = QHBoxLayout()
        self.imgrp_start_btn = QPushButton("🎨 재가공 시작")
        self.imgrp_start_btn.setStyleSheet("background-color: #f59e0b; color: white; font-size: 13px; font-weight: bold; padding: 10px; border-radius: 6px; border: none;")
        self.imgrp_start_btn.clicked.connect(self._on_imgrp_start)
        btn_row.addWidget(self.imgrp_start_btn, 3)
        self.imgrp_stop_btn = QPushButton("중지")
        self.imgrp_stop_btn.setEnabled(False)
        self.imgrp_stop_btn.setStyleSheet("background-color: #636e72; color: white; padding: 10px; border-radius: 6px; border: none;")
        self.imgrp_stop_btn.clicked.connect(self._on_imgrp_stop)
        btn_row.addWidget(self.imgrp_stop_btn, 1)
        lv.addLayout(btn_row)
        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        # 미리보기 헤더
        self.imgrp_preview_title = QLabel("미리보기 — 좌측 리스트에서 이미지를 클릭하세요")
        self.imgrp_preview_title.setStyleSheet("font-weight: bold; padding: 4px;")
        rv.addWidget(self.imgrp_preview_title)
        # 큰 이미지 미리보기
        self.imgrp_preview = QLabel()
        self.imgrp_preview.setAlignment(Qt.AlignCenter)
        self.imgrp_preview.setStyleSheet("background: #0d1117; border: 1px solid #30363d; border-radius: 4px;")
        self.imgrp_preview.setMinimumSize(600, 500)
        self.imgrp_preview.setScaledContents(False)
        rv.addWidget(self.imgrp_preview, 1)

        # 네비 컨트롤 — 이전/다음 + 원본/결과 토글
        nav_row = QHBoxLayout()
        btn_prev = QPushButton("◀  이전")
        btn_prev.setFixedHeight(34); btn_prev.setStyleSheet("padding: 4px 14px;")
        btn_prev.clicked.connect(self._imgrp_prev)
        nav_row.addWidget(btn_prev)

        btn_next = QPushButton("다음  ▶")
        btn_next.setFixedHeight(34); btn_next.setStyleSheet("padding: 4px 14px;")
        btn_next.clicked.connect(self._imgrp_next)
        nav_row.addWidget(btn_next)

        nav_row.addSpacing(20)
        nav_row.addWidget(QLabel("보기:"))
        self.imgrp_view_orig = QPushButton("원본")
        self.imgrp_view_orig.setCheckable(True)
        self.imgrp_view_orig.setFixedHeight(34); self.imgrp_view_orig.setStyleSheet(
            "QPushButton { padding: 4px 14px; background: #2d3436; color: white; border-radius: 4px; }"
            "QPushButton:checked { background: #6c5ce7; }")
        self.imgrp_view_orig.clicked.connect(lambda: self._imgrp_set_view('orig'))
        nav_row.addWidget(self.imgrp_view_orig)

        self.imgrp_view_done = QPushButton("재가공 미리보기")
        self.imgrp_view_done.setCheckable(True); self.imgrp_view_done.setChecked(True)  # 기본 ON
        self.imgrp_view_done.setFixedHeight(34); self.imgrp_view_done.setStyleSheet(
            "QPushButton { padding: 4px 14px; background: #2d3436; color: white; border-radius: 4px; }"
            "QPushButton:checked { background: #f59e0b; }")
        self.imgrp_view_done.clicked.connect(lambda: self._imgrp_set_view('done'))
        nav_row.addWidget(self.imgrp_view_done)

        nav_row.addStretch()
        rv.addLayout(nav_row)

        # 진행 상태 (작은 영역)
        self.imgrp_status = QLabel("대기 중")
        self.imgrp_status.setStyleSheet("color: #8b949e; padding: 6px; font-size: 11px;")
        rv.addWidget(self.imgrp_status)
        # 짧은 진행 로그 (한 줄짜리 — 선택)
        self.imgrp_log = QTextEdit()
        self.imgrp_log.setReadOnly(True)
        self.imgrp_log.setMaximumHeight(80)
        self.imgrp_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.imgrp_log)

        splitter.addWidget(left); splitter.addWidget(right)
        splitter.setSizes([400, 780])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)

        # 리스트 클릭 시 미리보기 갱신
        self.imgrp_files.itemClicked.connect(self._on_imgrp_preview)
        self.imgrp_files.currentItemChanged.connect(
            lambda cur, prev: self._on_imgrp_preview(cur) if cur else None)

        self._imgrp_stop_flag = False
        self._imgrp_preview_path = None  # 현재 선택 원본 경로
        self._imgrp_view_mode = 'done'   # 'orig' | 'done' (기본: 재가공 미리보기)
        return tab

    def _imgrp_prev(self):
        n = self.imgrp_files.count()
        if n == 0: return
        cur = self.imgrp_files.currentRow()
        new_row = (cur - 1) % n if cur >= 0 else 0
        self.imgrp_files.setCurrentRow(new_row)

    def _imgrp_next(self):
        n = self.imgrp_files.count()
        if n == 0: return
        cur = self.imgrp_files.currentRow()
        new_row = (cur + 1) % n if cur >= 0 else 0
        self.imgrp_files.setCurrentRow(new_row)

    def _imgrp_set_view(self, mode: str):
        """원본/결과 토글. 현재 선택된 이미지의 해당 버전 표시."""
        self._imgrp_view_mode = mode
        # 토글 상태 동기화
        if hasattr(self, 'imgrp_view_orig'):
            self.imgrp_view_orig.setChecked(mode == 'orig')
            self.imgrp_view_done.setChecked(mode == 'done')
        # 현재 선택 다시 그리기
        item = self.imgrp_files.currentItem()
        if item:
            self._on_imgrp_preview(item)

    def _imgrp_resolve_done_path(self, src_path: str) -> str:
        """원본 경로 → 출력 폴더 안 결과 파일 경로 매핑."""
        out_dir = self.imgrp_output.text().strip() if hasattr(self, 'imgrp_output') else ''
        if not out_dir or not os.path.isdir(out_dir):
            return ''
        name = os.path.basename(src_path)
        base, ext = os.path.splitext(name)
        # 입력=출력 폴더면 _new 접미사 사용
        if os.path.abspath(os.path.dirname(src_path)) == os.path.abspath(out_dir):
            cand_name = f'{base}_new{ext}'
        else:
            cand_name = name
        cand = os.path.join(out_dir, cand_name)
        if os.path.isfile(cand):
            return cand
        return ''

    def _on_imgrp_preview(self, item):
        """리스트에서 선택 시 우측 미리보기. 모드별:
        - 'orig': 원본 그대로
        - 'done': 실제 처리 결과 파일이 있으면 그것, 없으면 즉석 시뮬레이션
        """
        if item is None:
            return
        src_path = item.text() if hasattr(item, 'text') else str(item)
        self._imgrp_preview_path = src_path
        if not os.path.isfile(src_path):
            self.imgrp_preview_title.setText(f"파일 없음: {os.path.basename(src_path)}")
            self.imgrp_preview.clear()
            return
        mode = getattr(self, '_imgrp_view_mode', 'done')  # 기본을 'done'으로 (사용자: 결과 미리보기 우선)
        if mode == 'orig':
            self._imgrp_load_pixmap(src_path, label='원본')
            return
        # 'done' 모드: 실제 결과 파일 → 없으면 시뮬레이션
        done_path = self._imgrp_resolve_done_path(src_path)
        if done_path:
            self._imgrp_load_pixmap(done_path, label='재가공 결과')
        else:
            # 즉석 시뮬레이션 (메모리)
            self._on_imgrp_simulate()

    def _imgrp_load_pixmap(self, path: str, label: str = '', skip_title: bool = False):
        """경로 → QPixmap 로드 → 미리보기 라벨에 표시. 캐시 회피 위해 매번 새로 로드."""
        try:
            from PySide6.QtGui import QPixmap
            from PySide6.QtCore import QFile
            # 캐시 회피: QPixmap 직접 새로 만들고, mtime 기반 무효화
            pix = QPixmap()
            ok = pix.load(path)
            if not ok or pix.isNull():
                self.imgrp_preview.setText("이미지 로드 실패")
                return
            target_w = max(400, self.imgrp_preview.width())
            target_h = max(400, self.imgrp_preview.height())
            scaled = pix.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.imgrp_preview.setPixmap(scaled)
            if not skip_title:
                prefix = f"[{label}] " if label else ""
                self.imgrp_preview_title.setText(
                    f"{prefix}{os.path.basename(path)}  ({pix.width()}×{pix.height()})")
        except Exception as e:
            self.imgrp_preview.setText(f"미리보기 실패: {e}")

    def _on_imgrp_add_files(self):
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(
            self, "이미지 파일 선택", "",
            "이미지 (*.jpg *.jpeg *.png *.webp *.bmp);;모든 파일 (*.*)"
        )
        for f in files:
            self.imgrp_files.addItem(f)
        if files and self.imgrp_files.currentRow() < 0:
            self.imgrp_files.setCurrentRow(0)  # 자동 미리보기 트리거

    def _on_imgrp_add_folder(self):
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "이미지 폴더 선택")
        if not folder:
            return
        from core.image_reprocess import find_images
        added = 0
        for f in find_images(folder):
            self.imgrp_files.addItem(f)
            added += 1
        if added > 0 and self.imgrp_files.currentRow() < 0:
            self.imgrp_files.setCurrentRow(0)  # 자동 미리보기 트리거

    def _on_imgrp_stop(self):
        self._imgrp_stop_flag = True
        self._log_to(self.imgrp_log, "━━━ 중지 요청됨 ━━━", '#fdcb6e')

    def _imgrp_collect_options(self) -> dict:
        """슬라이더 값 → image_reprocess._apply_pipeline 옵션 dict."""
        resize_amp = self.imgrp_resize_slider.value()  # 슬라이더 N → ±N%
        border_max = self.imgrp_border_slider.value()
        return {
            'color': self.imgrp_color.isChecked(),
            'color_pct': self.imgrp_color_slider.value(),
            'rotate': self.imgrp_rotate.isChecked(),
            'rotate_deg': self.imgrp_rotate_slider.value() / 10.0,  # slider 5 → 0.5도
            'resize': self.imgrp_resize.isChecked(),
            'resize_min': max(50, 100 - resize_amp),
            'resize_max': min(150, 100 + resize_amp),
            'noise': self.imgrp_noise.isChecked(),
            'noise_amp': self.imgrp_noise_slider.value(),
            'sharpen': self.imgrp_sharpen.isChecked(),
            'sharpen_pct': self.imgrp_sharpen_slider.value(),
            'border': self.imgrp_border.isChecked() and border_max > 0,
            'border_min': max(0, border_max // 2),
            'border_max': border_max,
            'border_style': self.imgrp_border_style.currentText() if hasattr(self, 'imgrp_border_style') else '흰색',
            'border_color': '#FFFFFF',  # legacy 호환
            'quality': 92,
        }

    def _on_imgrp_simulate(self, _arg=None):
        """현재 선택 이미지를 메모리에서만 처리해 우측 미리보기에 즉시 표시.
        선택 항목 없으면 조용히 무시 (슬라이더 변경·옵션 토글 시 자동 호출됨)."""
        if not hasattr(self, 'imgrp_files'):
            return
        item = self.imgrp_files.currentItem()
        if item is None:
            return
        src = item.text()
        if not os.path.isfile(src):
            return
        opts = self._imgrp_collect_options()
        try:
            from core.image_reprocess import reprocess_in_memory
            data = reprocess_in_memory(src, **opts)
            if not data:
                return
            from PySide6.QtGui import QPixmap
            pix = QPixmap()
            pix.loadFromData(data, 'PNG')
            if pix.isNull():
                return
            target_w = max(400, self.imgrp_preview.width())
            target_h = max(400, self.imgrp_preview.height())
            scaled = pix.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.imgrp_preview.setPixmap(scaled)
            self.imgrp_preview_title.setText(
                f"🔍 미리보기 (저장 X): {os.path.basename(src)}  ({pix.width()}×{pix.height()})  — 슬라이더 조정 시 자동 갱신")
        except Exception as e:
            self._log_to(self.imgrp_log, f"[!] 미리보기 오류: {e}", '#ff6b6b')

    def _on_imgrp_start(self):
        n = self.imgrp_files.count()
        if n == 0:
            QMessageBox.warning(self, "경고", "이미지를 추가하세요"); return
        out_dir = self.imgrp_output.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "경고", "출력 폴더를 입력하세요"); return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            QMessageBox.warning(self, "경고", f"출력 폴더 생성 실패: {e}"); return

        # config 저장
        try:
            self._set_cfg('IMGRP', 'output_dir', out_dir)
            self._save_config_file()
        except Exception:
            pass

        src_paths = [self.imgrp_files.item(i).text() for i in range(n)]
        options = self._imgrp_collect_options()

        self._imgrp_stop_flag = False
        self.imgrp_start_btn.setEnabled(False)
        self.imgrp_stop_btn.setEnabled(True)
        self.imgrp_log.clear()
        opt_on = [k for k, v in options.items() if v is True and k not in ('border_color', 'quality')]
        self._log_to(self.imgrp_log,
            f"[시작] {n}장 / 옵션: {', '.join(opt_on) or '없음'}",
            '#74b9ff')
        self._bridge.set_text.emit(self.imgrp_status, f"처리 중 0/{n}")

        def log(msg, color='#c9d1d9'):
            self._log_to(self.imgrp_log, msg, color)

        def stopped():
            return self._imgrp_stop_flag

        # 한 장 처리할 때마다 미리보기 갱신을 위해 reprocess_one을 직접 루프
        def do():
            try:
                from core.image_reprocess import reprocess_one
                from PySide6.QtCore import QTimer as _QT
                ok_count = 0
                for i, src in enumerate(src_paths, 1):
                    if stopped():
                        log(f"[!] 중지 — {ok_count}장 완료", '#fdcb6e')
                        break
                    name = os.path.basename(src)
                    base, ext = os.path.splitext(name)
                    if os.path.abspath(os.path.dirname(src)) == os.path.abspath(out_dir):
                        dst_name = f'{base}_new{ext}'
                    else:
                        dst_name = name
                    dst = os.path.join(out_dir, dst_name)
                    log(f"[{i}/{n}] {name} → {dst_name}")
                    self._bridge.set_text.emit(self.imgrp_status, f"처리 중 {i}/{n}: {name}")
                    success = reprocess_one(
                        src, dst,
                        color=options['color'], rotate=options['rotate'],
                        resize=options['resize'], noise=options['noise'],
                        sharpen=options['sharpen'], border=options['border'],
                        border_color=options['border_color'], quality=options['quality'],
                    )
                    if success:
                        ok_count += 1
                        # 처리 후 — 결과를 미리보기에 띄움 (재가공 결과 모드로 전환 + 명시적 로드)
                        def _show(p=dst, src_p=src, idx=i-1):
                            try:
                                # 리스트의 해당 인덱스로 이동 + 결과 모드로 토글
                                self.imgrp_files.setCurrentRow(idx)
                                self._imgrp_view_mode = 'done'
                                if hasattr(self, 'imgrp_view_orig'):
                                    self.imgrp_view_orig.setChecked(False)
                                    self.imgrp_view_done.setChecked(True)
                                self._imgrp_load_pixmap(p, label='재가공 결과')
                            except Exception:
                                pass
                        _QT.singleShot(0, _show)
                        # 결과 보이는 시간 확보 (옵션마다 처리 빠르면 스쳐 지나감 방지)
                        import time as _t2
                        _t2.sleep(0.3)
                    else:
                        log(f"  [실패] {name}", '#ff6b6b')
                log(f"\n━━━ 완료! {ok_count}/{n} 장 성공 ━━━", '#00b894')
                self._bridge.set_text.emit(self.imgrp_status, f"완료 ({ok_count}/{n})")
            except Exception as e:
                import traceback
                log(f"[치명적 오류] {e}", '#ff6b6b')
                log(traceback.format_exc()[:400], '#ff6b6b')
            finally:
                self._bridge.set_enabled.emit(self.imgrp_start_btn, True)
                self._bridge.set_enabled.emit(self.imgrp_stop_btn, False)

        threading.Thread(target=do, daemon=True).start()

    def _imgrp_show_preview(self, path: str, label: str = ''):
        """주어진 경로 이미지를 미리보기에 표시. 메인 스레드에서 호출 보장 필요."""
        if not path or not os.path.isfile(path):
            return
        try:
            from PySide6.QtGui import QPixmap
            pix = QPixmap(path)
            if pix.isNull():
                return
            target_w = max(400, self.imgrp_preview.width())
            target_h = max(400, self.imgrp_preview.height())
            scaled = pix.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.imgrp_preview.setPixmap(scaled)
            prefix = f"[{label}] " if label else ""
            self.imgrp_preview_title.setText(
                f"{prefix}{os.path.basename(path)}  ({pix.width()}×{pix.height()})")
        except Exception:
            pass

    def _update_ac_settings_btn_text(self):
        """주기·딜레이 설정 버튼 텍스트를 현재 값으로 갱신."""
        if not hasattr(self, 'ac_settings_btn'):
            return
        self.ac_settings_btn.setText(
            f"⚙ 주기·딜레이 설정 (클릭하여 변경)\n"
            f"   • 체크 주기: {self._ac_interval_val} 분\n"
            f"   • 댓글 사이: {self._ac_delay_min_val} ~ {self._ac_delay_max_val} 분"
        )

    def _ac_open_settings_dialog(self):
        """팝업 다이얼로그로 주기·딜레이 설정 (메인 UI 짤림 회피)."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("자동댓글 — 주기·딜레이 설정")
        dlg.setMinimumWidth(380)
        lay = QVBoxLayout(dlg)
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignRight); form.setSpacing(12)

        sp_interval = QSpinBox(); sp_interval.setRange(1, 120); sp_interval.setSuffix(" 분")
        sp_interval.setValue(self._ac_interval_val); sp_interval.setMinimumHeight(36)
        form.addRow("체크 주기:", sp_interval)

        sp_dmin = QSpinBox(); sp_dmin.setRange(0, 60); sp_dmin.setSuffix(" 분")
        sp_dmin.setValue(self._ac_delay_min_val); sp_dmin.setMinimumHeight(36)
        form.addRow("댓글 사이 최소:", sp_dmin)

        sp_dmax = QSpinBox(); sp_dmax.setRange(0, 60); sp_dmax.setSuffix(" 분")
        sp_dmax.setValue(self._ac_delay_max_val); sp_dmax.setMinimumHeight(36)
        form.addRow("댓글 사이 최대:", sp_dmax)

        lay.addLayout(form)

        info = QLabel(
            "<b>안전 권장 값</b><br>"
            "• 체크 주기: 5분<br>"
            "• 댓글 사이: 5 ~ 15분 (랜덤)<br>"
            "• 한 시간 5건 이내 — 스팸 필터 회피"
        )
        info.setStyleSheet("padding: 8px; background: #1b1f24; border-radius: 4px; color: #8b949e;")
        info.setWordWrap(True)
        lay.addWidget(info)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        lay.addWidget(bbox)

        if dlg.exec() == QDialog.Accepted:
            self._ac_interval_val = sp_interval.value()
            self._ac_delay_min_val = sp_dmin.value()
            self._ac_delay_max_val = max(self._ac_delay_min_val, sp_dmax.value())
            self._update_ac_settings_btn_text()

    def _on_ac_save(self):
        try:
            self._set_cfg('AUTO_COMMENT', 'account_list', self.ac_accounts.toPlainText().strip().replace('\n', '\\n'))
            self._set_cfg('AUTO_COMMENT', 'urls', self.ac_urls.toPlainText().strip().replace('\n', '\\n'))
            self._set_cfg('AUTO_COMMENT', 'keywords', self.ac_keywords.toPlainText().strip().replace('\n', '\\n'))
            self._set_cfg('AUTO_COMMENT', 'pool', self.ac_pool.toPlainText().strip().replace('\n', '\\n'))
            self._set_cfg('AUTO_COMMENT', 'use_ai', '1' if self.ac_use_ai.isChecked() else '0')
            self._set_cfg('AUTO_COMMENT', 'interval_min', str(self._ac_interval_val))
            self._set_cfg('AUTO_COMMENT', 'delay_min', str(self._ac_delay_min_val))
            self._set_cfg('AUTO_COMMENT', 'delay_max', str(self._ac_delay_max_val))
            self._save_config_file()
            self.ac_save_status.setText("저장됨")
            self.ac_save_status.setStyleSheet("color: #00b894;")
            QTimer.singleShot(2000, lambda: self.ac_save_status.setText(""))
        except Exception as e:
            self.ac_save_status.setText(f"저장 실패: {e}")
            self.ac_save_status.setStyleSheet("color: #ff6b6b;")

    def _on_ac_stop(self):
        self._ac_stop_flag = True
        self._log_to(self.ac_log, "━━━ 중지 요청됨 ━━━", '#fdcb6e')
        self._bridge.set_text.emit(self.ac_status, "중지 중...")

    def _on_ac_start(self):
        urls_raw = self.ac_urls.toPlainText().strip()
        urls = [u.strip() for u in urls_raw.split('\n') if u.strip()]
        if not urls:
            QMessageBox.warning(self, "경고", "모니터링 게시판 URL을 입력하세요"); return

        accounts = self._parse_account_list(self.ac_accounts.toPlainText()) if hasattr(self, 'ac_accounts') else []
        if not accounts:
            QMessageBox.warning(self, "경고", "카페 자동댓글 탭의 '자동댓글 전용 계정'에 아이디 | 비밀번호 를 입력하세요"); return

        pool_raw = self.ac_pool.toPlainText().strip()
        pool = [s.strip() for s in pool_raw.split('\n') if s.strip()]
        kws_raw = self.ac_keywords.toPlainText().strip()
        keywords = [k.strip() for k in kws_raw.split('\n') if k.strip()]
        use_ai = self.ac_use_ai.isChecked()
        if not pool and not use_ai:
            QMessageBox.warning(self, "경고", "댓글 풀이 비어있고 AI 옵션도 꺼져있어요. 풀 입력 또는 AI 옵션 체크."); return

        claude_key = self._cfg('GENERATOR', 'claude_api_key', '') if use_ai else ''
        if use_ai and not claude_key:
            QMessageBox.warning(self, "경고", "AI 옵션이 켜져있는데 Claude API 키가 없습니다. 카페 원고생성기 탭에서 키 설정.")
            return

        interval_min = self._ac_interval_val
        dmin = self._ac_delay_min_val
        dmax = self._ac_delay_max_val
        interval_sec = interval_min * 60
        delay_min_sec = dmin * 60
        delay_max_sec = max(delay_min_sec, dmax * 60)

        # 저장 후 시작
        self._on_ac_save()

        self._ac_stop_flag = False
        self.ac_start_btn.setEnabled(False)
        self.ac_stop_btn.setEnabled(True)
        self.ac_log.clear()
        self._bridge.set_text.emit(self.ac_status, "실행 중...")

        self._log_to(self.ac_log, f"[시작] 모니터링 게시판 {len(urls)}개, 체크 주기 {interval_min}분", '#74b9ff')
        self._log_to(self.ac_log, f"[시작] 댓글 풀 {len(pool)}개, 키워드 풀 {len(keywords)}개, AI fallback={'ON' if use_ai else 'OFF'}", '#74b9ff')
        self._log_to(self.ac_log, f"[시작] 로그인 계정: {user}", '#74b9ff')

        user, pw = accounts[0]
        my_user_id = user

        def stopped():
            return self._ac_stop_flag

        def log(msg, color='#00cec9'):
            self._log_to(self.ac_log, msg, color)

        def do():
            import random as _rnd
            import time as _time
            from core.auto_commenter import (
                parse_board_url, fetch_recent_articles, post_comment,
                pick_comment, generate_ai_comment, _load_state, _save_state,
                _board_key, update_processed, is_processed,
            )
            from core.browser import NaverBrowser

            browser = NaverBrowser()
            page = None
            try:
                log(f"[로그인] {user} 로그인 중...", '#fdcb6e')
                ok = browser.login_manual(username=user, password=pw, callback=log)
                if not ok:
                    log(f"[실패] 로그인 실패 — 종료", '#ff6b6b')
                    return
                page = browser.start_headless()
                log(f"[로그인] 완료", '#00b894')

                state = _load_state()

                while not stopped():
                    log(f"\n────── 라운드 시작 ──────", '#6c5ce7')
                    new_count = 0
                    posted_count = 0

                    for url in urls:
                        if stopped(): break
                        info = parse_board_url(url)
                        if not info:
                            log(f"[스킵] URL 형식 오류: {url}", '#fdcb6e')
                            continue
                        cid, mid = info['cafe_id'], info['menu_id']
                        bkey = _board_key(cid, mid)

                        log(f"[체크] cafe={cid} menu={mid}")
                        articles = fetch_recent_articles(page, cid, mid, log_callback=log)
                        if not articles:
                            log(f"  글 0건 (또는 페이지 로드 실패)", '#fdcb6e')
                            continue

                        # 첫 라운드: 현재 글 baseline으로 등록 → 이후 새 글에만 댓글
                        # 이렇게 안 하면 시작 즉시 기존 30개 글에 다 댓글 달려서 스팸 위험
                        if not state.get(bkey):
                            state[bkey] = {'processed_ids': [a['aid'] for a in articles]}
                            _save_state(state)
                            log(f"  [baseline] 현재 {len(articles)}글 무시 등록 — 이후 신규 글에만 댓글", '#74b9ff')
                            continue

                        # 새 글만 추출 (미처리)
                        fresh = [a for a in articles if not is_processed(state, bkey, a['aid'])]
                        log(f"  최근 {len(articles)}건, 신규 {len(fresh)}건")

                        for a in fresh:
                            if stopped(): break
                            aid = a['aid']
                            title = a.get('title', '')
                            writer = a.get('writer', '')

                            # 자기 글 스킵
                            if my_user_id and writer and my_user_id.lower() in writer.lower():
                                log(f"  [스킵-내글] {aid} '{title[:25]}' (작성자: {writer})", '#fdcb6e')
                                update_processed(state, bkey, aid)
                                continue

                            # 댓글 생성 — 키워드 풀에서 랜덤 1개 삽입
                            def ai_cb(t, b, kw=''):
                                return generate_ai_comment(t, b, claude_key=claude_key, keyword=kw)
                            comment = pick_comment(pool, keywords=keywords,
                                                   post_title=title, post_body='',
                                                   use_ai_fallback=use_ai, ai_callback=ai_cb)
                            if not comment:
                                log(f"  [스킵-댓글없음] {aid}", '#fdcb6e')
                                update_processed(state, bkey, aid)
                                continue

                            # 댓글 작성
                            log(f"  [작성중] {aid} '{title[:25]}'")
                            ok2 = post_comment(page, cid, mid, aid, comment,
                                               log_callback=log, stop_check=stopped)
                            update_processed(state, bkey, aid)
                            _save_state(state)
                            if ok2:
                                posted_count += 1
                            new_count += 1

                            # 댓글 사이 랜덤 딜레이 — 1분마다 카운트다운 로그
                            if not stopped():
                                wait = _rnd.randint(delay_min_sec, delay_max_sec) if delay_max_sec > 0 else 0
                                if wait > 0:
                                    log(f"  [대기] 다음 댓글까지 {wait//60}분 {wait%60}초")
                                    remaining = wait
                                    while remaining > 0 and not stopped():
                                        sleep_t = min(30, remaining)
                                        _time.sleep(sleep_t)
                                        remaining -= sleep_t
                                        if remaining > 0 and not stopped():
                                            log(f"    [대기중] 다음 댓글까지 {remaining//60}분 {remaining%60}초 남음", '#74b9ff')

                    log(f"\n[라운드 완료] 발견 {new_count}건 / 작성 {posted_count}건", '#00b894')
                    if stopped(): break

                    # 다음 라운드까지 대기 — 1분마다 카운트다운 로그
                    log(f"[대기] 다음 체크까지 {interval_sec//60}분 {interval_sec%60}초", '#74b9ff')
                    remaining_round = interval_sec
                    while remaining_round > 0 and not stopped():
                        sleep_t = min(30, remaining_round)
                        _time.sleep(sleep_t)
                        remaining_round -= sleep_t
                        if remaining_round > 0 and not stopped():
                            log(f"  [대기중] 다음 체크까지 {remaining_round//60}분 {remaining_round%60}초 남음", '#74b9ff')
            except Exception as e:
                import traceback
                log(f"[치명적 오류] {e}", '#ff6b6b')
                log(traceback.format_exc()[:400], '#ff6b6b')
            finally:
                try:
                    if page is not None:
                        browser.close()
                except Exception:
                    pass
                self._bridge.set_enabled.emit(self.ac_start_btn, True)
                self._bridge.set_enabled.emit(self.ac_stop_btn, False)
                self._bridge.set_text.emit(self.ac_status, "중지됨" if self._ac_stop_flag else "완료")
                log(f"━━━ 자동댓글 종료 ━━━", '#fdcb6e')

        threading.Thread(target=do, daemon=True).start()

    # ═══════ 블로그 원고생성기 탭 ═══════
    def _build_blog_gen_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lv = QVBoxLayout(inner)

        # API
        g0 = QGroupBox("API 설정")
        f0 = QFormLayout(g0); f0.setLabelAlignment(Qt.AlignRight)

        self.blog_template = QComboBox()
        self.blog_template.addItems(TEMPLATE_LIST)
        _tpl = self._cfg('GENERATOR', 'template', '청소')
        _ti = self.blog_template.findText(_tpl)
        if _ti >= 0:
            self.blog_template.setCurrentIndex(_ti)
        self.blog_template.currentTextChanged.connect(self._on_template_changed)
        f0.addRow("템플릿", self.blog_template)

        self.blog_key = QLineEdit(self._cfg('GENERATOR', 'claude_api_key'))
        self.blog_key.setPlaceholderText("sk-ant-...")
        f0.addRow("Claude 키", self.blog_key)
        self.blog_model = QComboBox()
        self.blog_model.addItems(['claude-sonnet-4-6', 'claude-sonnet-4-5-20250929', 'claude-haiku-4-5'])
        saved_model = self._cfg('GENERATOR', 'blog_model', 'claude-sonnet-4-6')
        idx = self.blog_model.findText(saved_model)
        if idx >= 0:
            self.blog_model.setCurrentIndex(idx)
        f0.addRow("모델", self.blog_model)

        blog_key_save_row = QHBoxLayout()
        btn_save_blog_key = QPushButton("💾 API 키 저장")
        btn_save_blog_key.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px; border: none;")
        btn_save_blog_key.clicked.connect(self._on_save_blog_api_key)
        blog_key_save_row.addWidget(btn_save_blog_key)
        self.blog_key_status = QLabel("")
        blog_key_save_row.addWidget(self.blog_key_status, 1)
        f0.addRow("", blog_key_save_row)

        lv.addWidget(g0)

        # 기본 정보
        g1 = QGroupBox("01 기본 정보")
        f1 = QFormLayout(g1); f1.setLabelAlignment(Qt.AlignRight)
        self.blog_title = QLineEdit()
        self.blog_title.setPlaceholderText("예: 오산입주청소 꼼꼼함이 다른곳")
        f1.addRow("제목 *", self.blog_title)
        self.blog_brand = QComboBox()
        self.blog_brand.addItems(['새집느낌', '가족사랑클린', '새집환경365'])
        self.blog_brand.currentTextChanged.connect(self._on_brand_changed)
        f1.addRow("업체명", self.blog_brand)
        self.blog_bkw = QLineEdit()
        self.blog_bkw.setPlaceholderText("예: 오산입주청소")
        f1.addRow("대괄호 키워드 *", self.blog_bkw)
        self.blog_pkw = QTextEdit()
        self.blog_pkw.setMaximumHeight(45)
        self.blog_pkw.setPlaceholderText("Common words 붙여넣기 ([5/5],[4/5] 자동 추출)")
        f1.addRow("소괄호 키워드", self.blog_pkw)

        vp_row = QHBoxLayout()
        self.blog_vp_group = QButtonGroup()
        rb1 = QRadioButton("고객 관점"); rb1.setChecked(True)
        rb2 = QRadioButton("업체 관점")
        self.blog_vp_group.addButton(rb1, 0)
        self.blog_vp_group.addButton(rb2, 1)
        vp_row.addWidget(rb1); vp_row.addWidget(rb2)
        f1.addRow("작성 관점", vp_row)

        nb_row = QHBoxLayout()
        self.blog_nb_group = QButtonGroup()
        nb1 = QRadioButton("넘버링 있음"); nb1.setChecked(True)
        nb2 = QRadioButton("넘버링 없음")
        self.blog_nb_group.addButton(nb1, 0)
        self.blog_nb_group.addButton(nb2, 1)
        nb_row.addWidget(nb1); nb_row.addWidget(nb2)
        f1.addRow("사진 넘버링", nb_row)

        self.blog_img_count = QSpinBox()
        self.blog_img_count.setRange(0, 30)
        self.blog_img_count.setValue(18)
        f1.addRow("이미지(문단) 수", self.blog_img_count)
        lv.addWidget(g1)

        # 원고
        g2 = QGroupBox("02 원고")
        v2 = QVBoxLayout(g2)
        self.blog_manuscript = QTextEdit()
        self.blog_manuscript.setPlaceholderText("원고 전문을 여기에 붙여넣으세요...")
        self.blog_manuscript.setMinimumHeight(100)
        v2.addWidget(self.blog_manuscript)
        lv.addWidget(g2)

        # 저장 폴더
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("저장폴더"))
        self.blog_save_dir = QLineEdit(self._cfg('GENERATOR', 'blog_save_dir',
                                       os.path.join(BASE_DIR, '원고')))
        save_row.addWidget(self.blog_save_dir)
        btn_save_dir = QPushButton("폴더")
        btn_save_dir.setFixedWidth(40)
        btn_save_dir.clicked.connect(lambda: self._pick_folder(self.blog_save_dir))
        save_row.addWidget(btn_save_dir)
        btn_persist_save_dir = QPushButton("💾 저장")
        btn_persist_save_dir.setFixedWidth(70)
        btn_persist_save_dir.setStyleSheet("background-color: #6c5ce7; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_persist_save_dir.clicked.connect(self._on_save_blog_save_dir)
        save_row.addWidget(btn_persist_save_dir)
        lv.addLayout(save_row)

        blog_btn_row = QHBoxLayout()
        self.blog_gen_btn = QPushButton("블로그 포스팅 생성하기")
        self.blog_gen_btn.setStyleSheet("background-color: #6c5ce7; color: white; font-size: 12px; font-weight: bold; padding: 8px; border-radius: 6px; border: none;")
        self.blog_gen_btn.clicked.connect(self._on_blog_generate)
        blog_btn_row.addWidget(self.blog_gen_btn)
        btn_prompt = QPushButton("프롬프트 보기/수정")
        btn_prompt.clicked.connect(self._show_blog_prompt)
        blog_btn_row.addWidget(btn_prompt)
        lv.addLayout(blog_btn_row)

        scroll.setWidget(inner)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(scroll)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("블로그 원고 생성 로그"))
        self.blog_log = QTextEdit()
        self.blog_log.setReadOnly(True)
        self.blog_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.blog_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([380, 420])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    # ═══════ 포스팅 분석 탭 ═══════
    def _build_analyzer_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        # ── 좌측: 검색 + 결과 ──
        left = QWidget()
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        inner = QWidget()
        lv = QVBoxLayout(inner)
        lv.setSpacing(6)

        # 검색
        g0 = QGroupBox("1. 키워드 검색")
        f0 = QFormLayout(g0); f0.setLabelAlignment(Qt.AlignRight)
        search_row = QHBoxLayout()
        self.anal_keyword = QLineEdit()
        self.anal_keyword.setPlaceholderText("예: 천안입주청소")
        search_row.addWidget(self.anal_keyword)
        self.anal_count = QSpinBox()
        self.anal_count.setRange(1, 10)
        self.anal_count.setValue(5)
        self.anal_count.setSuffix("개")
        self.anal_count.setFixedWidth(65)
        search_row.addWidget(self.anal_count)
        btn_search = QPushButton("검색")
        btn_search.setFixedWidth(50)
        btn_search.clicked.connect(self._on_search_blogs)
        search_row.addWidget(btn_search)
        f0.addRow("키워드", search_row)

        self.anal_urls = QTextEdit()
        self.anal_urls.setMaximumHeight(70)
        self.anal_urls.setPlaceholderText("검색 시 자동 입력 또는 직접 URL 붙여넣기")
        self.anal_urls.setFont(QFont("Consolas", 9))
        f0.addRow("URL", self.anal_urls)
        lv.addWidget(g0)

        # 분석 + 전송 버튼
        btn_row = QHBoxLayout()
        self.anal_btn = QPushButton("분석")
        self.anal_btn.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; border: none; border-radius: 4px; padding: 4px 8px; max-height: 24px; font-size: 11px;")
        self.anal_btn.clicked.connect(self._on_analyze)
        btn_row.addWidget(self.anal_btn)
        self.anal_send_btn = QPushButton("생성기로 전송 →")
        self.anal_send_btn.setStyleSheet("background-color: #00b894; color: white; font-weight: bold; border: none; border-radius: 4px; padding: 4px 8px; max-height: 24px; font-size: 11px;")
        self.anal_send_btn.clicked.connect(self._send_to_blog_gen)
        self.anal_send_btn.setEnabled(False)
        btn_row.addWidget(self.anal_send_btn)
        lv.addLayout(btn_row)

        # 구분 여백
        spacer = QFrame()
        spacer.setFixedHeight(15)
        lv.addWidget(spacer)

        # 결과 — 제목 + 복사
        g2 = QGroupBox("2. 분석 결과")
        f2 = QFormLayout(g2); f2.setLabelAlignment(Qt.AlignRight)
        title_row = QHBoxLayout()
        self.anal_title = QLineEdit()
        self.anal_title.setReadOnly(True)
        self.anal_title.setPlaceholderText("분석 후 표시")
        title_row.addWidget(self.anal_title)
        btn_cp_title = QPushButton("복사")
        btn_cp_title.setFixedWidth(40)
        btn_cp_title.clicked.connect(lambda: QApplication.clipboard().setText(self.anal_title.text()))
        title_row.addWidget(btn_cp_title)
        f2.addRow("제목", title_row)
        lv.addWidget(g2)

        # 결과 — 형태소 + 복사
        g3 = QGroupBox("3. 형태소 (Common words)")
        v3 = QVBoxLayout(g3)
        morph_btn_row = QHBoxLayout()
        btn_cp_morph = QPushButton("형태소 복사")
        btn_cp_morph.clicked.connect(lambda: QApplication.clipboard().setText(self.anal_morphemes.toPlainText()))
        morph_btn_row.addWidget(btn_cp_morph)
        morph_btn_row.addStretch()
        v3.addLayout(morph_btn_row)
        self.anal_morphemes = QTextEdit()
        self.anal_morphemes.setReadOnly(True)
        self.anal_morphemes.setFont(QFont("Consolas", 9))
        self.anal_morphemes.setPlaceholderText("[5/5] : ...\n[4/5] : ...\n[3/5] : ...")
        v3.addWidget(self.anal_morphemes)
        lv.addWidget(g3)

        # 로그
        g5 = QGroupBox("로그")
        v5 = QVBoxLayout(g5)
        self.anal_log = QTextEdit()
        self.anal_log.setReadOnly(True)
        self.anal_log.setFont(QFont("Consolas", 9))
        self.anal_log.setMaximumHeight(80)
        v5.addWidget(self.anal_log)
        lv.addWidget(g5)

        left_scroll.setWidget(inner)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(left_scroll)

        # ── 우측: 원고 본문 ──
        right = QWidget()
        rv = QVBoxLayout(right)
        g4 = QGroupBox("원고 본문 (1위 글)")
        v4 = QVBoxLayout(g4)
        body_btn_row = QHBoxLayout()
        btn_cp_body = QPushButton("본문 복사")
        btn_cp_body.clicked.connect(lambda: QApplication.clipboard().setText(self.anal_body.toPlainText()))
        body_btn_row.addWidget(btn_cp_body)
        body_btn_row.addStretch()
        v4.addLayout(body_btn_row)
        self.anal_body = QTextEdit()
        self.anal_body.setReadOnly(True)
        self.anal_body.setFont(QFont("Consolas", 9))
        self.anal_body.setPlaceholderText("크롤링된 본문이 여기에 표시됩니다")
        v4.addWidget(self.anal_body)
        rv.addWidget(g4)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 380])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    def _on_search_blogs(self):
        """키워드로 네이버 검색 → URL 자동 채우기"""
        keyword = self.anal_keyword.text().strip()
        if not keyword:
            QMessageBox.warning(self, "경고", "키워드를 입력하세요"); return

        count = self.anal_count.value()
        self._log_to(self.anal_log, f"'{keyword}' 네이버 검색 중...")

        def do():
            try:
                from core.blog_analyzer import search_naver_blogs
                def cb(msg):
                    self._log_to(self.anal_log, msg)
                urls = search_naver_blogs(keyword, count=count, callback=cb)
                if urls:
                    self._bridge.set_plain.emit(self.anal_urls, '\n'.join(urls))
                    self._log_to(self.anal_log, f"{len(urls)}개 URL 자동 입력 완료!", '#00b894')
                else:
                    self._log_to(self.anal_log, "검색 결과가 없습니다", '#ff6b6b')
            except Exception as e:
                self._log_to(self.anal_log, f"검색 오류: {e}", '#ff6b6b')
        threading.Thread(target=do, daemon=True).start()

    def _on_analyze(self):
        urls_text = self.anal_urls.toPlainText().strip()
        urls = [u.strip() for u in urls_text.split('\n') if u.strip()]
        keyword = self.anal_keyword.text().strip()

        if not urls and not keyword:
            QMessageBox.warning(self, "경고", "키워드를 입력하거나 URL을 붙여넣으세요"); return

        # UI 업데이트 (메인 쓰레드)
        self.anal_btn.setEnabled(False)
        self.anal_log.clear()

        if not urls and keyword:
            # 키워드로 자동 검색 → 분석
            self._log_to(self.anal_log, f"'{keyword}' 자동 검색 + 분석 시작...")
            count = self.anal_count.value()

            def do_all():
                try:
                    from core.blog_analyzer import search_naver_blogs
                    def cb(msg):
                        self._log_to(self.anal_log, msg)
                    found_urls = search_naver_blogs(keyword, count=count, callback=cb)
                    if found_urls:
                        self._bridge.set_plain.emit(self.anal_urls, '\n'.join(found_urls))
                        self._run_analysis(found_urls)
                    else:
                        self._log_to(self.anal_log, "검색 결과가 없습니다", '#ff6b6b')
                        self._bridge.set_enabled.emit(self.anal_btn, True)
                except Exception as e:
                    self._log_to(self.anal_log, f"오류: {e}", '#ff6b6b')
                    self._bridge.set_enabled.emit(self.anal_btn, True)
            threading.Thread(target=do_all, daemon=True).start()
        else:
            # URL 직접 분석
            self._log_to(self.anal_log, f"{len(urls)}개 URL 분석 시작...")
            def do():
                self._run_analysis(urls)
            threading.Thread(target=do, daemon=True).start()

    def _run_analysis(self, urls):
        """크롤링 + 형태소 분석 실행 (Signal 기반 쓰레드 안전)"""
        self._bridge.set_text.emit(self.anal_title, '')
        self._bridge.set_plain.emit(self.anal_body, '')
        self._bridge.set_plain.emit(self.anal_morphemes, '')
        self._analyzed_data = None

        try:
            from core.blog_analyzer import crawl_blogs, analyze_morphemes

            def cb(msg):
                self._log_to(self.anal_log, msg)

            self._log_to(self.anal_log, f"━━ 1/3 블로그 크롤링 ({len(urls)}개) ━━", '#fdcb6e')
            posts = crawl_blogs(urls, callback=cb)
            if not posts:
                self._log_to(self.anal_log, "크롤링 실패", '#ff6b6b')
                self._bridge.set_enabled.emit(self.anal_btn, True)
                return

            self._log_to(self.anal_log, f"크롤링 완료: {len(posts)}개", '#00b894')

            self._log_to(self.anal_log, "━━ 2/3 본문 추출 ━━", '#fdcb6e')
            first = posts[0]
            title_text = first['title']
            body_text = first['body']
            self._log_to(self.anal_log, f"1위: {title_text[:40]}... ({len(body_text)}자)")
            self._bridge.set_text.emit(self.anal_title, title_text)
            self._bridge.set_plain.emit(self.anal_body, body_text)

            self._log_to(self.anal_log, "━━ 3/3 형태소 분석 ━━", '#fdcb6e')
            result = analyze_morphemes(posts, callback=cb)
            formatted = result['formatted']
            self._bridge.set_plain.emit(self.anal_morphemes, formatted)

            self._analyzed_data = {
                'title': title_text,
                'body': body_text,
                'morphemes': formatted,
                'posts': posts,
            }
            self._bridge.set_enabled.emit(self.anal_send_btn, True)
            self._log_to(self.anal_log, "━━ 완료! '생성기로 전송' 을 누르세요 ━━", '#00b894')

        except Exception as e:
            import traceback
            self._log_to(self.anal_log, f"오류: {e}", '#ff6b6b')
            self._log_to(self.anal_log, traceback.format_exc()[:300], '#ff6b6b')
        self._bridge.set_enabled.emit(self.anal_btn, True)

    def _send_to_blog_gen(self):
        """분석 결과를 블로그 원고생성기 탭으로 자동 입력"""
        if not self._analyzed_data:
            return

        data = self._analyzed_data
        title = data['title']
        words = title.split()
        main_kw = words[0] if words else ''

        # 자동 매핑:
        # 원고 → 원고
        self.blog_manuscript.setPlainText(data['body'])
        # 형태소 → 소괄호 키워드 ([5/5], [4/5]만 전송)
        morphemes = data['morphemes']
        filtered_lines = []
        for line in morphemes.split('\n'):
            line = line.strip()
            if line.startswith('[5/') or line.startswith('[4/') or line.startswith('■'):
                filtered_lines.append(line)
        self.blog_pkw.setPlainText('\n'.join(filtered_lines))
        # 제목 → 제목
        self.blog_title.setText(title)
        # 제목 첫 단어 → 대괄호 키워드
        self.blog_bkw.setText(main_kw)

        # 블로그 원고생성기 탭으로 이동
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "블로그 원고생성기":
                self.tabs.setCurrentIndex(i)
                break

        QMessageBox.information(self, "전송 완료",
                               f"블로그 원고생성기에 자동 입력 완료!\n\n"
                               f"제목 → 제목: {title[:30]}...\n"
                               f"제목 첫단어 → 대괄호 키워드: {main_kw}\n"
                               f"원고 → 원고\n"
                               f"형태소 → 소괄호 키워드")

    # ═══════ 동영상 생성 탭 ═══════
    def _build_video_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        g1 = QGroupBox("동영상 생성")
        f1 = QFormLayout(g1); f1.setLabelAlignment(Qt.AlignRight)

        # 키워드
        self.vid_keyword = QLineEdit()
        self.vid_keyword.setPlaceholderText("예: 천안입주청소 → 썸네일+사진 자동 탐색")
        f1.addRow("키워드", self.vid_keyword)

        # 사진 수
        self.vid_photo_count = QSpinBox()
        self.vid_photo_count.setRange(1, 20)
        self.vid_photo_count.setValue(5)
        f1.addRow("사진 수", self.vid_photo_count)

        # 출력 폴더
        out_row = QHBoxLayout()
        self.vid_output_dir = QLineEdit(self._cfg('VIDEO', 'output_dir',
                                        os.path.join(BASE_DIR, '동영상')))
        out_row.addWidget(self.vid_output_dir)
        btn_out = QPushButton("폴더")
        btn_out.setFixedWidth(40)
        btn_out.clicked.connect(lambda: self._pick_folder(self.vid_output_dir))
        out_row.addWidget(btn_out)
        f1.addRow("출력폴더", out_row)

        lv.addWidget(g1)

        # 수동 사진 폴더 (직접 지정)
        g_manual = QGroupBox("수동 지정 (키워드 자동 탐색 안 될 때)")
        f_m = QFormLayout(g_manual); f_m.setLabelAlignment(Qt.AlignRight)
        manual_row = QHBoxLayout()
        self.vid_folder = QLineEdit()
        self.vid_folder.setPlaceholderText("사진 폴더 직접 지정")
        manual_row.addWidget(self.vid_folder)
        btn_vid = QPushButton("폴더")
        btn_vid.setFixedWidth(40)
        btn_vid.clicked.connect(lambda: self._pick_folder(self.vid_folder))
        manual_row.addWidget(btn_vid)
        f_m.addRow("사진폴더", manual_row)
        lv.addWidget(g_manual)

        # 설정
        g2 = QGroupBox("설정")
        f2 = QFormLayout(g2); f2.setLabelAlignment(Qt.AlignRight)

        self.vid_duration = QSpinBox()
        self.vid_duration.setRange(1, 10)
        self.vid_duration.setValue(3)
        self.vid_duration.setSuffix("초")
        f2.addRow("사진당 시간", self.vid_duration)

        size_row = QHBoxLayout()
        self.vid_width = QSpinBox(); self.vid_width.setRange(640, 3840); self.vid_width.setValue(1920)
        size_row.addWidget(self.vid_width)
        size_row.addWidget(QLabel("x"))
        self.vid_height = QSpinBox(); self.vid_height.setRange(480, 2160); self.vid_height.setValue(1080)
        size_row.addWidget(self.vid_height)
        f2.addRow("해상도", size_row)

        preset_row = QHBoxLayout()
        btn_hd = QPushButton("가로 1920x1080")
        btn_hd.clicked.connect(lambda: (self.vid_width.setValue(1920), self.vid_height.setValue(1080)))
        preset_row.addWidget(btn_hd)
        btn_shorts = QPushButton("세로 1080x1920")
        btn_shorts.clicked.connect(lambda: (self.vid_width.setValue(1080), self.vid_height.setValue(1920)))
        preset_row.addWidget(btn_shorts)
        f2.addRow("프리셋", preset_row)

        lv.addWidget(g2)

        self.vid_gen_btn = QPushButton("동영상 생성")
        self.vid_gen_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 12px; font-weight: bold; padding: 8px; border-radius: 6px; border: none;")
        self.vid_gen_btn.clicked.connect(self._on_video_generate)
        lv.addWidget(self.vid_gen_btn)
        lv.addStretch()

        # 우측 — 로그
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("동영상 생성 로그"))
        self.vid_log = QTextEdit()
        self.vid_log.setReadOnly(True)
        self.vid_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.vid_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([380, 420])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    def _pick_vid_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "저장 위치", "", "MP4 (*.mp4)")
        if path:
            self.vid_output.setText(path)

    def _on_video_generate(self):
        keyword = self.vid_keyword.text().strip()
        manual_folder = self.vid_folder.text().strip()
        output_dir = self.vid_output_dir.text().strip()
        photo_count = self.vid_photo_count.value()
        duration = self.vid_duration.value()
        width = self.vid_width.value()
        height = self.vid_height.value()

        if not output_dir:
            QMessageBox.warning(self, "경고", "출력 폴더를 선택하세요"); return
        if not keyword and not manual_folder:
            QMessageBox.warning(self, "경고", "키워드 또는 사진 폴더를 입력하세요"); return

        # 출력 폴더 저장
        self._set_cfg('VIDEO', 'output_dir', output_dir)
        self._save_config_file()

        self.vid_gen_btn.setEnabled(False)
        self.vid_log.clear()

        def do():
            import shutil, tempfile

            photo_base = os.path.join(BASE_DIR, '사진')
            tmp_dir = tempfile.mkdtemp(prefix='vid_src_')
            collected = []
            exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')

            try:
                if keyword:
                    self._log_to(self.vid_log, f"'{keyword}' 자동 탐색...")

                    # 1. 썸네일 탐색 (키워드 매칭)
                    thumb_dir = os.path.join(photo_base, '썸네일')
                    thumb_done = os.path.join(photo_base, '썸네일(사용완료)')
                    thumb_found = None
                    for search_dir in [thumb_dir, thumb_done]:
                        if not os.path.isdir(search_dir):
                            continue
                        for f in os.listdir(search_dir):
                            if keyword in f and f.lower().endswith(exts):
                                thumb_found = os.path.join(search_dir, f)
                                break
                        if thumb_found:
                            break

                    if thumb_found:
                        ext = os.path.splitext(thumb_found)[1]
                        dst = os.path.join(tmp_dir, f'00_thumb{ext}')
                        shutil.copy2(thumb_found, dst)
                        collected.append(dst)
                        self._log_to(self.vid_log, f"  썸네일: {os.path.basename(thumb_found)}")
                    else:
                        self._log_to(self.vid_log, "  썸네일 못 찾음", '#fdcb6e')

                    # 2. 작업전/후 사진 탐색 (세트에서 N장)
                    photo_files = []
                    for num in range(1, 20):
                        for prefix in ['작업전', '작업후']:
                            d = os.path.join(photo_base, f'{prefix}-{num}')
                            if not os.path.isdir(d):
                                continue
                            for f in sorted(os.listdir(d)):
                                if f.lower().endswith(exts):
                                    photo_files.append(os.path.join(d, f))

                    # N장만 사용
                    for i, src in enumerate(photo_files[:photo_count]):
                        ext = os.path.splitext(src)[1]
                        dst = os.path.join(tmp_dir, f'{i+1:02d}_photo{ext}')
                        shutil.copy2(src, dst)
                        collected.append(dst)

                    self._log_to(self.vid_log, f"  사진: {len(collected)-1}장 수집")

                elif manual_folder and os.path.isdir(manual_folder):
                    # 수동 폴더
                    self._log_to(self.vid_log, f"수동 폴더: {manual_folder}")
                    for f in sorted(os.listdir(manual_folder)):
                        if f.lower().endswith(exts):
                            collected.append(os.path.join(manual_folder, f))

                if not collected:
                    self._log_to(self.vid_log, "사진이 없습니다!", '#ff6b6b')
                    self._bridge.set_enabled.emit(self.vid_gen_btn, True)
                    return

                # 출력 — 패키지 폴더(키워드 폴더) 우선, 없으면 동영상 폴더
                pkg_output = self._cfg('BLOG', 'package_output', '')
                if keyword and pkg_output:
                    keyword_dir = os.path.join(pkg_output, keyword)
                    if os.path.isdir(keyword_dir):
                        output_dir = keyword_dir  # 패키지 폴더에 직접 저장
                os.makedirs(output_dir, exist_ok=True)
                out_name = f'{keyword}.mp4' if keyword else 'video.mp4'
                output_path = os.path.join(output_dir, out_name)

                self._log_to(self.vid_log, f"총 {len(collected)}장 → 동영상 생성 중...")

                from core.video_maker import create_slideshow
                def cb(msg):
                    self._log_to(self.vid_log, msg)
                create_slideshow(
                    tmp_dir if keyword else manual_folder,
                    output_path,
                    duration=duration, width=width, height=height,
                    callback=cb
                )
                self._log_to(self.vid_log, f"완료! 저장: {output_path}", '#00b894')
                self._bridge.show_msgbox.emit("완료", f"동영상 생성 완료!\n{output_path}")

            except Exception as e:
                self._log_to(self.vid_log, f"오류: {e}", '#ff6b6b')
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            self._bridge.set_enabled.emit(self.vid_gen_btn, True)
        threading.Thread(target=do, daemon=True).start()

    # ═══════ 블로그 발행(패키징) 탭 ═══════
    def _build_blog_pack_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        g1 = QGroupBox("발행 자료 패키징 (원클릭)")
        f1 = QFormLayout(g1); f1.setLabelAlignment(Qt.AlignRight)

        # 템플릿 드롭다운 — 썸네일 생성 탭과 양방향 동기화
        self.bpack_template = QComboBox()
        self.bpack_template.addItems(['새집느낌', '가족사랑클린'])
        self.bpack_template.setCurrentText(self._cfg('IMAGE', 'template', '새집느낌'))
        self.bpack_template.setStyleSheet("font-weight: bold;")
        self.bpack_template.currentTextChanged.connect(self._on_img_template_changed)
        f1.addRow("썸네일 템플릿", self.bpack_template)

        self.bpub_keyword = QLineEdit()
        self.bpub_keyword.setPlaceholderText("예: 천안입주청소")
        f1.addRow("키워드", self.bpub_keyword)

        out_row = QHBoxLayout()
        self.bpub_output = QLineEdit(self._cfg('BLOG', 'package_output'))
        self.bpub_output.setPlaceholderText("패키지 저장 폴더")
        out_row.addWidget(self.bpub_output)
        btn_out = QPushButton("폴더")
        btn_out.setFixedWidth(40)
        btn_out.clicked.connect(lambda: self._pick_folder(self.bpub_output))
        out_row.addWidget(btn_out)
        f1.addRow("출력폴더", out_row)

        # 사진 폴더 설정 — 값은 hidden QLineEdit에 보관, 팝업으로 편집 (템플릿별 분리 저장)
        _tpl_b = getattr(self, '_current_img_template', None) or self._cfg('IMAGE', 'template', '새집느낌')
        self.bpub_thumb_dir = QLineEdit(self._load_photo_field('pack_thumb_dir', 'BLOG', _tpl_b))
        self.bpub_before_dirs = []
        self.bpub_after_dirs = []
        for i in range(1, 6):
            val = self._load_photo_field(f'pack_before_dir_{i}', 'BLOG', _tpl_b)
            if not val and i == 1 and _tpl_b == '새집느낌':
                val = self._cfg('BLOG', 'pack_before_dir', '')
            self.bpub_before_dirs.append(QLineEdit(val))
        for i in range(1, 6):
            val = self._load_photo_field(f'pack_after_dir_{i}', 'BLOG', _tpl_b)
            if not val and i == 1 and _tpl_b == '새집느낌':
                val = self._cfg('BLOG', 'pack_after_dir', '')
            self.bpub_after_dirs.append(QLineEdit(val))

        pack_folder_btn_row = QHBoxLayout()
        btn_open_pack_folder_dlg = QPushButton("📁  사진 폴더 설정(청소)")
        btn_open_pack_folder_dlg.setStyleSheet(
            "background-color: #2d3436; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: 1px solid #6c5ce7;")
        btn_open_pack_folder_dlg.clicked.connect(self._open_pack_folder_dialog)
        pack_folder_btn_row.addWidget(btn_open_pack_folder_dlg)
        btn_open_pack_packing_dlg = QPushButton("📦  사진 폴더 설정(포장이사)")
        btn_open_pack_packing_dlg.setStyleSheet(
            "background-color: #2d3436; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: 1px solid #f59e0b;")
        btn_open_pack_packing_dlg.clicked.connect(self._open_packing_folder_dialog)
        pack_folder_btn_row.addWidget(btn_open_pack_packing_dlg)
        self.pack_folder_status = QLabel("")
        pack_folder_btn_row.addWidget(self.pack_folder_status, 1)
        lv.addLayout(pack_folder_btn_row)

        # 동영상 생성 설정
        g_vid = QGroupBox("동영상 생성")
        fv = QFormLayout(g_vid); fv.setLabelAlignment(Qt.AlignRight)
        self.bpub_vid_duration = QSpinBox()
        self.bpub_vid_duration.setRange(1, 10)
        self.bpub_vid_duration.setValue(3)
        self.bpub_vid_duration.setSuffix("초")
        fv.addRow("사진당 시간", self.bpub_vid_duration)

        self.bpub_vid_count = QSpinBox()
        self.bpub_vid_count.setRange(1, 20)
        self.bpub_vid_count.setValue(5)
        fv.addRow("사진 수", self.bpub_vid_count)

        self.bpub_vid_effect = QComboBox()
        self.bpub_vid_effect.addItems(['켄 번스 (줌+페이드)', '페이드+블러', '페이드 전환', '심플 (효과 없음)'])
        fv.addRow("효과", self.bpub_vid_effect)

        preset_row = QHBoxLayout()
        btn_hd = QPushButton("가로 1920x1080")
        btn_hd.clicked.connect(lambda: (self.bpub_vid_w.setValue(1920), self.bpub_vid_h.setValue(1080)))
        preset_row.addWidget(btn_hd)
        btn_shorts = QPushButton("세로 1080x1920")
        btn_shorts.clicked.connect(lambda: (self.bpub_vid_w.setValue(1080), self.bpub_vid_h.setValue(1920)))
        preset_row.addWidget(btn_shorts)
        fv.addRow("프리셋", preset_row)

        size_row = QHBoxLayout()
        self.bpub_vid_w = QSpinBox(); self.bpub_vid_w.setRange(640, 3840); self.bpub_vid_w.setValue(1920)
        size_row.addWidget(self.bpub_vid_w)
        size_row.addWidget(QLabel("x"))
        self.bpub_vid_h = QSpinBox(); self.bpub_vid_h.setRange(480, 2160); self.bpub_vid_h.setValue(1080)
        size_row.addWidget(self.bpub_vid_h)
        fv.addRow("해상도", size_row)

        lv.addWidget(g_vid)

        # 원클릭 패키징 (최하단)
        lv.addWidget(g1)

        # 버튼들
        folder_row = QHBoxLayout()
        btn_add_set = QPushButton("세트 폴더 추가")
        btn_add_set.clicked.connect(self._add_photo_set)
        folder_row.addWidget(btn_add_set)
        btn_open_photo = QPushButton("사진 폴더 열기")
        btn_open_photo.clicked.connect(lambda: os.startfile(os.path.join(BASE_DIR, '사진')))
        folder_row.addWidget(btn_open_photo)
        lv.addLayout(folder_row)

        btn_row = QHBoxLayout()
        self.bpub_pack_btn = QPushButton("원클릭 패키징")
        self.bpub_pack_btn.setStyleSheet("background-color: #6c5ce7; color: white; font-size: 12px; font-weight: bold; padding: 8px; border-radius: 6px; border: none;")
        self.bpub_pack_btn.clicked.connect(self._on_blog_package)
        btn_row.addWidget(self.bpub_pack_btn)
        self.bpub_vid_btn = QPushButton("동영상 생성")
        self.bpub_vid_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 12px; font-weight: bold; padding: 8px; border-radius: 6px; border: none;")
        self.bpub_vid_btn.clicked.connect(self._on_pack_video)
        btn_row.addWidget(self.bpub_vid_btn)
        self.bpub_vid_stop = QPushButton("중지")
        self.bpub_vid_stop.setEnabled(False)
        self.bpub_vid_stop.clicked.connect(self._on_vid_stop)
        btn_row.addWidget(self.bpub_vid_stop)
        lv.addLayout(btn_row)
        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("패키징 로그"))
        self.bpack_log = QTextEdit()
        self.bpack_log.setReadOnly(True)
        self.bpack_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.bpack_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 380])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    # ═══════ 블로그 발행(자동) 탭 ═══════
    def _build_blog_auto_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        # 계정 목록 (항상 최상단)
        g_bacc = QGroupBox("계정 목록")
        v_bacc = QVBoxLayout(g_bacc)
        self.bpub_accounts = QTextEdit()
        self.bpub_accounts.setMaximumHeight(80)
        saved_blog_accounts = self._cfg('BLOG', 'account_list')
        if saved_blog_accounts:
            self.bpub_accounts.setPlainText(saved_blog_accounts.replace('\\n', '\n'))
        self.bpub_accounts.setPlaceholderText("아이디 | 비밀번호 | 블로그ID (한 줄에 하나)")
        v_bacc.addWidget(self.bpub_accounts)
        v_bacc.addWidget(QLabel("한 줄에: 아이디 | 비밀번호 | 블로그ID  (1개면 1개만, 여러 개면 순환)"))
        save_bacc_row = QHBoxLayout()
        btn_save_bacc = QPushButton("저장")
        btn_save_bacc.setFixedWidth(60)
        btn_save_bacc.setStyleSheet("background-color: #6c5ce7; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_save_bacc.clicked.connect(self._on_save_blog_account_list)
        save_bacc_row.addWidget(btn_save_bacc)
        # 발행 설정 (다이얼로그) 버튼
        btn_open_bsettings = QPushButton("⚙ 발행 설정")
        btn_open_bsettings.setFixedWidth(100)
        btn_open_bsettings.setStyleSheet("background-color: #3498db; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_open_bsettings.clicked.connect(self._on_open_blog_settings_dialog)
        save_bacc_row.addWidget(btn_open_bsettings)
        self.bpub_acc_status = QLabel("")
        save_bacc_row.addWidget(self.bpub_acc_status, 1)
        v_bacc.addLayout(save_bacc_row)
        lv.addWidget(g_bacc)

        g2 = QGroupBox("블로그 자동 발행")
        f2 = QFormLayout(g2); f2.setLabelAlignment(Qt.AlignRight)

        self.bpub_delay = QSpinBox()
        self.bpub_delay.setRange(5, 600)
        self.bpub_delay.setValue(int(self._cfg('BLOG', 'delay', '30')))
        self.bpub_delay.setSuffix("초")
        self.bpub_delay.setFixedWidth(100)
        f2.addRow("딜레이", self.bpub_delay)

        # 원고 폴더 선택
        default_blog_dir = os.path.join(BASE_DIR, 'postings_blog')
        bpub_dir_row = QHBoxLayout()
        self.bpub_postings_dir = QLineEdit(self._cfg('POSTING', 'blog_postings_dir', default_blog_dir))
        self.bpub_postings_dir.setPlaceholderText("원고가 있는 폴더 경로")
        bpub_dir_row.addWidget(self.bpub_postings_dir, 1)
        btn_bpub_dir_pick = QPushButton("폴더")
        btn_bpub_dir_pick.setFixedWidth(50)
        btn_bpub_dir_pick.clicked.connect(lambda: (self._pick_folder(self.bpub_postings_dir), self._refresh_blog_postings()))
        bpub_dir_row.addWidget(btn_bpub_dir_pick)
        btn_bpub_dir_save = QPushButton("💾")
        btn_bpub_dir_save.setFixedWidth(40)
        btn_bpub_dir_save.setToolTip("폴더 경로 저장")
        btn_bpub_dir_save.setStyleSheet("background-color: #6c5ce7; color: white; font-size: 11px; padding: 4px; border-radius: 3px; border: none;")
        btn_bpub_dir_save.clicked.connect(self._on_save_blog_postings_dir)
        bpub_dir_row.addWidget(btn_bpub_dir_save)
        f2.addRow("원고폴더", bpub_dir_row)

        self.bpub_postings = QListWidget()
        self.bpub_postings.setMaximumHeight(160)
        self.bpub_postings.setSelectionMode(QAbstractItemView.NoSelection)
        self.bpub_postings.setStyleSheet(self._posting_list_style())
        f2.addRow("원고목록", self.bpub_postings)

        bsel_row = QHBoxLayout()
        btn_bsel_all = QPushButton("전체 선택")
        btn_bsel_all.setFixedWidth(80)
        btn_bsel_all.setStyleSheet("font-size: 11px; padding: 4px;")
        btn_bsel_all.clicked.connect(lambda: self._toggle_postings(self.bpub_postings, True))
        bsel_row.addWidget(btn_bsel_all)
        btn_bsel_none = QPushButton("전체 해제")
        btn_bsel_none.setFixedWidth(80)
        btn_bsel_none.setStyleSheet("font-size: 11px; padding: 4px;")
        btn_bsel_none.clicked.connect(lambda: self._toggle_postings(self.bpub_postings, False))
        bsel_row.addWidget(btn_bsel_none)
        bsel_row.addStretch()
        btn_ref = QPushButton("새로고침")
        btn_ref.setFixedWidth(80)
        btn_ref.setStyleSheet("font-size: 11px; padding: 4px;")
        btn_ref.clicked.connect(self._refresh_blog_postings)
        bsel_row.addWidget(btn_ref)
        f2.addRow("", bsel_row)

        lv.addWidget(g2)

        # 임시저장 모드 (테스트용 기본 ON)
        self.bpub_draft_chk = QCheckBox("임시저장으로 처리 (발행 대신 저장 버튼 클릭 — 테스트용)")
        self.bpub_draft_chk.setChecked(True)
        self.bpub_draft_chk.setStyleSheet("color: #fdcb6e; padding: 4px;")
        lv.addWidget(self.bpub_draft_chk)

        pub_btn_row = QHBoxLayout()
        self.bpub_start_btn = QPushButton("블로그 발행 시작")
        self.bpub_start_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 12px; font-weight: bold; padding: 8px; border-radius: 6px; border: none;")
        self.bpub_start_btn.clicked.connect(self._on_blog_publish)
        pub_btn_row.addWidget(self.bpub_start_btn)
        self.bpub_stop_btn = QPushButton("중지")
        self.bpub_stop_btn.setEnabled(False)
        self.bpub_stop_btn.clicked.connect(self._on_blog_stop)
        pub_btn_row.addWidget(self.bpub_stop_btn)
        lv.addLayout(pub_btn_row)
        lv.addStretch()

        QTimer.singleShot(500, self._refresh_blog_postings)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("발행 로그"))
        self.bpub_log = QTextEdit()
        self.bpub_log.setReadOnly(True)
        self.bpub_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.bpub_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 380])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    def _refresh_blog_postings(self):
        from core.template_parser import list_postings
        default_blog_dir = os.path.join(BASE_DIR, 'postings_blog')
        directory = self.bpub_postings_dir.text().strip() if hasattr(self, 'bpub_postings_dir') else default_blog_dir
        if not directory or not os.path.isdir(directory):
            directory = default_blog_dir
        postings = list_postings(directory)
        self.blog_postings_data = postings
        if hasattr(self, 'bpub_postings'):
            self.bpub_postings.clear()
            for p in postings:
                item = QListWidgetItem(p['name'])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, p)
                self.bpub_postings.addItem(item)

    @staticmethod
    def _parse_blog_account_list(raw):
        """'아이디|비번|블로그ID' 또는 공백 구분 → [(user, pw, blog_id), ...]"""
        import re
        accounts = []
        for line in (raw or '').strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = [p for p in re.split(r'[|\t ]+', line) if p]
            if len(parts) < 2:
                continue
            user = parts[0]
            pw = parts[1]
            blog_id = parts[2] if len(parts) > 2 else user
            if user:
                accounts.append((user, pw, blog_id))
        return accounts

    def _on_save_blog_account_list(self):
        raw = self.bpub_accounts.toPlainText().strip()
        self._set_cfg('BLOG', 'account_list', raw.replace('\n', '\\n'))
        self._save_config_file()
        n = len(self._parse_blog_account_list(raw))
        self.bpub_acc_status.setText(f"저장됨 ({n}개 계정)")
        self.bpub_acc_status.setStyleSheet("color: #00b894;")
        QTimer.singleShot(2500, lambda: self.bpub_acc_status.setText(""))

    def _on_blog_publish(self):
        if not hasattr(self, 'blog_postings_data') or not self.blog_postings_data:
            self._log_to(self.bpub_log, "발행할 원고가 없습니다", '#ff6b6b'); return

        accounts = self._parse_blog_account_list(self.bpub_accounts.toPlainText())
        if not accounts:
            self._log_to(self.bpub_log, "계정 목록을 입력해주세요 (아이디 | 비밀번호 | 블로그ID)", '#ff6b6b'); return

        selected = self._checked_postings(self.bpub_postings)
        if not selected:
            self._log_to(self.bpub_log, "체크된 원고가 없습니다", '#ff6b6b'); return

        self.blog_stop_flag = False
        self._bridge.set_enabled.emit(self.bpub_start_btn, False)
        self._bridge.set_enabled.emit(self.bpub_stop_btn, True)
        delay = self.bpub_delay.value()

        def do():
            import time as _time
            from core.blog_poster import post_to_blog
            from core.browser import NaverBrowser
            if not hasattr(self, 'blog_browser') or self.blog_browser is None:
                self.blog_browser = NaverBrowser()

            page = None
            current_acc_idx = -1
            current_blog_id = ''

            total = len(selected)
            # 원고를 계정별로 균등 분할 (앞쪽 계정이 더 많이 가져갈 수 있음)
            # 예) 계정2개 × 원고4개 → acc1: P1,P2 / acc2: P3,P4
            per_acc = (total + len(accounts) - 1) // len(accounts)  # ceil
            for i, posting in enumerate(selected):
                if self.blog_stop_flag:
                    self._log_to(self.bpub_log, "사용자에 의해 중지됨", '#ff6b6b'); break

                # 순서: 계정1이 자기 몫 전부 발행 → 계정2가 자기 몫 전부 발행
                acc_idx = min(i // per_acc, len(accounts) - 1)
                if acc_idx != current_acc_idx:
                    if page is not None:
                        try: self.blog_browser.close()
                        except Exception: pass
                        page = None
                    user, pw, blog_id = accounts[acc_idx]
                    self._log_to(self.bpub_log, f"[계정 {acc_idx+1}/{len(accounts)}] {user} 로그인 중...", '#fdcb6e')
                    ok = self.blog_browser.login_manual(
                        username=user, password=pw,
                        callback=lambda m: self._log_to(self.bpub_log, m))
                    if not ok:
                        self._log_to(self.bpub_log, f"[실패] {user} 로그인 실패 - 건너뜀", '#ff6b6b')
                        continue
                    page = self.blog_browser.start_headless()
                    current_acc_idx = acc_idx
                    current_blog_id = blog_id

                name = posting['name'][:20]
                self._log_to(self.bpub_log, f"[{i+1}/{total}] {name} 발행 중... / 계정{current_acc_idx+1}({current_blog_id})")

                post_status = {'ok': False}
                def log_cb(msg, _s=post_status):
                    color = '#00b894' if '[완료]' in msg else '#ff6b6b' if '[실패]' in msg else '#00cec9'
                    if '[완료]' in msg:
                        _s['ok'] = True
                    self._log_to(self.bpub_log, msg, color)

                # 패키징 결과 폴더 추정 ({pack_output}/{keyword})
                pack_output = self._cfg('BLOG', 'package_output', '')
                posting_pkg_dir = self._derive_pkg_dir(posting['name'], pack_output)
                if posting_pkg_dir:
                    self._log_to(self.bpub_log, f"[정보] 패키징 폴더 연결: {posting_pkg_dir}")

                is_draft = self.bpub_draft_chk.isChecked()
                if is_draft:
                    self._log_to(self.bpub_log, "[정보] 임시저장 모드 — 저장 버튼만 클릭합니다", '#fdcb6e')

                try:
                    post_to_blog(page, current_blog_id, posting['path'],
                                log_callback=log_cb,
                                stop_check=lambda: self.blog_stop_flag,
                                pkg_dir=posting_pkg_dir,
                                draft=is_draft)
                except Exception as e:
                    self._log_to(self.bpub_log, f"[실패] {name}: {e}", '#ff6b6b')

                # 리스트에 완료/실패 마킹
                QTimer.singleShot(0, lambda p=posting, ok=post_status['ok']:
                                      self._mark_posting_done(self.bpub_postings, p, ok))

                if i < total - 1 and not self.blog_stop_flag:
                    for _ in range(delay):
                        if self.blog_stop_flag: break
                        _time.sleep(1)

            try:
                if page is not None:
                    self.blog_browser.close()
            except Exception:
                pass
            self.blog_browser = None
            self._bridge.set_enabled.emit(self.bpub_start_btn, True)
            self._bridge.set_enabled.emit(self.bpub_stop_btn, False)
            self._log_to(self.bpub_log, "블로그 발행 완료!", '#00b894')
        threading.Thread(target=do, daemon=True).start()

    def _on_blog_stop(self):
        self.blog_stop_flag = True
        self._log_to(self.bpub_log, "[중지] 요청 — 브라우저 즉시 종료", '#ff6b6b')
        # 브라우저 강제 종료 → 진행 중인 Playwright 호출이 전부 예외로 즉시 탈출
        try:
            if hasattr(self, 'blog_browser') and self.blog_browser:
                self.blog_browser.close()
        except Exception:
            pass
        self.blog_browser = None
        self._bridge.set_enabled.emit(self.bpub_start_btn, True)
        self._bridge.set_enabled.emit(self.bpub_stop_btn, False)

    def _on_vid_stop(self):
        self._vid_stop = True
        if hasattr(self, '_vid_process') and self._vid_process:
            try:
                self._vid_process.kill()
            except Exception:
                pass
        self._log_to(self.bpack_log, "동영상 생성 중지됨", '#ff6b6b')
        self._bridge.set_enabled.emit(self.bpub_vid_btn, True)
        self._bridge.set_enabled.emit(self.bpub_vid_stop, False)

    def _on_pack_video(self):
        """패키징 탭에서 동영상 생성 — 키워드 폴더에 저장"""
        keyword = self.bpub_keyword.text().strip()
        output_dir = self.bpub_output.text().strip()
        if not keyword:
            QMessageBox.warning(self, "경고", "키워드를 입력하세요"); return
        if not output_dir:
            QMessageBox.warning(self, "경고", "출력 폴더를 선택하세요"); return

        duration = self.bpub_vid_duration.value()
        vid_count = self.bpub_vid_count.value()
        width = self.bpub_vid_w.value()
        height = self.bpub_vid_h.value()
        effect_map = {'켄 번스 (줌+페이드)': 'kenburns', '페이드+블러': 'fadeblur', '페이드 전환': 'fade', '심플 (효과 없음)': 'simple'}
        effect = effect_map.get(self.bpub_vid_effect.currentText(), 'kenburns')
        thumb_dir_manual = self._resolve_packaging_thumb_dir(self.bpub_thumb_dir.text().strip())
        before_dirs_manual = self._collect_priority_paths(self.bpub_before_dirs)
        after_dirs_manual = self._collect_priority_paths(self.bpub_after_dirs)

        self.bpub_vid_btn.setEnabled(False)
        self.bpub_vid_stop.setEnabled(True)
        self._vid_stop = False
        self._log_to(self.bpack_log, f"'{keyword}' 동영상 생성 시작...")

        def do():
            import shutil, tempfile
            photo_base = os.path.join(BASE_DIR, '사진')
            exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
            tmp_dir = tempfile.mkdtemp(prefix='vid_')
            collected = []

            try:
                # 1. 썸네일 (키워드 매칭) — 템플릿별 완전 분리
                _tpl = self.img_template.currentText() if hasattr(self, 'img_template') else '새집느낌'
                thumb_search = [thumb_dir_manual] if thumb_dir_manual else []
                if _tpl == '가족사랑클린':
                    fam_out = self._cfg('IMAGE_FAMILY', 'output_path', '') \
                              or os.path.join(BASE_DIR, '가족사랑클린_썸네일')
                    for p in [fam_out, f'{fam_out}(사용완료)']:
                        if p and p not in thumb_search:
                            thumb_search.append(p)
                else:
                    thumb_search += [os.path.join(photo_base, '썸네일'), os.path.join(photo_base, '썸네일(사용완료)')]
                for td in thumb_search:
                    if not td or not os.path.isdir(td):
                        continue
                    for f in sorted(os.listdir(td)):
                        if f.lower().endswith(exts) and keyword in os.path.splitext(f)[0]:
                            dst = os.path.join(tmp_dir, f'00_thumb{os.path.splitext(f)[1]}')
                            shutil.copy2(os.path.join(td, f), dst)
                            collected.append(dst)
                            self._log_to(self.bpack_log, f"  썸네일: {f}")
                            break
                    if collected:
                        break

                # 2. 사진 N장 (작업전/후 1~3순위 전부)
                photo_files = []
                for d in before_dirs_manual + after_dirs_manual:
                    if d and os.path.isdir(d):
                        photo_files += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(exts)]

                if not photo_files:
                    for num in range(1, 20):
                        for prefix in ['작업전', '작업후']:
                            d = os.path.join(photo_base, f'{prefix}-{num}')
                            if os.path.isdir(d):
                                photo_files += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(exts)]

                for i, src in enumerate(photo_files[:vid_count]):
                    ext = os.path.splitext(src)[1]
                    dst = os.path.join(tmp_dir, f'{i+1:02d}_photo{ext}')
                    shutil.copy2(src, dst)
                    collected.append(dst)

                self._log_to(self.bpack_log, f"  사진 {len(collected)}장 수집")

                if not collected:
                    self._log_to(self.bpack_log, "사진 없음!", '#ff6b6b')
                    self._bridge.set_enabled.emit(self.bpub_vid_btn, True)
                    return

                # 출력: 키워드 폴더에 저장
                pkg_dir = os.path.join(output_dir, keyword)
                os.makedirs(pkg_dir, exist_ok=True)
                output_path = os.path.join(pkg_dir, f'{keyword}.mp4')

                self._log_to(self.bpack_log, f"  {len(collected)}장 → 동영상 생성 중...")
                from core.video_maker import create_slideshow
                def cb(msg):
                    self._log_to(self.bpack_log, msg)
                create_slideshow(tmp_dir, output_path,
                                duration=duration, width=width, height=height,
                                effect=effect, callback=cb)
                self._log_to(self.bpack_log, f"동영상 완료! → {output_path}", '#00b894')
                self._bridge.show_msgbox.emit("완료", f"동영상 생성 완료!\n{output_path}")
            except Exception as e:
                self._log_to(self.bpack_log, f"오류: {e}", '#ff6b6b')
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            self._bridge.set_enabled.emit(self.bpub_vid_btn, True)
            self._bridge.set_enabled.emit(self.bpub_vid_stop, False)
        threading.Thread(target=do, daemon=True).start()

    def _add_photo_set(self):
        """사진 세트 폴더 추가 (작업전-N, 작업후-N)"""
        photo_base = os.path.join(BASE_DIR, '사진')
        # 현재 최대 번호 찾기
        max_num = 0
        for d in os.listdir(photo_base):
            if d.startswith('작업전-') or d.startswith('작업후-'):
                try:
                    num = int(d.split('-')[1])
                    max_num = max(max_num, num)
                except (ValueError, IndexError):
                    pass
        new_num = max_num + 1
        os.makedirs(os.path.join(photo_base, f'작업전-{new_num}'), exist_ok=True)
        os.makedirs(os.path.join(photo_base, f'작업후-{new_num}'), exist_ok=True)
        QMessageBox.information(self, "폴더 생성",
                               f"작업전-{new_num}, 작업후-{new_num} 폴더 생성 완료!")

    def _on_blog_package(self):
        """키워드 입력 → 원고/썸네일/사진 자동 탐색 → 패키징 → 동영상 생성 (모두 필수)"""
        keyword = self.bpub_keyword.text().strip()
        output_dir = self.bpub_output.text().strip()

        if not keyword:
            QMessageBox.warning(self, "경고", "키워드를 입력하세요"); return
        if not output_dir:
            QMessageBox.warning(self, "경고", "출력 폴더를 선택하세요"); return

        thumb_dir_raw = self.bpub_thumb_dir.text().strip()
        # 템플릿에 따라 썸네일 폴더 라우팅 (가족사랑클린이면 자동 오버라이드)
        thumb_dir_manual = self._resolve_packaging_thumb_dir(thumb_dir_raw)
        before_dirs_manual = self._collect_priority_paths(self.bpub_before_dirs)
        after_dirs_manual = self._collect_priority_paths(self.bpub_after_dirs)
        # 동영상 설정
        vid_duration = self.bpub_vid_duration.value()
        vid_count = self.bpub_vid_count.value()
        vid_w = self.bpub_vid_w.value()
        vid_h = self.bpub_vid_h.value()
        effect_map = {'켄 번스 (줌+페이드)': 'kenburns', '페이드+블러': 'fadeblur',
                      '페이드 전환': 'fade', '심플 (효과 없음)': 'simple'}
        vid_effect = effect_map.get(self.bpub_vid_effect.currentText(), 'kenburns')

        self.bpub_pack_btn.setEnabled(False)
        self._set_cfg('BLOG', 'package_output', output_dir)
        _tpl_now = self.img_template.currentText() if hasattr(self, 'img_template') else '새집느낌'
        _section_now = self._img_section_for(_tpl_now)
        # 템플릿별 섹션에 저장
        self._set_cfg(_section_now, 'pack_thumb_dir', thumb_dir_raw)
        for i, e in enumerate(self.bpub_before_dirs, 1):
            self._set_cfg(_section_now, f'pack_before_dir_{i}', e.text().strip())
        for i, e in enumerate(self.bpub_after_dirs, 1):
            self._set_cfg(_section_now, f'pack_after_dir_{i}', e.text().strip())
        # 새집느낌일 때만 구형 BLOG 섹션도 함께 업데이트 (하위 호환)
        if _tpl_now == '새집느낌':
            self._set_cfg('BLOG', 'pack_thumb_dir', thumb_dir_raw)
            for i, e in enumerate(self.bpub_before_dirs, 1):
                self._set_cfg('BLOG', f'pack_before_dir_{i}', e.text().strip())
            for i, e in enumerate(self.bpub_after_dirs, 1):
                self._set_cfg('BLOG', f'pack_after_dir_{i}', e.text().strip())
        self._log_to(self.bpack_log, f"[썸네일 폴더] 템플릿={_tpl_now} → {thumb_dir_manual or '(기본 폴더)'}", '#74b9ff')
        self._save_config_file()
        self._log_to(self.bpack_log, f"━━━ '{keyword}' 패키징 시작 ━━━", '#fdcb6e')

        def do():
            try:
                pkg_dir = self._run_packaging_core(
                    keyword=keyword, output_dir=output_dir,
                    thumb_dir_manual=thumb_dir_manual,
                    before_dirs_manual=before_dirs_manual,
                    after_dirs_manual=after_dirs_manual,
                    log_widget=self.bpack_log,
                )
                if pkg_dir is None:
                    self._log_to(self.bpack_log, "━━━ 필수 자료 누락으로 중단 ━━━", '#ff6b6b')
                    return

                self._log_to(self.bpack_log, "━━━ 동영상 생성 중 ━━━", '#fdcb6e')
                self._run_video_core(
                    keyword=keyword, pkg_dir=pkg_dir,
                    duration=vid_duration, width=vid_w, height=vid_h,
                    effect=vid_effect, vid_count=vid_count,
                    log_widget=self.bpack_log,
                )
                self._log_to(self.bpack_log, f"━━━ 전체 완료! → {pkg_dir} ━━━", '#00b894')
                self._bridge.show_msgbox.emit("패키징 완료",
                                              f"'{keyword}' 발행 자료 + 동영상 완료!\n폴더: {pkg_dir}")
            except Exception as e:
                import traceback
                self._log_to(self.bpack_log, f"[오류] {e}", '#ff6b6b')
                self._log_to(self.bpack_log, traceback.format_exc()[:400], '#ff6b6b')
            finally:
                self._bridge.set_enabled.emit(self.bpub_pack_btn, True)

        threading.Thread(target=do, daemon=True).start()

    # ═══════ 헬퍼 ═══════
    def _pick_file(self, entry, filter_str):
        path, _ = QFileDialog.getOpenFileName(self, "파일 선택", "", filter_str)
        if path:
            entry.setText(path)

    def _pick_bg_file(self):
        """썸네일 배경 전용 파일 선택"""
        path, _ = QFileDialog.getOpenFileName(self, "배경 파일 선택", "", "이미지 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.img_bg.setText(path)
            self._bg_files = [path]
            self._bg_index = 0
            self._update_bg_nav()
            self._preview_thumbnail()

    def _pick_folder(self, entry):
        path = QFileDialog.getExistingDirectory(self, "폴더 선택")
        if path:
            entry.setText(path)

    def _pick_bg_folder(self):
        """썸네일 배경 전용 폴더 선택"""
        path = QFileDialog.getExistingDirectory(self, "배경 폴더 선택")
        if path:
            self.img_bg.setText(path)
            self._load_bg_folder(path)

    def _load_bg_folder(self, folder):
        exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
        try:
            all_files = os.listdir(folder)
            self._bg_files = sorted([os.path.join(folder, f) for f in all_files
                                      if f.lower().endswith(exts) and '사용완료' not in f
                                      and os.path.isfile(os.path.join(folder, f))])
        except Exception:
            self._bg_files = []
        self._bg_index = 0
        self._update_bg_nav()
        if self._bg_files:
            self._preview_thumbnail()

    def _bg_nav(self, d):
        if not self._bg_files:
            return
        self._bg_index = (self._bg_index + d) % len(self._bg_files)
        self._update_bg_nav()
        self._preview_thumbnail()

    def _update_bg_nav(self):
        if not self._bg_files:
            self.bg_nav_label.setText("사진을 선택하세요")
            return
        total = len(self._bg_files)
        name = os.path.basename(self._bg_files[self._bg_index])
        keywords = [t.strip() for t in self.img_t1.toPlainText().strip().split('\n') if t.strip()]
        kw = keywords[self._bg_index] if self._bg_index < len(keywords) else ''
        txt = f"[{self._bg_index+1}/{total}] {name}"
        if kw:
            txt += f" -> {kw}"
        self.bg_nav_label.setText(txt)

    def _log_to(self, widget, msg, color='#00cec9'):
        """쓰레드 안전 로그 (Signal로 메인 쓰레드에서 실행)"""
        ts = datetime.now().strftime('%H:%M:%S')
        html = f'<span style="color:{color}">[{ts}] {msg}</span>'
        self._bridge.append_log.emit(widget, html)

    # ═══════ 이미지 기능 ═══════
    @staticmethod
    def _img_section_for(template_name):
        """템플릿명 → config 섹션명 (설정 완전 분리)"""
        if template_name == '가족사랑클린':
            return 'IMAGE_FAMILY'
        if template_name == '포장이사':
            return 'IMAGE_PACKING'
        return 'IMAGE_SAEJIP'

    @staticmethod
    def _img_defaults(template_name):
        """템플릿별 기본값"""
        if template_name == '가족사랑클린':
            return {
                'text1': '서초구 입주청소',
                'text2': '',
                'font_name': '맑은 고딕 Bold',
                'font_color': '#22C55E',
                'outline': '보통',
                'font_size1': 120,
                'font_size2': 85,
                'text1_y': 830,
                'text2_y': 960,
                'offset_x': 0,
                'offset_y': 0,
                'bg_height': 650,
            }
        if template_name == '포장이사':
            return {
                'text1': '평택 포장이사',
                'text2': '',
                'font_name': '맑은 고딕 Bold',
                'font_color': '#FFFFFF',  # 트럭 옆 큰 글씨 — 흰색 + 진한 외곽선
                'outline': '매우 두껍게',
                'font_size1': 130,
                'font_size2': 85,
                'text1_y': 150,           # 새집느낌 위쪽 (트럭 상단)
                'text2_y': 960,
                'offset_x': 90,           # 좌우 — 텍스트를 오른쪽으로 90px
                'offset_y': -40,          # 상하 — 텍스트를 위로 40px
                'bg_height': 650,
            }
        # 새집느낌(default)
        return {
            'text1': '입주청소',
            'text2': '1660-0240',
            'font_name': '맑은 고딕 Bold',
            'font_color': '#3AB2EB',
            'outline': '보통',
            'font_size1': 100,
            'font_size2': 85,
            'text1_y': 850,
            'text2_y': 960,
            'offset_x': 0,
            'offset_y': 0,
            'bg_height': 650,
        }

    def _load_img_field(self, key, default='', template_name=None):
        """템플릿 섹션에서 값 읽기. 새집느낌이고 값이 없으면 기존 [IMAGE] 섹션으로 하위호환 폴백."""
        if template_name is None:
            template_name = getattr(self, '_current_img_template', '새집느낌')
        section = self._img_section_for(template_name)
        val = self._cfg(section, key, '')
        if not val and template_name == '새집느낌':
            val = self._cfg('IMAGE', key, '')
        return val if val else default

    def _load_photo_field(self, key, legacy_section, template_name=None):
        """템플릿별 사진 폴더 필드 로드. 새집느낌이고 값이 없으면 legacy 섹션에서 폴백.
        그래도 없으면 청소용 기본 폴더(`사진/썸네일 가공 후`, `사진/작업전-N`, `사진/작업후-N`)로 폴백."""
        if template_name is None:
            template_name = getattr(self, '_current_img_template', '새집느낌')
        section = self._img_section_for(template_name)
        val = self._cfg(section, key, '')
        if not val and template_name == '새집느낌':
            val = self._cfg(legacy_section, key, '')
        # 마지막 폴백 — 청소 사진 기본 경로 (업데이트 시 데이터가 지워져도 유지)
        if not val:
            val = self._default_cleaning_photo_path(key)
        return val

    @staticmethod
    def _default_cleaning_photo_path(key):
        """청소용 사진 폴더 기본 경로 매핑.
        cafe_thumb_dir / pack_thumb_dir → 사진/썸네일 가공 후
        cafe_before_dir_N / pack_before_dir_N → 사진/작업전-N
        cafe_after_dir_N  / pack_after_dir_N  → 사진/작업후-N
        그 외 키는 빈 문자열."""
        photo_base = os.path.join(BASE_DIR, '사진')
        if key in ('cafe_thumb_dir', 'pack_thumb_dir'):
            return os.path.join(photo_base, '썸네일 가공 후')
        # 작업전-N / 작업후-N (N=1~5)
        import re as _re
        m = _re.match(r'(?:cafe|pack)_(before|after)_dir(?:_(\d+))?$', key)
        if m:
            kind, num = m.group(1), m.group(2) or '1'
            label = '작업전' if kind == 'before' else '작업후'
            return os.path.join(photo_base, f'{label}-{num}')
        return ''

    def _save_photos_to_section(self, template_name):
        """현재 UI의 사진폴더 값을 주어진 템플릿 섹션에 저장 (카페+블로그 모두).
        포장이사 이미지 템플릿은 청소 사진 폴더와 무관 → 새집느낌으로 폴백."""
        if template_name == '포장이사':
            template_name = '새집느낌'
        section = self._img_section_for(template_name)
        if hasattr(self, 'cafe_thumb_dir'):
            self._set_cfg(section, 'cafe_thumb_dir', self.cafe_thumb_dir.text().strip())
        if hasattr(self, 'cafe_before_dirs'):
            for i, e in enumerate(self.cafe_before_dirs, 1):
                self._set_cfg(section, f'cafe_before_dir_{i}', e.text().strip())
        if hasattr(self, 'cafe_after_dirs'):
            for i, e in enumerate(self.cafe_after_dirs, 1):
                self._set_cfg(section, f'cafe_after_dir_{i}', e.text().strip())
        if hasattr(self, 'bpub_thumb_dir'):
            self._set_cfg(section, 'pack_thumb_dir', self.bpub_thumb_dir.text().strip())
        if hasattr(self, 'bpub_before_dirs'):
            for i, e in enumerate(self.bpub_before_dirs, 1):
                self._set_cfg(section, f'pack_before_dir_{i}', e.text().strip())
        if hasattr(self, 'bpub_after_dirs'):
            for i, e in enumerate(self.bpub_after_dirs, 1):
                self._set_cfg(section, f'pack_after_dir_{i}', e.text().strip())

    def _load_photos_from_section(self, template_name):
        """주어진 템플릿 섹션 값을 사진폴더 UI에 로드.
        포장이사 이미지 템플릿은 청소 사진 폴더와 무관 → 새집느낌으로 폴백."""
        if template_name == '포장이사':
            template_name = '새집느낌'
        if hasattr(self, 'cafe_thumb_dir'):
            self.cafe_thumb_dir.setText(self._load_photo_field('cafe_thumb_dir', 'GENERATOR', template_name))
        if hasattr(self, 'cafe_before_dirs'):
            for i, e in enumerate(self.cafe_before_dirs, 1):
                e.setText(self._load_photo_field(f'cafe_before_dir_{i}', 'GENERATOR', template_name))
        if hasattr(self, 'cafe_after_dirs'):
            for i, e in enumerate(self.cafe_after_dirs, 1):
                e.setText(self._load_photo_field(f'cafe_after_dir_{i}', 'GENERATOR', template_name))
        if hasattr(self, 'bpub_thumb_dir'):
            self.bpub_thumb_dir.setText(self._load_photo_field('pack_thumb_dir', 'BLOG', template_name))
        if hasattr(self, 'bpub_before_dirs'):
            for i, e in enumerate(self.bpub_before_dirs, 1):
                e.setText(self._load_photo_field(f'pack_before_dir_{i}', 'BLOG', template_name))
        if hasattr(self, 'bpub_after_dirs'):
            for i, e in enumerate(self.bpub_after_dirs, 1):
                e.setText(self._load_photo_field(f'pack_after_dir_{i}', 'BLOG', template_name))

    def _resolve_packaging_thumb_dir(self, fallback_dir=''):
        """패키징용 썸네일 폴더 — 현재 선택된 템플릿에 맞춰 자동 선택.
        가족사랑클린: IMAGE_FAMILY.output_path (기본: BASE_DIR/가족사랑클린_썸네일)
        포장이사    : IMAGE_PACKING.output_path (기본: BASE_DIR/포장이사_썸네일)
        새집느낌   : fallback_dir (기존 cafe_thumb_dir / bpub_thumb_dir)
        """
        template = self.img_template.currentText() if hasattr(self, 'img_template') else '새집느낌'
        if template == '가족사랑클린':
            path = self._cfg('IMAGE_FAMILY', 'output_path', '')
            if not path:
                path = os.path.join(BASE_DIR, '가족사랑클린_썸네일')
            return path
        if template == '포장이사':
            # 포장이사: 출력 + 베이스 PNG 통합 폴더 (사진/포장이사 썸네일 가공 후/)
            return os.path.join(BASE_DIR, '사진', '포장이사 썸네일 가공 후')
        return fallback_dir

    @staticmethod
    def _packing_thumb_dirs():
        """포장이사 폴더 4종.
        pre      : 사진/포장이사 썸네일 가공 전/         (가공 안 된 원본 베이스)
        pre_done : 사진/포장이사 썸네일 가공 전(사용완료)/ (썸네일 만든 베이스 — 종착지)
        post     : 사진/포장이사 썸네일 가공 후/         (생성된 썸네일 — 발행 대기)
        done     : 사진/포장이사 썸네일 가공 후(사용완료)/ (발행 끝난 썸네일)
        """
        base = os.path.join(BASE_DIR, '사진')
        return {
            'pre':      os.path.join(base, '포장이사 썸네일 가공 전'),
            'pre_done': os.path.join(base, '포장이사 썸네일 가공 전(사용완료)'),
            'post':     os.path.join(base, '포장이사 썸네일 가공 후'),
            'done':     os.path.join(base, '포장이사 썸네일 가공 후(사용완료)'),
        }

    def _packing_consume_base_for_keyword(self, keyword, log_fn=None):
        """[더 이상 사용 안 함 — no-op]
        이전 설계: 발행 시 썸네일 파일명에서 base stem 추출 → 베이스 PNG 추적 이동.
        새 설계: 썸네일 생성 시점에 베이스 PNG → '가공 전(사용완료)/' 직행.
                 발행 시점엔 썸네일만 (사용완료) 로 이동되면 충분.
        호환을 위해 stub 만 남김."""
        return False

    def _save_image_to_section(self, template_name):
        """현재 UI 값을 주어진 템플릿의 섹션에 저장"""
        section = self._img_section_for(template_name)
        self._set_cfg(section, 'bg_path', self.img_bg.text())
        self._set_cfg(section, 'output_path', self.img_out.text())
        self._set_cfg(section, 'text1', self.img_t1.toPlainText().strip())
        self._set_cfg(section, 'text2', self.img_t2.text())
        self._set_cfg(section, 'font_name', self.img_font.currentText())
        self._set_cfg(section, 'font_color', self.img_color.text())
        self._set_cfg(section, 'outline', self.img_outline.currentText())
        self._set_cfg(section, 'font_size1', self.img_fs1.value())
        self._set_cfg(section, 'font_size2', self.img_fs2.value())
        self._set_cfg(section, 'text1_y', self.img_t1y.value())
        self._set_cfg(section, 'text2_y', self.img_t2y.value())
        self._set_cfg(section, 'offset_x', self.img_ox.value())
        self._set_cfg(section, 'offset_y', self.img_oy.value())
        self._set_cfg(section, 'bg_height', self.img_bh.value())

    def _load_image_from_section(self, template_name):
        """해당 템플릿 섹션 값으로 UI 리로드"""
        d = self._img_defaults(template_name)
        get = lambda k, dv='': self._load_img_field(k, dv, template_name)
        self.img_bg.setText(get('bg_path', ''))
        self.img_out.setText(get('output_path', ''))
        self.img_t1.setPlainText(get('text1', d['text1']))
        self.img_t2.setText(get('text2', d['text2']))
        self.img_font.setCurrentText(get('font_name', d['font_name']))
        self.img_color.setText(get('font_color', d['font_color']))
        self.img_outline.setCurrentText(get('outline', d['outline']))
        self.img_fs1.setValue(int(get('font_size1', str(d['font_size1']))))
        self.img_fs2.setValue(int(get('font_size2', str(d['font_size2']))))
        self.img_t1y.setValue(int(get('text1_y', str(d['text1_y']))))
        self.img_t2y.setValue(int(get('text2_y', str(d['text2_y']))))
        self.img_ox.setValue(int(get('offset_x', str(d.get('offset_x', 0)))))
        self.img_oy.setValue(int(get('offset_y', str(d.get('offset_y', 0)))))
        self.img_bh.setValue(int(get('bg_height', str(d.get('bg_height', 650)))))

    def _save_image_config(self):
        """현재 선택된 템플릿 섹션에 저장"""
        template = self.img_template.currentText()
        self._save_image_to_section(template)
        self._set_cfg('IMAGE', 'template', template)
        self._save_config_file()
        QMessageBox.information(self, "저장", f"[{template}] 설정이 저장되었습니다!")

    def _get_thumb_opts(self):
        """메인 쓰레드에서 썸네일 옵션 읽기"""
        return {
            'bg_offset_x': self.img_ox.value(),
            'bg_offset_y': self.img_oy.value(),
            'bg_end_y': self.img_bh.value(),
            'text2': self.img_t2.text(),
            'font_name': self.img_font.currentText(),
            'font_color': self.img_color.text(),
            'font_size1': self.img_fs1.value(),
            'font_size2': self.img_fs2.value(),
            'text1_y': self.img_t1y.value(),
            'text2_y': self.img_t2y.value(),
            'outline': self.img_outline.currentText(),
        }

    def _on_brand_changed(self, new_brand):
        """업체명 드롭다운 변경 시 — 다른 업체명 드롭다운 + 이미지 템플릿도 동기화.
        새집느낌/가족사랑클린은 img_template과 1:1 매핑되므로 이미지 템플릿도 함께 전환.
        새집환경365는 이미지 템플릿이 없으므로 img_template은 건드리지 않음 (업체명 드롭다운만 맞춤)."""
        # 1) 다른 업체명 드롭다운들 동기화
        for attr in ['blog_brand', 'one_brand', 'oneb_brand']:
            combo = getattr(self, attr, None)
            if combo is None or combo.currentText() == new_brand:
                continue
            idx = combo.findText(new_brand)
            if idx < 0:
                continue
            combo.blockSignals(True)
            try:
                combo.setCurrentIndex(idx)
            finally:
                combo.blockSignals(False)
        # 2) 업체가 새집느낌/가족사랑클린이면 img_template 도 자동 전환
        if new_brand in ('새집느낌', '가족사랑클린'):
            cur_tpl = getattr(self, '_current_img_template', None) \
                      or (self.img_template.currentText() if hasattr(self, 'img_template') else '새집느낌')
            if cur_tpl != new_brand:
                # _on_img_template_changed 를 타도록 직접 변경 (저장/로드 자동화됨)
                if hasattr(self, 'img_template'):
                    self.img_template.setCurrentText(new_brand)
                elif hasattr(self, 'bpack_template'):
                    self.bpack_template.setCurrentText(new_brand)
        # 3) config 저장 (업체명만)
        try:
            self._set_cfg('GENERATOR', 'blog_brand', new_brand)
            self._save_config_file()
        except Exception:
            pass

    def _sync_template_dropdowns(self, template_name):
        """모든 템플릿 드롭다운을 동일한 값으로 동기화 (signal loop 방지).
        img_template(새집느낌/가족사랑클린) 변경 시 업체명 드롭다운(blog_brand, one_brand, oneb_brand)도
        자동으로 같은 값으로 맞춰줌 → 원고/패키징이 현재 템플릿에 맞는 업체로 자동 발행됨."""
        # 1) 이미지 템플릿 드롭다운들 동기화
        for attr in ['img_template', 'bpack_template']:
            combo = getattr(self, attr, None)
            if combo is None:
                continue
            if combo.currentText() == template_name:
                continue
            combo.blockSignals(True)
            try:
                combo.setCurrentText(template_name)
            finally:
                combo.blockSignals(False)
        # 2) 업체명 드롭다운들도 동기화 (새집느낌 ↔ 새집느낌, 가족사랑클린 ↔ 가족사랑클린)
        #    ※ 브랜드는 ['새집느낌','가족사랑클린','새집환경365'] 3개지만, img_template 값과 이름이
        #       정확히 일치하는 두 브랜드만 자동 전환. 새집환경365 는 건드리지 않음.
        if template_name in ('새집느낌', '가족사랑클린'):
            for attr in ['blog_brand', 'one_brand', 'oneb_brand']:
                combo = getattr(self, attr, None)
                if combo is None:
                    continue
                if combo.currentText() == template_name:
                    continue
                idx = combo.findText(template_name)
                if idx < 0:
                    continue
                combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(idx)
                finally:
                    combo.blockSignals(False)
            # 저장도 해줌 → 다음 실행에서도 유지
            try:
                self._set_cfg('GENERATOR', 'blog_brand', template_name)
                self._save_config_file()
            except Exception:
                pass

    def _on_img_template_changed(self, new_value):
        """템플릿 드롭다운 변경 시 — 이전 값 자동 저장 + 새 값 로드 + 모든 탭 드롭다운 동기화"""
        old_value = getattr(self, '_current_img_template', '새집느낌')
        if old_value == new_value:
            # 혹시 드롭다운 상태만 어긋나 있으면 동기화만
            self._sync_template_dropdowns(new_value)
            return
        # 1) 이전 템플릿 섹션에 현재 UI 값 자동 저장 (썸네일 생성 탭 UI 위젯 기준)
        try:
            if hasattr(self, 'img_bg'):  # 썸네일 생성 탭이 이미 빌드된 경우만
                self._save_image_to_section(old_value)
        except Exception:
            pass
        # 1-2) 이전 템플릿 섹션에 현재 사진 폴더 UI 값도 자동 저장
        try:
            self._save_photos_to_section(old_value)
        except Exception:
            pass
        # 2) 새 템플릿 섹션 값으로 UI 리로드
        try:
            if hasattr(self, 'img_bg'):
                self._load_image_from_section(new_value)
        except Exception:
            pass
        # 2-2) 새 템플릿 섹션 값으로 사진 폴더 UI도 리로드
        try:
            self._load_photos_from_section(new_value)
        except Exception:
            pass
        # 3) 현재 선택값 기록
        self._current_img_template = new_value
        self._set_cfg('IMAGE', 'template', new_value)
        self._save_config_file()
        # 4) 모든 탭의 템플릿 드롭다운 양방향 동기화
        self._sync_template_dropdowns(new_value)
        # 5) 배경 네비게이션 리셋
        self._bg_files = []
        self._bg_index = 0
        if hasattr(self, 'bg_nav_label'):
            self.bg_nav_label.setText("사진을 선택하세요")
        # 6) 포장이사: 기본 폴더 자동 로드 → 좌우 네비게이션 가능
        if new_value == '포장이사':
            default_dir = os.path.join(BASE_DIR, '사진', '포장이사 썸네일 가공 전')
            if os.path.isdir(default_dir) and hasattr(self, '_load_bg_folder'):
                if hasattr(self, 'img_bg'):
                    self.img_bg.setText(default_dir)
                self._load_bg_folder(default_dir)

    def _preview_thumbnail(self):
        template = self.img_template.currentText()
        bg = self._bg_files[self._bg_index] if self._bg_files else self.img_bg.text()

        # 가족사랑클린: 배경 미지정 시 기본 템플릿 사용
        if template == '가족사랑클린' and (not bg or not os.path.isfile(bg)):
            bg = os.path.join(BASE_DIR, 'template', '가족사랑클린.png')
            if not os.path.isfile(bg):
                self.img_preview.setText(
                    f"가족사랑클린 템플릿 없음\n{bg}")
                return
        # 포장이사: 배경 미지정 시 사진/포장이사 썸네일 가공 전/ 폴더에서 첫 PNG
        elif template == '포장이사' and (not bg or not os.path.isfile(bg)):
            default_dir = os.path.join(BASE_DIR, '사진', '포장이사 썸네일 가공 전')
            if os.path.isdir(default_dir):
                exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
                cands = sorted([os.path.join(default_dir, f) for f in os.listdir(default_dir)
                                if f.lower().endswith(exts) and '사용완료' not in f
                                and os.path.isfile(os.path.join(default_dir, f))])
                if cands:
                    bg = cands[0]
            if not bg or not os.path.isfile(bg):
                self.img_preview.setText(
                    f"포장이사 배경 없음\n사진/포장이사 썸네일 가공 전/ 폴더에 PNG 추가 필요")
                return
        elif not bg or not os.path.isfile(bg):
            return

        # 메인 쓰레드에서 값 미리 읽기
        keywords = [t.strip() for t in self.img_t1.toPlainText().strip().split('\n') if t.strip()]
        t1 = keywords[self._bg_index] if self._bg_index < len(keywords) else (keywords[0] if keywords else '')
        opts = self._get_thumb_opts()

        import tempfile
        self._preview_tmp = os.path.join(tempfile.gettempdir(), 'qt_preview.jpg')
        preview_path = self._preview_tmp

        def do():
            try:
                if template in ('가족사랑클린', '포장이사'):
                    from core.thumbnail import create_thumbnail_family
                    fit_mode = 'fit' if template == '포장이사' else 'cover'
                    # 좌우/상하 스핀박스도 1080→540 비례 축소
                    create_thumbnail_family(
                        bg, preview_path, size=(540, 540),
                        text=t1,
                        font_name=opts['font_name'], font_color=opts['font_color'],
                        font_size=int(opts['font_size1'] * 540 / 1080),
                        text_y=int(opts['text1_y'] * 540 / 1080),
                        outline=opts['outline'],
                        fit_mode=fit_mode,
                        offset_x=int(opts['bg_offset_x'] * 540 / 1080),
                        offset_y=int(opts['bg_offset_y'] * 540 / 1080),
                    )
                else:
                    from core.thumbnail import create_thumbnail
                    create_thumbnail(bg, preview_path, size=(540, 540),
                                   bg_offset_x=opts['bg_offset_x'], bg_offset_y=opts['bg_offset_y'],
                                   bg_end_y=int(opts['bg_end_y'] * 540 / 1080),
                                   text1=t1, text2=opts['text2'],
                                   font_name=opts['font_name'], font_color=opts['font_color'],
                                   font_size1=int(opts['font_size1'] * 540 / 1080),
                                   font_size2=int(opts['font_size2'] * 540 / 1080),
                                   text1_y=int(opts['text1_y'] * 540 / 1080),
                                   text2_y=int(opts['text2_y'] * 540 / 1080),
                                   outline=opts['outline'])
                # Signal로 메인 쓰레드에서 표시
                self._bridge.set_text.emit(self.img_preview, f"__PIXMAP__{preview_path}")
            except Exception as e:
                self._bridge.set_text.emit(self.img_preview, f"오류: {e}")
        threading.Thread(target=do, daemon=True).start()

    def _create_thumbnail(self):
        text1_list = [t.strip() for t in self.img_t1.toPlainText().strip().split('\n') if t.strip()]
        if not text1_list:
            QMessageBox.warning(self, "경고", "키워드(서비스명)을 입력하세요"); return
        template = self.img_template.currentText()
        out_dir = self.img_out.text().strip()
        bg_path = self.img_bg.text().strip()

        # 가족사랑클린: 배경/저장폴더 미입력 시 기본값 자동
        if template == '가족사랑클린':
            if not bg_path:
                bg_path = os.path.join(BASE_DIR, 'template', '가족사랑클린.png')
                if not os.path.isfile(bg_path):
                    QMessageBox.warning(self, "안내",
                        f"가족사랑클린 템플릿이 없습니다.\n"
                        f"아래 경로에 베이스 이미지를 저장하거나 배경사진 란에 경로를 직접 입력하세요:\n\n"
                        f"{bg_path}")
                    return
            if not out_dir:
                out_dir = os.path.join(BASE_DIR, '가족사랑클린_썸네일')
        # 포장이사: 배경/저장폴더 미입력 시 기본값 자동
        # 출력 폴더는 항상 '사진/포장이사 썸네일 가공 후/' 로 강제 (베이스 PNG와 같은 폴더)
        elif template == '포장이사':
            if not bg_path:
                bg_path = os.path.join(BASE_DIR, '사진', '포장이사 썸네일 가공 전')
                if not os.path.isdir(bg_path):
                    QMessageBox.warning(self, "안내",
                        f"포장이사 썸네일 베이스 폴더가 없습니다.\n"
                        f"아래 경로에 PNG 이미지들을 저장하거나 배경사진 란에 경로를 직접 입력하세요:\n\n"
                        f"{bg_path}")
                    return
            # 사용자가 어떻게 설정했든 출력은 항상 가공 후/
            out_dir = os.path.join(BASE_DIR, '사진', '포장이사 썸네일 가공 후')
        else:
            if not out_dir:
                QMessageBox.warning(self, "경고", "저장 폴더를 입력하세요"); return
            if not bg_path:
                QMessageBox.warning(self, "경고", "배경 사진을 선택하세요"); return

        # 메인 쓰레드에서 값 미리 읽기
        opts = self._get_thumb_opts()

        def do():
            try:
                os.makedirs(out_dir, exist_ok=True)
                if os.path.isdir(bg_path):
                    bg_files = sorted([os.path.join(bg_path, f) for f in os.listdir(bg_path)
                                      if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))])
                else:
                    bg_files = [bg_path]
                import shutil
                used_bgs = []

                if template == '가족사랑클린':
                    from core.thumbnail import create_thumbnail_family
                    # 가족사랑클린: 템플릿 이미지 1장을 모든 키워드에 재사용 (사용완료 이동 X)
                    template_img = bg_files[0]
                    for t1 in text1_list:
                        safe = t1.replace(" ", "_")
                        out_name = f'thumb_가족_{safe}.jpg'
                        create_thumbnail_family(
                            template_img, os.path.join(out_dir, out_name),
                            text=t1,
                            font_name=opts['font_name'], font_color=opts['font_color'],
                            font_size=opts['font_size1'],
                            text_y=opts['text1_y'],
                            outline=opts['outline'],
                            offset_x=opts['bg_offset_x'],
                            offset_y=opts['bg_offset_y'],
                        )
                elif template == '포장이사':
                    from core.thumbnail import create_thumbnail_family
                    # 포장이사: 폴더에서 베이스 PNG 순환 사용
                    # → 썸네일은 '가공 후/' 에 thumb_포장_*.jpg 로 저장
                    # → 사용된 베이스 PNG 는 '가공 전(사용완료)/' 로 이동 (종착)
                    bg_files_filtered = [f for f in bg_files
                                         if '사용완료' not in f
                                         and not os.path.basename(f).startswith('thumb_포장_')]
                    if not bg_files_filtered:
                        bg_files_filtered = bg_files
                    for i, t1 in enumerate(text1_list):
                        bg_file = bg_files_filtered[i % len(bg_files_filtered)]
                        safe = t1.replace(" ", "_")
                        out_name = f'thumb_포장_{safe}.jpg'
                        create_thumbnail_family(
                            bg_file, os.path.join(out_dir, out_name),
                            text=t1,
                            font_name=opts['font_name'], font_color=opts['font_color'],
                            font_size=opts['font_size1'],
                            text_y=opts['text1_y'],
                            outline=opts['outline'],
                            fit_mode='fit',  # 원본 비율 유지 — 짤림 없음
                            offset_x=opts['bg_offset_x'],
                            offset_y=opts['bg_offset_y'],
                        )
                        if bg_file not in used_bgs:
                            used_bgs.append(bg_file)
                    # 사용한 베이스 PNG → '포장이사 썸네일 가공 전(사용완료)/' 로 이동 (종착)
                    if os.path.isdir(bg_path) and used_bgs:
                        pre_done_dir = self._packing_thumb_dirs()['pre_done']
                        os.makedirs(pre_done_dir, exist_ok=True)
                        for bg in used_bgs:
                            try:
                                shutil.move(bg, os.path.join(pre_done_dir, os.path.basename(bg)))
                            except Exception:
                                pass
                else:
                    from core.thumbnail import create_thumbnail
                    for i, t1 in enumerate(text1_list):
                        bg_file = bg_files[i % len(bg_files)]
                        fname = os.path.basename(bg_file)
                        out_name = f'thumb_{t1.replace(" ", "_")}_{os.path.splitext(fname)[0]}.jpg'
                        create_thumbnail(bg_file, os.path.join(out_dir, out_name),
                                       bg_offset_x=opts['bg_offset_x'], bg_offset_y=opts['bg_offset_y'],
                                       bg_end_y=opts['bg_end_y'],
                                       text1=t1, text2=opts['text2'],
                                       font_name=opts['font_name'], font_color=opts['font_color'],
                                       font_size1=opts['font_size1'], font_size2=opts['font_size2'],
                                       text1_y=opts['text1_y'], text2_y=opts['text2_y'],
                                       outline=opts['outline'])
                        if bg_file not in used_bgs:
                            used_bgs.append(bg_file)
                    # 사용한 배경을 사용완료로 이동 (새집느낌만)
                    if os.path.isdir(bg_path):
                        done_dir = os.path.join(os.path.dirname(bg_path),
                                               f'{os.path.basename(bg_path)}(사용완료)')
                        os.makedirs(done_dir, exist_ok=True)
                        for bg in used_bgs:
                            try:
                                shutil.move(bg, os.path.join(done_dir, os.path.basename(bg)))
                            except Exception:
                                pass

                self._bridge.show_msgbox.emit("완료", f"[{template}] 썸네일 {len(text1_list)}장 생성 완료!\n저장: {out_dir}")
            except Exception as e:
                self._bridge.show_msgbox.emit("오류", str(e))
        threading.Thread(target=do, daemon=True).start()

    # ═══════ 카페 원고 생성 ═══════
    def _on_cafe_generate(self):
        keywords = [k.strip() for k in self.cafe_keywords.toPlainText().strip().split('\n') if k.strip()]
        if not keywords:
            self._log_to(self.cafe_log, "키워드를 입력해주세요", '#ff6b6b'); return
        model = self.cafe_model.currentText()
        oai = self.cafe_oai_key.text().strip()
        claude = self.cafe_claude_key.text().strip()
        img_count = self.cafe_img_count.value()
        thumb_dir = self._resolve_packaging_thumb_dir(self.cafe_thumb_dir.text().strip())
        before_dirs = self._collect_priority_paths(self.cafe_before_dirs)
        after_dirs = self._collect_priority_paths(self.cafe_after_dirs)
        brand = self.cafegen_brand.currentText() if hasattr(self, 'cafegen_brand') else '새집느낌'
        viewpoint = '업체' if (hasattr(self, 'cafegen_vp_group') and self.cafegen_vp_group.checkedId() == 1) else '고객'
        gen_mode = 'cardnews' if brand == '카드뉴스(정보성)' else 'default'
        cur_template = self.cafe_template.currentText() if hasattr(self, 'cafe_template') else '청소'
        packing_dir = self.cafe_packing_dir.text().strip() if hasattr(self, 'cafe_packing_dir') else ''

        self.cafe_gen_btn.setEnabled(False)
        if cur_template == '포장이사':
            mode_label = f'포장이사 (사진 폴더: {os.path.basename(packing_dir) or "미설정"})'
        elif gen_mode == 'cardnews':
            mode_label = '카드뉴스 5장'
        else:
            mode_label = f'관점: {viewpoint}'
        self._log_to(self.cafe_log, f"{len(keywords)}개 키워드 생성 시작... (템플릿: {cur_template}, 모델: {model}, 업체: {brand}, {mode_label})")
        if cur_template != '포장이사' and gen_mode == 'default' and (thumb_dir or before_dirs or after_dirs):
            self._log_to(self.cafe_log,
                f"사진 폴더: 썸네일={bool(thumb_dir)}, 작업전={len(before_dirs)}개, 작업후={len(after_dirs)}개")

        def do():
            from core.generator import generate_and_save
            for i, kw in enumerate(keywords):
                self._log_to(self.cafe_log, f"[{i+1}/{len(keywords)}] {kw} 생성 중...")
                result = generate_and_save(kw, model=model, image_count=img_count,
                                           openai_key=oai, claude_key=claude,
                                           thumb_dir=thumb_dir, before_dirs=before_dirs,
                                           after_dirs=after_dirs, brand=brand, viewpoint=viewpoint,
                                           mode=gen_mode, template=cur_template,
                                           packing_dir=packing_dir)
                if result:
                    self._log_to(self.cafe_log, f"[{i+1}/{len(keywords)}] {kw} 완료!", '#00b894')
                    # 포장이사: 사용된 썸네일에 대응하는 베이스 PNG → '가공 후(사용완료)/' 로 이동
                    if cur_template == '포장이사':
                        def _gen_log(msg, color=None):
                            self._log_to(self.cafe_log, msg, color or '#00b894')
                        self._packing_consume_base_for_keyword(kw, log_fn=_gen_log)
                else:
                    self._log_to(self.cafe_log, f"[{i+1}/{len(keywords)}] {kw} 실패", '#ff6b6b')
            self._log_to(self.cafe_log, "전체 완료!", '#00b894')
            self._bridge.set_enabled.emit(self.cafe_gen_btn, True)
            QTimer.singleShot(0, self._refresh_postings)
        threading.Thread(target=do, daemon=True).start()

    # ═══════ 카페 발행 ═══════
    # ── 계정 목록 파싱/저장 ──
    @staticmethod
    def _parse_account_list(raw):
        """'아이디|비밀번호' 또는 '아이디 비밀번호' 한 줄씩 → [(user, pw), ...]"""
        import re
        accounts = []
        for line in (raw or '').strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            # | / 탭 / 공백 모두 구분자로 허용
            parts = [p for p in re.split(r'[|\t ]+', line) if p]
            if len(parts) < 2:
                continue
            user = parts[0]
            pw = parts[1]
            if user:
                accounts.append((user, pw))
        return accounts

    def _on_save_account_list(self):
        raw = self.pub_accounts.toPlainText().strip()
        self._set_cfg('CAFE', 'account_list', raw.replace('\n', '\\n'))
        self._save_config_file()
        n = len(self._parse_account_list(raw))
        self.acc_list_status.setText(f"저장됨 ({n}개 계정)")
        self.acc_list_status.setStyleSheet("color: #00b894;")
        QTimer.singleShot(2500, lambda: self.acc_list_status.setText(""))

    def _on_save_cafe_list(self):
        raw = self.pub_cafes.toPlainText().strip()
        self._set_cfg('CAFE', 'cafe_list', raw.replace('\n', '\\n'))
        self._save_config_file()
        self.cafe_list_status.setText("저장됨")
        self.cafe_list_status.setStyleSheet("color: #00b894;")
        QTimer.singleShot(2000, lambda: self.cafe_list_status.setText(""))

    def _on_cafe_brand_changed(self, new_brand):
        """카페 발행 업체 드롭다운 변경 — config.ini에 즉시 저장. 다른 탭과는 동기화하지 않음(카페만 독립)."""
        try:
            self._set_cfg('CAFE', 'brand', new_brand)
            self._save_config_file()
        except Exception:
            pass

    def _on_onec_brand_changed(self, new_brand):
        """원큐(카페) 발행 업체 드롭다운 변경 — 카페 발행 탭과 분리해 별도 키에 저장."""
        try:
            self._set_cfg('CAFE', 'onec_brand', new_brand)
            self._save_config_file()
        except Exception:
            pass

    def _on_cafegen_brand_changed(self, new_brand):
        """카페 원고 생성기 업체 드롭다운 변경 — 발행 탭과 분리해 별도 키에 저장."""
        try:
            self._set_cfg('CAFE', 'cafegen_brand', new_brand)
            self._save_config_file()
        except Exception:
            pass

    def _on_cafegen_viewpoint_changed(self, viewpoint):
        """카페 원고 생성기 작성 관점 변경 — '고객'/'업체' 저장."""
        try:
            self._set_cfg('CAFE', 'cafegen_viewpoint', viewpoint)
            self._save_config_file()
        except Exception:
            pass

    def _on_onec_viewpoint_changed(self, viewpoint):
        """원큐(카페) 작성 관점 변경 — '고객'/'업체' 저장."""
        try:
            self._set_cfg('CAFE', 'onec_viewpoint', viewpoint)
            self._save_config_file()
        except Exception:
            pass

    def _refresh_postings(self):
        directory = self.pub_postings_dir.text().strip() if hasattr(self, 'pub_postings_dir') else POSTINGS_DIR
        if not directory or not os.path.isdir(directory):
            directory = POSTINGS_DIR
        postings = list_postings(directory)
        self.postings_data = postings
        if hasattr(self, 'pub_postings'):
            self.pub_postings.clear()
            for p in postings:
                item = QListWidgetItem(p['name'])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, p)
                self.pub_postings.addItem(item)

    def _toggle_postings(self, list_widget, check):
        state = Qt.Checked if check else Qt.Unchecked
        for i in range(list_widget.count()):
            list_widget.item(i).setCheckState(state)

    @staticmethod
    def _checked_postings(list_widget):
        result = []
        for i in range(list_widget.count()):
            it = list_widget.item(i)
            if it.checkState() == Qt.Checked:
                result.append(it.data(Qt.UserRole))
        return result

    @staticmethod
    def _posting_list_style():
        """체크박스를 크고 선명하게"""
        return """
        QListWidget {
            background: #1b1f24;
            border: 1px solid #30363d;
            border-radius: 4px;
            padding: 4px;
            outline: 0;
        }
        QListWidget::item {
            padding: 6px 4px;
            border-bottom: 1px solid #2a2f37;
            color: #e6edf3;
        }
        QListWidget::item:last { border-bottom: none; }
        QListWidget::indicator {
            width: 18px;
            height: 18px;
            border: 2px solid #8b949e;
            border-radius: 3px;
            background: #0d1117;
        }
        QListWidget::indicator:checked {
            background: #6c5ce7;
            border: 2px solid #6c5ce7;
            image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PHBhdGggZmlsbD0id2hpdGUiIGQ9Ik02LjUgMTAuNUwzIDcgNC41IDUuNSA2LjUgNy41IDExLjUgMi41IDEzIDR6Ii8+PC9zdmc+);
        }
        """

    def _find_posting_item(self, list_widget, posting):
        """특정 posting 데이터에 해당하는 QListWidgetItem 찾기"""
        for i in range(list_widget.count()):
            it = list_widget.item(i)
            if it.data(Qt.UserRole) == posting:
                return it
        return None

    def _mark_posting_done(self, list_widget, posting, ok):
        """발행 완료/실패 표시"""
        it = self._find_posting_item(list_widget, posting)
        if it is None:
            return
        base = posting.get('name', '')
        if ok:
            it.setText(f"✓ [완료] {base}")
            it.setForeground(Qt.GlobalColor.gray)
        else:
            it.setText(f"✗ [실패] {base}")
            it.setForeground(Qt.GlobalColor.red)
        it.setCheckState(Qt.Unchecked)
        it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)

    def _on_expand_cafe_list(self):
        """카페 목록 전체보기/편집 다이얼로그"""
        from PySide6.QtWidgets import QDialog
        dlg = QDialog(self)
        dlg.setWindowTitle("카페 목록 전체보기")
        dlg.resize(720, 520)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("한 줄에: 게시판URL | 카페URL  (여러 줄 = 순환 배분)"))
        txt = QTextEdit()
        txt.setFont(QFont("Consolas", 10))
        txt.setPlainText(self.pub_cafes.toPlainText())
        lay.addWidget(txt)
        btn_row = QHBoxLayout()
        btn_save = QPushButton("저장")
        btn_save.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; border: none;")
        def save_and_close():
            self.pub_cafes.setPlainText(txt.toPlainText())
            self._on_save_cafe_list()
            dlg.accept()
        btn_save.clicked.connect(save_and_close)
        btn_row.addWidget(btn_save)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        dlg.exec()

    def _parse_cafe_list(self):
        import re
        result = []
        for line in self.pub_cafes.toPlainText().strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            if '|' in line:
                parts = line.split('|', 1)
                board_url, cafe_url = parts[0].strip(), parts[1].strip()
            else:
                board_url, cafe_url = line, ''
            m = re.search(r'cafes/(\d+)/menus/(\d+)', board_url)
            if m:
                result.append((m.group(1), m.group(2), cafe_url))
        return result

    def _on_start(self):
        if not self.postings_data:
            self._log_to(self.pub_log, "발행할 원고가 없습니다", '#ff6b6b'); return
        cafes = self._parse_cafe_list()
        if not cafes:
            self._log_to(self.pub_log, "카페 목록을 입력해주세요", '#ff6b6b'); return

        accounts = self._parse_account_list(self.pub_accounts.toPlainText())
        if not accounts:
            self._log_to(self.pub_log, "계정 목록을 입력해주세요 (아이디 | 비밀번호)", '#ff6b6b'); return

        selected = self._checked_postings(self.pub_postings)
        if not selected:
            self._log_to(self.pub_log, "체크된 원고가 없습니다", '#ff6b6b'); return

        self.is_running = True
        self.stop_flag = False
        self.pub_start_btn.setEnabled(False)
        self.pub_stop_btn.setEnabled(True)
        delay = self.pub_delay.value()
        cafe_brand = self.cafe_brand.currentText() if hasattr(self, 'cafe_brand') else '새집느낌'

        def do():
            import time as _time
            page = None
            current_acc_idx = -1

            total = len(selected)
            for i, posting in enumerate(selected):
                if self.stop_flag:
                    self._log_to(self.pub_log, "사용자에 의해 중지됨", '#ff6b6b'); break

                # 순서: 계정 바깥 / 카페 안쪽
                # 예) 계정2개 × 카페2개 → acc1·cafe1, acc1·cafe2, acc2·cafe1, acc2·cafe2 …
                cafe_idx = i % len(cafes)
                acc_idx = (i // len(cafes)) % len(accounts)
                if acc_idx != current_acc_idx:
                    if page is not None:
                        try: self.browser.close()
                        except Exception: pass
                        page = None
                    user, pw = accounts[acc_idx]
                    self._log_to(self.pub_log, f"[계정 {acc_idx+1}/{len(accounts)}] {user} 로그인 중...", '#fdcb6e')
                    ok = self.browser.login_manual(
                        username=user, password=pw,
                        callback=lambda m: self._log_to(self.pub_log, m))
                    if not ok:
                        self._log_to(self.pub_log, f"[실패] {user} 로그인 실패 - 건너뜀", '#ff6b6b')
                        continue
                    page = self.browser.start_headless()
                    current_acc_idx = acc_idx

                c_id, m_id, c_url = cafes[cafe_idx]
                name = posting['name'][:20]
                self._log_to(self.pub_log, f"[{i+1}/{total}] {name} -> 카페{cafe_idx+1} / 계정{current_acc_idx+1}")
                post_status = {'ok': False}
                def log_cb(msg, _s=post_status):
                    color = '#00b894' if '[완료]' in msg else '#ff6b6b' if '[실패]' in msg else '#00cec9'
                    if '[완료]' in msg:
                        _s['ok'] = True
                    self._log_to(self.pub_log, msg, color)
                try:
                    post_to_cafe(page, c_url, '', posting['path'],
                                cafe_id=c_id, menu_id=m_id, log_callback=log_cb,
                                stop_check=lambda: self.stop_flag, brand=cafe_brand)
                except Exception as e:
                    self._log_to(self.pub_log, f"[실패] {name}: {e}", '#ff6b6b')
                # 리스트에 완료/실패 마킹
                QTimer.singleShot(0, lambda p=posting, ok=post_status['ok']:
                                      self._mark_posting_done(self.pub_postings, p, ok))
                if i < total - 1 and not self.stop_flag:
                    for _ in range(delay):
                        if self.stop_flag: break
                        _time.sleep(1)
            try:
                if page is not None:
                    self.browser.close()
            except Exception:
                pass
            self.is_running = False
            self._bridge.set_enabled.emit(self.pub_start_btn, True)
            self._bridge.set_enabled.emit(self.pub_stop_btn, False)
            self._log_to(self.pub_log, "발행 완료!", '#00b894')
        threading.Thread(target=do, daemon=True).start()

    def _on_stop(self):
        self.stop_flag = True
        self._log_to(self.pub_log, "[중지] 요청 — 브라우저 즉시 종료", '#ff6b6b')
        try:
            if hasattr(self, 'browser') and self.browser:
                self.browser.close()
        except Exception:
            pass
        self._bridge.set_enabled.emit(self.pub_start_btn, True)
        self._bridge.set_enabled.emit(self.pub_stop_btn, False)

    # ═══════ 카페 글수정(SEO) 탭 ═══════
    # 플로우: 로그인 → 내가 쓴 게시글 → 제목 키워드('세종') 추적·선택
    #         → 새 원고(SEO 생성) + 지정 폴더 이미지 순서매칭으로 게시물 수정 → 발행
    def _build_cafe_edit_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)

        # 계정 (카페 발행 탭과 동일 계정 목록 공유, 첫 계정 사용)
        g_acc = QGroupBox("계정 (첫 번째 계정 사용)")
        v_acc = QVBoxLayout(g_acc)
        self.cedit_accounts = QTextEdit()
        self.cedit_accounts.setMaximumHeight(56)
        self.cedit_accounts.setPlainText((self._cfg('CAFE', 'account_list') or '').replace('\\n', '\n'))
        self.cedit_accounts.setPlaceholderText("아이디 | 비밀번호")
        v_acc.addWidget(self.cedit_accounts)
        lv.addWidget(g_acc)

        # 대상 카페 + 추적 키워드
        g_t = QGroupBox("대상 — 내가 쓴 게시글 추적")
        f_t = QFormLayout(g_t); f_t.setLabelAlignment(Qt.AlignRight)
        self.cedit_cafe_id = QLineEdit(self._cfg('CAFE', 'cafe_id', ''))
        self.cedit_cafe_id.setPlaceholderText("예: 10174516")
        f_t.addRow("카페 ID", self.cedit_cafe_id)
        self.cedit_track_kw = QLineEdit('세종')
        self.cedit_track_kw.setPlaceholderText("제목에 이 단어가 들어간 내 글만")
        f_t.addRow("추적 키워드", self.cedit_track_kw)
        btn_fetch = QPushButton("내 글 불러오기")
        btn_fetch.setStyleSheet("background-color: #3498db; color: white; font-size: 12px; padding: 6px; border-radius: 5px; border: none;")
        btn_fetch.clicked.connect(self._on_fetch_my_articles)
        self.cedit_fetch_btn = btn_fetch
        f_t.addRow("", btn_fetch)
        lv.addWidget(g_t)

        # 글 목록 (체크)
        g_l = QGroupBox("내 글 목록 (체크해서 선택)")
        v_l = QVBoxLayout(g_l)
        self.cedit_list = QListWidget()
        self.cedit_list.setMaximumHeight(150)
        self.cedit_list.setSelectionMode(QAbstractItemView.NoSelection)
        v_l.addWidget(self.cedit_list)
        sel_row = QHBoxLayout()
        b_all = QPushButton("전체 선택"); b_all.setFixedWidth(80); b_all.setStyleSheet("font-size:11px;padding:4px;")
        b_all.clicked.connect(lambda: self._toggle_edit_list(True))
        b_none = QPushButton("전체 해제"); b_none.setFixedWidth(80); b_none.setStyleSheet("font-size:11px;padding:4px;")
        b_none.clicked.connect(lambda: self._toggle_edit_list(False))
        sel_row.addWidget(b_all); sel_row.addWidget(b_none); sel_row.addStretch()
        v_l.addLayout(sel_row)
        lv.addWidget(g_l)

        # 새 원고 생성 설정
        g_g = QGroupBox("새 원고 생성 (SEO)")
        f_g = QFormLayout(g_g); f_g.setLabelAlignment(Qt.AlignRight)
        self.cedit_category = QComboBox()
        for label, key in [('청소 계열', 'clean'), ('포장이사', 'move'), ('인터넷가입', 'internet')]:
            self.cedit_category.addItem(label, key)
        f_g.addRow("카테고리", self.cedit_category)
        self.cedit_style = QComboBox()
        for label, key in [('하이브리드', 'hybrid'), ('가이드형', 'guide'), ('후기형', 'review')]:
            self.cedit_style.addItem(label, key)
        f_g.addRow("글 스타일", self.cedit_style)
        self.cedit_region = QLineEdit('세종')
        f_g.addRow("지역", self.cedit_region)
        self.cedit_seo_kw = QLineEdit('입주청소')
        f_g.addRow("핵심 키워드", self.cedit_seo_kw)
        self.cedit_industry = QLineEdit('입주청소')
        f_g.addRow("업종", self.cedit_industry)
        lv.addWidget(g_g)

        # 이미지 폴더 (순서매칭) + 딜레이
        g_i = QGroupBox("이미지 / 딜레이")
        v_i = QVBoxLayout(g_i)
        img_row = QHBoxLayout()
        img_row.addWidget(QLabel("이미지 폴더"))
        self.cedit_img_dir = QLineEdit(self._cfg('CAFE', 'cafe_thumb_dir', ''))
        self.cedit_img_dir.setPlaceholderText("이 폴더의 사진을 마커 자리에 순서대로 삽입")
        img_row.addWidget(self.cedit_img_dir, 1)
        b_pick = QPushButton("폴더"); b_pick.setFixedWidth(50)
        b_pick.clicked.connect(lambda: self._pick_folder(self.cedit_img_dir))
        img_row.addWidget(b_pick)
        v_i.addLayout(img_row)
        v_i.addWidget(QLabel("순서매칭: 폴더 파일을 이름순 정렬해 각 글의 [이미지]/[배너] 마커 자리에 차례로 넣습니다"))
        d_row = QHBoxLayout()
        d_row.addWidget(QLabel("글 사이 딜레이"))
        self.cedit_delay = QSpinBox(); self.cedit_delay.setRange(5, 86400); self.cedit_delay.setValue(60)
        self.cedit_delay.setSuffix("초"); self.cedit_delay.setFixedWidth(110)
        d_row.addWidget(self.cedit_delay); d_row.addStretch()
        v_i.addLayout(d_row)
        lv.addWidget(g_i)

        # 시작 / 중지
        btn_row = QHBoxLayout()
        self.cedit_start_btn = QPushButton("선택 글 수정 시작")
        self.cedit_start_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 12px; font-weight: bold; padding: 8px; border-radius: 6px; border: none;")
        self.cedit_start_btn.clicked.connect(self._on_run_edit)
        btn_row.addWidget(self.cedit_start_btn)
        self.cedit_stop_btn = QPushButton("중지"); self.cedit_stop_btn.setEnabled(False)
        self.cedit_stop_btn.clicked.connect(self._on_stop_edit)
        btn_row.addWidget(self.cedit_stop_btn)
        lv.addLayout(btn_row)
        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("실행 로그"))
        self.cedit_log = QTextEdit(); self.cedit_log.setReadOnly(True)
        self.cedit_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.cedit_log)

        splitter.addWidget(left); splitter.addWidget(right)
        splitter.setSizes([380, 420])
        lay = QVBoxLayout(tab); lay.addWidget(splitter)
        self.cedit_articles = []
        self.cedit_stop = False
        return tab

    def _toggle_edit_list(self, checked: bool):
        st = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.cedit_list.count()):
            self.cedit_list.item(i).setCheckState(st)

    def _populate_edit_list(self, articles):
        self.cedit_articles = articles or []
        self.cedit_list.clear()
        for a in self.cedit_articles:
            item = QListWidgetItem(f"[{a['article_id']}] {a['title']}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, a)
            self.cedit_list.addItem(item)
        self._log_to(self.cedit_log, f"목록 {len(self.cedit_articles)}건 표시", '#00b894')

    def _checked_edit_articles(self):
        out = []
        for i in range(self.cedit_list.count()):
            it = self.cedit_list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    def _on_fetch_my_articles(self):
        accounts = self._parse_account_list(self.cedit_accounts.toPlainText())
        if not accounts:
            self._log_to(self.cedit_log, "계정을 입력해주세요 (아이디 | 비밀번호)", '#ff6b6b'); return
        cafe_id = self.cedit_cafe_id.text().strip()
        if not cafe_id:
            self._log_to(self.cedit_log, "카페 ID를 입력해주세요", '#ff6b6b'); return
        kw = self.cedit_track_kw.text().strip()
        self._bridge.set_enabled.emit(self.cedit_fetch_btn, False)

        def do():
            browser = NaverBrowser()
            try:
                user, pw = accounts[0]
                self._log_to(self.cedit_log, f"[{user}] 로그인 중...", '#fdcb6e')
                if not browser.login_manual(username=user, password=pw,
                                            callback=lambda m: self._log_to(self.cedit_log, m)):
                    self._log_to(self.cedit_log, "로그인 실패", '#ff6b6b'); return
                page = browser.start_headless()
                arts = fetch_my_articles(page, cafe_id, kw,
                                         log_callback=lambda m: self._log_to(self.cedit_log, m))
                QTimer.singleShot(0, lambda: self._populate_edit_list(arts))
            except Exception as e:
                self._log_to(self.cedit_log, f"[에러] {e}", '#ff6b6b')
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
                self._bridge.set_enabled.emit(self.cedit_fetch_btn, True)
        threading.Thread(target=do, daemon=True).start()

    def _on_run_edit(self):
        selected = self._checked_edit_articles()
        if not selected:
            self._log_to(self.cedit_log, "체크된 글이 없습니다 (먼저 '내 글 불러오기')", '#ff6b6b'); return
        accounts = self._parse_account_list(self.cedit_accounts.toPlainText())
        if not accounts:
            self._log_to(self.cedit_log, "계정을 입력해주세요", '#ff6b6b'); return
        claude_key = self._cfg('GENERATOR', 'claude_api_key', '').strip()
        if not claude_key:
            self._log_to(self.cedit_log, "Claude API 키가 없습니다 (설정에서 등록)", '#ff6b6b'); return
        cafe_id = self.cedit_cafe_id.text().strip()
        img_dir = self.cedit_img_dir.text().strip()
        region = self.cedit_region.text().strip()
        seo_kw = self.cedit_seo_kw.text().strip()
        industry = self.cedit_industry.text().strip()
        category = self.cedit_category.currentData()
        style = self.cedit_style.currentData()
        model = (self._cfg('GENERATOR', 'seo_model', '').strip()
                 or self._cfg('GENERATOR', 'blog_model', '').strip()
                 or 'claude-sonnet-4-20250514')
        delay = self.cedit_delay.value()

        self.cedit_stop = False
        self._bridge.set_enabled.emit(self.cedit_start_btn, False)
        self._bridge.set_enabled.emit(self.cedit_stop_btn, True)

        def do():
            import time as _t
            browser = NaverBrowser()
            try:
                user, pw = accounts[0]
                self._log_to(self.cedit_log, f"[{user}] 로그인 중...", '#fdcb6e')
                if not browser.login_manual(username=user, password=pw,
                                            callback=lambda m: self._log_to(self.cedit_log, m)):
                    self._log_to(self.cedit_log, "로그인 실패", '#ff6b6b'); return
                page = browser.start_headless()
                total = len(selected)
                for idx, art in enumerate(selected):
                    if self.cedit_stop:
                        self._log_to(self.cedit_log, "중지됨", '#ff6b6b'); break
                    self._log_to(self.cedit_log, f"[{idx+1}/{total}] 새 원고 생성: {art['title'][:24]}", '#00cec9')
                    try:
                        article = seo_generator.generate_article(
                            claude_key, region=region, keyword=seo_kw, industry=industry,
                            category=category, style=style, model=model,
                            callback=lambda m: self._log_to(self.cedit_log, m),
                            stop_check=lambda: self.cedit_stop)
                    except Exception as e:
                        self._log_to(self.cedit_log, f"[실패] 생성 오류: {e}", '#ff6b6b'); continue
                    if not article:
                        self._log_to(self.cedit_log, "[실패] 빈 원고 — 건너뜀", '#ff6b6b'); continue
                    new_title, _meta, body = seo_generator.split_title_body(article)
                    if not new_title:
                        new_title = art['title']
                    ok = edit_article_replace(
                        page, cafe_id, art.get('menu_id', ''), art['article_id'],
                        new_title, body, image_folder=img_dir,
                        log_callback=lambda m: self._log_to(
                            self.cedit_log, m,
                            '#00b894' if '[완료]' in m else '#ff6b6b' if '[실패]' in m else '#00cec9'),
                        stop_check=lambda: self.cedit_stop)
                    QTimer.singleShot(0, lambda i=idx, ok=ok: self._mark_edit_done(i, ok))
                    if idx < total - 1 and not self.cedit_stop:
                        for _ in range(delay):
                            if self.cedit_stop:
                                break
                            _t.sleep(1)
                self._log_to(self.cedit_log, "수정 작업 완료!", '#00b894')
            except Exception as e:
                self._log_to(self.cedit_log, f"[에러] {e}", '#ff6b6b')
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
                self._bridge.set_enabled.emit(self.cedit_start_btn, True)
                self._bridge.set_enabled.emit(self.cedit_stop_btn, False)
        threading.Thread(target=do, daemon=True).start()

    def _mark_edit_done(self, idx: int, ok: bool):
        if 0 <= idx < self.cedit_list.count():
            it = self.cedit_list.item(idx)
            it.setText(("✅ " if ok else "❌ ") + it.text())
            it.setCheckState(Qt.Unchecked)

    def _on_stop_edit(self):
        self.cedit_stop = True
        self._log_to(self.cedit_log, "[중지] 요청", '#ff6b6b')
        self._bridge.set_enabled.emit(self.cedit_start_btn, True)
        self._bridge.set_enabled.emit(self.cedit_stop_btn, False)

    # ═══════ 블로그 원고 생성 ═══════
    def _show_cafe_prompt(self):
        """카페 프롬프트 보기/수정"""
        try:
            from core.generator import CAFE_SYSTEM_PROMPT
            current = self._cfg('GENERATOR', 'cafe_custom_prompt', '') or CAFE_SYSTEM_PROMPT

            from PySide6.QtWidgets import QDialog
            dlg = QDialog(self)
            dlg.setWindowTitle(f"카페 프롬프트 보기/수정 ({len(current)}자)")
            dlg.resize(800, 600)
            lay = QVBoxLayout(dlg)
            txt = QTextEdit()
            txt.setPlainText(current)
            txt.setFont(QFont("Consolas", 10))
            lay.addWidget(txt)

            btn_row = QHBoxLayout()
            btn_save = QPushButton("프롬프트 저장")
            btn_save.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; border: none; border-radius: 5px; padding: 6px 12px;")
            def save_prompt():
                self._set_cfg('GENERATOR', 'cafe_custom_prompt', txt.toPlainText())
                self._save_config_file()
                QMessageBox.information(dlg, "저장", "저장 완료")
            btn_save.clicked.connect(save_prompt)
            btn_row.addWidget(btn_save)

            btn_reset = QPushButton("기본값 복원")
            def reset_prompt():
                txt.setPlainText(CAFE_SYSTEM_PROMPT)
                self._set_cfg('GENERATOR', 'cafe_custom_prompt', '')
                self._save_config_file()
            btn_reset.clicked.connect(reset_prompt)
            btn_row.addWidget(btn_reset)

            btn_close = QPushButton("닫기")
            btn_close.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_close)
            lay.addLayout(btn_row)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "오류", str(e))

    def _show_blog_prompt(self):
        """프롬프트 보기/수정 — 수정 후 저장 가능"""
        try:
            from core.blog_generator import build_prompt
            # 저장된 커스텀 프롬프트가 있으면 그걸 먼저 보여줌
            custom_saved = self._cfg('GENERATOR', 'blog_custom_prompt', '')
            input_data = {
                'title': self.blog_title.text().strip() or '(미입력)',
                'brand_name': self.blog_brand.currentText() if hasattr(self, 'blog_brand') else '새집느낌',
                'bracket_kw': self.blog_bkw.text().strip() or '(미입력)',
                'paren_kw': self.blog_pkw.toPlainText().strip(),
                'viewpoint': '고객' if self.blog_vp_group.checkedId() == 0 else '업체',
                'numbering': self.blog_nb_group.checkedId() == 0,
                'image_count': self.blog_img_count.value(),
                'manuscript': (self.blog_manuscript.toPlainText().strip()[:200] + '...') if self.blog_manuscript.toPlainText().strip() else '(원고 미입력 - 프롬프트 구조만 확인용)',
            }
            prompt = custom_saved.strip() if custom_saved.strip() else build_prompt(input_data)

            from PySide6.QtWidgets import QDialog
            dlg = QDialog(self)
            dlg.setWindowTitle(f"블로그 프롬프트 보기/수정 ({len(prompt)}자)")
            dlg.resize(900, 650)
            lay = QVBoxLayout(dlg)

            hint = QLabel("※ 저장된 커스텀 프롬프트가 있으면 그걸 표시, 없으면 현재 입력값으로 생성된 프롬프트를 표시합니다")
            hint.setStyleSheet("color: #8b949e; padding: 4px;")
            hint.setWordWrap(True)
            lay.addWidget(hint)

            txt = QTextEdit()
            txt.setPlainText(prompt)
            txt.setFont(QFont("Consolas", 10))
            lay.addWidget(txt)

            btn_row = QHBoxLayout()
            btn_save = QPushButton("프롬프트 저장")
            btn_save.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; border: none; border-radius: 5px; padding: 6px 12px;")
            def save_prompt():
                self._custom_blog_prompt = txt.toPlainText()
                self._set_cfg('GENERATOR', 'blog_custom_prompt', self._custom_blog_prompt)
                self._save_config_file()
                QMessageBox.information(dlg, "저장", "커스텀 프롬프트가 저장되었습니다.\n다음 생성 시 적용됩니다.")
            btn_save.clicked.connect(save_prompt)
            btn_row.addWidget(btn_save)

            btn_reset = QPushButton("기본값 복원")
            def reset_prompt():
                txt.setPlainText(build_prompt(input_data))
                self._custom_blog_prompt = None
                self._set_cfg('GENERATOR', 'blog_custom_prompt', '')
                self._save_config_file()
            btn_reset.clicked.connect(reset_prompt)
            btn_row.addWidget(btn_reset)

            btn_close = QPushButton("닫기")
            btn_close.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_close)

            lay.addLayout(btn_row)

            # 뒤에 숨지 않도록 확실히 앞으로
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            dlg.exec()
        except Exception as e:
            import traceback
            QMessageBox.critical(self, "프롬프트 오류",
                                 f"{e}\n\n{traceback.format_exc()[:500]}")

    def _on_blog_generate(self):
        api_key = self.blog_key.text().strip()
        title = self.blog_title.text().strip()
        bkw = self.blog_bkw.text().strip()
        manuscript = self.blog_manuscript.toPlainText().strip()

        if not api_key:
            QMessageBox.warning(self, "입력 필요", "Claude API 키를 입력해주세요"); return
        if not title or not bkw:
            QMessageBox.warning(self, "입력 필요", "제목과 대괄호 키워드를 입력해주세요"); return
        if not manuscript:
            QMessageBox.warning(self, "입력 필요", "원고를 붙여넣어주세요"); return

        model = self.blog_model.currentText()
        viewpoint = '고객' if self.blog_vp_group.checkedId() == 0 else '업체'
        numbering = self.blog_nb_group.checkedId() == 0

        input_data = {
            'title': title,
            'brand_name': self.blog_brand.currentText(),
            'bracket_kw': bkw,
            'paren_kw': self.blog_pkw.toPlainText().strip(),
            'viewpoint': viewpoint,
            'numbering': numbering,
            'image_count': self.blog_img_count.value(),
            'manuscript': manuscript,
        }

        save_dir = self.blog_save_dir.text().strip()
        # 설정 저장 (모델 + 폴더)
        self._set_cfg('GENERATOR', 'blog_save_dir', save_dir)
        self._set_cfg('GENERATOR', 'blog_model', model)
        self._save_config_file()

        self.blog_gen_btn.setEnabled(False)
        self.blog_log.clear()
        self._log_to(self.blog_log, f"블로그 원고 생성 시작... (모델: {model})")
        self._log_to(self.blog_log, f"[10%] API 호출 준비 중...")

        def do():
            try:
                from core.blog_generator import generate_blog_post, save_blog_post
                self._log_to(self.blog_log, "[20%] Claude API 호출 중... (1~2분 소요)")
                def cb(msg):
                    self._log_to(self.blog_log, msg)
                result = generate_blog_post(input_data, api_key, model=model, callback=cb)
                self._log_to(self.blog_log, "[80%] 응답 수신 완료, 저장 중...")
                if result and result.get('body'):
                    save_blog_post(bkw, result['body'], callback=cb, save_dir=save_dir)
                    self._log_to(self.blog_log, "[100%] 완료!", '#00b894')
                    self._log_to(self.blog_log,
                                f"글자수: {result['char_count']}자, 키워드: {result['keyword_count']}회",
                                '#00b894')
                    self._bridge.append_log.emit(self.blog_log, "\n" + result['body'])
                else:
                    self._log_to(self.blog_log, "[실패] 생성 실패 — 응답이 비어있습니다", '#ff6b6b')
            except Exception as e:
                self._log_to(self.blog_log, f"[실패] 오류: {e}", '#ff6b6b')
            self._bridge.set_enabled.emit(self.blog_gen_btn, True)
        threading.Thread(target=do, daemon=True).start()

    def _update_one_reprocess_visibility(self):
        """원큐(패키징) — 이미지 재가공 체크박스를 포장이사 템플릿일 때만 표시."""
        if not hasattr(self, 'one_reprocess_chk') or not hasattr(self, 'one_template'):
            return
        is_packing = self.one_template.currentText() == '포장이사'
        self.one_reprocess_chk.setVisible(is_packing)
        if hasattr(self, 'one_reprocess_row_label'):
            self.one_reprocess_row_label.setVisible(is_packing)

    def _update_oneb_reprocess_visibility(self):
        """원큐(블로그) — 이미지 재가공 체크박스를 포장이사 템플릿일 때만 표시."""
        if not hasattr(self, 'oneb_reprocess_chk') or not hasattr(self, 'oneb_template'):
            return
        is_packing = self.oneb_template.currentText() == '포장이사'
        self.oneb_reprocess_chk.setVisible(is_packing)
        if hasattr(self, 'oneb_reprocess_row_label'):
            self.oneb_reprocess_row_label.setVisible(is_packing)

    # ═══════ 원큐 자동화 탭 ═══════
    def _build_one_shot_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        g = QGroupBox("원큐 자동화 — 분석 → 원고 생성 → 패키징 → 동영상")
        f = QFormLayout(g); f.setLabelAlignment(Qt.AlignRight)

        self.one_template = QComboBox()
        self.one_template.addItems(TEMPLATE_LIST)
        _tpl = self._cfg('GENERATOR', 'template', '청소')
        _ti = self.one_template.findText(_tpl)
        if _ti >= 0:
            self.one_template.setCurrentIndex(_ti)
        self.one_template.currentTextChanged.connect(self._on_template_changed)
        f.addRow("템플릿", self.one_template)

        self.one_keyword = QTextEdit()
        self.one_keyword.setPlaceholderText("예:\n천안입주청소\n부산입주청소\n오산입주청소\n(한 줄에 하나씩 - 위에서 아래로 순차 실행)")
        self.one_keyword.setMaximumHeight(90)
        f.addRow("키워드", self.one_keyword)

        self.one_count = QSpinBox()
        self.one_count.setRange(3, 20)
        self.one_count.setValue(5)
        f.addRow("검색 개수", self.one_count)

        # 이미지 수 (원큐 자체) — 패키징·동영상에 사용
        self.one_img_count = QSpinBox()
        self.one_img_count.setRange(0, 30)
        self.one_img_count.setValue(int(self._cfg('POSTING', 'one_img_count', '18')))
        f.addRow("이미지 수", self.one_img_count)

        # 업체명
        self.one_brand = QComboBox()
        self.one_brand.addItems(['새집느낌', '가족사랑클린', '새집환경365'])
        saved_brand = self._cfg('GENERATOR', 'blog_brand', '새집느낌')
        if saved_brand:
            idx = self.one_brand.findText(saved_brand)
            if idx >= 0:
                self.one_brand.setCurrentIndex(idx)
        self.one_brand.currentTextChanged.connect(self._on_brand_changed)
        f.addRow("업체명", self.one_brand)

        # 관점
        vp_row = QHBoxLayout()
        self.one_vp_group = QButtonGroup(self)
        rb_customer = QRadioButton("고객")
        rb_company = QRadioButton("업체")
        rb_customer.setChecked(True)
        self.one_vp_group.addButton(rb_customer, 0)
        self.one_vp_group.addButton(rb_company, 1)
        vp_row.addWidget(rb_customer)
        vp_row.addWidget(rb_company)
        vp_row.addStretch()
        f.addRow("관점", vp_row)

        # 키워드 사이 딜레이 (원큐 자체)
        self.one_delay = QSpinBox()
        self.one_delay.setRange(0, 86400)
        self.one_delay.setValue(int(self._cfg('POSTING', 'one_delay', '0')))
        self.one_delay.setSuffix(" 초")
        self.one_delay.setFixedWidth(120)
        f.addRow("패키징 딜레이 (키워드 사이)", self.one_delay)

        # 포장이사 전용 — 이미지 재가공 (원본 보존하면서 매번 다른 변형값으로 사용)
        self.one_reprocess_chk = QCheckBox("이미지 재가공 (원본 유지 + 매번 다른 변형값)")
        self.one_reprocess_chk.setChecked(self._cfg('POSTING', 'one_reprocess', '0') == '1')
        self.one_reprocess_chk.setStyleSheet("color: #74b9ff;")
        self.one_reprocess_chk.toggled.connect(
            lambda v: self._set_cfg('POSTING', 'one_reprocess', '1' if v else '0') or self._save_config_file())
        self.one_reprocess_row_label = QLabel("이미지 재가공")
        f.addRow(self.one_reprocess_row_label, self.one_reprocess_chk)
        # 포장이사 템플릿일 때만 표시
        self._update_one_reprocess_visibility()
        self.one_template.currentTextChanged.connect(lambda _: self._update_one_reprocess_visibility())

        info = QLabel(
            "• API키·모델·이미지수·저장폴더 → 블로그 원고생성기 탭 값 사용\n"
            "• 출력폴더·썸네일·작업전/후·동영상 설정 → 블로그발행(패키징) 탭 값 사용\n"
            "• 4단계 자동 실행: 분석 → 생성 → 패키징 → 동영상\n"
            "• 썸네일/작업전/작업후/원고/동영상 5개 필수 — 하나라도 없으면 해당 키워드 건너뜀\n"
            "• [포장이사] 이미지 재가공 ON → 사진/포장이사 재가공용/ 폴더 사용 (원본 보존 + 매번 SEO-회피용 변형)"
        )
        info.setStyleSheet("color: #8b949e; padding: 6px; background: #161b22; border-radius: 4px;")
        info.setWordWrap(True)
        f.addRow("", info)

        lv.addWidget(g)

        one_btn_row = QHBoxLayout()
        self.one_btn = QPushButton("🚀 원큐 실행")
        self.one_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 14px; font-weight: bold; padding: 14px; border-radius: 6px; border: none;")
        self.one_btn.clicked.connect(self._on_one_shot)
        one_btn_row.addWidget(self.one_btn, 3)
        self.one_stop_btn = QPushButton("중지")
        self.one_stop_btn.setEnabled(False)
        self.one_stop_btn.setStyleSheet("background-color: #636e72; color: white; font-size: 13px; font-weight: bold; padding: 14px; border-radius: 6px; border: none;")
        self.one_stop_btn.clicked.connect(self._on_one_shot_stop)
        one_btn_row.addWidget(self.one_stop_btn, 1)
        lv.addLayout(one_btn_row)
        self._one_stop_flag = False

        self.one_status = QLabel("대기 중")
        self.one_status.setStyleSheet("color: #8b949e; padding: 8px; font-size: 12px;")
        lv.addWidget(self.one_status)

        # 카운트다운 progress bar — 다음 발행까지 대기 중일 때만 표시
        self.one_wait_bar = QProgressBar()
        self.one_wait_bar.setRange(0, 100)
        self.one_wait_bar.setValue(0)
        self.one_wait_bar.setTextVisible(True)
        self.one_wait_bar.setFormat("대기 없음 (다음 키워드 사이 카운트다운 표시)")
        self.one_wait_bar.setVisible(True)
        self.one_wait_bar.setStyleSheet(
            "QProgressBar { background: #161b22; border: 1px solid #30363d; "
            "border-radius: 4px; text-align: center; color: #c9d1d9; "
            "padding: 2px; min-height: 28px; font-size: 12px; }"
            "QProgressBar::chunk { background: #1f6feb; border-radius: 3px; }"
        )
        lv.addWidget(self.one_wait_bar)

        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("실행 로그"))
        self.one_log = QTextEdit()
        self.one_log.setReadOnly(True)
        self.one_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.one_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([360, 640])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    # ═══════ 원큐(블로그) 탭 ═══════
    def _build_one_shot_blog_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        g = QGroupBox("원큐(블로그) — 분석 → 원고 생성 → 패키징 → 동영상 → 블로그 발행")
        f = QFormLayout(g); f.setLabelAlignment(Qt.AlignRight)

        self.oneb_template = QComboBox()
        self.oneb_template.addItems(TEMPLATE_LIST)
        _tpl = self._cfg('GENERATOR', 'template', '청소')
        _ti = self.oneb_template.findText(_tpl)
        if _ti >= 0:
            self.oneb_template.setCurrentIndex(_ti)
        self.oneb_template.currentTextChanged.connect(self._on_template_changed)
        f.addRow("템플릿", self.oneb_template)

        self.oneb_keyword = QTextEdit()
        self.oneb_keyword.setPlaceholderText("예:\n천안입주청소\n부산입주청소\n(한 줄에 하나씩 - 위에서 아래로 순차 실행)")
        self.oneb_keyword.setMaximumHeight(90)
        f.addRow("키워드", self.oneb_keyword)

        self.oneb_count = QSpinBox()
        self.oneb_count.setRange(3, 20)
        self.oneb_count.setValue(5)
        f.addRow("검색 개수", self.oneb_count)

        # 이미지 수 (원큐 자체)
        self.oneb_img_count = QSpinBox()
        self.oneb_img_count.setRange(0, 30)
        self.oneb_img_count.setValue(int(self._cfg('POSTING', 'oneb_img_count', '18')))
        f.addRow("이미지 수", self.oneb_img_count)

        self.oneb_brand = QComboBox()
        self.oneb_brand.addItems(['새집느낌', '가족사랑클린', '새집환경365'])
        saved_brand = self._cfg('GENERATOR', 'blog_brand', '새집느낌')
        if saved_brand:
            idx = self.oneb_brand.findText(saved_brand)
            if idx >= 0:
                self.oneb_brand.setCurrentIndex(idx)
        self.oneb_brand.currentTextChanged.connect(self._on_brand_changed)
        f.addRow("업체명", self.oneb_brand)

        vp_row = QHBoxLayout()
        self.oneb_vp_group = QButtonGroup(self)
        rb_customer = QRadioButton("고객")
        rb_company = QRadioButton("업체")
        rb_customer.setChecked(True)
        self.oneb_vp_group.addButton(rb_customer, 0)
        self.oneb_vp_group.addButton(rb_company, 1)
        vp_row.addWidget(rb_customer)
        vp_row.addWidget(rb_company)
        vp_row.addStretch()
        f.addRow("관점", vp_row)

        self.oneb_draft_chk = QCheckBox("임시저장으로 처리 (발행 대신 저장 버튼 — 테스트용)")
        self.oneb_draft_chk.setChecked(True)
        self.oneb_draft_chk.setStyleSheet("color: #fdcb6e;")
        f.addRow("발행 모드", self.oneb_draft_chk)

        # 키워드 사이 발행 딜레이 (원큐 자체)
        self.oneb_delay = QSpinBox()
        self.oneb_delay.setRange(0, 86400)
        self.oneb_delay.setValue(int(self._cfg('POSTING', 'oneb_delay', '60')))
        self.oneb_delay.setSuffix(" 초")
        self.oneb_delay.setFixedWidth(120)
        f.addRow("발행 딜레이 (키워드 사이)", self.oneb_delay)

        # 포장이사 전용 — 이미지 재가공 (원본 보존하면서 매번 다른 변형값으로 사용)
        self.oneb_reprocess_chk = QCheckBox("이미지 재가공 (원본 유지 + 매번 다른 변형값)")
        self.oneb_reprocess_chk.setChecked(self._cfg('POSTING', 'oneb_reprocess', '0') == '1')
        self.oneb_reprocess_chk.setStyleSheet("color: #74b9ff;")
        self.oneb_reprocess_chk.toggled.connect(
            lambda v: self._set_cfg('POSTING', 'oneb_reprocess', '1' if v else '0') or self._save_config_file())
        self.oneb_reprocess_row_label = QLabel("이미지 재가공")
        f.addRow(self.oneb_reprocess_row_label, self.oneb_reprocess_chk)
        # 포장이사 템플릿일 때만 표시
        self._update_oneb_reprocess_visibility()
        self.oneb_template.currentTextChanged.connect(lambda _: self._update_oneb_reprocess_visibility())

        info = QLabel(
            "• API키·모델·이미지수·저장폴더 → 블로그 원고생성기 탭 값 사용\n"
            "• 출력폴더·썸네일·작업전/후·동영상 설정 → 블로그발행(패키징) 탭 값 사용\n"
            "• 계정·발행설정 → 블로그발행(자동) 탭 값 사용\n"
            "• 발행 딜레이는 위 값 사용 (원큐 자체)\n"
            "• 5단계 자동 실행: 분석 → 생성 → 패키징 → 동영상 → 블로그 발행\n"
            "• 키워드별로 발행까지 이어서 실행 (계정은 키워드 순서대로 순환)\n"
            "• [포장이사] 이미지 재가공 ON → 사진/포장이사 재가공용/ 폴더 사용 (원본 보존 + 매번 SEO-회피용 변형)"
        )
        info.setStyleSheet("color: #8b949e; padding: 6px; background: #161b22; border-radius: 4px;")
        info.setWordWrap(True)
        f.addRow("", info)

        lv.addWidget(g)

        btn_row = QHBoxLayout()
        self.oneb_btn = QPushButton("🚀 원큐(블로그) 실행")
        self.oneb_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 14px; font-weight: bold; padding: 14px; border-radius: 6px; border: none;")
        self.oneb_btn.clicked.connect(self._on_one_shot_blog)
        btn_row.addWidget(self.oneb_btn, 3)
        self.oneb_stop_btn = QPushButton("중지")
        self.oneb_stop_btn.setEnabled(False)
        self.oneb_stop_btn.setStyleSheet("background-color: #636e72; color: white; font-size: 13px; font-weight: bold; padding: 14px; border-radius: 6px; border: none;")
        self.oneb_stop_btn.clicked.connect(self._on_one_shot_blog_stop)
        btn_row.addWidget(self.oneb_stop_btn, 1)
        lv.addLayout(btn_row)
        self._oneb_stop_flag = False

        self.oneb_status = QLabel("대기 중")
        self.oneb_status.setStyleSheet("color: #8b949e; padding: 8px; font-size: 12px;")
        lv.addWidget(self.oneb_status)

        # 카운트다운 progress bar
        self.oneb_wait_bar = QProgressBar()
        self.oneb_wait_bar.setRange(0, 100)
        self.oneb_wait_bar.setValue(0)
        self.oneb_wait_bar.setTextVisible(True)
        self.oneb_wait_bar.setFormat("대기 없음 (다음 키워드 사이 카운트다운 표시)")
        self.oneb_wait_bar.setVisible(True)
        self.oneb_wait_bar.setStyleSheet(
            "QProgressBar { background: #161b22; border: 1px solid #30363d; "
            "border-radius: 4px; text-align: center; color: #c9d1d9; "
            "padding: 2px; min-height: 28px; font-size: 12px; }"
            "QProgressBar::chunk { background: #1f6feb; border-radius: 3px; }"
        )
        lv.addWidget(self.oneb_wait_bar)

        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("실행 로그"))
        self.oneb_log = QTextEdit()
        self.oneb_log.setReadOnly(True)
        self.oneb_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.oneb_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([360, 640])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    # ═══════ 원큐(카페) 탭 ═══════
    def _build_one_shot_cafe_tab(self):
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)

        g = QGroupBox("원큐(카페) — 카페 원고 생성 → 카페 발행")
        f = QFormLayout(g); f.setLabelAlignment(Qt.AlignRight)

        self.onec_template = QComboBox()
        self.onec_template.addItems(TEMPLATE_LIST)
        _tpl = self._cfg('GENERATOR', 'template', '청소')
        _ti = self.onec_template.findText(_tpl)
        if _ti >= 0:
            self.onec_template.setCurrentIndex(_ti)
        self.onec_template.currentTextChanged.connect(self._on_template_changed)
        f.addRow("템플릿", self.onec_template)

        self.onec_keyword = QTextEdit()
        self.onec_keyword.setPlaceholderText("예:\n천안입주청소\n부산입주청소\n(한 줄에 하나씩 - 위에서 아래로 순차 실행)")
        self.onec_keyword.setMaximumHeight(90)
        f.addRow("키워드", self.onec_keyword)

        self.onec_img_count = QSpinBox()
        self.onec_img_count.setRange(0, 30)
        self.onec_img_count.setValue(9)
        f.addRow("이미지 수", self.onec_img_count)

        # 발행 업체 — 카페 발행 탭과 독립적으로 운영
        self.onec_brand = QComboBox()
        self.onec_brand.addItems(['새집느낌', '가족사랑클린', '카드뉴스(정보성)'])
        _ocb = self._cfg('CAFE', 'onec_brand', '새집느낌') or '새집느낌'
        _oci = self.onec_brand.findText(_ocb)
        self.onec_brand.setCurrentIndex(_oci if _oci >= 0 else 0)
        self.onec_brand.setFixedWidth(160)
        self.onec_brand.currentTextChanged.connect(self._on_onec_brand_changed)
        f.addRow("발행 업체", self.onec_brand)

        # 작성 관점 — 고객 후기 vs 업체 소개 톤 분기
        onec_vp_row = QHBoxLayout()
        self.onec_vp_group = QButtonGroup()
        _onec_vp = (self._cfg('CAFE', 'onec_viewpoint', '고객') or '고객').strip()
        _onec_rb1 = QRadioButton("고객 관점")
        _onec_rb2 = QRadioButton("업체 관점")
        if _onec_vp == '업체':
            _onec_rb2.setChecked(True)
        else:
            _onec_rb1.setChecked(True)
        self.onec_vp_group.addButton(_onec_rb1, 0)
        self.onec_vp_group.addButton(_onec_rb2, 1)
        onec_vp_row.addWidget(_onec_rb1); onec_vp_row.addWidget(_onec_rb2)
        self.onec_vp_group.buttonClicked.connect(
            lambda btn: self._on_onec_viewpoint_changed('업체' if btn is _onec_rb2 else '고객'))
        f.addRow("작성 관점", onec_vp_row)

        # 키워드 사이 발행 딜레이 (원큐 자체)
        self.onec_delay = QSpinBox()
        self.onec_delay.setRange(0, 86400)
        self.onec_delay.setValue(int(self._cfg('POSTING', 'onec_delay', '60')))
        self.onec_delay.setSuffix(" 초")
        self.onec_delay.setFixedWidth(120)
        f.addRow("발행 딜레이 (키워드 사이)", self.onec_delay)

        info = QLabel(
            "• 모델·API키·사진폴더 → 카페 원고생성기 탭 값 사용\n"
            "• 계정·카페목록·발행설정 → 카페 발행(자동) 탭 값 사용\n"
            "• 발행 딜레이는 위 값 사용 (원큐 자체)\n"
            "• 2단계 자동 실행: 카페 원고 생성 → 카페 발행\n"
            "• 키워드별로 발행까지 이어서 실행 (계정·카페 모두 순서대로 순환)"
        )
        info.setStyleSheet("color: #8b949e; padding: 6px; background: #161b22; border-radius: 4px;")
        info.setWordWrap(True)
        f.addRow("", info)

        lv.addWidget(g)

        btn_row = QHBoxLayout()
        self.onec_btn = QPushButton("🚀 원큐(카페) 실행")
        self.onec_btn.setStyleSheet("background-color: #e94560; color: white; font-size: 14px; font-weight: bold; padding: 14px; border-radius: 6px; border: none;")
        self.onec_btn.clicked.connect(self._on_one_shot_cafe)
        btn_row.addWidget(self.onec_btn, 3)
        self.onec_stop_btn = QPushButton("중지")
        self.onec_stop_btn.setEnabled(False)
        self.onec_stop_btn.setStyleSheet("background-color: #636e72; color: white; font-size: 13px; font-weight: bold; padding: 14px; border-radius: 6px; border: none;")
        self.onec_stop_btn.clicked.connect(self._on_one_shot_cafe_stop)
        btn_row.addWidget(self.onec_stop_btn, 1)
        lv.addLayout(btn_row)
        self._onec_stop_flag = False

        self.onec_status = QLabel("대기 중")
        self.onec_status.setStyleSheet("color: #8b949e; padding: 8px; font-size: 12px;")
        lv.addWidget(self.onec_status)

        # 카운트다운 progress bar
        self.onec_wait_bar = QProgressBar()
        self.onec_wait_bar.setRange(0, 100)
        self.onec_wait_bar.setValue(0)
        self.onec_wait_bar.setTextVisible(True)
        self.onec_wait_bar.setFormat("대기 없음 (다음 키워드 사이 카운트다운 표시)")
        self.onec_wait_bar.setVisible(True)
        self.onec_wait_bar.setStyleSheet(
            "QProgressBar { background: #161b22; border: 1px solid #30363d; "
            "border-radius: 4px; text-align: center; color: #c9d1d9; "
            "padding: 2px; min-height: 28px; font-size: 12px; }"
            "QProgressBar::chunk { background: #1f6feb; border-radius: 3px; }"
        )
        lv.addWidget(self.onec_wait_bar)

        lv.addStretch()

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("실행 로그"))
        self.onec_log = QTextEdit()
        self.onec_log.setReadOnly(True)
        self.onec_log.setFont(QFont("Consolas", 9))
        rv.addWidget(self.onec_log)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([360, 640])

        lay = QVBoxLayout(tab)
        lay.addWidget(splitter)
        return tab

    def _open_pack_folder_dialog(self):
        dlg = CafeFolderDialog(
            self,
            thumb_val=self.bpub_thumb_dir.text(),
            before_vals=[e.text() for e in self.bpub_before_dirs],
            after_vals=[e.text() for e in self.bpub_after_dirs],
        )
        if dlg.exec() == QDialog.Accepted:
            thumb, before_vals, after_vals = dlg.get_values()
            self.bpub_thumb_dir.setText(thumb)
            for e, val in zip(self.bpub_before_dirs, before_vals):
                e.setText(val)
            for e, val in zip(self.bpub_after_dirs, after_vals):
                e.setText(val)
            self._on_save_pack_folders()

    def _on_save_pack_folders(self):
        """블로그발행(패키징) 탭의 폴더 경로(출력/썸네일/작업전×3/작업후×3) 저장 (템플릿별 분리).
        청소용 사진 폴더는 새집느낌/가족사랑클린 두 섹션 중 하나만 사용 — 포장이사 이미지
        템플릿은 썸네일 스타일이지 청소 사진 폴더와 무관하므로 새집느낌 섹션으로 폴백."""
        # 출력 폴더는 템플릿 무관 공용
        self._set_cfg('BLOG', 'package_output', self.bpub_output.text().strip())
        # 나머지는 현재 선택된 템플릿 섹션에 저장
        template = getattr(self, '_current_img_template', None) or self._cfg('IMAGE', 'template', '새집느낌')
        # 포장이사 이미지 템플릿이면 청소 사진 폴더는 새집느낌 섹션으로 강제 (혼동 방지)
        if template == '포장이사':
            template = '새집느낌'
        section = self._img_section_for(template)
        self._set_cfg(section, 'pack_thumb_dir', self.bpub_thumb_dir.text().strip())
        for i, e in enumerate(self.bpub_before_dirs, 1):
            self._set_cfg(section, f'pack_before_dir_{i}', e.text().strip())
        for i, e in enumerate(self.bpub_after_dirs, 1):
            self._set_cfg(section, f'pack_after_dir_{i}', e.text().strip())
        # 새집느낌일 때만 구형 BLOG 섹션도 함께 업데이트 (하위 호환)
        if template == '새집느낌':
            self._set_cfg('BLOG', 'pack_thumb_dir', self.bpub_thumb_dir.text().strip())
            for i, e in enumerate(self.bpub_before_dirs, 1):
                self._set_cfg('BLOG', f'pack_before_dir_{i}', e.text().strip())
            for i, e in enumerate(self.bpub_after_dirs, 1):
                self._set_cfg('BLOG', f'pack_after_dir_{i}', e.text().strip())
            self._set_cfg('BLOG', 'pack_before_dir', self.bpub_before_dirs[0].text().strip())
            self._set_cfg('BLOG', 'pack_after_dir', self.bpub_after_dirs[0].text().strip())
        self._save_config_file()
        self.pack_folder_status.setText(f"[청소/{template}] 저장됨")
        self.pack_folder_status.setStyleSheet("color: #00b894;")
        QTimer.singleShot(2000, lambda: self.pack_folder_status.setText(""))

    def _on_template_changed(self, new_value):
        """5개 탭의 템플릿 드롭다운 동기화 + 템플릿별 이미지 수 자동 저장/로드.
        + img_template (썸네일 스타일) 도 같이 동기화 — 청소/포장이사 혼동 방지."""
        # 0) 직전 템플릿의 이미지 수 저장 (있으면)
        prev = self._cfg('GENERATOR', 'template', '청소')
        if prev and prev != new_value:
            try:
                self._save_template_settings(prev)
            except Exception:
                pass

        # 1) 새 템플릿 저장
        self._set_cfg('GENERATOR', 'template', new_value)
        self._save_config_file()

        # 2) 다른 탭의 템플릿 드롭다운 동기화
        for attr in ('cafe_template', 'blog_template', 'one_template', 'oneb_template', 'onec_template'):
            cb = getattr(self, attr, None)
            if cb is None:
                continue
            if cb.currentText() == new_value:
                continue
            cb.blockSignals(True)
            idx = cb.findText(new_value)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)

        # 2.5) img_template (썸네일 스타일) 자동 매칭 — 패키징 템플릿과 일관성 보장
        #   - 패키징 = 청소 → 썸네일 스타일도 청소 호환(새집느낌/가족사랑클린)으로
        #     ※ 현재 img_template 가 '포장이사' 면 강제로 '새집느낌' 으로 전환
        #   - 패키징 = 포장이사 → 썸네일 스타일도 무조건 '포장이사'
        target_img_tpl = None
        if new_value == '포장이사':
            target_img_tpl = '포장이사'
        elif new_value == '청소':
            cur_img_tpl = getattr(self, '_current_img_template', '새집느낌') or '새집느낌'
            if cur_img_tpl == '포장이사':
                target_img_tpl = '새집느낌'
        if target_img_tpl:
            img_combo = getattr(self, 'img_template', None)
            if img_combo is not None and img_combo.currentText() != target_img_tpl:
                # _on_img_template_changed 가 정상 동작하도록 signal 살림
                idx = img_combo.findText(target_img_tpl)
                if idx >= 0:
                    img_combo.setCurrentIndex(idx)
            else:
                # combo 가 없거나 이미 일치 — 내부 상태만 업데이트
                self._current_img_template = target_img_tpl
                self._set_cfg('IMAGE', 'template', target_img_tpl)
                self._save_config_file()

        # 3) 새 템플릿의 이미지 수 자동 로드
        try:
            self._load_template_settings(new_value)
        except Exception:
            pass

    def _save_template_settings(self, template: str):
        """현재 UI의 이미지 수들을 [TEMPLATE_{template}] 섹션에 저장."""
        section = f'TEMPLATE_{template}'
        pairs = [
            ('cafe_img_count', getattr(self, 'cafe_img_count', None)),
            ('blog_img_count', getattr(self, 'blog_img_count', None)),
            ('one_img_count', getattr(self, 'one_img_count', None)),
            ('oneb_img_count', getattr(self, 'oneb_img_count', None)),
            ('onec_img_count', getattr(self, 'onec_img_count', None)),
        ]
        for key, w in pairs:
            if w is not None:
                self._set_cfg(section, key, str(w.value()))
        self._save_config_file()

    def _load_template_settings(self, template: str):
        """[TEMPLATE_{template}] 섹션 값을 UI에 로드. 없으면 디폴트 유지."""
        section = f'TEMPLATE_{template}'
        # 기본값 — 청소면 9, 포장이사면 19 (외 기본 9/18)
        if template == '포장이사':
            defaults = {'cafe_img_count': 19, 'blog_img_count': 19,
                        'one_img_count': 19, 'oneb_img_count': 19, 'onec_img_count': 19}
        else:
            defaults = {'cafe_img_count': 9, 'blog_img_count': 18,
                        'one_img_count': 18, 'oneb_img_count': 18, 'onec_img_count': 9}
        for key, default in defaults.items():
            w = getattr(self, key, None)
            if w is None:
                continue
            try:
                val = int(self._cfg(section, key, str(default)))
                w.blockSignals(True)
                w.setValue(val)
                w.blockSignals(False)
            except Exception:
                pass

    def _on_save_blog_api_key(self):
        """블로그 원고생성기 탭의 Claude API 키 + 모델 저장"""
        self._set_cfg('GENERATOR', 'claude_api_key', self.blog_key.text().strip())
        self._set_cfg('GENERATOR', 'blog_model', self.blog_model.currentText())
        self._save_config_file()
        self.blog_key_status.setText("저장됨")
        self.blog_key_status.setStyleSheet("color: #00b894;")
        QTimer.singleShot(2000, lambda: self.blog_key_status.setText(""))

    def _on_save_cafe_api_keys(self):
        """카페 원고생성기 탭의 OpenAI/Claude API 키 + 모델 저장"""
        self._set_cfg('GENERATOR', 'openai_api_key', self.cafe_oai_key.text().strip())
        self._set_cfg('GENERATOR', 'claude_api_key', self.cafe_claude_key.text().strip())
        self._set_cfg('GENERATOR', 'cafe_model', self.cafe_model.currentText())
        self._save_config_file()
        self.cafe_key_status.setText("저장됨")
        self.cafe_key_status.setStyleSheet("color: #00b894;")
        QTimer.singleShot(2000, lambda: self.cafe_key_status.setText(""))

    def _open_cafe_folder_dialog(self):
        dlg = CafeFolderDialog(
            self,
            thumb_val=self.cafe_thumb_dir.text(),
            before_vals=[e.text() for e in self.cafe_before_dirs],
            after_vals=[e.text() for e in self.cafe_after_dirs],
        )
        if dlg.exec() == QDialog.Accepted:
            thumb, before_vals, after_vals = dlg.get_values()
            self.cafe_thumb_dir.setText(thumb)
            for e, val in zip(self.cafe_before_dirs, before_vals):
                e.setText(val)
            for e, val in zip(self.cafe_after_dirs, after_vals):
                e.setText(val)
            self._on_save_cafe_folders()

    def _open_packing_folder_dialog(self):
        """포장이사 전용 사진 폴더 — 단일 폴더 입력. 카페·블로그 양쪽 탭에서 호출 가능 (config 공유)."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        # 현재값 — UI 위젯이 있으면 그 값, 없으면 config에서
        _default = os.path.join(BASE_DIR, '사진', '포장이사')
        cur_path = self._cfg('PACKING', 'photo_dir', _default)
        if hasattr(self, 'cafe_packing_dir'):
            cur_path = self.cafe_packing_dir.text() or cur_path

        dlg = QDialog(self)
        dlg.setWindowTitle("📦 사진 폴더 설정 (포장이사)")
        dlg.setMinimumWidth(560)
        lay = QVBoxLayout(dlg)

        info = QLabel(
            "<b>포장이사 사진 폴더</b> — 단일 폴더 1개만 사용<br>"
            "• 폴더 안에 <b>1.png, 2.png, 3.png ...</b> 순서대로 사진 배치<br>"
            "• 또는 임의 파일명도 OK (이름순 정렬해서 사용)<br>"
            "• 작업 전·후 분리 없음 (단일 시퀀스 흐름)<br>"
            "• 카페 원고생성기·블로그 패키징 두 탭이 같은 폴더 공유"
        )
        info.setStyleSheet("padding: 8px; background: #1b1f24; border-radius: 4px; color: #c9d1d9;")
        info.setWordWrap(True)
        lay.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("사진 폴더:"))
        entry = QLineEdit(cur_path)
        row.addWidget(entry, 1)
        btn_pick = QPushButton("폴더…")
        btn_pick.clicked.connect(lambda: self._pick_folder(entry))
        row.addWidget(btn_pick)
        lay.addLayout(row)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(dlg.accept); bbox.rejected.connect(dlg.reject)
        lay.addWidget(bbox)

        if dlg.exec() == QDialog.Accepted:
            path = entry.text().strip()
            if hasattr(self, 'cafe_packing_dir'):
                self.cafe_packing_dir.setText(path)
            self._set_cfg('PACKING', 'photo_dir', path)
            self._save_config_file()
            if path and not os.path.isdir(path):
                try:
                    os.makedirs(path, exist_ok=True)
                except Exception:
                    pass

    def _on_save_cafe_folders(self):
        """카페 원고생성기 탭의 사진 폴더(썸네일/작업전×3/작업후×3) 저장 (템플릿별 분리).
        포장이사 이미지 템플릿이면 청소 사진 폴더는 새집느낌 섹션으로 강제 (혼동 방지)."""
        template = getattr(self, '_current_img_template', None) or self._cfg('IMAGE', 'template', '새집느낌')
        if template == '포장이사':
            template = '새집느낌'
        section = self._img_section_for(template)
        self._set_cfg(section, 'cafe_thumb_dir', self.cafe_thumb_dir.text().strip())
        for i, e in enumerate(self.cafe_before_dirs, 1):
            self._set_cfg(section, f'cafe_before_dir_{i}', e.text().strip())
        for i, e in enumerate(self.cafe_after_dirs, 1):
            self._set_cfg(section, f'cafe_after_dir_{i}', e.text().strip())
        # 새집느낌일 때만 구형 GENERATOR 섹션도 함께 업데이트 (하위 호환)
        if template == '새집느낌':
            self._set_cfg('GENERATOR', 'cafe_thumb_dir', self.cafe_thumb_dir.text().strip())
            for i, e in enumerate(self.cafe_before_dirs, 1):
                self._set_cfg('GENERATOR', f'cafe_before_dir_{i}', e.text().strip())
            for i, e in enumerate(self.cafe_after_dirs, 1):
                self._set_cfg('GENERATOR', f'cafe_after_dir_{i}', e.text().strip())
            self._set_cfg('GENERATOR', 'cafe_before_dir', self.cafe_before_dirs[0].text().strip())
            self._set_cfg('GENERATOR', 'cafe_after_dir', self.cafe_after_dirs[0].text().strip())
        self._save_config_file()
        self.cafe_folder_status.setText(f"[청소/{template}] 저장됨")
        self.cafe_folder_status.setStyleSheet("color: #00b894;")
        QTimer.singleShot(2000, lambda: self.cafe_folder_status.setText(""))

    def _on_save_img_folders(self):
        """썸네일 생성 탭의 배경/저장 폴더 경로를 현재 선택된 템플릿 섹션에 저장"""
        template = self.img_template.currentText()
        section = self._img_section_for(template)
        self._set_cfg(section, 'bg_path', self.img_bg.text().strip())
        self._set_cfg(section, 'output_path', self.img_out.text().strip())
        self._save_config_file()
        self.img_folder_status.setText(f"[{template}] 저장됨")
        self.img_folder_status.setStyleSheet("color: #00b894;")
        QTimer.singleShot(2000, lambda: self.img_folder_status.setText(""))

    def _on_save_cafe_postings_dir(self):
        """카페 발행 원고 폴더 경로 저장"""
        path = self.pub_postings_dir.text().strip()
        self._set_cfg('POSTING', 'cafe_postings_dir', path)
        self._save_config_file()
        QMessageBox.information(self, "저장", f"카페 원고 폴더 경로가 저장됐습니다\n{path}")
        self._refresh_postings()

    def _on_save_blog_postings_dir(self):
        """블로그 발행 원고 폴더 경로 저장"""
        path = self.bpub_postings_dir.text().strip()
        self._set_cfg('POSTING', 'blog_postings_dir', path)
        self._save_config_file()
        QMessageBox.information(self, "저장", f"블로그 원고 폴더 경로가 저장됐습니다\n{path}")
        self._refresh_blog_postings()

    # ── 발행 설정 다이얼로그 (공용) ──
    def _open_publish_settings_dialog(self, section, title):
        """section='POSTING' (카페) or 'BLOG' (블로그)로 공용"""
        from PySide6.QtWidgets import QDialog
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(420, 340)
        lay = QVBoxLayout(dlg)
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignRight); form.setSpacing(8)

        sp_intra = QSpinBox(); sp_intra.setRange(0, 99999); sp_intra.setSuffix("초")
        sp_intra.setValue(int(self._cfg(section, 'intra_account_interval', '2500')))
        form.addRow("계정 내 업로드 간격", sp_intra)

        sp_switch = QSpinBox(); sp_switch.setRange(0, 99999); sp_switch.setSuffix("초")
        sp_switch.setValue(int(self._cfg(section, 'account_switch_wait', '2500')))
        form.addRow("계정 전환 대기", sp_switch)

        sp_per = QSpinBox(); sp_per.setRange(1, 100); sp_per.setSuffix("개")
        sp_per.setValue(int(self._cfg(section, 'uploads_per_account', '1')))
        form.addRow("계정당 업로드 수", sp_per)

        chk_tether = QCheckBox("사용 (갤럭시 USB 연결 시 IP 자동 변경)")
        chk_tether.setChecked(self._cfg(section, 'tethering_enabled', '0') == '1')
        chk_tether.setStyleSheet("color: #fdcb6e;")
        form.addRow("테더링", chk_tether)

        chk_secret = QCheckBox("사용 (쿠키/캐시 공유 안 함 — 계정 분리 강화)")
        chk_secret.setChecked(self._cfg(section, 'secret_mode', '0') == '1')
        chk_secret.setStyleSheet("color: #74b9ff;")
        form.addRow("시크릿 모드", chk_secret)

        # IP 확인
        ip_row = QHBoxLayout()
        ip_label = QLabel("-")
        ip_label.setStyleSheet("color: #8b949e;")
        ip_row.addWidget(ip_label, 1)
        btn_ip = QPushButton("IP 확인"); btn_ip.setFixedWidth(80)
        def check_ip():
            ip_label.setText("확인 중...")
            def do():
                try:
                    from core.tethering import get_current_ip
                    ip = get_current_ip()
                except Exception as e:
                    ip = f'오류: {e}'
                self._bridge.set_text.emit(ip_label, ip)
            threading.Thread(target=do, daemon=True).start()
        btn_ip.clicked.connect(check_ip)
        ip_row.addWidget(btn_ip)
        form.addRow("현재 IP", ip_row)

        lay.addLayout(form)

        # 버튼
        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 저장")
        btn_save.setStyleSheet("background-color: #6c5ce7; color: white; font-weight: bold; padding: 8px 20px; border-radius: 4px; border: none;")
        def save():
            self._set_cfg(section, 'intra_account_interval', str(sp_intra.value()))
            self._set_cfg(section, 'account_switch_wait', str(sp_switch.value()))
            self._set_cfg(section, 'uploads_per_account', str(sp_per.value()))
            self._set_cfg(section, 'tethering_enabled', '1' if chk_tether.isChecked() else '0')
            self._set_cfg(section, 'secret_mode', '1' if chk_secret.isChecked() else '0')
            self._save_config_file()
            QMessageBox.information(dlg, "저장", "발행 설정이 저장됐습니다")
            dlg.accept()
        btn_save.clicked.connect(save)
        btn_row.addWidget(btn_save)

        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # 자동으로 IP 한번 확인
        check_ip()

        dlg.exec()

    def _on_open_cafe_settings_dialog(self):
        self._open_publish_settings_dialog('POSTING', '카페 발행 설정')

    def _on_open_blog_settings_dialog(self):
        self._open_publish_settings_dialog('BLOG', '블로그 발행 설정')

    def _auto_generate_thumbnail(self, keyword, output_path, force_template=None):
        """썸네일이 없을 때 자동 생성 — 현재 선택된 템플릿 규칙 적용.
        새집느낌: img_bg(파일/폴더)에서 배경 + 레이어 합성
        가족사랑클린: template/가족사랑클린.png 단독 사용 (배경 미입력이면 자동 폴백)
        포장이사: 사진/포장이사 썸네일 가공 전/ 폴더에서 PNG 골라 키워드 오버레이
        force_template: 호출자가 템플릿을 명시적으로 지정 (img_template UI 무시).
            이 경우 bg_path 도 그 템플릿의 config 섹션에서 읽음.
        성공 시 (output_path, used_bg_path) 반환, 실패 시 (None, 에러메시지)."""
        try:
            cur_img_tpl = self.img_template.currentText() if hasattr(self, 'img_template') else '새집느낌'
            if force_template:
                template = force_template
                if force_template != cur_img_tpl:
                    # img_template UI 가 다른 값이면 bg_path 도 force_template 의 config 에서 읽기
                    bg_raw = self._cfg(self._img_section_for(template), 'bg_path', '')
                else:
                    bg_raw = self.img_bg.text().strip() if hasattr(self, 'img_bg') else ''
            else:
                template = cur_img_tpl
                bg_raw = self.img_bg.text().strip() if hasattr(self, 'img_bg') else ''
            exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')

            # === 가족사랑클린: 배경(파일/폴더/기본템플릿) + 키워드 오버레이 ===
            if template == '가족사랑클린':
                bg_file = None
                # 1) 파일이면 그대로
                if bg_raw and os.path.isfile(bg_raw):
                    bg_file = bg_raw
                # 2) 폴더면 안에서 첫 이미지 꺼내 쓰기 (사용완료 제외)
                elif bg_raw and os.path.isdir(bg_raw):
                    candidates = sorted([os.path.join(bg_raw, f) for f in os.listdir(bg_raw)
                                         if f.lower().endswith(exts) and '사용완료' not in f
                                         and os.path.isfile(os.path.join(bg_raw, f))])
                    if candidates:
                        bg_file = candidates[0]
                # 3) 여전히 못 찾으면 기본 템플릿으로 폴백
                if not bg_file:
                    default_tpl = os.path.join(BASE_DIR, 'template', '가족사랑클린.png')
                    if os.path.isfile(default_tpl):
                        bg_file = default_tpl
                    else:
                        return None, (f"가족사랑클린 배경 없음: {bg_raw or '(경로 미지정)'} "
                                      f"(폴더에 이미지 없거나 기본 템플릿도 없음)")
                opts = self._get_thumb_opts()
                from core.thumbnail import create_thumbnail_family
                os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
                create_thumbnail_family(
                    bg_file, output_path,
                    text=keyword,
                    font_name=opts['font_name'], font_color=opts['font_color'],
                    font_size=opts['font_size1'],
                    text_y=opts['text1_y'],
                    outline=opts['outline'],
                    offset_x=opts['bg_offset_x'],
                    offset_y=opts['bg_offset_y'],
                )
                return (output_path, bg_file), None

            # === 포장이사: 사진/포장이사 썸네일 가공 전(또는 가공 후)/ 에서 PNG 골라 오버레이 ===
            if template == '포장이사':
                bg_file = None
                # 1) 사용자가 명시한 경로(파일/폴더) 우선
                if bg_raw and os.path.isfile(bg_raw):
                    bg_file = bg_raw
                elif bg_raw and os.path.isdir(bg_raw):
                    candidates = sorted([os.path.join(bg_raw, f) for f in os.listdir(bg_raw)
                                         if f.lower().endswith(exts) and '사용완료' not in f
                                         and os.path.isfile(os.path.join(bg_raw, f))])
                    if candidates:
                        bg_file = candidates[0]
                # 2) 기본 폴백 — 가공 전/ 우선, 비어있으면 가공 후/ (사용완료 제외)
                if not bg_file:
                    pdirs = self._packing_thumb_dirs()
                    import random as _rnd
                    for try_dir in [pdirs['pre'], pdirs['post']]:
                        if os.path.isdir(try_dir):
                            candidates = sorted([os.path.join(try_dir, f) for f in os.listdir(try_dir)
                                                 if f.lower().endswith(exts) and '사용완료' not in f
                                                 and os.path.isfile(os.path.join(try_dir, f))])
                            if candidates:
                                bg_file = _rnd.choice(candidates)
                                break
                # 3) 그래도 없으면 template/포장이사.png 시도
                if not bg_file:
                    default_tpl = os.path.join(BASE_DIR, 'template', '포장이사.png')
                    if os.path.isfile(default_tpl):
                        bg_file = default_tpl
                    else:
                        return None, (f"포장이사 배경 없음: {bg_raw or '(경로 미지정)'} "
                                      f"(가공 전/가공 후 폴더 모두 PNG 없음)")
                opts = self._get_thumb_opts()
                from core.thumbnail import create_thumbnail_family
                os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
                create_thumbnail_family(
                    bg_file, output_path,
                    text=keyword,
                    font_name=opts['font_name'], font_color=opts['font_color'],
                    font_size=opts['font_size1'],
                    text_y=opts['text1_y'],
                    outline=opts['outline'],
                    fit_mode='fit',  # 원본 비율 유지 — 짤림 없음
                    offset_x=opts['bg_offset_x'],
                    offset_y=opts['bg_offset_y'],
                )
                return (output_path, bg_file), None

            # === 새집느낌: 기존 로직 (배경+레이어) ===
            if not bg_raw:
                return None, "배경 경로(img_bg)가 비어있음 - 썸네일 생성 탭에서 배경 설정 후 저장 필요"
            bg_file = None
            if os.path.isfile(bg_raw):
                bg_file = bg_raw
            elif os.path.isdir(bg_raw):
                # 사용완료 제외하고 첫 번째 이미지 사용
                candidates = sorted([os.path.join(bg_raw, f) for f in os.listdir(bg_raw)
                                     if f.lower().endswith(exts) and '사용완료' not in f
                                     and os.path.isfile(os.path.join(bg_raw, f))])
                if not candidates:
                    return None, f"배경 폴더에 이미지 없음: {bg_raw}"
                bg_file = candidates[0]
            else:
                return None, f"배경 경로 존재하지 않음: {bg_raw}"

            opts = self._get_thumb_opts()
            from core.thumbnail import create_thumbnail
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
            create_thumbnail(
                bg_file, output_path,
                bg_offset_x=opts['bg_offset_x'], bg_offset_y=opts['bg_offset_y'],
                bg_end_y=opts['bg_end_y'],
                text1=keyword, text2=opts['text2'],
                font_name=opts['font_name'], font_color=opts['font_color'],
                font_size1=opts['font_size1'], font_size2=opts['font_size2'],
                text1_y=opts['text1_y'], text2_y=opts['text2_y'],
                outline=opts['outline'],
            )
            return (output_path, bg_file), None
        except Exception as e:
            import traceback
            return None, f"{e}\n{traceback.format_exc()[:300]}"

    def _on_save_blog_save_dir(self):
        """블로그 원고생성기 저장 폴더를 config.ini 에 저장"""
        self._set_cfg('GENERATOR', 'blog_save_dir', self.blog_save_dir.text().strip())
        self._save_config_file()
        QMessageBox.information(self, "저장", "원고 저장 폴더 설정이 저장됐습니다")

    @staticmethod
    def _derive_pkg_dir(posting_name, pack_output):
        """원고 폴더 이름으로 패키징 결과 폴더 추정.
        '부산입주청소_원고' → '{pack_output}/부산입주청소/'
        """
        if not pack_output or not os.path.isdir(pack_output):
            return ''
        # 1) 정확 일치
        p = os.path.join(pack_output, posting_name)
        if os.path.isdir(p):
            return p
        # 2) 흔한 접미사 제거
        candidates = [posting_name]
        for suffix in ('_원고', '_manuscript', '_post'):
            if posting_name.endswith(suffix):
                candidates.append(posting_name[:-len(suffix)])
        for c in candidates:
            p = os.path.join(pack_output, c)
            if os.path.isdir(p):
                return p
        # 3) 하위 폴더명 부분 일치
        try:
            for d in sorted(os.listdir(pack_output)):
                full = os.path.join(pack_output, d)
                if os.path.isdir(full) and (d in posting_name or posting_name in d):
                    return full
        except Exception:
            pass
        return ''

    @staticmethod
    def _kw_match(text, keyword):
        """공백/언더스코어/하이픈 무시하고 키워드 포함 여부 확인"""
        import re
        t = re.sub(r'[\s_\-]', '', text.lower())
        k = re.sub(r'[\s_\-]', '', keyword.lower())
        return bool(k) and k in t

    @staticmethod
    def _safe_move(src, dst):
        """shutil.move가 OneDrive/권한 문제로 실패할 수 있어서 copy+remove 폴백"""
        import shutil
        try:
            shutil.move(src, dst)
            return True, None
        except Exception as e1:
            try:
                shutil.copy2(src, dst)
                os.remove(src)
                return True, None
            except Exception as e2:
                return False, f"{e1} / {e2}"

    @staticmethod
    def _consume_source(src, done_dir):
        """사용한 원본 파일 처리:
        1) 사용완료 폴더로 이동/복사+삭제 시도
        2) 이동 실패 시 원본만이라도 삭제
        반환값: 'moved' | 'deleted' | 'failed', 에러 메시지(또는 None)
        """
        import shutil
        dst = os.path.join(done_dir, os.path.basename(src))
        # 1차: shutil.move
        try:
            shutil.move(src, dst)
            return 'moved', None
        except Exception as e1:
            # 2차: copy + remove
            try:
                shutil.copy2(src, dst)
                os.remove(src)
                return 'moved', None
            except Exception as e2:
                # 3차: 최소한 원본 삭제
                try:
                    os.remove(src)
                    return 'deleted', f"사용완료 복사 실패했지만 원본은 삭제함: {e2}"
                except Exception as e3:
                    return 'failed', f"move={e1} / copy={e2} / remove={e3}"

    def _run_packaging_core(self, keyword, output_dir,
                            thumb_dir_manual='',
                            before_dirs_manual=None, after_dirs_manual=None,
                            log_widget=None, stop_check=None,
                            template: str = '청소', image_count: int = 18,
                            packing_dir: str = '',
                            reprocess: bool = False):
        """패키징 핵심 로직.
        - 청소: 작업전/후 분리 풀에서 image_count장 복사
        - 포장이사: packing_dir 단일 폴더에서 image_count장 순차 복사 (이름 1.png/2.png...)
        - reprocess=True (포장이사 전용): 원본 보존, 매번 다른 random 변형값으로 재가공해서 사용
        하나라도 누락되면 None 반환."""
        before_dirs_manual = [d for d in (before_dirs_manual or []) if d]
        after_dirs_manual = [d for d in (after_dirs_manual or []) if d]
        import shutil
        log = log_widget if log_widget is not None else self.bpack_log
        stop = stop_check if stop_check else (lambda: False)

        def llog(msg, color='#00cec9'):
            self._log_to(log, msg, color)

        base = BASE_DIR
        photo_base = os.path.join(base, '사진')
        pkg_dir = os.path.join(output_dir, keyword)
        os.makedirs(pkg_dir, exist_ok=True)
        img_dir = pkg_dir
        exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')

        # ── 포장이사 모드: 원고 탐색 + 단일 폴더에서 image_count장 순차 복사 ──
        if (template or '').strip() == '포장이사':
            llog(f"[포장이사 모드] {image_count}장 + 원고 패키징")

            # 1) 원고 탐색·복사 (청소 모드와 동일 로직)
            if stop(): return None
            llog("[1] 원고 탐색 중...")
            ms_found = False
            search_dirs = [os.path.join(base, '원고'),
                           os.path.join(base, 'postings_blog'),
                           os.path.join(base, 'postings_cafe'), base]
            for search_dir in search_dirs:
                if not os.path.isdir(search_dir):
                    continue
                # 1-1: 직속 .txt 파일 (키워드 매칭)
                for f in os.listdir(search_dir):
                    if f.lower().endswith('.txt') and self._kw_match(os.path.splitext(f)[0], keyword):
                        try:
                            shutil.copy2(os.path.join(search_dir, f),
                                         os.path.join(pkg_dir, f'{keyword}_원고.txt'))
                            llog(f"  원고 발견: {f}")
                            ms_found = True
                            break
                        except Exception:
                            pass
                if ms_found:
                    break
                # 1-2: 하위 폴더 안 원고.txt
                for folder in os.listdir(search_dir):
                    folder_path = os.path.join(search_dir, folder)
                    if os.path.isdir(folder_path) and self._kw_match(folder, keyword):
                        ms_file = os.path.join(folder_path, '원고.txt')
                        if os.path.isfile(ms_file):
                            try:
                                shutil.copy2(ms_file, os.path.join(pkg_dir, f'{keyword}_원고.txt'))
                                llog(f"  원고 발견: {folder}/원고.txt")
                                ms_found = True
                                break
                            except Exception:
                                pass
                if ms_found:
                    break
            if not ms_found:
                llog(f"  [중단] 필수 누락: 원고 ('{keyword}' 포함 파일 없음)", '#ff6b6b')
                return None

            # 2) 사진 복사
            # 재가공 모드면 전용 폴더 '사진/포장이사 재가공용/' 사용 (원본 보존 목적)
            if reprocess:
                packing_dir = os.path.join(BASE_DIR, '사진', '포장이사 재가공용')
                if not os.path.isdir(packing_dir):
                    os.makedirs(packing_dir, exist_ok=True)
                    llog(f"  [중단] 포장이사 재가공용 폴더가 비어있음 — 사진을 넣어주세요: {packing_dir}",
                         '#ff6b6b')
                    return None
            elif not packing_dir:
                packing_dir = self._cfg('PACKING', 'photo_dir',
                                        os.path.join(BASE_DIR, '사진', '포장이사'))
            if not os.path.isdir(packing_dir):
                llog(f"  [중단] 포장이사 사진 폴더 없음: {packing_dir}", '#ff6b6b')
                return None
            # 자연어 정렬 — 1.png, 2.png, ..., 10.png 순서 보장 (알파벳 정렬은 1, 10, 11, 2 식)
            import re as _re
            def _nat_key(s):
                return [int(t) if t.isdigit() else t.lower()
                        for t in _re.split(r'(\d+)', s)]
            files = sorted([f for f in os.listdir(packing_dir)
                            if f.lower().endswith(exts) and os.path.isfile(os.path.join(packing_dir, f))],
                           key=_nat_key)
            if not files:
                llog(f"  [중단] 포장이사 사진 폴더 비어있음: {packing_dir}", '#ff6b6b')
                return None
            llog(f"  [I] 사진 소스: {packing_dir} ({len(files)}장, 1→{len(files)} 순서대로)", '#74b9ff')
            mode_label = "재가공 (원본 유지)" if reprocess else "복사"
            llog(f"[2] 사진 {min(len(files), image_count)}장 {mode_label} 중...")
            used = []
            # 재가공 모드 — 매 이미지마다 변형 옵션 자체를 랜덤화 (강도·테두리 두께/스타일 모두)
            if reprocess:
                try:
                    from core.image_reprocess import reprocess_one, BORDER_STYLES
                except ImportError:
                    llog(f"  [경고] core.image_reprocess 임포트 실패 — 일반 복사로 폴백", '#fdcb6e')
                    reprocess = False
                    BORDER_STYLES = []
                import random as _rnd

            def _make_random_opts():
                """매 이미지마다 색상/회전/리사이즈/노이즈/샤프닝/테두리 강도 랜덤 결정."""
                # 테두리는 항상 ON (최소 6px 보장). 두께/스타일만 랜덤
                border_on = True
                border_min = _rnd.randint(6, 12)  # 얇은 쪽 베이스 (최소 6px 보장)
                border_max = _rnd.randint(20, 40) # 두꺼운 쪽 베이스
                border_style = _rnd.choice(BORDER_STYLES) if BORDER_STYLES else '흰색'
                return {
                    'color': True,
                    'color_pct': _rnd.uniform(1.5, 5.0),       # ±1.5% ~ ±5%
                    'rotate': True,
                    'rotate_deg': _rnd.uniform(0.3, 1.2),      # ±0.3° ~ ±1.2°
                    'resize': True,
                    'resize_min': _rnd.uniform(94, 98),        # 94% ~ 98%
                    'resize_max': _rnd.uniform(102, 106),      # 102% ~ 106%
                    'noise': True,
                    'noise_amp': _rnd.randint(2, 7),           # ±2 ~ ±7
                    'sharpen': True,
                    'sharpen_pct': _rnd.randint(30, 100),      # 30% ~ 100%
                    'border': border_on,
                    'border_min': border_min,
                    'border_max': border_max,
                    'border_style': border_style,
                    'quality': _rnd.randint(85, 95),           # 85 ~ 95
                }

            for i, fname in enumerate(files[:image_count]):
                if stop():
                    return None
                src = os.path.join(packing_dir, fname)
                ext = os.path.splitext(fname)[1]
                # 재가공 모드는 항상 .jpg 로 출력 (image_reprocess가 quality 적용)
                dst_ext = '.jpg' if reprocess else ext
                dst = os.path.join(img_dir, f'{i + 1}{dst_ext}')
                try:
                    if reprocess:
                        opts_i = _make_random_opts()
                        ok = reprocess_one(src, dst, **opts_i)
                        if not ok:
                            # 재가공 실패 시 일반 복사 폴백
                            shutil.copy2(src, os.path.join(img_dir, f'{i + 1}{ext}'))
                        else:
                            # 변형값 요약 로그 (DEBUG)
                            b_info = (f"테두리 {opts_i['border_min']}~{opts_i['border_max']}px "
                                      f"({opts_i['border_style']})") if opts_i['border'] else "테두리 X"
                            llog(f"  [{i+1}] color±{opts_i['color_pct']:.1f}% / "
                                 f"rot±{opts_i['rotate_deg']:.1f}° / "
                                 f"resize {opts_i['resize_min']:.0f}~{opts_i['resize_max']:.0f}% / "
                                 f"noise±{opts_i['noise_amp']} / "
                                 f"sharpen {opts_i['sharpen_pct']}% / {b_info} / q{opts_i['quality']}",
                                 '#8b949e')
                    else:
                        shutil.copy2(src, dst)
                    used.append(src)
                except Exception as e:
                    llog(f"  [실패] {fname}: {e}", '#ff6b6b')
            llog(f"  [I] {len(used)}장 {mode_label} 완료 (1~{len(used)})")

            # 3) 썸네일 — 필수. 다음 순서로 시도:
            #    a) '가공 후/' 에서 'thumb_포장_*' + 키워드 매칭
            #    b) '가공 후(사용완료)/' 에서 동일 (이미 발행된 것 재사용)
            #    c) 자동 생성 (force_template='포장이사' → 베이스 PNG 사용)
            #    d) 모두 실패 → None 반환 (필수 누락)
            thumb_matched = False
            search_thumb_dirs = []
            if thumb_dir_manual:
                search_thumb_dirs.append(thumb_dir_manual)
                done_thumb_alt = thumb_dir_manual.rstrip('/\\') + '(사용완료)'
                search_thumb_dirs.append(done_thumb_alt)
            for td in search_thumb_dirs:
                if not td or not os.path.isdir(td):
                    continue
                for f in sorted(os.listdir(td)):
                    if not f.lower().endswith(exts):
                        continue
                    if not f.startswith('thumb_포장_'):
                        continue
                    if not self._kw_match(os.path.splitext(f)[0], keyword):
                        continue
                    src_thumb = os.path.join(td, f)
                    try:
                        shutil.copy2(src_thumb, os.path.join(img_dir, f'{keyword}.jpg'))
                        llog(f"  [I] 썸네일 매칭: {f} (소스: {os.path.basename(td)})")
                        thumb_matched = True
                    except Exception:
                        pass
                    if thumb_matched:
                        # (사용완료) 폴더에서 가져왔으면 재이동 X — 그대로 둠
                        if not td.rstrip('/\\').endswith('(사용완료)'):
                            done_thumb_dir = td.rstrip('/\\') + '(사용완료)'
                            try:
                                os.makedirs(done_thumb_dir, exist_ok=True)
                                r, e = self._consume_source(src_thumb, done_thumb_dir)
                                if r == 'moved':
                                    llog(f"  → 썸네일 (사용완료) 이동: {f}", '#00b894')
                            except Exception as e:
                                llog(f"  [경고] 썸네일 (사용완료) 이동 실패: {e}", '#fdcb6e')
                        break
                if thumb_matched:
                    break
            if thumb_matched:
                # 발행에 사용된 썸네일에 대응하는 베이스 PNG → '가공 후(사용완료)/' 로 이동
                self._packing_consume_base_for_keyword(keyword, log_fn=llog)
            else:
                # 매칭 썸네일 없으면 자동 생성 — '포장이사' 템플릿 강제
                llog(f"  [포장이사] 썸네일 매칭 없음 → 자동 생성 시도", '#fdcb6e')
                auto_path = os.path.join(img_dir, f'{keyword}.jpg')
                result, err = self._auto_generate_thumbnail(
                    keyword, auto_path, force_template='포장이사')
                if result:
                    out_path, bg_used = result
                    llog(f"  [포장이사] 자동 생성 완료: {os.path.basename(bg_used)} → {keyword}.jpg",
                         '#00b894')
                    thumb_matched = True
                    # 사용된 베이스 PNG → 가공 전(사용완료)/ 직행 (종착)
                    pdirs = self._packing_thumb_dirs()
                    try:
                        os.makedirs(pdirs['pre_done'], exist_ok=True)
                        r, e = self._consume_source(bg_used, pdirs['pre_done'])
                        if r == 'moved':
                            llog(f"  → 베이스 PNG 가공 전(사용완료) 이동: {os.path.basename(bg_used)}",
                                 '#00b894')
                    except Exception:
                        pass
                else:
                    llog(f"  [중단] 필수 누락: 썸네일 (자동 생성 실패: {err})", '#ff6b6b')
                    return None

            # 4) 사용한 사진 처리 — 재가공 모드면 원본 유지, 일반 모드면 (사용완료) 이동
            if used and not reprocess:
                done_dir = packing_dir.rstrip('/\\') + '(사용완료)'
                try:
                    os.makedirs(done_dir, exist_ok=True)
                    moved = 0
                    for src in used:
                        try:
                            shutil.move(src, os.path.join(done_dir, os.path.basename(src)))
                            moved += 1
                        except Exception:
                            try: os.remove(src)
                            except Exception: pass
                    llog(f"  [I] 사용 사진 {moved}장 → (사용완료) 이동")
                except Exception as e:
                    llog(f"  [경고] 사용완료 이동 실패: {e}", '#fdcb6e')
            elif used and reprocess:
                llog(f"  [I] 재가공 모드 — 원본 {len(used)}장 그대로 유지", '#74b9ff')
            return pkg_dir

        # ── 청소 모드 (기본 흐름) ──
        count = 0
        import time  # mtime 표시용 (함수 스코프 안전 임포트)

        # 1. 원고 탐색 — 정확 일치 우선 → 같은 폴더 내 mtime 최신 우선
        # 사용된 원고는 '원고(사용후)/' 로 자동 이동 (기존 원고/ 에서 삭제)
        if stop(): return
        llog("[1] 원고 탐색 중...")
        ms_found = False
        used_manuscript_src = None  # 사용된 원본 경로 (이동 대상)
        search_dirs = [os.path.join(base, '원고'),
                       os.path.join(base, 'postings_blog'),
                       os.path.join(base, 'postings_cafe'), base]

        # 1-1. 정확 일치 ('{키워드}_원고.txt') 먼저 탐색 — 방금 저장한 파일 잡기
        exact_name = f'{keyword}_원고.txt'
        for search_dir in search_dirs:
            if not os.path.isdir(search_dir):
                continue
            exact_path = os.path.join(search_dir, exact_name)
            if os.path.isfile(exact_path):
                shutil.copy2(exact_path, os.path.join(pkg_dir, f'{keyword}_원고.txt'))
                _mtime = time.strftime('%Y-%m-%d %H:%M:%S',
                                        time.localtime(os.path.getmtime(exact_path)))
                llog(f"  원고 발견 (정확 일치): {exact_name} (수정: {_mtime})", '#00b894')
                count += 1
                ms_found = True
                used_manuscript_src = exact_path
                break

        # 1-2. 정확 일치 없으면 fuzzy 매칭 — mtime 최신 우선
        if not ms_found:
            for search_dir in search_dirs:
                if not os.path.isdir(search_dir):
                    continue
                # 키워드 매칭되는 .txt 모두 수집
                matched = []
                for f in os.listdir(search_dir):
                    if not f.endswith('.txt'):
                        continue
                    if not self._kw_match(f, keyword):
                        continue
                    full = os.path.join(search_dir, f)
                    if not os.path.isfile(full):
                        continue
                    try:
                        mtime = os.path.getmtime(full)
                    except OSError:
                        mtime = 0
                    matched.append((mtime, full, f))
                if matched:
                    # 가장 최신 파일 선택
                    matched.sort(reverse=True)  # mtime 내림차순
                    _mt, _path, _name = matched[0]
                    shutil.copy2(_path, os.path.join(pkg_dir, f'{keyword}_원고.txt'))
                    _mtime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_mt))
                    llog(f"  원고 발견 (fuzzy, 최신): {_name} (수정: {_mtime_str}, 후보 {len(matched)}건)",
                         '#74b9ff')
                    if len(matched) > 1:
                        for _, _p, _n in matched[1:]:
                            llog(f"    └ (사용 안 함) {_n}", '#8b949e')
                    count += 1
                    ms_found = True
                    used_manuscript_src = _path
                    break
                # 하위 폴더 안 원고.txt 도 탐색
                for folder in os.listdir(search_dir):
                    folder_path = os.path.join(search_dir, folder)
                    if os.path.isdir(folder_path) and self._kw_match(folder, keyword):
                        ms_file = os.path.join(folder_path, '원고.txt')
                        if os.path.isfile(ms_file):
                            shutil.copy2(ms_file, os.path.join(pkg_dir, f'{keyword}_원고.txt'))
                            _mt_str = time.strftime('%Y-%m-%d %H:%M:%S',
                                                    time.localtime(os.path.getmtime(ms_file)))
                            llog(f"  원고 발견 (하위폴더): {folder}/원고.txt (수정: {_mt_str})", '#74b9ff')
                            count += 1
                            ms_found = True
                            used_manuscript_src = ms_file
                            break
                if ms_found:
                    break
        if not ms_found:
            llog(f"  [중단] 필수 누락: 원고 ('{keyword}' 포함 파일 없음)", '#ff6b6b')
            return None

        # 1-3. 사용된 원고 → '원고(사용후)/' 로 이동 (기존 원고/ 에서 삭제)
        if used_manuscript_src and os.path.isfile(used_manuscript_src):
            try:
                done_ms_dir = os.path.join(base, '원고(사용후)')
                os.makedirs(done_ms_dir, exist_ok=True)
                dst_name = os.path.basename(used_manuscript_src)
                dst_path = os.path.join(done_ms_dir, dst_name)
                # 이미 같은 이름 있으면 타임스탬프 붙여 보존
                if os.path.exists(dst_path):
                    stem, ext = os.path.splitext(dst_name)
                    ts = time.strftime('%Y%m%d_%H%M%S')
                    dst_path = os.path.join(done_ms_dir, f'{stem}_{ts}{ext}')
                shutil.move(used_manuscript_src, dst_path)
                llog(f"  → 원고 사용후 이동: {os.path.basename(dst_path)}", '#00b894')
            except Exception as e:
                llog(f"  [경고] 원고 사용후 이동 실패: {e}", '#fdcb6e')

        # 2. 썸네일 — 템플릿별 완전 분리
        if stop(): return
        llog("[2] 썸네일 탐색 중...")
        thumb_found = False
        _tpl = self.img_template.currentText() if hasattr(self, 'img_template') else '새집느낌'
        # 포장이사 썸네일 스타일은 포장이사 패키징 전용. 청소 패키징에선 새집느낌으로 강제.
        # (template 파라미터는 패키징 템플릿 — '청소' or '포장이사')
        if template != '포장이사' and _tpl == '포장이사':
            llog(f"  [경고] 썸네일 템플릿이 '포장이사'지만 패키징은 '{template}' — 새집느낌으로 폴백",
                 '#fdcb6e')
            _tpl = '새집느낌'
        thumb_search = [thumb_dir_manual] if thumb_dir_manual else []
        if _tpl == '가족사랑클린':
            # 가족사랑클린 전용 폴더만 탐색 (새집느낌 폴더 완전 격리)
            fam_out = self._cfg('IMAGE_FAMILY', 'output_path', '') \
                      or os.path.join(BASE_DIR, '가족사랑클린_썸네일')
            for p in [fam_out, f'{fam_out}(사용완료)']:
                if p and p not in thumb_search:
                    thumb_search.append(p)
            llog(f"  [가족사랑클린] 썸네일 폴더만 탐색: {thumb_search}", '#74b9ff')
        else:
            # 새집느낌 — 기존 기본 폴백 유지
            thumb_search += [os.path.join(photo_base, '썸네일'),
                             os.path.join(photo_base, '썸네일(사용완료)')]
        for td in thumb_search:
            if not td or not os.path.isdir(td):
                continue
            for f in sorted(os.listdir(td)):
                if f.lower().endswith(exts) and self._kw_match(os.path.splitext(f)[0], keyword):
                    src = os.path.join(td, f)
                    ext = os.path.splitext(f)[1]
                    shutil.copy2(src, os.path.join(pkg_dir, f'{keyword}{ext}'))
                    llog(f"  썸네일: {f} → {keyword}{ext}")
                    count += 1
                    thumb_found = True
                    # 원본이 이미 (사용완료) 폴더면 재이동 불필요
                    if td.rstrip('/\\').endswith('(사용완료)'):
                        break
                    done_dir = os.path.join(os.path.dirname(td), f'{os.path.basename(td)}(사용완료)')
                    try:
                        os.makedirs(done_dir, exist_ok=True)
                    except Exception as e:
                        llog(f"    [경고] 사용완료 폴더 생성 실패: {e}", '#fdcb6e')
                    result, err = self._consume_source(src, done_dir)
                    if result == 'moved':
                        llog(f"  → 사용완료 이동")
                    elif result == 'deleted':
                        llog(f"  → 원본만 삭제됨: {err}", '#fdcb6e')
                    else:
                        llog(f"  [경고] 사용완료 이동 실패: {err}", '#fdcb6e')
                    break
            if thumb_found:
                break
        if not thumb_found:
            llog(f"  썸네일 없음 — 자동 생성 시도 중...", '#fdcb6e')
            # pkg_dir에 직접 생성 — 패키징 템플릿이 청소면 _tpl='새집느낌'으로 강제 (위에서 폴백 처리됨)
            auto_path = os.path.join(pkg_dir, f'{keyword}.jpg')
            result, err = self._auto_generate_thumbnail(keyword, auto_path, force_template=_tpl)
            if result:
                out_path, bg_used = result
                llog(f"  자동 생성 완료: {os.path.basename(bg_used)} + '{keyword}' → {keyword}.jpg", '#00b894')
                # 포장이사: 자동 생성으로 사용된 베이스 PNG → 가공 전(사용완료)/ 로 직행
                if _tpl == '포장이사':
                    pdirs = self._packing_thumb_dirs()
                    try:
                        os.makedirs(pdirs['pre_done'], exist_ok=True)
                        r, e = self._consume_source(bg_used, pdirs['pre_done'])
                        if r == 'moved':
                            llog(f"  → 베이스 PNG 가공 전(사용완료) 이동: {os.path.basename(bg_used)}", '#00b894')
                    except Exception:
                        pass
                else:
                    # 새집느낌/가족사랑클린: 사용한 배경을 (사용완료)로 이동 (폴더 모드인 경우만)
                    bg_raw = self.img_bg.text().strip() if hasattr(self, 'img_bg') else ''
                    if bg_raw and os.path.isdir(bg_raw) and os.path.commonpath([bg_raw, bg_used]) == os.path.abspath(bg_raw):
                        done_dir = os.path.join(os.path.dirname(bg_raw.rstrip('/\\')),
                                                f"{os.path.basename(bg_raw.rstrip('/\\'))}(사용완료)")
                        try:
                            os.makedirs(done_dir, exist_ok=True)
                            r, e = self._consume_source(bg_used, done_dir)
                            if r == 'moved':
                                llog(f"  → 배경 사용완료 이동: {os.path.basename(bg_used)}")
                        except Exception:
                            pass
                count += 1
                thumb_found = True
            else:
                llog(f"  [중단] 필수 누락: 썸네일 (자동 생성 실패: {err})", '#ff6b6b')
                return None

        # 3. 작업 전/후 — Sequential 사용 (1순위 다 쓰면 2순위, ..., 5순위 까지 폴백)
        if stop(): return
        llog("[3] 작업 전/후 사진 탐색 중...")
        any_before_valid = any(os.path.isdir(d) for d in before_dirs_manual)
        any_after_valid = any(os.path.isdir(d) for d in after_dirs_manual)
        if any_before_valid or any_after_valid:
            photo_count = 0
            num_start = 1
            for manual_dirs, label, limit in [(before_dirs_manual, '작업 전', 3),
                                               (after_dirs_manual, '작업 후', 15)]:
                remaining = limit
                # 1순위 → 2순위 → ... → 5순위 순서대로 사용. 빈 폴더는 다음 순위로 폴백.
                for rank, manual_dir in enumerate(manual_dirs, 1):
                    if remaining <= 0:
                        break
                    if not manual_dir:
                        llog(f"  {label} {rank}순위: 경로 미설정 → 다음 순위")
                        continue
                    if not os.path.isdir(manual_dir):
                        llog(f"  {label} {rank}순위({manual_dir}): 폴더 없음 → 다음 순위", '#fdcb6e')
                        continue
                    skip_move = manual_dir.rstrip('/\\').endswith('(사용완료)')
                    try:
                        all_files = os.listdir(manual_dir)
                    except Exception as e:
                        llog(f"  {label} {rank}순위({os.path.basename(manual_dir.rstrip('/\\'))}): 접근 오류 ({e}) → 다음 순위", '#fdcb6e')
                        continue
                    photos = sorted([f for f in all_files if f.lower().endswith(exts)])[:remaining]
                    if not photos:
                        llog(f"  {label} {rank}순위({os.path.basename(manual_dir.rstrip('/\\'))}): 비어있음 → 다음 순위")
                        continue
                    used_srcs = []
                    for f in photos:
                        src = os.path.join(manual_dir, f)
                        ext = os.path.splitext(f)[1]
                        shutil.copy2(src, os.path.join(img_dir, f'{num_start}{ext}'))
                        used_srcs.append(src)
                        num_start += 1
                        photo_count += 1
                        remaining -= 1
                    # 사용완료 이동 / 원본 삭제
                    if not skip_move:
                        base_dir = manual_dir.rstrip('/\\')
                        done_dir = f"{base_dir}(사용완료)"
                        try:
                            os.makedirs(done_dir, exist_ok=True)
                        except Exception as e:
                            llog(f"    [경고] 사용완료 폴더 생성 실패: {e}", '#fdcb6e')
                        moved = 0; deleted = 0; failed = 0; errs = []
                        for src in used_srcs:
                            result, err = self._consume_source(src, done_dir)
                            if result == 'moved': moved += 1
                            elif result == 'deleted':
                                deleted += 1
                                if err: errs.append(err)
                            else:
                                failed += 1
                                if err: errs.append(err)
                        llog(f"  {label} {rank}순위({os.path.basename(base_dir)}): 사용 {len(photos)}장 / 이동 {moved} / 삭제만 {deleted} / 실패 {failed}")
                        if errs:
                            llog(f"    [경고] {errs[0]}", '#fdcb6e')
                    else:
                        llog(f"  {label} {rank}순위({os.path.basename(manual_dir)}): {len(photos)}장 (이미 사용완료 폴더 - 원본 유지)")
            count += photo_count
            if photo_count == 0:
                llog("  [중단] 필수 누락: 작업 전/후 사진 (1~5순위 폴더 모두 비어있음)", '#ff6b6b')
                return None
        else:
            photo_count = 0
            used_set = None
            pair_error = False
            for num in range(1, 6):
                before_dir = os.path.join(photo_base, f'작업전-{num}')
                after_dir = os.path.join(photo_base, f'작업후-{num}')
                before_ok = os.path.isdir(before_dir) and any(
                    f.lower().endswith(exts) for f in os.listdir(before_dir))
                after_ok = os.path.isdir(after_dir) and any(
                    f.lower().endswith(exts) for f in os.listdir(after_dir))
                if before_ok != after_ok:
                    llog(f"  [오류] 세트 {num} 쌍 불일치: 작업전-{num}={'있음' if before_ok else '없음'} / "
                         f"작업후-{num}={'있음' if after_ok else '없음'}", '#ff6b6b')
                    llog(f"  → 작업전-{num} 과 작업후-{num} 은 반드시 한 세트여야 합니다", '#ff6b6b')
                    pair_error = True
                    break
                if before_ok and after_ok:
                    used_set = num
                    break
            if used_set:
                llog(f"  세트 {used_set} 사용")
                num_start = 1
                for prefix, label, limit in [('작업전', '작업 전', 3), ('작업후', '작업 후', 15)]:
                    d = os.path.join(photo_base, f'{prefix}-{used_set}')
                    if not os.path.isdir(d):
                        continue
                    photos = sorted([f for f in os.listdir(d) if f.lower().endswith(exts)])[:limit]
                    used_srcs = []
                    for f in photos:
                        src = os.path.join(d, f)
                        ext = os.path.splitext(f)[1]
                        shutil.copy2(src, os.path.join(img_dir, f'{num_start}{ext}'))
                        used_srcs.append(src)
                        num_start += 1
                        photo_count += 1
                    done_dir = os.path.join(photo_base, f'{prefix}-{used_set}(사용완료)')
                    try:
                        os.makedirs(done_dir, exist_ok=True)
                    except Exception as e:
                        llog(f"    [경고] 사용완료 폴더 생성 실패: {e}", '#fdcb6e')
                    moved = 0; deleted = 0; failed = 0; errs = []
                    for src in used_srcs:
                        result, err = self._consume_source(src, done_dir)
                        if result == 'moved': moved += 1
                        elif result == 'deleted':
                            deleted += 1
                            if err: errs.append(err)
                        else:
                            failed += 1
                            if err: errs.append(err)
                    if photos:
                        llog(f"  {label}-{used_set}: 사용 {len(photos)}장 / 사용완료 이동 {moved} / 원본만 삭제 {deleted} / 실패 {failed}")
                        if errs:
                            llog(f"    [경고] {errs[0]}", '#fdcb6e')
            elif not pair_error:
                llog("  [중단] 필수 누락: 작업 전/후 사진 (작업전-N / 작업후-N 세트 없음)", '#ff6b6b')
                return None
            if pair_error:
                llog("  [중단] 세트 쌍 불일치로 진행 불가", '#ff6b6b')
                return None
            count += photo_count

        llog(f"패키징 완료! ({count}개 파일) → {pkg_dir}", '#00b894')
        return pkg_dir

    def _run_video_core(self, keyword, pkg_dir, duration, width, height, effect, vid_count,
                        log_widget=None, stop_check=None):
        """pkg_dir 내 파일로 동영상 생성 (썸네일 먼저, 그 뒤 1,2,3... 사진)"""
        import shutil, tempfile
        log = log_widget if log_widget is not None else self.bpack_log
        stop = stop_check if stop_check else (lambda: False)
        def llog(msg, color='#00cec9'):
            self._log_to(log, msg, color)

        if stop(): return
        llog("[동영상] 사진 수집 중...")
        exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
        tmp_dir = tempfile.mkdtemp(prefix='vid_')
        try:
            collected = 0

            # 썸네일: {keyword}.ext
            thumb_file = None
            for f in os.listdir(pkg_dir):
                if f.lower().endswith(exts) and os.path.splitext(f)[0] == keyword:
                    thumb_file = f
                    break
            if thumb_file:
                src = os.path.join(pkg_dir, thumb_file)
                ext = os.path.splitext(thumb_file)[1]
                shutil.copy2(src, os.path.join(tmp_dir, f'00_thumb{ext}'))
                collected += 1
                llog(f"  썸네일 포함: {thumb_file}")

            # 숫자 이름 사진: 1.jpg, 2.jpg, ...
            numbered = []
            for f in os.listdir(pkg_dir):
                stem, ext = os.path.splitext(f)
                if f.lower().endswith(exts) and stem.isdigit():
                    numbered.append((int(stem), f))
            numbered.sort()
            # vid_count 한계 (썸네일 포함 카운트)
            take = max(0, vid_count - collected)
            for idx, (_, f) in enumerate(numbered[:take]):
                src = os.path.join(pkg_dir, f)
                ext = os.path.splitext(f)[1]
                shutil.copy2(src, os.path.join(tmp_dir, f'{idx+1:02d}_photo{ext}'))
                collected += 1

            if collected == 0:
                llog("  동영상용 사진 없음 (썸네일/사진 부족)", '#fdcb6e')
                return

            if stop(): return
            output_path = os.path.join(pkg_dir, f'{keyword}.mp4')
            llog(f"  {collected}장 → 동영상 인코딩 ({width}x{height}, {effect})")
            from core.video_maker import create_slideshow
            create_slideshow(tmp_dir, output_path,
                             duration=duration, width=width, height=height,
                             effect=effect, callback=lambda m: llog(m))
            llog(f"  동영상 완료 → {output_path}", '#00b894')
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _on_one_shot_stop(self):
        self._one_stop_flag = True
        self._log_to(self.one_log, "━━━ 중지 요청됨 — 현재 단계 완료 후 종료 ━━━", '#fdcb6e')
        self._bridge.set_text.emit(self.one_status, "중지 중...")

    def _on_one_shot(self):
        raw = self.one_keyword.toPlainText().strip()
        keywords = [line.strip() for line in raw.split('\n') if line.strip()]
        if not keywords:
            QMessageBox.warning(self, "경고", "키워드를 입력하세요"); return

        api_key = self.blog_key.text().strip() if hasattr(self, 'blog_key') else ''
        if not api_key:
            QMessageBox.warning(self, "경고", "블로그 원고생성기 탭에서 Claude API 키를 먼저 입력하세요"); return
        output_dir = self.bpub_output.text().strip() if hasattr(self, 'bpub_output') else ''
        if not output_dir:
            QMessageBox.warning(self, "경고", "블로그발행(패키징) 탭에서 출력 폴더를 먼저 선택하세요"); return

        count = self.one_count.value()
        # 원큐 탭의 업체명/관점 사용 (생성기 탭 값 override)
        brand = self.one_brand.currentText()
        viewpoint = '고객' if self.one_vp_group.checkedId() == 0 else '업체'
        # 키워드 사이 딜레이
        delay = self.one_delay.value() if hasattr(self, 'one_delay') else 0
        try:
            self._set_cfg('POSTING', 'one_delay', str(delay))
            self._save_config_file()
        except Exception:
            pass
        model = self.blog_model.currentText()
        numbering = self.blog_nb_group.checkedId() == 0
        # 원큐 자체 이미지 수 우선, 없으면 블로그 원고생성기 탭의 값
        img_count = self.one_img_count.value() if hasattr(self, 'one_img_count') else self.blog_img_count.value()
        try:
            self._set_cfg('POSTING', 'one_img_count', str(img_count))
            self._save_config_file()
        except Exception:
            pass
        save_dir = self.blog_save_dir.text().strip()
        thumb_dir_manual = self._resolve_packaging_thumb_dir(self.bpub_thumb_dir.text().strip())
        before_dirs_manual = self._collect_priority_paths(self.bpub_before_dirs)
        after_dirs_manual = self._collect_priority_paths(self.bpub_after_dirs)
        # 동영상 설정
        vid_duration = self.bpub_vid_duration.value()
        vid_count = self.bpub_vid_count.value()
        vid_w = self.bpub_vid_w.value()
        vid_h = self.bpub_vid_h.value()
        effect_map = {'켄 번스 (줌+페이드)': 'kenburns', '페이드+블러': 'fadeblur',
                      '페이드 전환': 'fade', '심플 (효과 없음)': 'simple'}
        vid_effect = effect_map.get(self.bpub_vid_effect.currentText(), 'kenburns')
        pkg_output_dir = output_dir

        self._one_stop_flag = False
        self.one_btn.setEnabled(False)
        self.one_stop_btn.setEnabled(True)
        self.one_log.clear()
        self._bridge.set_text.emit(self.one_status, "실행 중...")

        def stopped():
            return self._one_stop_flag

        def do():
            log = self.one_log
            success_count = 0
            fail_count = 0
            total_kw = len(keywords)

            from core.blog_analyzer import search_naver_blogs, crawl_blogs, analyze_morphemes
            from core.blog_generator import generate_blog_post, save_blog_post

            try:
                for kw_idx, keyword in enumerate(keywords, 1):
                    if stopped():
                        break
                    prefix = f"[{kw_idx}/{total_kw}] {keyword}"
                    self._log_to(log, f"\n═════════════ {prefix} ═════════════", '#6c5ce7')
                    self._bridge.set_text.emit(self.one_status, f"실행 중 {kw_idx}/{total_kw}: {keyword}")

                    try:
                        def lcb(msg): self._log_to(log, msg)

                        # ── 1/4 포스팅 분석 ──
                        if stopped(): break
                        self._log_to(log, f"━━━ {prefix} · 1/4 포스팅 분석 ━━━", '#fdcb6e')
                        self._log_to(log, f"'{keyword}' 검색 중 (상위 {count}개)...")
                        urls = search_naver_blogs(keyword, count=count, callback=lcb)
                        if not urls:
                            self._log_to(log, f"[{keyword}] 검색 결과 없음 - 건너뜀", '#ff6b6b')
                            fail_count += 1
                            continue

                        if stopped(): break
                        self._log_to(log, f"{len(urls)}개 URL 크롤링 중...")
                        posts = crawl_blogs(urls, callback=lcb)
                        if not posts:
                            self._log_to(log, f"[{keyword}] 크롤링 실패 - 건너뜀", '#ff6b6b')
                            fail_count += 1
                            continue

                        if stopped(): break
                        self._log_to(log, "형태소 분석 중...")
                        morpheme_result = analyze_morphemes(posts, callback=lcb)
                        formatted = morpheme_result['formatted']

                        # 참고 원고 — 검색 1순위 글 사용 (의도된 동작)
                        first = posts[0]
                        title = first['title']
                        body = first['body']
                        filtered = [l for l in formatted.split('\n')
                                    if l.strip().startswith('[5/')
                                    or l.strip().startswith('[4/')
                                    or l.strip().startswith('■')]
                        paren_kw = '\n'.join(filtered)
                        bkw = keyword

                        self._bridge.set_text.emit(self.anal_title, title)
                        self._bridge.set_plain.emit(self.anal_body, body)
                        self._bridge.set_plain.emit(self.anal_morphemes, formatted)
                        self._analyzed_data = {'title': title, 'body': body,
                                               'morphemes': formatted, 'posts': posts}
                        self._log_to(log, f"분석 완료 — 제목: {title[:40]}...", '#00b894')
                        self._log_to(log, f"  [소괄호키워드] {paren_kw[:200]}...", '#8b949e')

                        # ── 2/4 블로그 원고 생성 ──
                        if stopped(): break
                        self._log_to(log, f"━━━ {prefix} · 2/4 블로그 원고 생성 ━━━", '#fdcb6e')
                        cur_tpl = self.one_template.currentText() if hasattr(self, 'one_template') else '청소'
                        input_data = {
                            'title': title,
                            'brand_name': brand,
                            'bracket_kw': bkw,
                            'paren_kw': paren_kw,
                            'viewpoint': viewpoint,
                            'numbering': numbering,
                            'image_count': img_count,
                            'manuscript': body,
                            'template': cur_tpl,
                        }
                        self._log_to(log, f"Claude API 호출 중 ({model}, 템플릿: {cur_tpl})...")
                        result = generate_blog_post(input_data, api_key, model=model, callback=lcb)
                        if not result or not result.get('body'):
                            self._log_to(log, f"[{keyword}] 원고 생성 실패 - 건너뜀", '#ff6b6b')
                            fail_count += 1
                            continue
                        save_blog_post(bkw, result['body'], callback=lcb, save_dir=save_dir)
                        self._log_to(log,
                            f"생성 완료 — {result['char_count']}자, 키워드 {result['keyword_count']}회",
                            '#00b894')

                        self._bridge.set_text.emit(self.blog_title, title)
                        self._bridge.set_text.emit(self.blog_bkw, bkw)
                        self._bridge.set_plain.emit(self.blog_pkw, paren_kw)
                        self._bridge.set_plain.emit(self.blog_manuscript, body)

                        # ── 3/4 패키징 ──
                        if stopped(): break
                        self._log_to(log, f"━━━ {prefix} · 3/4 발행 자료 패키징 ━━━", '#fdcb6e')
                        self._bridge.set_text.emit(self.bpub_keyword, bkw)
                        _packing_dir = self._cfg('PACKING', 'photo_dir',
                                                  os.path.join(BASE_DIR, '사진', '포장이사'))
                        # 원큐(패키징) — 포장이사 + 이미지 재가공 체크 시 원본 보존 + 변형 재사용
                        _reprocess = (cur_tpl == '포장이사'
                                      and hasattr(self, 'one_reprocess_chk')
                                      and self.one_reprocess_chk.isChecked())
                        pkg_dir = self._run_packaging_core(
                            keyword=bkw,
                            output_dir=pkg_output_dir,
                            thumb_dir_manual=thumb_dir_manual,
                            before_dirs_manual=before_dirs_manual,
                            after_dirs_manual=after_dirs_manual,
                            log_widget=log,
                            stop_check=stopped,
                            template=cur_tpl, image_count=img_count,
                            packing_dir=_packing_dir,
                            reprocess=_reprocess,
                        )
                        if stopped(): break
                        if pkg_dir is None:
                            self._log_to(log,
                                f"[{keyword}] 필수 자료 누락 (사진 1·2순위 모두 없음 등) — 다음 키워드로 건너뜀",
                                '#fdcb6e')
                            fail_count += 1
                            continue

                        # ── 4/4 동영상 (필수) ──
                        self._log_to(log, f"━━━ {prefix} · 4/4 동영상 생성 ━━━", '#fdcb6e')
                        self._run_video_core(
                            keyword=bkw, pkg_dir=pkg_dir,
                            duration=vid_duration, width=vid_w, height=vid_h,
                            effect=vid_effect, vid_count=vid_count,
                            log_widget=log, stop_check=stopped,
                        )
                        if stopped(): break

                        self._log_to(log, f"━━━ [{kw_idx}/{total_kw}] {keyword} 완료! ━━━", '#00b894')
                        success_count += 1

                        # 다음 키워드 전 딜레이 — progress bar 로 시각화 (로그는 시작/끝만)
                        if kw_idx < total_kw and not stopped() and delay > 0:
                            import time as _t
                            self._log_to(log, f"[대기] 다음 패키징까지 {delay//60}분 {delay%60}초", '#74b9ff')
                            elapsed = 0
                            self._bridge.set_progress.emit(self.one_wait_bar, 0, delay,
                                f"다음 패키징까지 0/{delay}초")
                            while elapsed < delay and not stopped():
                                sleep_t = min(1, delay - elapsed)
                                _t.sleep(sleep_t)
                                elapsed += sleep_t
                                remaining = delay - elapsed
                                self._bridge.set_progress.emit(
                                    self.one_wait_bar, elapsed, delay,
                                    f"다음 패키징까지 {elapsed}/{delay}초 (남음 {remaining//60}분 {remaining%60}초)")
                            # 끝나면 숨김
                            self._bridge.set_progress.emit(self.one_wait_bar, 0, 0, "")

                    except Exception as inner:
                        import traceback
                        self._log_to(log, f"[{keyword}] 오류 - 건너뜀: {inner}", '#ff6b6b')
                        self._log_to(log, traceback.format_exc()[:400], '#ff6b6b')
                        fail_count += 1

                # 전체 요약
                if stopped():
                    self._log_to(log, f"\n━━━ 중지됨 (성공 {success_count} / 실패 {fail_count} / 남음 {total_kw - success_count - fail_count}) ━━━", '#fdcb6e')
                else:
                    self._log_to(log, f"\n━━━ 원큐 전체 완료! 성공 {success_count}개 / 실패 {fail_count}개 / 총 {total_kw}개 ━━━", '#00b894')

            except Exception as e:
                import traceback
                self._log_to(self.one_log, f"[치명적 오류] {e}", '#ff6b6b')
                self._log_to(self.one_log, traceback.format_exc()[:500], '#ff6b6b')
            finally:
                self._bridge.set_enabled.emit(self.one_btn, True)
                self._bridge.set_enabled.emit(self.one_stop_btn, False)
                status = "중지됨" if self._one_stop_flag else f"완료 ({success_count}/{total_kw})"
                self._bridge.set_text.emit(self.one_status, status)

        threading.Thread(target=do, daemon=True).start()

    # ═══════ 원큐(블로그) 실행 ═══════
    def _on_one_shot_blog_stop(self):
        self._oneb_stop_flag = True
        self._log_to(self.oneb_log, "━━━ 중지 요청됨 — 브라우저 즉시 종료 ━━━", '#fdcb6e')
        self._bridge.set_text.emit(self.oneb_status, "중지 중...")
        try:
            if hasattr(self, 'blog_browser') and self.blog_browser:
                self.blog_browser.close()
        except Exception:
            pass
        self.blog_browser = None

    def _on_one_shot_blog(self):
        raw = self.oneb_keyword.toPlainText().strip()
        keywords = [line.strip() for line in raw.split('\n') if line.strip()]
        if not keywords:
            QMessageBox.warning(self, "경고", "키워드를 입력하세요"); return

        api_key = self.blog_key.text().strip() if hasattr(self, 'blog_key') else ''
        if not api_key:
            QMessageBox.warning(self, "경고", "블로그 원고생성기 탭에서 Claude API 키를 먼저 입력하세요"); return
        output_dir = self.bpub_output.text().strip() if hasattr(self, 'bpub_output') else ''
        if not output_dir:
            QMessageBox.warning(self, "경고", "블로그발행(패키징) 탭에서 출력 폴더를 먼저 선택하세요"); return

        accounts = self._parse_blog_account_list(self.bpub_accounts.toPlainText()) if hasattr(self, 'bpub_accounts') else []
        if not accounts:
            QMessageBox.warning(self, "경고", "블로그발행(자동) 탭에서 계정을 입력하세요 (아이디 | 비밀번호 | 블로그ID)"); return

        count = self.oneb_count.value()
        brand = self.oneb_brand.currentText()
        viewpoint = '고객' if self.oneb_vp_group.checkedId() == 0 else '업체'
        model = self.blog_model.currentText()
        numbering = self.blog_nb_group.checkedId() == 0
        # 원큐 자체 이미지 수 우선, 없으면 블로그 원고생성기 탭의 값
        img_count = self.oneb_img_count.value() if hasattr(self, 'oneb_img_count') else self.blog_img_count.value()
        try:
            self._set_cfg('POSTING', 'oneb_img_count', str(img_count))
            self._save_config_file()
        except Exception:
            pass
        save_dir = self.blog_save_dir.text().strip()
        thumb_dir_manual = self._resolve_packaging_thumb_dir(self.bpub_thumb_dir.text().strip())
        before_dirs_manual = self._collect_priority_paths(self.bpub_before_dirs)
        after_dirs_manual = self._collect_priority_paths(self.bpub_after_dirs)
        vid_duration = self.bpub_vid_duration.value()
        vid_count = self.bpub_vid_count.value()
        vid_w = self.bpub_vid_w.value()
        vid_h = self.bpub_vid_h.value()
        effect_map = {'켄 번스 (줌+페이드)': 'kenburns', '페이드+블러': 'fadeblur',
                      '페이드 전환': 'fade', '심플 (효과 없음)': 'simple'}
        vid_effect = effect_map.get(self.bpub_vid_effect.currentText(), 'kenburns')
        is_draft = self.oneb_draft_chk.isChecked()
        # 원큐(블로그) 자체 딜레이 우선, 없으면 블로그 발행 탭 bpub_delay
        delay = self.oneb_delay.value() if hasattr(self, 'oneb_delay') else (
            self.bpub_delay.value() if hasattr(self, 'bpub_delay') else 30)
        try:
            self._set_cfg('POSTING', 'oneb_delay', str(delay))
            self._save_config_file()
        except Exception:
            pass

        self._oneb_stop_flag = False
        self.oneb_btn.setEnabled(False)
        self.oneb_stop_btn.setEnabled(True)
        self.oneb_log.clear()
        self._bridge.set_text.emit(self.oneb_status, "실행 중...")

        def stopped():
            return self._oneb_stop_flag

        def do():
            log = self.oneb_log
            success_count = 0
            fail_count = 0
            total_kw = len(keywords)

            import time as _time
            from core.blog_analyzer import search_naver_blogs, crawl_blogs, analyze_morphemes
            from core.blog_generator import generate_blog_post, save_blog_post
            from core.blog_poster import post_to_blog
            from core.browser import NaverBrowser

            if not hasattr(self, 'blog_browser') or self.blog_browser is None:
                self.blog_browser = NaverBrowser()
            page = None
            current_acc_idx = -1
            current_blog_id = ''
            # 원고를 계정별로 균등 분할 (계정1이 자기 몫 전부 → 계정2가 자기 몫 전부)
            per_acc = max(1, (total_kw + len(accounts) - 1) // len(accounts))

            try:
                for kw_idx, keyword in enumerate(keywords, 1):
                    if stopped():
                        break
                    prefix = f"[{kw_idx}/{total_kw}] {keyword}"
                    self._log_to(log, f"\n═════════════ {prefix} ═════════════", '#6c5ce7')
                    self._bridge.set_text.emit(self.oneb_status, f"실행 중 {kw_idx}/{total_kw}: {keyword}")

                    try:
                        def lcb(msg): self._log_to(log, msg)

                        # ── 1/5 포스팅 분석 ──
                        if stopped(): break
                        self._log_to(log, f"━━━ {prefix} · 1/5 포스팅 분석 ━━━", '#fdcb6e')
                        urls = search_naver_blogs(keyword, count=count, callback=lcb)
                        if not urls:
                            self._log_to(log, f"[{keyword}] 검색 결과 없음 - 건너뜀", '#ff6b6b')
                            fail_count += 1
                            continue
                        if stopped(): break
                        posts = crawl_blogs(urls, callback=lcb)
                        if not posts:
                            self._log_to(log, f"[{keyword}] 크롤링 실패 - 건너뜀", '#ff6b6b')
                            fail_count += 1
                            continue
                        if stopped(): break
                        morpheme_result = analyze_morphemes(posts, callback=lcb)
                        formatted = morpheme_result['formatted']
                        # 참고 원고 — 검색 1순위 글 사용 (의도된 동작)
                        first = posts[0]
                        title = first['title']
                        body = first['body']
                        filtered = [l for l in formatted.split('\n')
                                    if l.strip().startswith('[5/')
                                    or l.strip().startswith('[4/')
                                    or l.strip().startswith('■')]
                        paren_kw = '\n'.join(filtered)
                        bkw = keyword
                        self._bridge.set_text.emit(self.anal_title, title)
                        self._bridge.set_plain.emit(self.anal_body, body)
                        self._bridge.set_plain.emit(self.anal_morphemes, formatted)
                        self._log_to(log, f"분석 완료 — 제목: {title[:40]}...", '#00b894')
                        self._log_to(log, f"  [소괄호키워드] {paren_kw[:200]}...", '#8b949e')

                        # ── 2/5 블로그 원고 생성 ──
                        if stopped(): break
                        self._log_to(log, f"━━━ {prefix} · 2/5 블로그 원고 생성 ━━━", '#fdcb6e')
                        cur_tpl = self.oneb_template.currentText() if hasattr(self, 'oneb_template') else '청소'
                        input_data = {
                            'title': title, 'brand_name': brand, 'bracket_kw': bkw,
                            'paren_kw': paren_kw, 'viewpoint': viewpoint,
                            'numbering': numbering, 'image_count': img_count,
                            'manuscript': body, 'template': cur_tpl,
                        }
                        result = generate_blog_post(input_data, api_key, model=model, callback=lcb)
                        if not result or not result.get('body'):
                            self._log_to(log, f"[{keyword}] 원고 생성 실패 - 건너뜀", '#ff6b6b')
                            fail_count += 1
                            continue
                        save_blog_post(bkw, result['body'], callback=lcb, save_dir=save_dir)
                        self._log_to(log, f"생성 완료 — {result['char_count']}자", '#00b894')
                        self._bridge.set_text.emit(self.blog_title, title)
                        self._bridge.set_text.emit(self.blog_bkw, bkw)
                        self._bridge.set_plain.emit(self.blog_pkw, paren_kw)
                        self._bridge.set_plain.emit(self.blog_manuscript, body)

                        # ── 3/5 패키징 ──
                        if stopped(): break
                        self._log_to(log, f"━━━ {prefix} · 3/5 발행 자료 패키징 ━━━", '#fdcb6e')
                        self._bridge.set_text.emit(self.bpub_keyword, bkw)
                        _packing_dir2 = self._cfg('PACKING', 'photo_dir',
                                                   os.path.join(BASE_DIR, '사진', '포장이사'))
                        # 원큐(블로그) — 포장이사 + 이미지 재가공 체크 시 원본 보존 + 변형 재사용
                        _reprocess = (cur_tpl == '포장이사'
                                      and hasattr(self, 'oneb_reprocess_chk')
                                      and self.oneb_reprocess_chk.isChecked())
                        pkg_dir = self._run_packaging_core(
                            keyword=bkw, output_dir=output_dir,
                            thumb_dir_manual=thumb_dir_manual,
                            before_dirs_manual=before_dirs_manual,
                            after_dirs_manual=after_dirs_manual,
                            log_widget=log, stop_check=stopped,
                            template=cur_tpl, image_count=img_count,
                            packing_dir=_packing_dir2,
                            reprocess=_reprocess,
                        )
                        if stopped(): break
                        if pkg_dir is None:
                            self._log_to(log,
                                f"[{keyword}] 필수 자료 누락 (사진 1·2순위 모두 없음 등) — 다음 키워드로 건너뜀",
                                '#fdcb6e')
                            fail_count += 1
                            continue

                        # ── 4/5 동영상 ──
                        self._log_to(log, f"━━━ {prefix} · 4/5 동영상 생성 ━━━", '#fdcb6e')
                        self._run_video_core(
                            keyword=bkw, pkg_dir=pkg_dir,
                            duration=vid_duration, width=vid_w, height=vid_h,
                            effect=vid_effect, vid_count=vid_count,
                            log_widget=log, stop_check=stopped,
                        )
                        if stopped(): break

                        # ── 5/5 블로그 발행 ──
                        self._log_to(log, f"━━━ {prefix} · 5/5 블로그 발행 ━━━", '#fdcb6e')
                        # 계정1이 자기 몫 전부 → 계정2가 자기 몫 전부
                        acc_idx = min((kw_idx - 1) // per_acc, len(accounts) - 1)
                        # 계정 변경 OR 직전 대기가 길었으면(세션 만료 가능성) 강제 재로그인
                        need_relogin = (acc_idx != current_acc_idx) or getattr(self, '_oneb_session_stale', False)
                        if need_relogin:
                            if page is not None:
                                try: self.blog_browser.close()
                                except Exception: pass
                                page = None
                            user, pw, blog_id = accounts[acc_idx]
                            stale_msg = " (긴 대기 후 세션 갱신)" if getattr(self, '_oneb_session_stale', False) else ""
                            self._log_to(log, f"[계정 {acc_idx+1}/{len(accounts)}] {user} 로그인 중...{stale_msg}", '#fdcb6e')
                            self.blog_browser = NaverBrowser()
                            ok = self.blog_browser.login_manual(
                                username=user, password=pw, callback=lcb)
                            if not ok:
                                self._log_to(log, f"[실패] {user} 로그인 실패 - 건너뜀", '#ff6b6b')
                                fail_count += 1
                                self._oneb_session_stale = False
                                continue
                            page = self.blog_browser.start_headless()
                            current_acc_idx = acc_idx
                            current_blog_id = blog_id
                            self._oneb_session_stale = False
                        if is_draft:
                            self._log_to(log, "[정보] 임시저장 모드 — 저장 버튼만 클릭합니다", '#fdcb6e')
                        publish_ok = {'v': False}
                        def pub_cb(msg, _s=publish_ok):
                            color = '#00b894' if '[완료]' in msg else '#ff6b6b' if '[실패]' in msg else '#00cec9'
                            if '[완료]' in msg:
                                _s['v'] = True
                            self._log_to(log, msg, color)
                        post_failed = False
                        try:
                            post_to_blog(page, current_blog_id, pkg_dir,
                                         log_callback=pub_cb,
                                         stop_check=stopped,
                                         pkg_dir=pkg_dir, draft=is_draft)
                        except Exception as e:
                            self._log_to(log, f"[실패] 발행 오류: {e}", '#ff6b6b')
                            post_failed = True

                        if stopped(): break
                        if publish_ok['v']:
                            self._log_to(log, f"━━━ [{kw_idx}/{total_kw}] {keyword} 발행 완료! ━━━", '#00b894')
                            success_count += 1
                        else:
                            self._log_to(log, f"━━━ [{kw_idx}/{total_kw}] {keyword} 발행 실패 ━━━", '#ff6b6b')
                            fail_count += 1
                            # 사용자 정책: 발행 도중 오류/실패 시 다음 키워드로 가지 말고 즉시 정지
                            self._log_to(log, "━━━ 오류로 인해 일시정지 — 원인 확인 후 다시 실행해주세요 ━━━", '#fdcb6e')
                            break

                        # 다음 키워드 전 딜레이 — progress bar 로 시각화
                        if kw_idx < total_kw and not stopped() and delay > 0:
                            self._log_to(log, f"[대기] 다음 발행까지 {delay//60}분 {delay%60}초", '#74b9ff')
                            elapsed = 0
                            self._bridge.set_progress.emit(self.oneb_wait_bar, 0, delay,
                                f"다음 발행까지 0/{delay}초")
                            while elapsed < delay and not stopped():
                                sleep_t = min(1, delay - elapsed)
                                _time.sleep(sleep_t)
                                elapsed += sleep_t
                                remaining = delay - elapsed
                                self._bridge.set_progress.emit(
                                    self.oneb_wait_bar, elapsed, delay,
                                    f"다음 발행까지 {elapsed}/{delay}초 (남음 {remaining//60}분 {remaining%60}초)")
                            self._bridge.set_progress.emit(self.oneb_wait_bar, 0, 0, "")
                            # 10분 이상 대기했으면 세션 만료 가능성 — 다음 키워드 발행 전 재로그인
                            if delay >= 600:
                                self._oneb_session_stale = True

                    except Exception as inner:
                        import traceback
                        self._log_to(log, f"[{keyword}] 오류 - 일시정지: {inner}", '#ff6b6b')
                        self._log_to(log, traceback.format_exc()[:400], '#ff6b6b')
                        fail_count += 1
                        # 다음 키워드로 가지 말고 정지
                        self._log_to(log, "━━━ 오류로 인해 일시정지 — 원인 확인 후 다시 실행해주세요 ━━━", '#fdcb6e')
                        break

                if stopped():
                    self._log_to(log, f"\n━━━ 중지됨 (성공 {success_count} / 실패 {fail_count}) ━━━", '#fdcb6e')
                else:
                    self._log_to(log, f"\n━━━ 원큐(블로그) 전체 완료! 성공 {success_count}개 / 실패 {fail_count}개 / 총 {total_kw}개 ━━━", '#00b894')

            except Exception as e:
                import traceback
                self._log_to(self.oneb_log, f"[치명적 오류] {e}", '#ff6b6b')
                self._log_to(self.oneb_log, traceback.format_exc()[:500], '#ff6b6b')
            finally:
                try:
                    if page is not None and hasattr(self, 'blog_browser') and self.blog_browser:
                        self.blog_browser.close()
                except Exception:
                    pass
                self.blog_browser = None
                self._bridge.set_enabled.emit(self.oneb_btn, True)
                self._bridge.set_enabled.emit(self.oneb_stop_btn, False)
                status = "중지됨" if self._oneb_stop_flag else f"완료 ({success_count}/{total_kw})"
                self._bridge.set_text.emit(self.oneb_status, status)

        threading.Thread(target=do, daemon=True).start()

    # ═══════ 원큐(카페) 실행 ═══════
    def _on_one_shot_cafe_stop(self):
        self._onec_stop_flag = True
        self.stop_flag = True
        self._log_to(self.onec_log, "━━━ 중지 요청됨 — 브라우저 즉시 종료 ━━━", '#fdcb6e')
        self._bridge.set_text.emit(self.onec_status, "중지 중...")
        try:
            if hasattr(self, 'browser') and self.browser:
                self.browser.close()
        except Exception:
            pass

    def _on_one_shot_cafe(self):
        raw = self.onec_keyword.toPlainText().strip()
        keywords = [k.strip() for k in raw.split('\n') if k.strip()]
        if not keywords:
            QMessageBox.warning(self, "경고", "키워드를 입력하세요"); return

        model = self.cafe_model.currentText() if hasattr(self, 'cafe_model') else 'gpt-4o-mini'
        oai = self.cafe_oai_key.text().strip() if hasattr(self, 'cafe_oai_key') else ''
        claude = self.cafe_claude_key.text().strip() if hasattr(self, 'cafe_claude_key') else ''
        if not oai and not claude:
            QMessageBox.warning(self, "경고", "카페 원고생성기 탭에서 OpenAI/Claude API 키를 먼저 입력하세요"); return

        cafes = self._parse_cafe_list()
        if not cafes:
            QMessageBox.warning(self, "경고", "카페 발행(자동) 탭에서 카페 목록을 입력하세요"); return
        accounts = self._parse_account_list(self.pub_accounts.toPlainText()) if hasattr(self, 'pub_accounts') else []
        if not accounts:
            QMessageBox.warning(self, "경고", "카페 발행(자동) 탭에서 계정을 입력하세요 (아이디 | 비밀번호)"); return

        # ── 발행 업체(onec_brand)에 맞춰 이미지 템플릿 자동 동기화 ──
        # 이래야 썸네일·작업전/후 폴더가 발행 업체 기준으로 잡힘. 안 그러면
        # 발행 업체=새집느낌인데 이미지 템플릿=가족사랑클린이면 가족사랑클린 폴더 사용해버림.
        onec_brand_now = self.onec_brand.currentText() if hasattr(self, 'onec_brand') else '새집느낌'
        if hasattr(self, 'img_template') and self.img_template.currentText() != onec_brand_now:
            if self.img_template.findText(onec_brand_now) >= 0:
                # _on_img_template_changed 가 cafe_thumb_dir / cafe_before_dirs / cafe_after_dirs UI 자동 재로드
                self.img_template.setCurrentText(onec_brand_now)

        img_count = self.onec_img_count.value()
        _cafe_thumb_raw = self.cafe_thumb_dir.text().strip() if hasattr(self, 'cafe_thumb_dir') else ''
        thumb_dir = self._resolve_packaging_thumb_dir(_cafe_thumb_raw)
        _tpl_now = self.img_template.currentText() if hasattr(self, 'img_template') else '새집느낌'
        before_dirs = self._collect_priority_paths(self.cafe_before_dirs) if hasattr(self, 'cafe_before_dirs') else []
        after_dirs  = self._collect_priority_paths(self.cafe_after_dirs)  if hasattr(self, 'cafe_after_dirs')  else []
        # 원큐(카페) 자체 딜레이 우선, 없으면 카페 발행 탭 pub_delay
        delay = self.onec_delay.value() if hasattr(self, 'onec_delay') else (
            self.pub_delay.value() if hasattr(self, 'pub_delay') else 30)
        # 자체 딜레이값 저장
        try:
            self._set_cfg('POSTING', 'onec_delay', str(delay))
            self._save_config_file()
        except Exception:
            pass

        self._onec_stop_flag = False
        self.stop_flag = False
        self.onec_btn.setEnabled(False)
        self.onec_stop_btn.setEnabled(True)
        self.onec_log.clear()
        self._bridge.set_text.emit(self.onec_status, "실행 중...")
        self._log_to(self.onec_log, f"[썸네일 폴더] 템플릿={_tpl_now} → {thumb_dir or '(없음)'}", '#74b9ff')

        def stopped():
            return self._onec_stop_flag

        def do():
            log = self.onec_log
            success_count = 0
            fail_count = 0
            total_kw = len(keywords)

            import time as _time
            from core.generator import generate_and_save

            page = None
            current_acc_idx = -1

            try:
                for kw_idx, keyword in enumerate(keywords, 1):
                    if stopped():
                        break
                    prefix = f"[{kw_idx}/{total_kw}] {keyword}"
                    self._log_to(log, f"\n═════════════ {prefix} ═════════════", '#6c5ce7')
                    self._bridge.set_text.emit(self.onec_status, f"실행 중 {kw_idx}/{total_kw}: {keyword}")

                    try:
                        def lcb(msg): self._log_to(log, msg)

                        # 카드뉴스 모드는 슬라이드 이미지를 자체 생성하므로 썸네일 자동 생성 우회
                        _is_cardnews_now = (self.onec_brand.currentText() == '카드뉴스(정보성)') if hasattr(self, 'onec_brand') else False

                        # ── 0/2 썸네일 자동 생성 (키워드 매칭 파일 없으면) ──
                        if (not _is_cardnews_now) and thumb_dir and os.path.isdir(thumb_dir):
                            exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
                            matched = any(
                                f.lower().endswith(exts) and self._kw_match(os.path.splitext(f)[0], keyword)
                                for f in os.listdir(thumb_dir)
                                if os.path.isfile(os.path.join(thumb_dir, f))
                            )
                            if not matched:
                                self._log_to(log, f"썸네일 없음 — 자동 생성 시도 중...", '#fdcb6e')
                                auto_path = os.path.join(thumb_dir, f'{keyword}.jpg')
                                gen_result, gen_err = self._auto_generate_thumbnail(keyword, auto_path)
                                if gen_result:
                                    _, bg_used = gen_result
                                    self._log_to(log, f"썸네일 자동 생성 완료: {keyword}.jpg", '#00b894')
                                    # 사용한 배경 원본을 (사용완료)로 이동 (폴더 모드일 때)
                                    bg_raw = self.img_bg.text().strip() if hasattr(self, 'img_bg') else ''
                                    if bg_raw and os.path.isdir(bg_raw) and bg_used and \
                                       os.path.commonpath([os.path.abspath(bg_raw), os.path.abspath(bg_used)]) == os.path.abspath(bg_raw):
                                        done_bg = os.path.join(os.path.dirname(bg_raw.rstrip('/\\')),
                                                               f"{os.path.basename(bg_raw.rstrip('/\\'))}(사용완료)")
                                        try:
                                            os.makedirs(done_bg, exist_ok=True)
                                            self._consume_source(bg_used, done_bg)
                                        except Exception:
                                            pass
                                else:
                                    self._log_to(log,
                                        f"[{keyword}] 썸네일 자동 생성 실패: {gen_err} — 다음 키워드로 건너뜀",
                                        '#fdcb6e')
                                    fail_count += 1
                                    continue

                        # ── 1/2 카페 원고 생성 ──
                        if stopped(): break
                        self._log_to(log, f"━━━ {prefix} · 1/2 카페 원고 생성 ━━━", '#fdcb6e')
                        _onec_brand_for_gen = self.onec_brand.currentText() if hasattr(self, 'onec_brand') else '새집느낌'
                        _onec_vp_for_gen = '업체' if (hasattr(self, 'onec_vp_group') and self.onec_vp_group.checkedId() == 1) else '고객'
                        _onec_mode = 'cardnews' if _onec_brand_for_gen == '카드뉴스(정보성)' else 'default'
                        folder_path = generate_and_save(
                            keyword, model=model, image_count=img_count,
                            openai_key=oai, claude_key=claude, callback=lcb,
                            thumb_dir=thumb_dir,
                            before_dirs=before_dirs, after_dirs=after_dirs,
                            brand=_onec_brand_for_gen, viewpoint=_onec_vp_for_gen,
                            mode=_onec_mode)
                        if not folder_path or not os.path.isdir(folder_path):
                            self._log_to(log, f"[{keyword}] 원고 생성 실패 — 일시정지", '#ff6b6b')
                            fail_count += 1
                            self._log_to(log, "━━━ 오류로 인해 일시정지 — 원인 확인 후 다시 실행해주세요 ━━━", '#fdcb6e')
                            break
                        self._log_to(log, f"생성 완료 → {folder_path}", '#00b894')

                        # ── 2/2 카페 발행 ──
                        if stopped(): break
                        self._log_to(log, f"━━━ {prefix} · 2/2 카페 발행 ━━━", '#fdcb6e')
                        # 순서: 계정 바깥 / 카페 안쪽
                        # 예) 계정2개 × 카페2개 × 키워드4개 → acc1·cafe1, acc1·cafe2, acc2·cafe1, acc2·cafe2
                        cafe_idx = (kw_idx - 1) % len(cafes)
                        acc_idx = ((kw_idx - 1) // len(cafes)) % len(accounts)
                        # 계정 변경 OR 직전 대기가 길었으면(세션 만료 가능성) 강제 재로그인
                        need_relogin = (acc_idx != current_acc_idx) or getattr(self, '_onec_session_stale', False)
                        if need_relogin:
                            if page is not None:
                                try: self.browser.close()
                                except Exception: pass
                                page = None
                            user, pw = accounts[acc_idx]
                            stale_msg = " (긴 대기 후 세션 갱신)" if getattr(self, '_onec_session_stale', False) else ""
                            self._log_to(log, f"[계정 {acc_idx+1}/{len(accounts)}] {user} 로그인 중...{stale_msg}", '#fdcb6e')
                            ok = self.browser.login_manual(
                                username=user, password=pw, callback=lcb)
                            if not ok:
                                self._log_to(log, f"[실패] {user} 로그인 실패 - 건너뜀", '#ff6b6b')
                                fail_count += 1
                                self._onec_session_stale = False
                                continue
                            page = self.browser.start_headless()
                            current_acc_idx = acc_idx
                            self._onec_session_stale = False

                        c_id, m_id, c_url = cafes[cafe_idx]
                        self._log_to(log, f"[{keyword}] 카페{cafe_idx+1} 발행 중...")
                        publish_ok = {'v': False}
                        def pub_cb(msg, _s=publish_ok):
                            color = '#00b894' if '[완료]' in msg else '#ff6b6b' if '[실패]' in msg else '#00cec9'
                            if '[완료]' in msg:
                                _s['v'] = True
                            self._log_to(log, msg, color)
                        try:
                            _onec_brand = self.onec_brand.currentText() if hasattr(self, 'onec_brand') else '새집느낌'
                            post_to_cafe(page, c_url, '', folder_path,
                                         cafe_id=c_id, menu_id=m_id,
                                         log_callback=pub_cb,
                                         stop_check=stopped, brand=_onec_brand)
                        except Exception as e:
                            self._log_to(log, f"[실패] {keyword}: {e}", '#ff6b6b')

                        if stopped(): break
                        if publish_ok['v']:
                            self._log_to(log, f"━━━ [{kw_idx}/{total_kw}] {keyword} 발행 완료! ━━━", '#00b894')
                            success_count += 1
                        else:
                            self._log_to(log, f"━━━ [{kw_idx}/{total_kw}] {keyword} 발행 실패 ━━━", '#ff6b6b')
                            fail_count += 1
                            # 사용자 정책: 발행 실패 시 다음 키워드로 가지 말고 즉시 정지
                            self._log_to(log, "━━━ 오류로 인해 일시정지 — 원인 확인 후 다시 실행해주세요 ━━━", '#fdcb6e')
                            break

                        # 다음 키워드 전 딜레이 — progress bar 로 시각화
                        if kw_idx < total_kw and not stopped() and delay > 0:
                            self._log_to(log, f"[대기] 다음 발행까지 {delay//60}분 {delay%60}초", '#74b9ff')
                            elapsed = 0
                            self._bridge.set_progress.emit(self.onec_wait_bar, 0, delay,
                                f"다음 발행까지 0/{delay}초")
                            while elapsed < delay and not stopped():
                                sleep_t = min(1, delay - elapsed)
                                _time.sleep(sleep_t)
                                elapsed += sleep_t
                                remaining = delay - elapsed
                                self._bridge.set_progress.emit(
                                    self.onec_wait_bar, elapsed, delay,
                                    f"다음 발행까지 {elapsed}/{delay}초 (남음 {remaining//60}분 {remaining%60}초)")
                            self._bridge.set_progress.emit(self.onec_wait_bar, 0, 0, "")
                            # 10분 이상 대기했으면 세션 만료 가능성 — 다음 키워드 발행 전 재로그인
                            if delay >= 600:
                                self._onec_session_stale = True

                    except Exception as inner:
                        import traceback
                        self._log_to(log, f"[{keyword}] 오류 - 일시정지: {inner}", '#ff6b6b')
                        self._log_to(log, traceback.format_exc()[:400], '#ff6b6b')
                        fail_count += 1
                        # 사용자 정책: 오류 시 다음 키워드로 가지 말고 정지
                        self._log_to(log, "━━━ 오류로 인해 일시정지 — 원인 확인 후 다시 실행해주세요 ━━━", '#fdcb6e')
                        break

                if stopped():
                    self._log_to(log, f"\n━━━ 중지됨 (성공 {success_count} / 실패 {fail_count}) ━━━", '#fdcb6e')
                else:
                    self._log_to(log, f"\n━━━ 원큐(카페) 전체 완료! 성공 {success_count}개 / 실패 {fail_count}개 / 총 {total_kw}개 ━━━", '#00b894')

            except Exception as e:
                import traceback
                self._log_to(self.onec_log, f"[치명적 오류] {e}", '#ff6b6b')
                self._log_to(self.onec_log, traceback.format_exc()[:500], '#ff6b6b')
            finally:
                try:
                    if page is not None:
                        self.browser.close()
                except Exception:
                    pass
                QTimer.singleShot(0, self._refresh_postings)
                self._bridge.set_enabled.emit(self.onec_btn, True)
                self._bridge.set_enabled.emit(self.onec_stop_btn, False)
                status = "중지됨" if self._onec_stop_flag else f"완료 ({success_count}/{total_kw})"
                self._bridge.set_text.emit(self.onec_status, status)

        threading.Thread(target=do, daemon=True).start()


CUSTOM_CSS = """
* {
    font-family: 'Malgun Gothic', 'Segoe UI', sans-serif;
    font-size: 11px;
}
QMainWindow { background-color: #0d1117; }
QTabWidget::pane { border: 1px solid #21262d; border-radius: 6px; background: #0d1117; }
QTabBar::tab {
    font-size: 11px; font-weight: bold; padding: 6px 12px;
    color: #8b949e; background: transparent;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: #e6edf3; border-bottom: 2px solid #6c5ce7; }
QTabBar::tab:hover { color: #e6edf3; }

QGroupBox {
    font-size: 11px; font-weight: bold; color: #00cec9;
    border: 1px solid #21262d; border-radius: 6px;
    margin-top: 12px; padding: 16px 8px 6px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 2px 8px;
    background: #0d1117;
}

QLabel { font-size: 11px; color: #c9d1d9; }

QLineEdit, QTextEdit, QComboBox, QSpinBox {
    font-size: 11px; padding: 4px 6px;
    background: #161b22; color: #e6edf3;
    border: 1px solid #30363d; border-radius: 5px;
    selection-background-color: #6c5ce7;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #6c5ce7;
}
QComboBox { min-height: 22px; padding-right: 18px; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #161b22; color: #e6edf3;
    border: 1px solid #30363d; selection-background-color: #6c5ce7;
}
QSpinBox { min-height: 22px; min-width: 50px; }
QSpinBox::up-button, QSpinBox::down-button {
    width: 16px; border: none; background: #21262d; border-radius: 3px;
}

QPushButton {
    font-size: 11px; font-weight: bold; padding: 4px 10px;
    border-radius: 5px; border: 1px solid #30363d;
    background: #21262d; color: #e6edf3;
    min-height: 22px;
}
QPushButton:hover { background: #30363d; border-color: #6c5ce7; }
QPushButton:pressed { background: #6c5ce7; }
QPushButton:disabled { color: #484f58; background: #161b22; }

QRadioButton { font-size: 11px; spacing: 4px; color: #c9d1d9; }
QRadioButton::indicator { width: 14px; height: 14px; }

QScrollBar:vertical {
    background: #0d1117; width: 6px; border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: #30363d; border-radius: 3px; min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QSplitter::handle { background: #21262d; width: 2px; }
QSplitter::handle:hover { background: #6c5ce7; }

QFormLayout { spacing: 4px; }
"""


if __name__ == '__main__':
    app = QApplication(sys.argv)
    apply_stylesheet(app, theme='dark_teal.xml')
    app.setStyleSheet(app.styleSheet() + CUSTOM_CSS)
    window = CafePosterQt()
    window.show()
    sys.exit(app.exec())

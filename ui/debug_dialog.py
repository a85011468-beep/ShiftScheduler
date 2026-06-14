import pandas as pd
from datetime import datetime, timedelta
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QCheckBox, QTextEdit, QGroupBox, QMessageBox)
from PySide6.QtGui import QFont, QColor

from config.settings import (WORK_SHIFTS, OFF_SHIFTS, NOON_SHIFTS, NIGHT_SHIFTS,
                             FORBIDDEN_AFTER_NOON, FORBIDDEN_AFTER_NIGHT, SHIFT_DEMANDS)

class DebugDialog(QDialog):
    def __init__(self, db_manager, start_date, end_date, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"🛠️ 班表規則診斷器 ({start_date} 至 {end_date})")
        self.resize(800, 600)
        self.db = db_manager
        self.start_date = start_date
        self.end_date = end_date
        
        self.setup_ui()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        
        # 左側：控制面板 (勾選條件)
        left_panel = QVBoxLayout()
        
        info_label = QLabel(f"<b>診斷區間：</b><br>{self.start_date} ~ {self.end_date}")
        left_panel.addWidget(info_label)
        
        # 硬規則群組
        group_hard = QGroupBox("🚨 勞基法與物理硬規則")
        layout_hard = QVBoxLayout()
        self.cb_seven_in_one = QCheckBox("七休一 (任意連7天內必有1天休/例)")
        self.cb_seven_in_one.setChecked(True)
        self.cb_weekly_r = QCheckBox("每週一例 (週日至週六必有1天R)")
        self.cb_weekly_r.setChecked(True)
        self.cb_shift_clash = QCheckBox("交接班相剋 (午禁早、夜禁早午)")
        self.cb_shift_clash.setChecked(True)
        self.cb_daily_demands = QCheckBox("每日班別人數上下限")
        self.cb_daily_demands.setChecked(True)
        
        layout_hard.addWidget(self.cb_seven_in_one)
        layout_hard.addWidget(self.cb_weekly_r)
        layout_hard.addWidget(self.cb_shift_clash)
        layout_hard.addWidget(self.cb_daily_demands)
        group_hard.setLayout(layout_hard)
        left_panel.addWidget(group_hard)
        
        # 軟規則與特殊限制群組
        group_soft = QGroupBox("⚖️ 專案與排班特殊限制")
        layout_soft = QVBoxLayout()
        self.cb_mid_a_limit = QCheckBox("中A班單日不得超過 1 人")
        self.cb_mid_a_limit.setChecked(True)
        self.cb_consecutive_off = QCheckBox("不連續排休 4 天 (人工圖釘除外)")
        self.cb_consecutive_off.setChecked(True)
        
        layout_soft.addWidget(self.cb_mid_a_limit)
        layout_soft.addWidget(self.cb_consecutive_off)
        group_soft.setLayout(layout_soft)
        left_panel.addWidget(group_soft)
        
        left_panel.addStretch()
        
        self.btn_run = QPushButton("🚀 執行勾選診斷")
        self.btn_run.setStyleSheet("background-color: #E91E63; color: white; font-weight: bold; height: 40px;")
        self.btn_run.clicked.connect(self.run_diagnostics)
        left_panel.addWidget(self.btn_run)
        
        # 右側：報告輸出區
        right_panel = QVBoxLayout()
        right_panel.addWidget(QLabel("<b>📋 診斷報告輸出：</b>"))
        self.text_output = QTextEdit()
        self.text_output.setReadOnly(True)
        self.text_output.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; background-color: #F5F5F5;")
        right_panel.addWidget(self.text_output)
        
        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 3)

    def run_diagnostics(self):
        self.text_output.clear()
        self.log("啟動診斷引擎...\n資料載入中...", "blue")
        
        # 1. 載入排班資料
        employees = self.db.get_all_active_employees()
        emp_ids = [str(e['emp_id']).strip() for e in employees]
        
        dates = pd.date_range(start=self.start_date, end=self.end_date).strftime('%Y-%m-%d').tolist()
        schedules = self.db.get_schedule_by_date_range(dates[0], dates[-1])
        
        # 建立字典加速查詢: dict_sched[(emp_id, date)] = shift_code
        dict_sched = {}
        dict_locked = {} # 記錄是否為圖釘
        for s in schedules:
            eid = str(s['emp_id']).strip()
            dict_sched[(eid, s['date'])] = s['shift_code']
            dict_locked[(eid, s['date'])] = s['is_locked']

        errors = []
        
        # 2. 執行勾選的檢查項目
        if self.cb_seven_in_one.isChecked():
            errors.extend(self._check_seven_in_one(emp_ids, dates, dict_sched))
            
        if self.cb_weekly_r.isChecked():
            errors.extend(self._check_weekly_r(emp_ids, dates, dict_sched))
            
        if self.cb_shift_clash.isChecked():
            errors.extend(self._check_shift_clash(emp_ids, dates, dict_sched))
            
        if self.cb_daily_demands.isChecked():
            errors.extend(self._check_daily_demands(dates, dict_sched))
            
        if self.cb_mid_a_limit.isChecked():
            errors.extend(self._check_mid_a_limit(dates, dict_sched))
            
        if self.cb_consecutive_off.isChecked():
            errors.extend(self._check_consecutive_off(emp_ids, dates, dict_sched, dict_locked))

        # 3. 輸出報告
        self.log("\n==========================================", "black")
        if not errors:
            self.log("\n✅ 恭喜！目前班表完美通過所有已勾選的規則檢驗。", "green")
        else:
            self.log(f"\n❌ 發現 {len(errors)} 項違規：\n", "red")
            for err in errors:
                self.log(f"👉 {err}", "black")

    def log(self, text, color="black"):
        html = f"<span style='color: {color};'>{text.replace(chr(10), '<br>')}</span>"
        self.text_output.append(html)

    # ==========================================================
    # 診斷邏輯實作區
    # ==========================================================
    def _check_seven_in_one(self, emp_ids, dates, dict_sched):
        errs = []
        for eid in emp_ids:
            for i in range(len(dates) - 6):
                window = [dates[i+j] for j in range(7)]
                work_count = sum(1 for d in window if dict_sched.get((eid, d)) in WORK_SHIFTS or dict_sched.get((eid, d)) in ['L', 'P'])
                if work_count == 7:
                    errs.append(f"[七休一] {eid} 於 {window[0]} 至 {window[-1]} 連續出勤/排休達 7 天。")
        return errs

    def _check_weekly_r(self, emp_ids, dates, dict_sched):
        errs = []
        weeks = {}
        for d_str in dates:
            d_obj = datetime.strptime(d_str, '%Y-%m-%d')
            days_since_sun = (d_obj.weekday() + 1) % 7
            sun_str = (d_obj - timedelta(days=days_since_sun)).strftime('%Y-%m-%d')
            if sun_str not in weeks: weeks[sun_str] = []
            weeks[sun_str].append(d_str)

        for eid in emp_ids:
            for sun_str, week_dates in weeks.items():
                if len(week_dates) == 7: # 只檢查完整週
                    r_count = sum(1 for d in week_dates if dict_sched.get((eid, d)) == 'R')
                    if r_count == 0:
                        errs.append(f"[每週一例] {eid} 於 {sun_str} 該週沒有 R 假。")
                    elif r_count > 1:
                        errs.append(f"[每週一例] {eid} 於 {sun_str} 該週排了 {r_count} 天 R 假 (依法每週限1天)。")
        return errs

    def _check_shift_clash(self, emp_ids, dates, dict_sched):
        errs = []
        for eid in emp_ids:
            for i in range(len(dates) - 1):
                today = dates[i]
                tmr = dates[i+1]
                s_today = dict_sched.get((eid, today))
                s_tmr = dict_sched.get((eid, tmr))
                if s_today in NOON_SHIFTS and s_tmr in FORBIDDEN_AFTER_NOON:
                    errs.append(f"[交接班] {eid} 於 {today} 跨 {tmr} 違規：午班接{s_tmr}。")
                if s_today in NIGHT_SHIFTS and s_tmr in FORBIDDEN_AFTER_NIGHT:
                    errs.append(f"[交接班] {eid} 於 {today} 跨 {tmr} 違規：夜班接{s_tmr}。")
        return errs

    def _check_daily_demands(self, dates, dict_sched):
        errs = []
        for d in dates:
            daily_counts = {}
            for (_, d_iter), shift in dict_sched.items():
                if d_iter == d and shift:
                    daily_counts[shift] = daily_counts.get(shift, 0) + 1
            
            for shift, (min_req, max_req) in SHIFT_DEMANDS.items():
                count = daily_counts.get(shift, 0)
                if count < min_req:
                    errs.append(f"[人數下限] {d} 的 {shift} 僅有 {count} 人，低於最低需求 {min_req} 人。")
                elif count > max_req:
                    errs.append(f"[人數上限] {d} 的 {shift} 有 {count} 人，超出最大需求 {max_req} 人。")
        return errs

    def _check_mid_a_limit(self, dates, dict_sched):
        errs = []
        for d in dates:
            count = sum(1 for (_, d_iter), shift in dict_sched.items() if d_iter == d and shift == '01中A')
            if count > 1:
                errs.append(f"[中A人數] {d} 的 01中A 排了 {count} 人 (單日限1人)。")
        return errs

    def _check_consecutive_off(self, emp_ids, dates, dict_sched, dict_locked):
        errs = []
        for eid in emp_ids:
            for i in range(len(dates) - 3):
                window = [dates[i+j] for j in range(4)]
                off_count = sum(1 for d in window if dict_sched.get((eid, d)) in OFF_SHIFTS)
                if off_count == 4:
                    locked_off_count = sum(1 for d in window if dict_sched.get((eid, d)) in OFF_SHIFTS and dict_locked.get((eid, d)) == 1)
                    if locked_off_count < 4:
                        errs.append(f"[連休防呆] {eid} 於 {window[0]} 至 {window[-1]} 系統連續排休4天 (非全圖釘鎖定)。")
        return errs
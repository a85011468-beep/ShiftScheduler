from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QDateEdit, QMessageBox, QFileDialog)
from PySide6.QtCore import QDate
import pandas as pd
from engine.solver import ScheduleEngine

class SchedulerDialog(QDialog):
    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🚀 排班與匯出中心")
        self.resize(450, 200)
        self.db = db_manager

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 1. 獨立的日期選擇區 (排班/匯出專用，不受主畫面八週限制)
        date_layout = QHBoxLayout()
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addDays(-QDate.currentDate().day() + 1))
        date_layout.addWidget(QLabel("目標運算/匯出區間："))
        date_layout.addWidget(self.date_start)

        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate().addDays(self.date_start.date().daysInMonth() - QDate.currentDate().day()))
        date_layout.addWidget(QLabel("至"))
        date_layout.addWidget(self.date_end)
        layout.addLayout(date_layout)

        # 2. 執行按鈕
        self.btn_run = QPushButton("⚡ 執行智能排班")
        self.btn_run.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; height: 45px; font-size: 14px;")
        self.btn_run.clicked.connect(self.on_run_engine_clicked)
        layout.addWidget(self.btn_run)

        # 3. 匯出按鈕
        self.btn_export = QPushButton("💾 匯出 Excel 班表")
        self.btn_export.setStyleSheet("height: 40px;")
        self.btn_export.clicked.connect(self.on_export_clicked)
        layout.addWidget(self.btn_export)

    def get_selected_dates(self):
        return self.date_start.date().toString("yyyy-MM-dd"), self.date_end.date().toString("yyyy-MM-dd")

    def on_run_engine_clicked(self):
        start_date, end_date = self.get_selected_dates()
        engine = ScheduleEngine(self.db)
        
        # 呼叫排班大腦
        success, message = engine.run_scheduler(start_date, end_date)
        if success:
            QMessageBox.information(self, "排班結果", message)
            # 關閉視窗，讓主畫面接手重新整理
            self.accept() 
        else:
            QMessageBox.warning(self, "排班失敗", message)

    def on_export_clicked(self):
        start_date, end_date = self.get_selected_dates()
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        if not schedules:
            QMessageBox.warning(self, "匯出失敗", "指定區間內無班表可匯出！")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "匯出班表", f"排班表_{start_date}_至_{end_date}.xlsx", "Excel Files (*.xlsx)")
        if file_path:
            try:
                df = pd.DataFrame(schedules)
                pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')
                pivot_df.to_excel(file_path)
                QMessageBox.information(self, "匯出成功", f"檔案已儲存至：\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "匯出失敗", f"寫入錯誤：\n{str(e)}")
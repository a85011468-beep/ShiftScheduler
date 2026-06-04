from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFileDialog,
                               QLabel, QDateEdit)
from PySide6.QtCore import Qt, QDate
import pandas as pd
from database.db_manager import DatabaseManager
from database.data_importer import DataImporter
from engine.solver import ScheduleEngine

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧排班系統 - 八週變形工時架構")
        self.resize(1900, 900)
        self.db = DatabaseManager()

        self.setup_ui()
        self.refresh_table()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # === 頂部工具列 ===
        toolbar_layout = QHBoxLayout()

        # 1. 匯入按鈕
        self.btn_import = QPushButton("📂 匯入名單")
        self.btn_import.clicked.connect(self.on_import_clicked)
        toolbar_layout.addWidget(self.btn_import)

        # 2. 日期選擇區 (重點更新)
        toolbar_layout.addWidget(QLabel(" 📅 顯示/排班起訖："))
        
        # 起始日期選擇器 (預設為當月 1 號)
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addDays(-QDate.currentDate().day() + 1))
        toolbar_layout.addWidget(self.date_start)
        
        toolbar_layout.addWidget(QLabel("至"))
        
        # 結束日期選擇器 (預設為當月月底)
        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate().addDays(self.date_start.date().daysInMonth() - QDate.currentDate().day()))
        toolbar_layout.addWidget(self.date_end)

        # 重新整理按鈕 (切換日期後更新畫面)
        self.btn_refresh = QPushButton("🔄 讀取班表")
        self.btn_refresh.clicked.connect(self.refresh_table)
        toolbar_layout.addWidget(self.btn_refresh)

        # 3. 排班與匯出按鈕
        self.btn_run_engine = QPushButton("🚀 執行智能排班")
        self.btn_run_engine.setStyleSheet("font-weight: bold; background-color: #4CAF50; color: white;")
        self.btn_run_engine.clicked.connect(self.on_run_engine_clicked)
        toolbar_layout.addWidget(self.btn_run_engine)

        self.btn_export = QPushButton("💾 匯出 Excel")
        self.btn_export.clicked.connect(self.on_export_clicked)
        toolbar_layout.addWidget(self.btn_export)

        layout.addLayout(toolbar_layout)

        # === 核心顯示區 (表格) ===
        self.table = QTableWidget()
        layout.addWidget(self.table)

    def get_selected_dates(self):
        """輔助函式：取得 UI 上選擇的日期字串"""
        start_date = self.date_start.date().toString("yyyy-MM-dd")
        end_date = self.date_end.date().toString("yyyy-MM-dd")
        return start_date, end_date


    def refresh_table(self):
        start_date, end_date = self.get_selected_dates()
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        
        if not schedules:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        # 1. 撈取員工靜態資料，建立職級對照表
        employees = self.db.get_all_active_employees()
        level_dict = {e['emp_id']: e['job_level'] for e in employees}
        name_dict = {e['emp_id']: e['name'] for e in employees}

        # 2. 轉換為 Pandas DataFrame
        df = pd.DataFrame(schedules)
        pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')

        # 3. 自訂排序邏輯：職級 M 優先，接著按員工編號排序
        def sort_key(emp_id):
            level = level_dict.get(emp_id, 'Normal')
            # 優先權重：M 為 0排最前面，其他為 1。次要條件為 emp_id 本身。
            return (0 if level == 'M' else 1, emp_id)

        sorted_emp_ids = sorted(pivot_df.index, key=sort_key)
        pivot_df = pivot_df.reindex(sorted_emp_ids)

        # 4. 繪製表格 UI
        self.table.setRowCount(len(pivot_df))
        self.table.setColumnCount(len(pivot_df.columns))
        self.table.setHorizontalHeaderLabels(pivot_df.columns)
        
        # 將 Y 軸標籤改為「員工編號 - 姓名」方便閱讀
        y_labels = [f"{emp_id} {name_dict.get(emp_id, '')}" for emp_id in pivot_df.index]
        self.table.setVerticalHeaderLabels(y_labels)

        # 5. 填寫儲存格資料
        for row_idx, emp_id in enumerate(pivot_df.index):
            for col_idx, date in enumerate(pivot_df.columns):
                val = pivot_df.at[emp_id, date]
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)

        # ==========================================
        # 🌟 視覺與滾動條優化設定
        # ==========================================
        # 取消原本的 Stretch (自動擠滿)，改為 Fixed (固定大小)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        
        # 設定每個格子的黃金比例 (寬 75px, 高 40px)
        self.table.horizontalHeader().setDefaultSectionSize(75)
        self.table.verticalHeader().setDefaultSectionSize(40)
        
        # 開啟交替列背景色，讓眼睛不會看花
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("alternate-background-color: #f2f2f2;")
   
    def on_import_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "選擇員工名單", "", "Excel Files (*.xlsx)")
        if file_path:
            importer = DataImporter(self.db)
            success, msg = importer.import_employee_excel(file_path)
            if success:
                QMessageBox.information(self, "匯入成功", msg)
            else:
                QMessageBox.critical(self, "匯入失敗", msg)

    def on_run_engine_clicked(self):
        start_date, end_date = self.get_selected_dates()
        engine = ScheduleEngine(self.db)
        
        # 啟動排班
        success, message = engine.run_scheduler(start_date, end_date)
        if success:
            QMessageBox.information(self, "排班結果", message)
            self.refresh_table() 
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
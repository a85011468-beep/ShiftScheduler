from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFileDialog)
from PySide6.QtCore import Qt
import pandas as pd
from database.db_manager import DatabaseManager
from database.data_importer import DataImporter
from engine.solver import ScheduleEngine

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧排班系統 MVP")
        self.resize(1024, 600)
        self.db = DatabaseManager()

        self.setup_ui()
        self.refresh_table()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # === 建立頂部工具列 (橫向排列) ===
        toolbar_layout = QHBoxLayout()

        # 按鈕 1：匯入
        self.btn_import = QPushButton("📂 匯入員工名單")
        self.btn_import.setFixedHeight(40)
        self.btn_import.clicked.connect(self.on_import_clicked)
        toolbar_layout.addWidget(self.btn_import)

        # 按鈕 2：排班
        self.btn_run_engine = QPushButton("🚀 一鍵自動排班")
        self.btn_run_engine.setFixedHeight(40)
        self.btn_run_engine.setStyleSheet("font-weight: bold; background-color: #4CAF50; color: white;")
        self.btn_run_engine.clicked.connect(self.on_run_engine_clicked)
        toolbar_layout.addWidget(self.btn_run_engine)

        # 按鈕 3：匯出
        self.btn_export = QPushButton("💾 匯出最終班表")
        self.btn_export.setFixedHeight(40)
        self.btn_export.clicked.connect(self.on_export_clicked)
        toolbar_layout.addWidget(self.btn_export)

        layout.addLayout(toolbar_layout)

        # === 建立核心顯示區 (表格) ===
        self.table = QTableWidget()
        layout.addWidget(self.table)

    def refresh_table(self):
        schedules = self.db.get_schedule_by_date_range('2026-06-01', '2026-06-03')
        if not schedules:
            # 如果沒有資料，清空表格
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        df = pd.DataFrame(schedules)
        pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')

        self.table.setRowCount(len(pivot_df))
        self.table.setColumnCount(len(pivot_df.columns))
        self.table.setHorizontalHeaderLabels(pivot_df.columns)
        self.table.setVerticalHeaderLabels(pivot_df.index)

        for row_idx, emp_id in enumerate(pivot_df.index):
            for col_idx, date in enumerate(pivot_df.columns):
                val = pivot_df.at[emp_id, date]
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    # ================= 操作邏輯 =================

    def on_import_clicked(self):
        """處理匯入邏輯"""
        # 開啟檔案選擇視窗，限定只能選 .xlsx
        file_path, _ = QFileDialog.getOpenFileName(self, "選擇員工名單 Excel", "", "Excel Files (*.xlsx)")
        if file_path:
            importer = DataImporter(self.db)
            success, msg = importer.import_employee_excel(file_path)
            if success:
                QMessageBox.information(self, "匯入成功", msg)
                # 實務上這裡可能需要呼叫一個「自動鋪設空白班表」的函式
            else:
                QMessageBox.critical(self, "匯入失敗", msg)

    def on_run_engine_clicked(self):
        """觸發 OR-Tools 引擎"""
        start_date = '2026-06-01'
        end_date = '2026-06-03'
        engine = ScheduleEngine(self.db)
        
        # 為了避免找不到員工，這裡可以加個雙重保險，執行前先鋪設空白班表
        # (實務上可以寫在 db_manager 裡，目前依賴先前寫入的資料)
        
        success, message = engine.run_scheduler(start_date, end_date)
        if success:
            QMessageBox.information(self, "排班結果", message)
            self.refresh_table() 
        else:
            QMessageBox.warning(self, "排班失敗", message)

    def on_export_clicked(self):
        """處理匯出邏輯"""
        schedules = self.db.get_schedule_by_date_range('2026-06-01', '2026-06-03')
        if not schedules:
            QMessageBox.warning(self, "匯出失敗", "目前沒有班表可以匯出！")
            return

        # 使用 QFileDialog 讓使用者選擇存檔位置與檔名
        file_path, _ = QFileDialog.getSaveFileName(self, "匯出班表至 Excel", "排班結果.xlsx", "Excel Files (*.xlsx)")
        
        if file_path:
            try:
                df = pd.DataFrame(schedules)
                # 這裡直接轉置成橫向樞紐分析，方便人類在 Excel 裡閱讀
                pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')
                
                # 呼叫 Pandas 直接寫入 Excel
                pivot_df.to_excel(file_path)
                QMessageBox.information(self, "匯出成功", f"班表已成功存檔至：\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "匯出失敗", f"寫入檔案時發生錯誤：\n{str(e)}")
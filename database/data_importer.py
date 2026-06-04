import pandas as pd
from database.db_manager import DatabaseManager

class DataImporter:
    def __init__(self, db_manager=None):
        # 允許外部傳入已建立的 DB 連線，避免重複初始化
        self.db_manager = db_manager or DatabaseManager()

    def import_employee_excel(self, excel_path):
        """讀取 Excel 並更新進 SQLite"""
        try:
            # 讀取 Excel (底層需要 openpyxl 套件)
            df = pd.read_excel(excel_path)
            
            # 檢查欄位防呆
            required_cols = ['emp_id', 'name', 'job_level', 'shift_pref', 'block_pref', 'is_active']
            for col in required_cols:
                if col not in df.columns:
                    return False, f"Excel 缺少必要欄位: {col}"

            # 寫入 SQLite
            conn = self.db_manager.get_connection()
            df.to_sql('employees', conn, if_exists='replace', index=False)
            conn.close()
            
            return True, f"✅ 成功匯入 {len(df)} 筆員工資料！"
            
        except Exception as e:
            return False, f"❌ 匯入發生錯誤:\n{str(e)}"

    def import_historical_schedule(self, excel_path, start_date):
        """
        讀取過往歷史班表並存入資料庫。
        :param excel_path: Excel 檔案路徑
        :param start_date: 該班表第一天對應的真實日期 (格式 'YYYY-MM-DD')
        """
        try:
            # === [防呆機制] 檢查該歷史班表的起訖日是否已被鎖定 ===
            # (此處簡化判斷：用 start_date 往後推算第一週作為檢查代表)
            if self.db_manager.is_period_locked(start_date, start_date):
                return False, "❌ 匯入失敗：該區間已被系統鎖定結算，禁止匯入覆蓋！"
            # ===============================================
            
            # header=None 讓 Pandas 不要管表頭，把整張表當作純二維陣列讀取
            df = pd.read_excel(excel_path, header=None)
            # 讀取完之後立刻清理資料
            df = df.replace("r'", "r", regex=False)
            
            records = []
            # 計算班表天數：總欄數扣除前 3 欄 (索引 0, 1, 2)
            num_days = len(df.columns) - 3
            # 自動生成對應的日期序列
            dates = pd.date_range(start=start_date, periods=num_days).strftime('%Y-%m-%d').tolist()

            # 從第 3 列 (索引 2) 開始往下迭代每一位員工
            for idx, row in df.iloc[2:].iterrows():
                # 第 2 欄 (索引 1) 是員工編號
                emp_id = str(row[1]).strip()
                
                # 如果員工編號是空的，代表讀到底了或是空白列，直接跳過
                if emp_id == 'nan' or not emp_id:
                    continue
                    
                # 第 4 欄 (索引 3) 開始是每日班別
                for day_idx, date in enumerate(dates):
                    shift_val = row[3 + day_idx]
                    # 處理 Pandas 讀取空白儲存格產生的 nan
                    shift_code = str(shift_val).strip() if pd.notna(shift_val) else None
                    if shift_code == 'nan':
                        shift_code = None
                        
                    # 歷史班表視為「已發生且不可變動」，所以 is_locked 強制設為 1
                    records.append((emp_id, date, shift_code, 1))

            # 寫入 SQLite (使用 UPSERT 確保不會產生重複資料)
            conn = self.db_manager.get_connection()
            cursor = conn.cursor()
            cursor.executemany('''
                INSERT INTO schedule (emp_id, date, shift_code, is_locked)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(emp_id, date) DO UPDATE SET 
                    shift_code = excluded.shift_code,
                    is_locked = excluded.is_locked
            ''', records)
            conn.commit()
            conn.close()
            
            return True, f"✅ 成功匯入歷史班表！共處理 {len(records)} 筆格子紀錄。"
            
        except Exception as e:
            return False, f"❌ 歷史班表匯入發生錯誤:\n{str(e)}"
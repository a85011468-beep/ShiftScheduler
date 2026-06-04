import sqlite3
import os

class DatabaseManager:
    def __init__(self, db_path="config/schedule_data.db"):
        self.db_path = db_path
        # 確保資料夾存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def get_connection(self):
        return sqlite3.connect(self.db_path)
    def insert_or_update_schedule(self, emp_id, date, shift_code, is_locked=0):
        """新增或更新某一天的排班紀錄 (用於 UI 介面手動修改)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 使用 SQLite 的 UPSERT 語法 (若日期與人重複就更新，否則新增)
        cursor.execute('''
            INSERT INTO schedule (emp_id, date, shift_code, is_locked)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(emp_id, date) DO UPDATE SET 
                shift_code = excluded.shift_code,
                is_locked = excluded.is_locked
        ''', (emp_id, date, shift_code, is_locked))
        
        conn.commit()
        conn.close()

    def get_schedule_by_date_range(self, start_date, end_date):
        """撈取特定區間的排班表 (用於餵給 OR-Tools 或是繪製 UI)"""
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM schedule 
            WHERE date >= ? AND date <= ?
        ''', (start_date, end_date))
        
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def initialize_database(self):
        """建立所有需要的資料表"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 建立員工靜態設定表 (Schema)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS employees (
                emp_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                job_level TEXT NOT NULL,
                shift_pref TEXT NOT NULL,
                block_pref TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        ''')

	# 建立動態排班表 (Schema)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL,
                date TEXT NOT NULL,          -- 格式: YYYY-MM-DD
                shift_code TEXT,             -- 班別代號 (如: 01早M, L, Train, 空白代表待排)
                is_locked INTEGER DEFAULT 0, -- 1 代表這是手動排定的(如特休)，引擎不可更動
                FOREIGN KEY (emp_id) REFERENCES employees (emp_id),
                UNIQUE(emp_id, date) -- 加入這行，確保同一天同個人只有一筆紀錄
            )
        ''')
                
        conn.commit()
        conn.close()
        print("✅ SQLite 資料庫與員工資料表初始化完成！")



        # 建立索引，加快引擎查詢特定日期的速度
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_schedule_date ON schedule(date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_schedule_emp ON schedule(emp_id)')

    def get_all_active_employees(self):
        """引擎(Engine)運算時會呼叫這個方法來拿名單"""
        conn = self.get_connection()
        # 回傳字典格式，方便後續程式調用
        conn.row_factory = sqlite3.Row 
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM employees WHERE is_active = 1")
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
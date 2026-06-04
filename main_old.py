import sqlite3
import pandas as pd
import os
from ortools.sat.python import cp_model
from database.db_manager import DatabaseManager
from database.data_importer import DataImporter

def workflow_step_1_import_excel(db: DatabaseManager):
    """
    步驟 1：HR 從 Excel 匯入「半成品班表」(已包含畫休、事假、訓練)
    實務上這裡會呼叫 Pandas 讀取 .xlsx
    """
    print("🔄 [步驟 1] 讀取 Excel 並寫入資料庫...")
    # 這裡我們模擬從 Excel 讀取到的「手動排定」資料
    # 假設我们要排 2026-06-01 到 06-03 這三天的班表
    mock_excel_data = [
        # Alice 6/1 請特休 (L)
        ('T00557', '2026-06-01', 'L', 1),
        # Bob 6/2 去受訓 (Train)
        ('T02233', '2026-06-02', 'Train', 1),
        # 其他格子都是空白 (用 None 表示待排)，且 is_locked 為 0
        ('T00557', '2026-06-02', None, 0),
        ('T00557', '2026-06-03', None, 0),
        ('T02233', '2026-06-01', None, 0),
        ('T02233', '2026-06-03', None, 0),
    ]
    
    conn = db.get_connection()
    cursor = conn.cursor()
    # 使用 UPSERT 語法，把 Excel 的狀態寫入資料庫
    cursor.executemany('''
        INSERT INTO schedule (emp_id, date, shift_code, is_locked)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(emp_id, date) DO UPDATE SET 
            shift_code = excluded.shift_code,
            is_locked = excluded.is_locked
    ''', mock_excel_data)
    conn.commit()
    conn.close()
    print("✅ 已將 Excel 請假狀態存入 SQLite。\n")

def workflow_step_2_run_engine(db: DatabaseManager, start_date: str, end_date: str):
    print("🧠 [步驟 2] 啟動 OR-Tools 排班引擎...")
    
    # === [除錯點 1：盤點資料輸入] ===
    employees = db.get_all_active_employees()
    schedules = db.get_schedule_by_date_range(start_date, end_date)
    
    print(f"   -> 🔍 讀取到 {len(employees)} 位在職員工")
    if len(employees) == 0:
        print("   ❌ 致命錯誤：員工名單為空！引擎無法排班。請確認是否執行過 excel 匯入。")
        return # 直接中斷程式
        
    print(f"   -> 🔍 讀取到 {len(schedules)} 筆班表/請假紀錄")
    # ===============================

    dict_sched = {(s['emp_id'], s['date']): s for s in schedules}
    
    model = cp_model.CpModel()
    emp_ids = [e['emp_id'] for e in employees]
    dates = ['2026-06-01', '2026-06-02', '2026-06-03']
    ALL_STATES = ['01早M', '01午M', '01夜B1', 'L', 'Train', 'r']
    
    works = {}
    for emp_id in emp_ids:
        for date in dates:
            for state in ALL_STATES:
                works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')
                
    # 限制 1：每人每天只有一種狀態
    for emp_id in emp_ids:
        for date in dates:
            model.AddExactlyOne(works[(emp_id, date, state)] for state in ALL_STATES)
            
    # 限制 2：套用已鎖定狀態
    for emp_id in emp_ids:
        for date in dates:
            record = dict_sched.get((emp_id, date))
            if record and record['is_locked'] == 1 and record['shift_code'] in ALL_STATES:
                model.Add(works[(emp_id, date, record['shift_code'])] == 1)
                print(f"   -> 🔒 鎖定條件: {emp_id} 於 {date} 排定 {record['shift_code']}")
                
    # 限制 3：每天至少要有一個人上「01早M」
    for date in dates:
        model.Add(sum(works[(emp_id, date, '01早M')] for emp_id in emp_ids) >= 1)

    # === [除錯點 2：啟動引擎的 X 光機] ===
    solver = cp_model.CpSolver()
    # 打開這行！引擎會把底層的 C++ 運算邏輯、為何衝突全部印在終端機上
    # 💡 終極防呆 1：強制設定運算時間上限 (10秒)
    # 時間一到，不管有沒有排出來都會強制停止，防止無限卡死
    solver.parameters.max_time_in_seconds = 10.0 
    
    print("\n   ⏳ 開始求解...")
    
    # 💡 終極防呆 2：捕捉執行結果
    status = solver.Solve(model)
    
    # 💡 終極防呆 3：第一時間把底層狀態碼「無條件」印出來
    print(f"\n   🛑 引擎停止運作。原始狀態代碼: {status}")
    print(f"   (狀態碼對照表: {cp_model.OPTIMAL}=完美解, {cp_model.FEASIBLE}=合法解, {cp_model.INFEASIBLE}=無解/條件衝突, {cp_model.UNKNOWN}=超時卡死, {cp_model.MODEL_INVALID}=語法錯誤)")
    print("   ========================================")
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        results_to_update = []
        for emp_id in emp_ids:
            for date in dates:
                for state in ALL_STATES:
                    if solver.Value(works[(emp_id, date, state)]) == 1:
                        record = dict_sched.get((emp_id, date))
                        if not record or record['is_locked'] == 0:
                            results_to_update.append((state, emp_id, date))
                            
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.executemany('''
            UPDATE schedule 
            SET shift_code = ? 
            WHERE emp_id = ? AND date = ? AND is_locked = 0
        ''', results_to_update)
        conn.commit()
        conn.close()
        print("   ✅ 運算結果已成功覆蓋回 SQLite！")
    else:
        print("   💡 請檢查「鎖定條件」是否與「限制3」衝突。例如：是不是把所有人都畫休了，導致沒人能上早班？")

def workflow_step_3_export_to_ui(db: DatabaseManager, start_date: str, end_date: str):
    """
    步驟 3：UI 或報表從資料庫撈出最終結果呈現
    """
    print("📊 [步驟 3] 最終班表結果：")
    final_schedules = db.get_schedule_by_date_range(start_date, end_date)
    
    # 轉成 Pandas DataFrame 漂亮地印出來 (模擬 UI 的表格顯示)
    df = pd.DataFrame(final_schedules)
    # 將 Long Format 轉回 Excel 的 Wide Format，方便人類閱讀
    pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code')
    print(pivot_df.to_string())

if __name__ == "__main__":
    # ==========================================
    # 開發模式特權：每次執行前，先無情刪除舊資料庫
    # ==========================================
    db_file_path = "config/schedule_data.db"
    if os.path.exists(db_file_path):
        os.remove(db_file_path)
        print("🗑️ [開發模式] 已清除舊有受污染的資料庫。")
        
    # ==========================================
    # 重新初始化與執行
    # ==========================================
    db = DatabaseManager()
    db.initialize_database()
    
    # 1. 重新匯入乾淨的靜態員工名單 (這步很重要，千萬不能漏！)
    importer = DataImporter()
    importer.import_employee_excel("employee_settings.xlsx")
    print("✅ 已重新匯入 excel 員工靜態設定。\n")
    
    # 2. 執行我們寫好的工作流
    workflow_step_1_import_excel(db)
    workflow_step_2_run_engine(db, start_date='2026-06-01', end_date='2026-06-03')
    workflow_step_3_export_to_ui(db, start_date='2026-06-01', end_date='2026-06-03')
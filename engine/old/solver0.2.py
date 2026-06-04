import pandas as pd
from ortools.sat.python import cp_model

class ScheduleEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        # 定義系統支援的所有班別
        self.ALL_STATES = ['01早M', '01午M', '01夜B1', 'L', 'Train', 'r']

    def run_scheduler(self, start_date, end_date):
        """執行排班運算，並自動將結果寫回資料庫"""
        # 1. 從資料庫撈取「真實員工名單」與「已存在班表」
        employees = self.db.get_all_active_employees()
        if not employees:
            return False, "❌ 找不到任何員工資料，請確認是否已匯入 Excel 名單。"
            
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        dict_sched = {(s['emp_id'], s['date']): s for s in schedules}

        # 動態萃取員工 ID 列表
        emp_ids = [e['emp_id'] for e in employees]
        
        # 動態產生日期列表 (利用 Pandas 自動生成連續日期字串)
        dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()

        # 2. 初始化模型
        model = cp_model.CpModel()
        works = {}

        # [宣告變數]
        for emp_id in emp_ids:
            for date in dates:
                for state in self.ALL_STATES:
                    works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')

        # [限制 1] 每人每天只能有一種狀態
        for emp_id in emp_ids:
            for date in dates:
                model.AddExactlyOne([works[(emp_id, date, state)] for state in self.ALL_STATES])

        # [限制 2] 鎖定手動排定的假別 (L, Train 等)
        for emp_id in emp_ids:
            for date in dates:
                record = dict_sched.get((emp_id, date))
                if record and record['is_locked'] == 1 and record['shift_code'] in self.ALL_STATES:
                    model.Add(works[(emp_id, date, record['shift_code'])] == 1)

        # [限制 3] 每天至少 1 人上早班 (基礎測試限制，後續再堆疊勞基法)
        for date in dates:
            model.Add(sum([works[(emp_id, date, '01早M')] for emp_id in emp_ids]) >= 1)

        # 3. 設定求解器並運算
        solver = cp_model.CpSolver()
        solver.parameters.log_search_progress = False
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = 10.0 

        status = solver.Solve(model)

        # 4. 處理結果並寫回資料庫
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            results_to_update = []
            for emp_id in emp_ids:
                for date in dates:
                    for state in self.ALL_STATES:
                        if solver.Value(works[(emp_id, date, state)]) == 1:
                            record = dict_sched.get((emp_id, date))
                            # 只覆蓋沒有被鎖定的格子
                            if not record or record['is_locked'] == 0:
                                results_to_update.append((state, emp_id, date))

            # 寫入 SQLite
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.executemany('''
                UPDATE schedule 
                SET shift_code = ? 
                WHERE emp_id = ? AND date = ? AND is_locked = 0
            ''', results_to_update)
            conn.commit()
            conn.close()
            
            return True, "✅ 引擎排班成功！已更新至資料庫。"
        else:
            return False, f"❌ 求解失敗 (狀態碼: {status})。請檢查排班條件是否與請假衝突。"
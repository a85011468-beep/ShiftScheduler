import pandas as pd
from ortools.sat.python import cp_model
from PySide6.QtCore import QSettings, QDate
from datetime import datetime, timedelta
from PySide6.QtWidgets import QMessageBox

# 🛡️ 引入統一設定檔 (新增 MANAGER_ONLY_SHIFTS)
from config.settings import ALL_STATES, WORK_SHIFTS, SHIFT_DEMANDS, MANAGER_ONLY_SHIFTS

class ScheduleEngine:
    def __init__(self, db_manager):
        self.db = db_manager

    def _ensure_blank_grid(self, employees, dates):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        blank_grid = [(emp['emp_id'], d, None, 0) for emp in employees for d in dates]
        cursor.executemany('''
            INSERT OR IGNORE INTO schedule (emp_id, date, shift_code, is_locked)
            VALUES (?, ?, ?, ?)
        ''', blank_grid)
        conn.commit()
        conn.close()

    def run_scheduler(self, start_date, end_date, leave_quotas=None):
        if leave_quotas is None: leave_quotas = {}
        
        employees = self.db.get_all_active_employees()
        if not employees:
            return False, "❌ 找不到員工資料，請先匯入名單。"

        emp_ids = [e['emp_id'] for e in employees]
        
        # ==========================================
        # 🧠 [新增] 建立極度乾淨的職級對照名冊
        # ==========================================
        job_levels = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}

        target_dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()

        settings = QSettings("MyUnit", "ShiftScheduler")
        anchor_str = settings.value("audit_start_date", "")
        if not anchor_str:
            anchor_str = QDate.currentDate().addDays(-QDate.currentDate().day() + 1).toString("yyyy-MM-dd")
        
        anchor_date = datetime.strptime(anchor_str, '%Y-%m-%d')
        
        unique_cycles = set()
        for d_str in target_dates:
            dt = datetime.strptime(d_str, '%Y-%m-%d')
            days_diff = (dt - anchor_date).days
            cycle_offset = days_diff // 56
            c_start_dt = anchor_date + timedelta(days=cycle_offset * 56)
            unique_cycles.add(c_start_dt)
            
        expanded_dates_set = set(target_dates)
        all_cycles_info = [] 
        
        for c_start_dt in sorted(list(unique_cycles)):
            c_dates = pd.date_range(start=c_start_dt, end=c_start_dt + timedelta(days=55)).strftime('%Y-%m-%d').tolist()
            expanded_dates_set.update(c_dates)
            all_cycles_info.append((c_start_dt.strftime('%Y-%m-%d'), (c_start_dt + timedelta(days=55)).strftime('%Y-%m-%d'), c_dates))
            
        eval_dates = sorted(list(expanded_dates_set))

        self._ensure_blank_grid(employees, eval_dates)

        schedules = self.db.get_schedule_by_date_range(eval_dates[0], eval_dates[-1])
        dict_sched = {(s['emp_id'], s['date']): s for s in schedules}

        debug_msg = "🧠 引擎多週期參數攔截：\n\n"
        debug_msg += f"基準定位點：{anchor_str}\n"
        debug_msg += f"目標排班範圍：{start_date} 至 {end_date}\n"
        debug_msg += "本次觸發之 56 天法規週期群：\n"
        for s, e, _ in all_cycles_info:
            debug_msg += f" 📅 區間：{s} ~ {e}\n"
        
        debug_msg += "\n各週期內員工已鎖定(📌)之 R/r 統計：\n"
        for s, e, c_dates in all_cycles_info:
            debug_msg += f" [{s} ~ {e}]\n"
            for emp_id in emp_ids:
                emp_R = sum(1 for d in c_dates if dict_sched.get((emp_id, d)) and dict_sched.get((emp_id, d))['shift_code'] == 'R' and dict_sched.get((emp_id, d))['is_locked'] == 1)
                emp_r = sum(1 for d in c_dates if dict_sched.get((emp_id, d)) and dict_sched.get((emp_id, d))['shift_code'] == 'r' and dict_sched.get((emp_id, d))['is_locked'] == 1)
                alert = " ⚠️(超標危險)" if emp_R > 8 or emp_r > 8 else ""
                debug_msg += f"   - {emp_id}: 例(R) {emp_R}天, 休(r) {emp_r}天{alert}\n"
                
        debug_box = QMessageBox()
        debug_box.setWindowTitle("🛠️ 引擎週期 Debug 探針")
        debug_box.setText(debug_msg)
        debug_box.setStyleSheet("QLabel { font-size: 13px; font-family: Consolas; }") 
        debug_box.exec()

        eval_start_dt = datetime.strptime(eval_dates[0], '%Y-%m-%d')
        history_start = (eval_start_dt - timedelta(days=6)).strftime('%Y-%m-%d')
        history_end = (eval_start_dt - timedelta(days=1)).strftime('%Y-%m-%d')
        history_schedules = self.db.get_schedule_by_date_range(history_start, history_end)
        dict_history = {(s['emp_id'], s['date']): s['shift_code'] for s in history_schedules}

        model = cp_model.CpModel()
        works = {}

        for emp_id in emp_ids:
            for date in eval_dates:
                for state in ALL_STATES:
                    works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')

        for emp_id in emp_ids:
            # 💡 [新增] 驗明正身：是不是管理層？
            is_manager = job_levels.get(str(emp_id).strip(), 'Normal') in ('M', 'Chief')
            
            for date in eval_dates:
                model.AddExactlyOne([works[(emp_id, date, state)] for state in ALL_STATES])
                record = dict_sched.get((emp_id, date))
                
                if date in target_dates:
                    if record and record['is_locked'] == 1 and record['shift_code'] in ALL_STATES:
                        model.Add(works[(emp_id, date, record['shift_code'])] == 1)
                    else:
                        model.Add(works[(emp_id, date, 'Train')] == 0)
                        model.Add(works[(emp_id, date, '日')] == 0)
                        model.Add(works[(emp_id, date, '01早')] == 0)
                        model.Add(works[(emp_id, date, '01午')] == 0)
                        model.Add(works[(emp_id, date, '01夜')] == 0)
                        
                        # 🚨 職級階級防線 (真實目標區間)
                        if not is_manager:
                            for m_shift in MANAGER_ONLY_SHIFTS:
                                model.Add(works[(emp_id, date, m_shift)] == 0)
                else:
                    if record and record['shift_code'] in ALL_STATES:
                        model.Add(works[(emp_id, date, record['shift_code'])] == 1)
                    else:
                        # 🚨 職級階級防線 (模擬區間) 
                        # 確保引擎在腦補放假或塞班別時，也不會把一般員工排去上主管班
                        if not is_manager:
                            for m_shift in MANAGER_ONLY_SHIFTS:
                                model.Add(works[(emp_id, date, m_shift)] == 0)

        for date in target_dates:
            for shift, (min_req, max_req) in SHIFT_DEMANDS.items():
                shift_vars = [works[(emp_id, date, shift)] for emp_id in emp_ids]
                model.Add(sum(shift_vars) >= min_req)
                model.Add(sum(shift_vars) <= max_req)

        all_eval_dates = pd.date_range(start=history_start, end=eval_dates[-1]).strftime('%Y-%m-%d').tolist()
        for emp_id in emp_ids:
            for i in range(len(all_eval_dates) - 6):
                window_dates = all_eval_dates[i : i + 7]
                window_vars = []
                for d in window_dates:
                    if d < eval_dates[0]:
                        shift = dict_history.get((emp_id, d))
                        if shift in WORK_SHIFTS: window_vars.append(1)
                    else:
                        for shift in WORK_SHIFTS: window_vars.append(works[(emp_id, d, shift)])
                model.Add(sum(window_vars) <= 6)

        for _, _, c_dates in all_cycles_info:
            for emp_id in emp_ids:
                emp_r_vars = [works[(emp_id, d, 'r')] for d in c_dates]
                emp_R_vars = [works[(emp_id, d, 'R')] for d in c_dates]
                model.Add(sum(emp_r_vars) == 8)
                model.Add(sum(emp_R_vars) == 8)

        for emp_id in emp_ids:
            l_target = leave_quotas.get(emp_id, {}).get('L', 0)
            p_target = leave_quotas.get(emp_id, {}).get('P', 0)
            emp_L_vars = [works[(emp_id, d, 'L')] for d in target_dates]
            emp_P_vars = [works[(emp_id, d, 'P')] for d in target_dates]
            model.Add(sum(emp_L_vars) == l_target)
            model.Add(sum(emp_P_vars) == p_target)

        all_work_vars = [works[(emp_id, d, shift)] for emp_id in emp_ids for d in target_dates for shift in WORK_SHIFTS]
        model.Maximize(sum(all_work_vars))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 15.0 
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            results_to_update = []
            for emp_id in emp_ids:
                for date in eval_dates:
                    for state in ALL_STATES:
                        if solver.Value(works[(emp_id, date, state)]) == 1:
                            record = dict_sched.get((emp_id, date))
                            if not record or record['is_locked'] == 0:
                                results_to_update.append((state, emp_id, date))

            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.executemany('''
                UPDATE schedule SET shift_code = ? WHERE emp_id = ? AND date = ? AND is_locked = 0
            ''', results_to_update)
            conn.commit()
            conn.close()
            return True, "✅ 智能排班完成！已完美套用職級特權防線。"
        else:
            return False, f"❌ 求解失敗 (狀態碼: {status})。\n原因：極度可能是因為指定給管理層的班別總需求數，大於您現有的活躍管理層人數，導致無人可頂班。"
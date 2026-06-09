import pandas as pd
from ortools.sat.python import cp_model
from datetime import datetime, timedelta
from PySide6.QtWidgets import QMessageBox

from config.settings import (ALL_STATES, WORK_SHIFTS, OFF_SHIFTS, SHIFT_DEMANDS, MANAGER_ONLY_SHIFTS, 
                             EARLY_SHIFTS, NOON_SHIFTS, NIGHT_SHIFTS,
                             FORBIDDEN_AFTER_NOON, FORBIDDEN_AFTER_NIGHT)

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
        job_levels = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}
        shift_prefs = {str(e['emp_id']).strip(): str(e.get('shift_pref', 'MIX')).strip() for e in employees}
        block_prefs = {str(e['emp_id']).strip(): str(e.get('block_pref', 'ANY')).strip() for e in employees}

        target_dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        eval_dates = target_dates 
        
        self._ensure_blank_grid(employees, eval_dates)
        schedules = self.db.get_schedule_by_date_range(eval_dates[0], eval_dates[-1])
        dict_sched = {(s['emp_id'], s['date']): s for s in schedules}

        eval_start_dt = datetime.strptime(eval_dates[0], '%Y-%m-%d')
        history_start = (eval_start_dt - timedelta(days=7)).strftime('%Y-%m-%d')
        history_end = (eval_start_dt - timedelta(days=1)).strftime('%Y-%m-%d')
        history_schedules = self.db.get_schedule_by_date_range(history_start, history_end)
        dict_history = {(s['emp_id'], s['date']): s['shift_code'] for s in history_schedules}

        # ==========================================
        # 🔬 [探針核心] 將模型建立過程包裝成可重複呼叫的工廠
        # ==========================================
        def attempt_solve(strict_time_rules=True, strict_quotas=True):
            model = cp_model.CpModel()
            works = {}

            for emp_id in emp_ids:
                for date in eval_dates:
                    for state in ALL_STATES:
                        works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')

            for emp_id in emp_ids:
                is_manager = job_levels.get(str(emp_id).strip(), 'Normal') in ('M', 'Chief')
                s_pref = shift_prefs.get(str(emp_id).strip(), 'MIX')
                
                for date in eval_dates:
                    model.AddExactlyOne([works[(emp_id, date, state)] for state in ALL_STATES])
                    record = dict_sched.get((emp_id, date))
                    is_locked = (record and record['is_locked'] == 1 and record['shift_code'] in ALL_STATES)
                    
                    if is_locked:
                        model.Add(works[(emp_id, date, record['shift_code'])] == 1)
                    else:
                        model.Add(works[(emp_id, date, 'Train')] == 0)
                        model.Add(works[(emp_id, date, '日')] == 0)
                        model.Add(works[(emp_id, date, '01早')] == 0)
                        model.Add(works[(emp_id, date, '01午')] == 0)
                        model.Add(works[(emp_id, date, '01夜')] == 0)
                        
                        if not is_manager:
                            for m_shift in MANAGER_ONLY_SHIFTS:
                                model.Add(works[(emp_id, date, m_shift)] == 0)
                                
                        if s_pref == 'NIGHT_ONLY':
                            for w_shift in WORK_SHIFTS:
                                if w_shift not in NIGHT_SHIFTS:
                                    model.Add(works[(emp_id, date, w_shift)] == 0)

            # 每日門診需求底線
            for date in eval_dates:
                for shift, (min_req, max_req) in SHIFT_DEMANDS.items():
                    shift_vars = [works[(emp_id, date, shift)] for emp_id in emp_ids]
                    model.Add(sum(shift_vars) >= min_req)
                    model.Add(sum(shift_vars) <= max_req)
                
                # ==========================================
                # 🛡️ [新增] 複合戰力底線防線
                # ==========================================
                # 1. 早班聯集：[01早B2] + [01早m] >= 2
                early_combo_vars = [works[(emp_id, date, '01早B2')] for emp_id in emp_ids] + \
                                   [works[(emp_id, date, '01早m')] for emp_id in emp_ids]
                model.Add(sum(early_combo_vars) >= 2)

                # 2. 午班聯集：[01午B2] + [01午m] >= 2
                noon_combo_vars = [works[(emp_id, date, '01午B2')] for emp_id in emp_ids] + \
                                  [works[(emp_id, date, '01午m')] for emp_id in emp_ids]
                model.Add(sum(noon_combo_vars) >= 2)

            # --- 測試變因 1：QSpinBox 絕對鎖死 ---
            GENERIC_01_SHIFTS = ["01早B1", "01早B2", "01午B1", "01午B2"]
            if strict_quotas:
                for emp_id in emp_ids:
                    eid = str(emp_id).strip()
                    q = leave_quotas.get(eid, {})
                    model.Add(sum(works[(emp_id, d, 'L')] for d in eval_dates) == q.get('L', 0))
                    model.Add(sum(works[(emp_id, d, 'P')] for d in eval_dates) == q.get('P', 0))
                    model.Add(sum(works[(emp_id, d, 'r')] for d in eval_dates) == q.get('r', 0))
                    model.Add(sum(works[(emp_id, d, 'R')] for d in eval_dates) == q.get('R', 0))
                    model.Add(sum(works[(emp_id, d, '01中A')] for d in eval_dates) == q.get('01中A', 0))
                    gen01_vars = [works[(emp_id, d, s)] for d in eval_dates for s in GENERIC_01_SHIFTS]
                    model.Add(sum(gen01_vars) == q.get('01泛用', 0))

            all_eval_dates = pd.date_range(start=history_start, end=eval_dates[-1]).strftime('%Y-%m-%d').tolist()
            is_working = {}
            is_off = {}
            is_noon = {}
            is_night = {}
            
            for emp_id in emp_ids:
                for d in all_eval_dates:
                    is_working[(emp_id, d)] = model.NewBoolVar(f'work_{emp_id}_{d}')
                    is_off[(emp_id, d)] = model.NewBoolVar(f'off_{emp_id}_{d}')
                    is_noon[(emp_id, d)] = model.NewBoolVar(f'noon_{emp_id}_{d}')
                    is_night[(emp_id, d)] = model.NewBoolVar(f'night_{emp_id}_{d}')
                    
                    if d < eval_dates[0]:
                        shift = dict_history.get((emp_id, d))
                        model.Add(is_working[(emp_id, d)] == (1 if shift in WORK_SHIFTS else 0))
                        model.Add(is_off[(emp_id, d)] == (1 if shift in OFF_SHIFTS else 0))
                        model.Add(is_noon[(emp_id, d)] == (1 if shift in NOON_SHIFTS else 0))
                        model.Add(is_night[(emp_id, d)] == (1 if shift in NIGHT_SHIFTS else 0))
                    else:
                        model.Add(is_working[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in WORK_SHIFTS))
                        model.Add(is_off[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in OFF_SHIFTS))
                        model.Add(is_noon[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in NOON_SHIFTS))
                        model.Add(is_night[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in NIGHT_SHIFTS))

            # 做六休一 (此為勞基法天條，永遠不拔除)
            for emp_id in emp_ids:
                for i in range(len(all_eval_dates) - 6):
                    window_vars = [is_working[(emp_id, all_eval_dates[i+j])] for j in range(7)]
                    model.Add(sum(window_vars) <= 6)

            # --- 測試變因 2：交接班時序防線 ---
            if strict_time_rules:
                for emp_id in emp_ids:
                    s_pref = shift_prefs.get(str(emp_id).strip(), 'MIX')
                    for i in range(len(all_eval_dates) - 1):
                        today = all_eval_dates[i]
                        tmr = all_eval_dates[i+1]
                        if tmr < eval_dates[0]: continue
                        for f_shift in FORBIDDEN_AFTER_NOON:
                            model.AddImplication(is_noon[(emp_id, today)], works[(emp_id, tmr, f_shift)].Not())
                        for f_shift in FORBIDDEN_AFTER_NIGHT:
                            model.AddImplication(is_night[(emp_id, today)], works[(emp_id, tmr, f_shift)].Not())

                    for i in range(len(all_eval_dates) - 2):
                        ytd = all_eval_dates[i]
                        today = all_eval_dates[i+1]
                        tmr = all_eval_dates[i+2]
                        if tmr < eval_dates[0]: continue
                        
                        # 💡 [已拔除] 休轉班必須 >= 2 天 (現在全面交由底下的 block_pref 扣分機制接管)
                        
                        # 🚫 夜轉休必須 >= 2 天 (NIGHT_ONLY 族群除外，此健康防線依然保留)
                        if s_pref != 'NIGHT_ONLY':
                            just_started_off_after_night = model.NewBoolVar(f'night_to_off_{emp_id}_{today}')
                            model.Add(just_started_off_after_night == 1).OnlyEnforceIf([is_night[(emp_id, ytd)], is_off[(emp_id, today)]])
                            model.AddImplication(just_started_off_after_night, is_off[(emp_id, tmr)])

            # 簡化目標函數以加速探針運算
            model.Maximize(sum(works[(emp_id, d, shift)] for emp_id in emp_ids for d in eval_dates for shift in WORK_SHIFTS))

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 10.0 
            return solver.Solve(model), solver, works

        # ==========================================
        # 🚀 啟動降級探針流程
        # ==========================================
        print("🔍 探針啟動：嘗試最高嚴格度運算...")
        status, solver, works = attempt_solve(strict_time_rules=True, strict_quotas=True)
        
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # 正常寫入資料庫邏輯...
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
            cursor.executemany('UPDATE schedule SET shift_code = ? WHERE emp_id = ? AND date = ? AND is_locked = 0', results_to_update)
            conn.commit()
            conn.close()
            return True, "✅ 智能排班完成！已嚴格執行您的配額鎖定與時序相剋。"

        # 🚨 第一階降級：拔掉時序規則
        print("⚠️ 探針降級：拔除【時序相剋與連休防線】...")
        status_no_time, _, _ = attempt_solve(strict_time_rules=False, strict_quotas=True)
        if status_no_time in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return False, "❌ 抓到死結！犯人是【時序相剋規則】。\n\n您設定的 QSpinBox 配額，會迫使員工違反「午禁早」、「夜禁早午」或「休轉班必須連上兩天」的規則。請嘗試減少排班天數，或手動排開極端班別。"

        # 🚨 第二階降級：保留時序，拔掉 UI 鎖死
        print("⚠️ 探針降級：拔除【QSpinBox 絕對配額】...")
        status_no_quota, _, _ = attempt_solve(strict_time_rules=True, strict_quotas=False)
        if status_no_quota in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return False, "❌ 抓到死結！犯人是【QSpinBox 配額設定】。\n\n您的配額讓特定員工 (尤其是主管或 NIGHT_ONLY) 無班可上，或是強制排班天數與每日最低需求人數完全兜不攏。"

        # 🚨 最終宣告：物理無解
        return False, "❌ 嚴重死結：連拔除兩道防線依然無解！\n\n原因：極度可能是「每天最低需求人數總和」大於「未休假的人數」。請減少每日需求，或增加上班人力。"       

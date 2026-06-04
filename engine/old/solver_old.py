from ortools.sat.python import cp_model

def create_shift_model():
    model = cp_model.CpModel()
    
    # ==========================================
    # 1. 參數與常數定義 (實務上這些會從 SQLite / 參數檔 讀取)
    # ==========================================
    num_days = 30
    num_employees = 15
    
    # 定義所有「上班」班別
    SHIFTS = [
        '01早M', '01早m', '01午M', '01午m', 
        '01早B1', '01早B2', '01午B1', '01午B2', 
        '01中A', '01夜B1', '01夜B2'
    ]
    
    # 定義所有「休假與特殊狀態」
    LEAVES = ['r', 'R', 'P', 'L', 'Train'] # Train 就是手動加入的訓練
    
    # 所有可能的狀態總和
    ALL_STATES = SHIFTS + LEAVES
    
    # 建立分類群組，方便後續寫「擋班邏輯」
    EARLY_SHIFTS = ['01早M', '01早m', '01早B1', '01早B2']
    NOON_SHIFTS = ['01午M', '01午m', '01午B1', '01午B2']
    NIGHT_SHIFTS = ['01夜B1', '01夜B2']
    MID_SHIFTS = ['01中A']
    
    # 模擬員工職級參數 (0: 上半部可值M/m/夜, 1: 一般人員)
    # 實務上這是一個從資料庫撈出來的 Dictionary，例如 {0: True, 1: False...}
    can_work_M_and_night = {e: (e < 7) for e in range(num_employees)} 

    # ==========================================
    # 2. 宣告核心變數
    # ==========================================
    works = {}
    for e in range(num_employees):
        for d in range(num_days):
            for state in ALL_STATES:
                # works[員工, 天數, 狀態] = 1 代表那天處於該狀態
                works[(e, d, state)] = model.NewBoolVar(f'w_{e}_{d}_{state}')
                
    # 限制：每人每天【必定且只能】有一種狀態 (上班或休假)
    for e in range(num_employees):
        for d in range(num_days):
            model.AddExactlyOne(works[(e, d, state)] for state in ALL_STATES)

    # ==========================================
    # 3. 技能與職級限制 (Upper-half constraint)
    # ==========================================
    for e in range(num_employees):
        if not can_work_M_and_night[e]:
            # 如果不是上半部人員，把 M/m/夜班 的變數直接鎖死為 0
            for d in range(num_days):
                for s in ['01早M', '01早m', '01午M', '01午m', '01夜B1', '01夜B2']:
                    model.Add(works[(e, d, s)] == 0)

    # ==========================================
    # 4. 每天班別所需人數限制 (Daily Headcount)
    # ==========================================
    # 定義每個班別的人數需求 (Min, Max)
    shift_requirements = {
        '01早M': (1, 1), '01早m': (0, 1), '01午M': (1, 1), '01午m': (0, 1),
        '01早B1': (1, 2), '01早B2': (1, 2), '01午B1': (1, 2), '01午B2': (1, 2),
        '01中A': (0, 1), '01夜B1': (1, 1), '01夜B2': (1, 1)
    }
    
    for d in range(num_days):
        for shift, (min_req, max_req) in shift_requirements.items():
            assigned_count = sum(works[(e, d, shift)] for e in range(num_employees))
            model.Add(assigned_count >= min_req)
            model.Add(assigned_count <= max_req)
            
        # 每天總上班人數限制 (最少8人, 最多15人)
        total_working_today = sum(works[(e, d, s)] for e in range(num_employees) for s in SHIFTS)
        model.Add(total_working_today >= 8)
        model.Add(total_working_today <= 15)

    # ==========================================
    # 5. 輪班順序邏輯 (Sequence Constraints)
    # ==========================================
    for e in range(num_employees):
        for d in range(num_days - 1): # 檢查到倒數第二天即可
            
            # 條件 A：午班後不能接早班 (您提到日班，若日班有具體代號可加入此 List)
            # 邏輯寫法：如果今天上了午班，明天的早班狀態總和必須是 0
            for noon_s in NOON_SHIFTS:
                model.AddImplication(
                    works[(e, d, noon_s)], 
                    sum(works[(e, d+1, early_s)] for early_s in EARLY_SHIFTS) == 0
                )
                
            # 條件 B：夜班後不能接 L、早班、午班 (您提到的日班亦同)
            # 實務情境：因為夜班跨日，隔天白天實體上還在補眠，不能排假或白天班
            for night_s in NIGHT_SHIFTS:
                forbidden_next_day = EARLY_SHIFTS + NOON_SHIFTS + ['L']
                model.AddImplication(
                    works[(e, d, night_s)],
                    sum(works[(e, d+1, forbid_s)] for forbid_s in forbidden_next_day) == 0
                )
                
            # 條件 C：中A班前後皆可接... (這是寬鬆條件，我們「不寫限制」就等於預設允許)

    # ==========================================
    # 6. 目標函數：盡量接近平均人數 (Soft Constraint)
    # ==========================================
    # 假設平均理想值是 11 人。在 OR-Tools 中不能直接用浮點數算方差，
    # 替代方案是：我們給「接近11人的總數」較高的分數。
    # 這裡先省略複雜的線性化語法，保留架構概念。

    return model
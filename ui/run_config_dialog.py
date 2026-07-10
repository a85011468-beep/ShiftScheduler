from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QCheckBox, QGroupBox)
from PySide6.QtCore import Qt

class RunConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 智能排班發射控制台")
        self.resize(650, 450)
        self.config = {}
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        
        info_label = QLabel("請勾選本次排班欲套用的引擎規則：\n<span style='color: #757575;'>(註：勞基法相關硬性防線為系統底線，強制啟用無法取消)</span>")
        main_layout.addWidget(info_label)
        
        columns_layout = QHBoxLayout()
        
        # ==========================================
        # 🛡️ 左側：硬規則 (Hard Constraints)
        # ==========================================
        group_hard = QGroupBox("🚨 物理與勞基法防線 (硬限制)")
        layout_hard = QVBoxLayout()

        # 💡 [新增] 左側全選與全不選按鈕
        btn_layout_hard = QHBoxLayout()
        btn_all_hard = QPushButton("✅ 全選")
        btn_none_hard = QPushButton("❌ 全不選")
        btn_all_hard.clicked.connect(lambda: self.toggle_hard_rules(True))
        btn_none_hard.clicked.connect(lambda: self.toggle_hard_rules(False))
        btn_layout_hard.addWidget(btn_all_hard)
        btn_layout_hard.addWidget(btn_none_hard)
        layout_hard.addLayout(btn_layout_hard)
        layout_hard.addSpacing(5)
        
        
        # 強制鎖定不可取消的項目
        self.cb_law_7_in_1 = self._create_checkbox("七休一 (連續7天必有1休)", True)
        self.cb_law_r = self._create_checkbox("每週一例 (週日至六必有1R)", True)
        self.cb_clash = self._create_checkbox("交接班相剋 (如午不接早)", True)
        self.cb_demands = self._create_checkbox("滿足每日班別人數上下限", True)
        
        # 可自由切換的專案硬限制
        self.cb_strict_quotas = self._create_checkbox("嚴格遵守 QSpinBox 休假配額", True)
        self.cb_mid_a = self._create_checkbox("中A班單日不得超過 1 人", True)
        self.cb_no_4_off = self._create_checkbox("引擎禁止主動排連續 4 天休假", True)
        self.cb_night_2_off = self._create_checkbox("夜班下莊後強制連休 2 天", True) # 新增此行
        
        layout_hard.addWidget(self.cb_law_7_in_1)
        layout_hard.addWidget(self.cb_law_r)
        layout_hard.addWidget(self.cb_clash)
        layout_hard.addWidget(self.cb_demands)
        layout_hard.addSpacing(15)
        layout_hard.addWidget(self.cb_strict_quotas)
        layout_hard.addWidget(self.cb_mid_a)
        layout_hard.addWidget(self.cb_no_4_off)
        layout_hard.addStretch()
        layout_hard.addWidget(self.cb_night_2_off) # 新增此行將元件放入排版
        group_hard.setLayout(layout_hard)
        
        # ==========================================
        # ⚖️ 右側：軟規則 (Soft Preferences & Balance)
        # ==========================================
        group_soft = QGroupBox("⚖️ 偏好與公平性優化 (軟限制)")
        layout_soft = QVBoxLayout()

        # 💡 [新增] 右側全選與全不選按鈕
        btn_layout_soft = QHBoxLayout()
        btn_all_soft = QPushButton("✅ 全選")
        btn_none_soft = QPushButton("❌ 全不選")
        btn_all_soft.clicked.connect(lambda: self.toggle_soft_rules(True))
        btn_none_soft.clicked.connect(lambda: self.toggle_soft_rules(False))
        btn_layout_soft.addWidget(btn_all_soft)
        btn_layout_soft.addWidget(btn_none_soft)
        layout_soft.addLayout(btn_layout_soft)
        layout_soft.addSpacing(5)
        
        self.cb_shift_pref = self._create_checkbox("優先滿足員工【班別】意願", True)
        self.cb_block_pref = self._create_checkbox("優先滿足員工【連班】偏好", True)
        self.cb_loc_mix = self._create_checkbox("避免連續兩天同地點執勤 (G1/G2)", True)
        self.cb_night_seg = self._create_checkbox("盡量滿足夜班連續段數偏好", True)
        
        layout_soft.addSpacing(15)
        self.cb_bal_early_noon = self._create_checkbox("平分：每日早/午班人數均衡", True)
        #self.cb_bal_night = self._create_checkbox("平分：主管夜班天數平均 + 負債制", True)
        self.cb_bal_m_day = self._create_checkbox("平分：早M/午M天數平均 + 負債制", True)
        self.cb_bal_c = self._create_checkbox("平分：基層 C 班天數平均 + 負債制", True)
        self.cb_bal_a = self._create_checkbox("平分：中A班天數平均 + 負債制", True)
        self.cb_bal_support = self._create_checkbox("平分：溢流支援班(日)天數平均 + 負債制", True) # 💡 新增
        
        layout_soft.addWidget(self.cb_shift_pref)
        layout_soft.addWidget(self.cb_block_pref)
        layout_soft.addWidget(self.cb_loc_mix)
        layout_soft.addWidget(self.cb_night_seg)
        layout_soft.addWidget(self.cb_bal_early_noon)
        #layout_soft.addWidget(self.cb_bal_night)
        layout_soft.addWidget(self.cb_bal_m_day)
        layout_soft.addWidget(self.cb_bal_c)
        layout_soft.addWidget(self.cb_bal_a)
        layout_soft.addWidget(self.cb_bal_support) # 💡 新增
        layout_soft.addStretch()
        group_soft.setLayout(layout_soft)
        
        columns_layout.addWidget(group_hard)
        columns_layout.addWidget(group_soft)
        main_layout.addLayout(columns_layout)
        
        # ==========================================
        # 按鈕區
        # ==========================================
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        
        self.btn_run = QPushButton("⚡ 確認條件並開始排班")
        self.btn_run.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; height: 40px; font-size: 14px;")
        self.btn_run.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_run)
        main_layout.addLayout(btn_layout)

    def _create_checkbox(self, text, checked=True, disabled=False):
        cb = QCheckBox(text)
        cb.setChecked(checked)
        if disabled:
            cb.setDisabled(True)
            cb.setStyleSheet("color: #9E9E9E;") # 反灰字體
        return cb
    
    # 💡 [新增] 硬規則切換邏輯 (避開勞基法鎖定項目)
    def toggle_hard_rules(self, state):
        self.cb_law_7_in_1.setChecked(state)
        self.cb_law_r.setChecked(state)
        self.cb_clash.setChecked(state)
        self.cb_demands.setChecked(state)
        self.cb_strict_quotas.setChecked(state)
        self.cb_mid_a.setChecked(state)
        self.cb_no_4_off.setChecked(state)
        self.cb_night_2_off.setChecked(state)

    # 💡 [新增] 軟規則切換邏輯
    def toggle_soft_rules(self, state):
        self.cb_shift_pref.setChecked(state)
        self.cb_block_pref.setChecked(state)
        self.cb_loc_mix.setChecked(state)
        self.cb_night_seg.setChecked(state)
        self.cb_bal_early_noon.setChecked(state)
        #self.cb_bal_night.setChecked(state)
        self.cb_bal_m_day.setChecked(state)
        self.cb_bal_c.setChecked(state)
        self.cb_bal_a.setChecked(state)
        self.cb_bal_support.setChecked(state) # 💡 新增
        
    def get_config(self):
        """將 UI 的勾選狀態打包成 Dictionary 供引擎讀取"""
        return {
            'hard_law_7_in_1': self.cb_law_7_in_1.isChecked(),
            'hard_law_r': self.cb_law_r.isChecked(),
            'hard_clash': self.cb_clash.isChecked(),
            'hard_demands': self.cb_demands.isChecked(),
            
            'strict_quotas': self.cb_strict_quotas.isChecked(),
            'hard_mid_a': self.cb_mid_a.isChecked(),
            'hard_no_4_off': self.cb_no_4_off.isChecked(),
            'hard_night_2_off': self.cb_night_2_off.isChecked(), # 新增此行
            
            'soft_shift_pref': self.cb_shift_pref.isChecked(),
            'soft_block_pref': self.cb_block_pref.isChecked(),
            'soft_loc_mix': self.cb_loc_mix.isChecked(),
            'soft_night_seg': self.cb_night_seg.isChecked(),
            
            'soft_bal_early_noon': self.cb_bal_early_noon.isChecked(),
            #self.cb_bal_night.isChecked(),
            'soft_bal_m_day': self.cb_bal_m_day.isChecked(),
            'soft_bal_c': self.cb_bal_c.isChecked(),
            'soft_bal_a': self.cb_bal_a.isChecked(),
            'soft_bal_support': self.cb_bal_support.isChecked() # 💡 新增
        }
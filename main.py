import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    # 初始化 PySide6 應用程式
    app = QApplication(sys.argv)
    
    # 建立並顯示主視窗
    window = MainWindow()
    window.show()
    
    # 進入主迴圈 (讓視窗保持開啟，直到使用者點擊 X 關閉)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
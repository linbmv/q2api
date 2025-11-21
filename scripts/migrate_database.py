#!/usr/bin/env python3
"""
数据库路径迁移脚本
将数据库从 data/data.sqlite3 迁移到根目录 data.sqlite3
"""
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OLD_DB_PATH = BASE_DIR / "data" / "data.sqlite3"
NEW_DB_PATH = BASE_DIR / "data" / "data.sqlite3"

def migrate():
    if OLD_DB_PATH.exists():
        print(f"发现旧数据库: {OLD_DB_PATH}")
        if NEW_DB_PATH.exists():
            print(f"新数据库已存在: {NEW_DB_PATH}")
            response = input("是否覆盖新数据库? (y/N): ")
            if response.lower() != 'y':
                print("取消迁移")
                return

        print(f"迁移数据库到: {NEW_DB_PATH}")
        shutil.copy2(OLD_DB_PATH, NEW_DB_PATH)
        print("迁移完成!")
        print(f"建议备份后删除旧数据库: {OLD_DB_PATH}")
    else:
        print("未发现旧数据库文件，无需迁移")
        if NEW_DB_PATH.exists():
            print(f"当前数据库位置: {NEW_DB_PATH}")
        else:
            print("数据库文件不存在，首次运行时会自动创建")

if __name__ == "__main__":
    migrate()

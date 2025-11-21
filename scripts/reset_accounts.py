#!/usr/bin/env python3
"""
重新启用所有账号的脚本
将所有账号的 enabled 设置为 1，保留错误和成功次数
"""
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "data.sqlite3"


def reset_all_accounts():
    """重新启用所有账号（不重置错误和成功次数）"""
    if not DB_PATH.exists():
        print(f"错误: 数据库文件不存在: {DB_PATH}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 获取当前禁用的账号数量
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE enabled=0")
        disabled_count = cursor.fetchone()[0]
        
        # 获取总账号数量
        cursor.execute("SELECT COUNT(*) FROM accounts")
        total_count = cursor.fetchone()[0]
        
        print(f"数据库中共有 {total_count} 个账号")
        print(f"其中 {disabled_count} 个账号已被禁用")
        
        if disabled_count == 0:
            print("所有账号都已启用，无需操作")
            return
        
        # 只重新启用账号，不重置错误和成功次数
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        cursor.execute("""
            UPDATE accounts
            SET enabled=1, updated_at=?
            WHERE enabled=0
        """, (now,))
        conn.commit()

        print(f"[OK] 已重新启用 {disabled_count} 个账号")
        print("[OK] 保留了所有账号的错误和成功次数")
        
        # 显示更新后的状态
        cursor.execute("""
            SELECT id, label, enabled, error_count, success_count
            FROM accounts
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        
        print("\n当前账号状态:")
        print("-" * 80)
        print(f"{'ID':<38} {'标签':<20} {'启用':<6} {'错误':<6} {'成功':<6}")
        print("-" * 80)
        for row in rows:
            acc_id, label, enabled, error_count, success_count = row
            label = label or "(无标签)"
            enabled_str = "是" if enabled else "否"
            print(f"{acc_id:<38} {label:<20} {enabled_str:<6} {error_count or 0:<6} {success_count or 0:<6}")
    
    finally:
        cursor.close()
        conn.close()


def main():
    print("=" * 80)
    print("重新启用所有账号")
    print("=" * 80)
    print()
    
    try:
        reset_all_accounts()
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    print()
    print("=" * 80)
    print("操作完成")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    exit(main())
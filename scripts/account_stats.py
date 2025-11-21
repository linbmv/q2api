#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

# --- 配置 ---
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "data.sqlite3"
# --- 配置结束 ---


def get_db_connection():
    """建立并返回数据库连接，如果数据库不存在则退出。"""
    if not DB_PATH.exists():
        print(f"错误: 数据库文件未找到: {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"数据库连接错误: {e}", file=sys.stderr)
        sys.exit(1)


def check_table_and_columns(conn: sqlite3.Connection):
    """检查 'accounts' 表和必要的列是否存在。"""
    try:
        cursor = conn.execute("PRAGMA table_info(accounts)")
        columns = [row['name'] for row in cursor.fetchall()]
        if not columns:
            print("错误: 'accounts' 表不存在。", file=sys.stderr)
            sys.exit(1)

        required_cols = ['id', 'label', 'enabled', 'success_count', 'error_count', 'last_refresh_status', 'last_refresh_time']
        missing_cols = [col for col in required_cols if col not in columns]
        if missing_cols:
            print(f"错误: 'accounts' 表缺少以下列: {', '.join(missing_cols)}", file=sys.stderr)
            sys.exit(1)
    except sqlite3.Error as e:
        print(f"检查表结构时出错: {e}", file=sys.stderr)
        sys.exit(1)


def gather_stats():
    """连接数据库，查询并打印全面的账户统计信息。"""
    conn = get_db_connection()
    check_table_and_columns(conn)

    try:
        accounts = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    except sqlite3.Error as e:
        print(f"查询账户时出错: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    total_accounts = len(accounts)
    if total_accounts == 0:
        print("数据库中没有找到任何账户。")
        conn.close()
        return

    # --- 汇总统计 ---
    enabled_accounts = [acc for acc in accounts if acc['enabled'] == 1]
    disabled_accounts = [acc for acc in accounts if acc['enabled'] == 0]
    refresh_failed_accounts = [acc for acc in accounts if acc['last_refresh_status'] == 'failed']
    never_used_accounts = [acc for acc in accounts if acc['success_count'] == 0]
    error_accounts = [acc for acc in accounts if acc['error_count'] > 0]
    total_success_count = sum(acc['success_count'] for acc in accounts)

    print("--- 账户统计摘要 ---")
    print(f"总账户数: {total_accounts}")
    print(f"  - 启用中: {len(enabled_accounts)}")
    print(f"  - 已禁用: {len(disabled_accounts)}")
    print("-" * 20)
    print(f"Token刷新失败数: {len(refresh_failed_accounts)}")
    print(f"从未使用过的账户数: {len(never_used_accounts)}")
    print(f"有错误记录的账户数: {len(error_accounts)}")
    print(f"所有账户总成功调用次数: {total_success_count}")
    print("-" * 20)

    # --- 详细列表 ---
    print("\n--- 账户详细列表 ---")
    header = "| {:<8s} | {:<6s} | {:<15s} | {:<5s} | {:<5s} | {:<12s} | {:<20s} |".format(
        "状态", "启用", "标签 (Label)", "成功", "错误", "刷新状态", "最后刷新时间"
    )
    print(header)
    print("-" * len(header))

    for acc in accounts:
        # 状态图标
        status_icon = "[OK]" if acc['enabled'] else "[X]"
        if acc['last_refresh_status'] == 'failed':
            status_icon = "[!]"

        # 格式化输出
        enabled_str = "是" if acc['enabled'] else "否"
        label = acc['label'] if acc['label'] else "(无)"

        # 截断过长的标签
        if len(label) > 15:
            label = label[:12] + "..."

        last_refresh_time = acc['last_refresh_time'] if acc['last_refresh_time'] else "从未"

        print("| {:<10s} | {:<8s} | {:<15s} | {:<5d} | {:<5d} | {:<12s} | {:<20s} |".format(
            status_icon,
            enabled_str,
            label,
            acc['success_count'],
            acc['error_count'],
            acc['last_refresh_status'],
            last_refresh_time
        ))

    print("-" * len(header))
    conn.close()


def main():
    """脚本主入口"""
    gather_stats()
    sys.exit(0)


if __name__ == "__main__":
    main()

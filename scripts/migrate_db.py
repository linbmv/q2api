import asyncio
import aiosqlite
from pathlib import Path

async def main():
    """
    Applies database migrations to an existing SQLite database.
    - Adds indexes for performance.
    - Ensures all columns exist.
    """
    base_dir = Path(__file__).resolve().parent.parent
    db_path = base_dir / "data.sqlite3"

    if not db_path.exists():
        print(f"Database not found at {db_path}, skipping migration.")
        return

    print(f"Applying migrations to {db_path}...")

    async with aiosqlite.connect(db_path) as conn:
        # 1. Add indexes
        print("Creating indexes...")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_enabled ON accounts (enabled);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_created_at ON accounts (created_at);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_success_count ON accounts (success_count);")
        print("Indexes created successfully.")

        # 2. Add missing columns (idempotent)
        print("Checking for missing columns...")
        try:
            async with conn.execute("PRAGMA table_info(accounts)") as cursor:
                rows = await cursor.fetchall()
                cols = [row[1] for row in rows]
                
                if "enabled" not in cols:
                    print("Adding 'enabled' column...")
                    await conn.execute("ALTER TABLE accounts ADD COLUMN enabled INTEGER DEFAULT 1")
                
                if "error_count" not in cols:
                    print("Adding 'error_count' column...")
                    await conn.execute("ALTER TABLE accounts ADD COLUMN error_count INTEGER DEFAULT 0")
                
                if "success_count" not in cols:
                    print("Adding 'success_count' column...")
                    await conn.execute("ALTER TABLE accounts ADD COLUMN success_count INTEGER DEFAULT 0")
                
                print("Column check complete.")
        except Exception as e:
            print(f"Error checking/adding columns: {e}")

        await conn.commit()
        print("Migrations applied successfully.")

if __name__ == "__main__":
    asyncio.run(main())
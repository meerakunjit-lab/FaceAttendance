import sqlite3

DB_NAME = "attendance.db"

conn = sqlite3.connect(DB_NAME)
cur = conn.cursor()

# Check if admin exists
cur.execute("SELECT * FROM users WHERE username='admin'")
if not cur.fetchone():
    # Create default admin
    cur.execute("INSERT INTO users (username, password) VALUES (?,?)", ("mppl","0000"))
    print("Default admin created: username=mppl, password=0000")
else:
    print("Admin already exists")

conn.commit()
conn.close()

import sqlite3

conn = sqlite3.connect("attendance.db")
cursor = conn.cursor()

rows = cursor.execute("SELECT * FROM users").fetchall()

print("USERS TABLE:")
for row in rows:
    print(row)

conn.close()

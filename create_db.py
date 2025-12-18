import sqlite3

# DB connect (illa na create aagum)
conn = sqlite3.connect('attendance.db')
c = conn.cursor()

# Users table create
c.execute('''
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    password TEXT
)
''')

conn.commit()
conn.close()
print("Database ready!")

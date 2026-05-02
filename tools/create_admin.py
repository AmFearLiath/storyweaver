import sqlite3, hashlib
from pathlib import Path
DB='f:/Adventure/backend/adventure.db'
conn=sqlite3.connect(DB)
cur=conn.cursor()
cur.execute("SELECT id,username,created_at FROM users")
rows=cur.fetchall()
print('Existing users:')
for r in rows:
    print(r)
# Check admin
cur.execute("SELECT id FROM users WHERE username=?",('admin',))
if cur.fetchone():
    print('admin exists')
else:
    pw='admin123'
    h=hashlib.sha256(pw.encode('utf-8')).hexdigest()
    cur.execute("INSERT INTO users(username,password_hash) VALUES(?,?)",('admin',h))
    conn.commit()
    print('admin created with password admin123')
conn.close()

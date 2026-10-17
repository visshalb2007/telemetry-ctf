import sqlite3

path = r'd:\VIT_MAIN\PROJECTS\GDG\extracted\opt\gdg-diner\prisma\dev.db'
conn = sqlite3.connect(path)
cur = conn.cursor()

for t in ['User', 'Order', 'OrderItem']:
    cur.execute(f'PRAGMA table_info("{t}");')
    cols = [c[1] for c in cur.fetchall()]
    print(f"=== Table '{t}': {cols}")
    cur.execute(f'SELECT * FROM "{t}";')
    for r in cur.fetchall():
        print(" ", r)

conn.close()

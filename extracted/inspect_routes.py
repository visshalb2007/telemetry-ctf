import os
import re

app_dir = r'd:\VIT_MAIN\PROJECTS\GDG\extracted\opt\gdg-diner\.next\server\app'
routes = [
    'api/admin/logs/route.js',
    'api/admin/orders/route.js',
    'api/admin/orders/[id]/route.js',
    'api/admin/orders/[id]/status/route.js',
    'api/auth/login/route.js',
    'api/auth/register/route.js',
    'api/me/route.js',
    'api/orders/route.js',
    'logs/route.js',
]

for r in routes:
    path = os.path.join(app_dir, *r.split('/'))
    if os.path.exists(path):
        print('========================================')
        print('ROUTE:', r)
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        # Find userland export or route handler
        # Look for GET, POST, PUT, DELETE functions
        m = re.findall(r'async function (?:GET|POST|PUT|DELETE|PATCH)\([^)]*\)\{[^}]+\}', content)
        if m:
            for fn in m:
                print('--- FN ---')
                print(fn[:1000])
        else:
            # print snippet around userland
            idx = content.find('userland:')
            if idx != -1:
                print(content[idx:idx+1500])
            else:
                print(content[:1000])

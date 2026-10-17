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
        
        # In Next.js webpack output, user code is usually in module functions like:
        # 1234: (a, b, c) => { ... }
        # Let's search for function definitions before userland
        idx = content.find('let y=new e.AppRouteRouteModule')
        if idx == -1:
            idx = content.find('AppRouteRouteModule')
        
        # print first 3000 chars before AppRouteRouteModule
        start = max(0, idx - 4000)
        print(content[start:idx])

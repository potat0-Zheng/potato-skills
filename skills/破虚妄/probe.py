# -*- coding: utf-8 -*-
import socket, sys

targets = {
    "china_sources": ("www.piyao.org.cn", 443),
    "gov_stats": ("data.stats.gov.cn", 443),
    "world_bank": ("api.worldbank.org", 443),
    "google_factcheck": ("contentfactchecktools.googleapis.com", 443),
    "wayback": ("archive.org", 443),
}

results = {}
for name, (host, port) in targets.items():
    try:
        socket.setdefaulttimeout(5)
        s = socket.create_connection((host, port), timeout=5)
        s.close()
        results[name] = "OK"
    except Exception as e:
        results[name] = f"UNREACHABLE ({e})"

for name, status in results.items():
    print(f"PROBE:{name}={status}")

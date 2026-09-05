# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\Users\郑懿宸\.claude\skills\check\tools')
from china_sources import ChinaVerifyClient
client = ChinaVerifyClient()

# Search multiple topics related to Trump's China visit
results = {}

# 1. Piyao search for any rumors about the visit
results['piyao_trump'] = client.piyao_search('特朗普访华')

# 2. Thepaper search for news
results['thepaper_trump'] = client.thepaper_search('特朗普访华 2026')

# 3. Weibo search for public discussion
results['weibo_trump'] = client.weibo_search('特朗普访华')

# 4. General verify
results['verify_trump'] = client.verify('特朗普2026年5月13日至15日对中国进行国事访问', claim_type='general')

print(json.dumps(results, ensure_ascii=False))

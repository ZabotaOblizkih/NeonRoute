import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NeonRoute/0.9.0",
    "Accept": "application/json"
}

# ID версии v2.5 CounterfeitXL из предыдущего шага
# Сначала найдем ID версии через поиск
search_url = "https://civitai.com/api/v1/models?query=counterfeitxl%20v25&types=Checkpoint&limit=1"
r = requests.get(search_url, headers=headers, timeout=8.0).json()
v25_id = None
for v in r["items"][0]["modelVersions"]:
    if "2.5" in v["name"] or "25" in v["name"]:
        v25_id = v["id"]
        break

print(f"[*] ID версии v2.5 на Civitai: {v25_id}")
if v25_id:
    ver_url = f"https://civitai.com/api/v1/model-versions/{v25_id}"
    ver_data = requests.get(ver_url, headers=headers, timeout=8.0).json()
    images = ver_data.get("images", [])
    print(f"[*] Получено изображений из версии: {len(images)}")
    for idx, img in enumerate(images):
        meta = img.get("meta")
        if meta:
            print(f"\n[+] Найдены реальные авторские метаданные в изображении #{idx+1}!")
            print(f"    - Sampler: {meta.get('sampler') or meta.get('Sampler')}")
            print(f"    - Steps:   {meta.get('steps')}")
            print(f"    - CFG:     {meta.get('cfgScale')}")
            print(f"    - ClipSkip:{meta.get('Clip skip') or meta.get('clipSkip')}")
            print(f"    - Size:    {meta.get('Size')}")
            break

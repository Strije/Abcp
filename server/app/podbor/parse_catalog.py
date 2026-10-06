import json
import re
import pandas as pd
from tqdm import tqdm

# Путь к вашему jsonl-файлу
INPUT_FILE = "./llm_ready_automarket_dataset.jsonl"   # поменяйте на ваш путь
OUTPUT_FILE = "catalog.parquet"

# Регулярка для извлечения бренда и артикула из output
# Пример: "...соответствует производитель 'fenox', артикул для заказа в каталоге: bp43234."
PATTERN = re.compile(
    r"производитель\s+'([^']+)'.*?артикул.*?:\s*([^\s.]+)",
    re.IGNORECASE | re.DOTALL,
)

rows = []
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in tqdm(f, desc="Парсинг"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        
        output = obj.get("output", "")
        # Извлекаем название из instruction
        instr = obj.get("instruction", "")
        m_name = re.search(r"следующей детали:\s*(.+)$", instr, re.IGNORECASE)
        name = m_name.group(1).strip() if m_name else ""
        
        # Извлекаем бренд и артикул
        m = PATTERN.search(output)
        if not m or not name:
            continue
        brand, article = m.group(1).strip(), m.group(2).strip()
        
        rows.append({
            "name": name,
            "brand": brand,
            "article": article,
        })

df = pd.DataFrame(rows)
df = df.drop_duplicates(subset=["name", "brand", "article"]).reset_index(drop=True)
df.to_parquet(OUTPUT_FILE, index=False)
print(f"Сохранено: {len(df):,} позиций → {OUTPUT_FILE}")
print(df.head(10).to_string(index=False))
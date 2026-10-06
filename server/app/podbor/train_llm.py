# train_llm.py
import torch
from unsloth import FastLanguageModel
from datasets import load_dataset
from transformers import TrainingArguments
from trl import SFTTrainer

# 1. Конфигурация модели и LoRA-адаптеров под 8 ГБ VRAM
max_seq_length = 2048  # Длина контекста
dtype = None           # Автоопределение (Float16 для RTX 3060 Ti)
load_in_4bit = True    # Принудительная 4-битная квантизация для экономии памяти

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = "unsloth/llama-3-8b-Instruct-bnb-4bit", # Базовый "умный" мозг
    max_seq_length = max_seq_length,
    dtype = dtype,
    load_in_4bit = load_in_4bit,
)

# Настраиваем LoRA параметры (обучаем только 1-2% важных весов, ответственных за память артикулов)
model = FastLanguageModel.get_peft_model(
    model,
    r = 16, 
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha = 16,
    lora_dropout = 0, 
    bias = "none",    
    use_gradient_checkpointing = "unsloth", # Экономит VRAM
    random_state = 3407,
    use_rslora = False,  
    loftq_config = None, 
)

# 2. Подготовка вашего датасета прайсов
# Шаблон сообщения, к которому привыкла Llama-3
prompt_template = """<|begin_of_text|><|start_header_id|>system<|end_header_id|>
{}<|eot_id|><|start_header_id|>user<|end_header_id|>
{}<|eot_id|><|start_header_id|>assistant<|end_header_id|>
{}<|eot_id|>"""

def formatting_prompts_func(examples):
    systems = examples["system"]
    inputs  = examples["input"]
    outputs = examples["output"]
    texts = []
    for system, input_text, output in zip(systems, inputs, outputs):
        text = prompt_template.format(system, input_text, output)
        texts.append(text)
    return { "text" : texts, }

print("Загрузка вашего датасета прайсов...")
dataset = load_dataset("json", data_files="llm_ready_automarket_dataset.jsonl", split="train")
dataset = dataset.map(formatting_prompts_func, batched = True,)

# 3. Настройка параметров тренировки
trainer = SFTTrainer(
    model = model,
    tokenizer = tokenizer,
    train_dataset = dataset,
    dataset_text_field = "text",
    max_seq_length = max_seq_length,
    dataset_num_proc = 2,
    packing = False, # Скорость для коротких строк
    args = TrainingArguments(
        per_device_train_batch_size = 2, # Маленький батч специально под 8 ГБ VRAM
        gradient_accumulation_steps = 4,
        warmup_steps = 5,
        max_steps = 60, # Сколько шагов учить. Поставьте 1000-2000 для полной базы прайсов
        learning_rate = 2e-4,
        fp16 = not torch.cuda.is_bf16_supported(),
        bf16 = torch.cuda.is_bf16_supported(),
        logging_steps = 1,
        optim = "adamw_8bit", # 8-битный оптимизатор экономит память
        weight_decay = 0.01,
        lr_scheduler_type = "linear",
        seed = 3407,
        output_dir = "outputs",
    ),
)

# 4. Запуск обучения
print("Инициализация обучения. Поехали!")
trainer_stats = trainer.train()

# 5. Сохранение обученной модели (локально)
print("Обучение завершено. Сохраняю Локальную ИИ модель...")
model.save_pretrained("zapgpt_lora_model")
tokenizer.save_pretrained("zapgpt_lora_model")
print("Успешно! Папка 'zapgpt_lora_model' готова к интеграции с вашим ботом.")
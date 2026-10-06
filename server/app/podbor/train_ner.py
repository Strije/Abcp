# train_ner.py
import spacy
from spacy.training import Example
import random
from train_data import TRAIN_DATA

def train_custom_ner():
    print("Загрузка базовой модели ru_core_news_sm...")
    nlp = spacy.load("ru_core_news_sm")
    
    # Получаем или создаем компонент NER
    if "ner" not in nlp.pipe_names:
        ner = nlp.add_pipe("ner", last=True)
    else:
        ner = nlp.get_pipe("ner")
        
    # Добавляем наши новые автомобильные теги в модель
    for _, annotations in TRAIN_DATA:
        for ent in annotations.get("entities"):
            ner.add_label(ent[2])
            
    # Отключаем остальные компоненты (токенизатор, теггер), чтобы учить только NER
    other_pipes = [pipe for pipe in nlp.pipe_names if pipe != "ner"]
    
    print("Начало обучения локальной NER-модели...")
    with nlp.disable_pipes(*other_pipes):
        optimizer = nlp.resume_training()
        
        # Запускаем 30 эпох обучения (для маленького датасета этого достаточно)
        for epoch in range(30):
            random.shuffle(TRAIN_DATA)
            losses = {}
            
            for text, annotations in TRAIN_DATA:
                doc = nlp.make_doc(text)
                example = Example.from_dict(doc, annotations)
                nlp.update([example], drop=0.3, losses=losses, sgd=optimizer)
                
            if epoch % 5 == 0:
                print(f"Эпоха {epoch} - Ошибки (Losses): {losses}")
                
    # Сохраняем готовую модель в локальную папку
    output_dir = "./auto_ner_model"
    nlp.to_disk(output_dir)
    print(f"Обучение завершено! Модель сохранена в папку '{output_dir}'")

if __name__ == "__main__":
    train_custom_ner()
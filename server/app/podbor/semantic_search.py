from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

class LocalSemanticSearch:
    def __init__(self):
        print("Загрузка компактной локальной модели смыслов...")
        # Модель от Сбера/Яндекса, весит всего ~400 МБ, идеально знает русский язык и автосленг
        self.model = SentenceTransformer('DeepPavlov/rubert-base-cased-sentence')
        self.catalog_vectors = None
        self.catalog_node_ids = []

    def index_catalog_tree(self, tree_index_nodes: list):
        """Вызывается 1 раз при запуске. Переводит все названия групп Laximo в векторы"""
        print(f"Индексация дерева каталога: расчет векторов для {len(tree_index_nodes)} узлов...")
        node_names = [node.name for node in tree_index_nodes]
        self.catalog_node_ids = [node.id for node in tree_index_nodes]

        # Модель кодирует текст в смысловые координаты
        self.catalog_vectors = self.model.encode(node_names, convert_to_numpy=True)
        print("Индексация завершена. Локальный ИИ-поиск готов.")

    def match_query(self, client_text: str, top_k: int = 3):
        """Принимает 'хочу поменять гидрики' и находит лучшие официальные группы"""
        query_vector = self.model.encode([client_text], convert_to_numpy=True)

        # Математически считаем косинусное сходство между запросом и каталогом
        similarities = cosine_similarity(query_vector, self.catalog_vectors)[0]

        # Сортируем по убыванию совпадения
        top_indices = np.argsort(similarities)[::-1][:top_k]

        matched_groups = []
        for idx in top_indices:
            score = similarities[idx]
            # Если сходство по смыслу выше 40-50%, узел нам подходит
            if score >= 0.45:
                matched_groups.append((self.catalog_node_ids[idx], score))
        return matched_groups
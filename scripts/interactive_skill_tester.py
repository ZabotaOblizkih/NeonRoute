import sys
import os
import logging
import numpy as np

logging.basicConfig(level=logging.ERROR)
for name in ["NeonRoute", "fastembed", "urllib3", "onnxruntime"]:
    logging.getLogger(name).setLevel(logging.ERROR)

sys.path.insert(0, os.path.abspath("."))
from fastembed import TextEmbedding

TEST_ANCHORS = {
    "deep_engineer": [
        "Объясни физику, кинематику и механику работы этого узла или механизма",
        "Как устроена эта деталь, расчет нагрузок, сопромат и принципиальная схема",
        "Инженерный расчет прочности, гидравлическая система или электромеханика агрегата",
        "Принцип действия и спецификация промышленного механизма, трансмиссии или двигателя",
        "Фундаментальная физика, законы механики, термодинамика и технические расчеты"
    ],
    "gallery": [
        "Нарисуй концепт-арт или детальную цифровую иллюстрацию",
        "Визуализируй эту сцену в виде художественного изображения или портрета",
        "Сделай красивый визуальный кадр по текстовому описанию",
        "Сгенерируй изображение персонажа или локации в высоком качестве",
        "Покажи мне как выглядит этот персонаж, животное, предмет или сцена в виде арта"
    ],
    "code_architect": [
        "Напиши чистый рабочий код функции или скрипт с обработкой исключений",
        "Помоги найти причину бага, разобрать traceback ошибки и отрефакторить код",
        "Спроектируй архитектуру сервиса, схему базы данных или асинхронный пайплайн",
        "Реализуй эффективный алгоритм со строгой типизацией и тестами pytest",
        "Оптимизируй производительность скрипта, запросы к базе или сетевой код"
    ],
    "text_crafter": [
        "Отредактируй текст, убери воду и канцелярит, улучши подачу",
        "Составь убедительное деловое письмо, коммерческое предложение или статью",
        "Сделай краткую структурированную выжимку, тезисы и конспект материала",
        "Перепиши черновик в емком понятном инфостиле для публикации",
        "Помоги составить пост, цепляющий заголовок и структуру текста"
    ],
    "analyst_logic": [
        "Проведи глубокий сравнительный анализ и покажи ключевые различия",
        "В чем принципиальная разница между этими двумя решениями или технологиями",
        "Разбери компромиссы, скрытые риски, плюсы и минусы каждого варианта",
        "Сопоставь альтернативы и помоги выбрать оптимальный путь с учетом trade-offs",
        "Составь матрицу выбора, взвесив сильные и слабые стороны подходов",
        "Проанализируй данные, таблицу или отчет и сделай логические выводы"
    ],
    "wellness_bio": [
        "Сколько калорий мне нужно потреблять в день, расчет нормы базового обмена и дефицита",
        "Как эргономично настроить рабочее место, монитор и кресло для осанки",
        "Правильная биомеханика упражнений и предотвращение травм спины и суставов",
        "Оптимизация фаз сна, циркадные ритмы и способы восстановления сил",
        "Влияние сидячего образа жизни на организм, баланс БЖУ и разминка"
    ],
    "tutor_feynman": [
        "Объясни мне эту сложную концепцию простыми словами на пальцах",
        "Приведи наглядную бытовую аналогию, чтобы понять физическую суть явления",
        "В чем фундаментальный смысл этой абстрактной теории без заумных терминов",
        "Как устроены квантовая физика, расщепление атомов или теория относительности наглядно",
        "Интуитивное понимание сложных научных тем через жизненные примеры"
    ]
}

def resolve_test_skill(query: str, embedder, anchor_matrices: dict) -> tuple:
    if not query or len(query.strip()) < 3:
        return None, 0.0, "Слишком короткий"

    q_low = query.lower().strip()

    # 1. Быстрые эвристики (0 мс)
    gallery_keys = (
        "нарисуй", "сгенерируй арт", "сгенерируй картинку", "визуализируй", "сделай арт",
        "покажи как выглядит", "покажи как выглядят", "покажи картинку", "покажи арт",
        "покажи рисунок", "просьба показать"
    )
    if any(k in q_low for k in gallery_keys):
        return "GALLERY", 1.0, "0 ms эвристика"

    analyst_keys = (
        "сравни", "сравнить", "сравнение", "сравнительн",
        "разница между", "разницу между", "различия между", "в чем разница", "в чём разница",
        "плюсы и минусы", "за и против", "плюсов и минусов",
        "что лучше", "проведи анализ", "проанализируй", "сопоставь",
        "матрица решений", "trade-off", "компромиссы"
    )
    if any(k in q_low for k in analyst_keys):
        return "ANALYST_LOGIC", 1.0, "0 ms эвристика"

    wellness_keys = ("калор", "каллор", "ккал", "bmr", "бжу", "осанк", "эргономик", "фазы сна", "циркадн", "разминк")
    if any(k in q_low for k in wellness_keys):
        return "WELLNESS_BIO", 1.0, "0 ms эвристика"

    feynman_keys = ("на пальцах", "простыми словами", "как для пятиклассника", "метод фейнмана", "объясни суть простым языком", "простым языком")
    if any(k in q_low for k in feynman_keys):
        return "TUTOR_FEYNMAN", 1.0, "0 ms эвристика"

    # 2. Семантический скоринг FastEmbed CPU
    q_vec = list(embedder.embed([query]))[0]
    q_norm = np.linalg.norm(q_vec)
    if q_norm == 0:
        return None, 0.0, "Пустой вектор"
    q_vec = np.array(q_vec, dtype=np.float32) / q_norm

    best_skill = None
    best_score = -1.0
    for name, matrix in anchor_matrices.items():
        sims = np.dot(matrix, q_vec)
        score = float(np.max(sims))
        if score > best_score:
            best_score = score
            best_skill = name

    # Скорректированный порог уверенности
    CONFIDENCE_THRESHOLD = 0.50
    if best_score >= CONFIDENCE_THRESHOLD:
        return best_skill.upper(), best_score, f"FastEmbed ({best_score:.3f})"

    return None, best_score, f"Дефолт (score: {best_score:.3f})"


print("=" * 60)
print("  НЕОНРОУТ: АВТОНОМНЫЙ ТЕСТОВЫЙ СТЕНД (ПОРОГ 0.50 + ПОКАЖИ)")
print("=" * 60)
print("Инициализация FastEmbed на CPU...")

try:
    embedder = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
except Exception:
    embedder = TextEmbedding()

anchor_matrices = {}
for name, phrases in TEST_ANCHORS.items():
    vecs = [np.array(v, dtype=np.float32) for v in embedder.embed(phrases)]
    normed = [v / (np.linalg.norm(v) if np.linalg.norm(v) > 0 else 1.0) for v in vecs]
    anchor_matrices[name] = np.array(normed, dtype=np.float32)

print("Стенд готов! Вводите фразы. Выход: q\n")

while True:
    try:
        user_input = input(">> Запрос: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nВыход.")
        break

    if not user_input:
        continue
    if user_input.lower() in ("q", "quit", "exit", "й"):
        print("Тестирование завершено.")
        break

    skill, score, source = resolve_test_skill(user_input, embedder, anchor_matrices)
    result_text = f"{skill} ({source})" if skill else f"ДЕФОЛТ (Элария) [{source}]"
    print(f"   [ СКИЛЛ ]: {result_text}")
    print("-" * 60)

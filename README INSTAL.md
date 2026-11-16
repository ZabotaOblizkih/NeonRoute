<div align="center">

# NeonRoute

### Версия v0.9.8-beta

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyQt6 Fluent](https://img.shields.io/badge/UI-Windows%2011%20Fluent%20Design-00F0FF?style=for-the-badge&logo=qt&logoColor=white)](https://riverbankcomputing.com/software/pyqt/)
[![8GB VRAM Optimized](https://img.shields.io/badge/VRAM-8GB%20Optimized-10B981?style=for-the-badge&logo=nvidia&logoColor=white)](https://nvidia.com)
[![Windows 11 / 10](https://img.shields.io/badge/OS-Windows%2011%20%2F%2010-0078D4?style=for-the-badge&logo=windows&logoColor=white)](https://www.microsoft.com/)

**Универсальный десктопный ИИ-клиент и персональный компаньон для Windows.**  
Объединяет локальные языковые модели (LLM), гибридную память RAG на CPU, генерацию артов через Stable Diffusion Forge и автономную среду разработки «Инженер» в современном интерфейсе Windows 11 Fluent Design.

</div>

---

## ⚡ Быстрый старт (Бесшовный запуск за 3 шага)

1. **Запустите установку**:
   ```bat
   install.bat
   ```
   Скрипт автоматически подключит встроенный портативный Python, установит библиотеки и скачает оптимизированный движок `llama-server.exe` под ваше оборудование (NVIDIA CUDA + runtime / AMD Radeon Vulkan / Intel / AVX2 CPU).

2. **Положите любую `.gguf` модель в папку `models\`**:
   *(Например, `Qwen2.5-7B-Instruct-Q4_K_M.gguf` или любую другую модель формата GGUF).*

3. **Запустите приложение**:
   * **`run.bat`** — стандартный тихий запуск без лишних консольных окон (через `runtime\python\pythonw.exe`);
   * **`run_debug.bat`** — запуск с открытым окном терминала для отладки и просмотра логов.

NeonRoute автоматически подхватит первую найденную `.gguf` модель из `models\`, честно определит видеокарту (NVIDIA / AMD / Intel), сбалансирует слои в `LayerBudgeter` и сразу начнет диалог.

---

### Системные требования
* **ОС**: Windows 10 или Windows 11 (64-bit).
* **Python**: **Не требуется устанавливать вручную!** Релизная сборка NeonRoute уже содержит автономный портативный Python 3.10 x64 (`runtime\python\`). *(Для разработчиков также поддерживается системный Python 3.10–3.13 с автоматическим созданием `.\venv`).*
* **Оперативная память**: от 16 ГБ RAM.
* **Видеокарта**: NVIDIA GeForce (RTX 30xx/40xx/50xx, GTX 16xx), AMD Radeon (Vulkan), Intel Arc или процессор в CPU-режиме.
* **Дисковое пространство**: ~500 МБ для клиента и интерпретатора (без учета весов GGUF/SDXL моделей).

---

## 🔌 Как можно пользоваться приложением:

### Вариант А: Только текстовый LLM-сервер (без генерации артов)
NeonRoute полностью автономен и не требует обязательного наличия Stable Diffusion Forge. Вы можете использовать только текстовый режим:
1. **LM Studio**: запустите LM Studio, загрузите модель (например, *Qwen 2.5 7B/14B* или *Llama 3.1 8B*) и включите локальный сервер (порт по умолчанию: `1234`).
2. **Встроенный llama-server**: запустите файл `run_llama_server.bat` (параметры и пути к моделям настраиваются в `server_config.ini`).
*Приложение запустится мгновенно, без навязчивых окон и предупреждений.*

### Вариант Б: Всё что описано в А + генерация изображений (SD WebUI Forge)
Если вам нужна визуализация сцен и генерация артов через Арт-директора:
1. Запустите [SD WebUI Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge) (порт по умолчанию: `7860`).
2. NeonRoute автоматически обнаружит установку Forge и подключит генератор картинок. При первом вызове генерации, если Forge не запущен в фоне, приложение предложит запустить его в один клик.
3. **Настройка Forge и патчи совместимости**:
   * Через меню GUI: **«Параметры и настройки» → «Система» → «SD Forge...»**
     - Выбор пути к каталогу Forge с автоматической валидацией лаунчеров (`forge_launcher.bat`, `webui-user.bat` и др.);
     - Переключатель квантования SDXL FP8 и режим генерации артов («Авто» / «Только вручную»);
     - Проверка состояния патча совместимости с возможностью установки и отката в один клик (перед патчингом автоматически создаётся резервная копия оригиналов в `.neonroute_backup/original/`).
   * Через CLI:
     ```bash
     python scripts/patch_forge.py --check               # Проверить статус совместимости
     python scripts/patch_forge.py --apply               # Применить патч (с автобэкапом)
     python scripts/patch_forge.py --restore             # Откатить патч из бэкапа
     python scripts/patch_forge.py --path C:\Path\To\Forge --apply
     ```
     *Патч NeonRoute обеспечивает динамическую выгрузку VRAM (`/sdapi/v1/free-vram`), смену моделей на лету без блокировок и исправление Pydantic-схем Forge 2.0.*

### Вариант В: Всё, что описанов А и Б + Мобильный веб-компаньон (Mobile Gateway)

> Чтобы общаться с компаньоном со смартфона в локальной сети, посмотрите в нижний левый угол главного окна, там изображен значок вай-фай, нажмите на него и отобразится QR код, отсканируйте его и откроется интерфейс приложения на телефоне.

---

## 🛠 Сборка чистого релиза

Для формирования дистрибутива релиза без мусора, кэшей и личных файлов:
```bat
build_release.bat
```
Или через CLI:
```bash
python scripts/build_release.py [--fast] [--with-tests] [--bundle-wheels]
```
Сборщик автоматически:
1. **[Stage A]** Проведёт AST-аудит импортов в коде и сверит их с `requirements.txt`.
2. **[Stage B]** Запустит барьер качества тестов (`pytest -q`).
3. **[Stage C]** Создаст стерильную папку `dist/NeonRoute_v0.9.8-beta/` и упакует её в `dist/NeonRoute_v0.9.8-beta.zip`.

---

## 📄 Лицензия и безопасность
* Все данные чатов, векторная база и память хранятся **строго локально на вашем ПК**.
* В режиме «Инженер» код выполняется локально в песочнице `workspace/`.

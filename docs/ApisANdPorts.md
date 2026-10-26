# ApisANdPorts.md
# Сетевая карта портов, протоколы взаимодействия и схемы API в NeonRoute

> **Среда выполнения**: Локальный хост Windows x64  
> **Сетевой периметр**: Строго обратная петля (`127.0.0.1` / `localhost`), внешние подключения отсутствуют  
> **Версия архитектуры**: NeonRoute v0.9.0-beta

---

## 1. Реестр сервисов, портов и таймаутов

Все взаимодействия между компонентами NeonRoute и локальными бэкендами осуществляются по протоколу HTTP/REST с использованием пулов постоянных соединений Keep-Alive (`requests.Session` + `HTTPAdapter`).

| Сервис | Хост и Порт | Протокол | Переменная в коде | Метод Health Check | Рабочий таймаут |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **LM Studio API** | `127.0.0.1:1234` | HTTP / REST | `DEFAULT_LM_STUDIO_URL` (`config.py`) | `GET /v1/models` | 180.0 с (инференс) / 1.2 с (пинг) |
| **SD WebUI Forge** | `127.0.0.1:7860` | HTTP / REST | `DEFAULT_FORGE_URL` (`config.py`) | `GET /sdapi/v1/progress` | 120.0 с (рендер) / 0.3 с (сокет) |
| **F5-TTS Service** | `127.0.0.1:5010` | HTTP / REST | `DEFAULT_TTS_URL` (`config.py`) | `GET /health` | 90.0 с (синтез) / 1.5 с (пинг) |

---

## 2. Спецификация API-эндпоинтов

### 2.1. LM Studio REST API (`http://127.0.0.1:1234/v1`)

#### 1. Опрос доступных моделей (`GET /v1/models`)
- **Назначение**: обнаружение загруженных моделей в LM Studio.
- **Таймаут**: 2.5 секунды.
- **Схема ответа**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen2.5-7b-instruct-abliterated-v2",
      "object": "model",
      "owned_by": "organization"
    }
  ]
}
```

#### 2. Генерация ответа и стриминг (`POST /v1/chat/completions`)
- **Назначение**: основной диалоговый инференс, Vision-анализ, Structured Outputs.
- **Таймаут**: 180.0 секунд.
- **Заголовки**: `Content-Type: application/json`.
- **Схема полезной нагрузки (Multimodal Vision Payload)**:
```json
{
  "model": "qwen2.5-vl-7b-instruct",
  "messages": [
    {
      "role": "system",
      "content": "Ты — персональный напарник Элария."
    },
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Изучи макет таблицы на скриншоте и подготовь код для openpyxl."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ..."
          }
        }
      ]
    }
  ],
  "temperature": 0.15,
  "max_tokens": 4096,
  "min_p": 0.05,
  "repetition_penalty": 1.05,
  "stream": true
}
```

#### 3. Структурированный вывод (`Structured Outputs`)
Для надежного извлечения сущностей в `RPCastExtractor` и `MemoryExtractor` запрос дополняется блоком `response_format`:
```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "extracted_scene_data",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "new_location": {"type": ["string", "null"]},
          "active_quest": {"type": ["string", "null"]},
          "visual_prompt": {"type": ["string", "null"]}
        },
        "required": ["new_location", "active_quest", "visual_prompt"]
      }
    }
  }
}
```

---

### 2.2. SD WebUI Forge REST API (`http://127.0.0.1:7860`)

#### 1. Генерация изображений (`POST /sdapi/v1/txt2img`)
- **Назначение**: двухпроходный диффузионный рендер кадра сцены.
- **Таймаут**: 120.0 секунд.
- **Схема полезной нагрузки**:
```json
{
  "prompt": "1girl, elven warrior, silver hair, green eyes, holding sword, ancient crypt, cinematic lighting",
  "negative_prompt": "deformed iris, bad anatomy, bad hands, low quality, blurry",
  "steps": 28,
  "width": 512,
  "height": 768,
  "cfg_scale": 6.8,
  "sampler_name": "DPM++ 2M",
  "scheduler": "Karras",
  "seed": 20261021,
  "clip_skip": 1,
  "enable_hr": true,
  "hr_scale": 1.30,
  "hr_upscaler": "R-ESRGAN 4x+",
  "denoising_strength": 0.35,
  "hr_second_pass_steps": 14,
  "override_settings": {
    "sd_model_checkpoint": "realisticVisionV60B1_v51HyperVAE_pruned_fp16.safetensors",
    "forge_vae_tiling": true,
    "sd_vae_tile": true
  },
  "alwayson_scripts": {
    "ADetailer": {
      "args": [
        true,
        false,
        {
          "ad_model": "face_yolov8n.pt",
          "ad_confidence": 0.35,
          "ad_denoising_strength": 0.30,
          "ad_inpaint_only_masked": true
        },
        {
          "ad_model": "hand_yolov8n.pt",
          "ad_confidence": 0.40,
          "ad_denoising_strength": 0.22,
          "ad_inpaint_only_masked": true
        }
      ]
    }
  }
}
```

#### 2. Опрос прогресса генерации (`GET /sdapi/v1/progress`)
- **Параметры**: `skip_current_image=true`.
- **Таймаут**: 2.0 секунды (опрос каждые 350 мс из фонового потока).
- **Схема ответа**:
```json
{
  "progress": 0.65,
  "state": {
    "sampling_step": 18,
    "sampling_steps": 28,
    "textinfo": "face_yolov8n.pt"
  }
}
```

#### 3. Аппаратное освобождение памяти VRAM
- **Выгрузка чекпоинта в системную RAM**: `POST /sdapi/v1/unload-checkpoint` (выгружает ~3.2 ГБ весов из VRAM).
- **Очистка кэша аллокатора PyTorch**: `POST /sdapi/v1/free-vram` (сбрасывает зарезервированные блоки CUDA).
- **Мгновенное прерывание рендера**: `POST /sdapi/v1/interrupt` (timeout=5.0s).

---

### 2.3. F5-TTS Service (`http://127.0.0.1:5010`)

#### Синтез аудиофайла (`POST /synthesize`)
- **Таймаут**: 90.0 секунд.
- **Схема полезной нагрузки**:
```json
{
  "text": "Привет! Я готова к работе, с чего начнем?",
  "ref_audio": null,
  "ref_text": null,
  "speed": 1.0,
  "device": "cpu"
}
```
- **Формат ответа**: бинарный поток аудиоданных `audio/wav`. Файл сохраняется по пути `C:\NeonRoute\audio\tts_<timestamp>_<hex>.wav`.

---

## 3. Схемы файлов локального хранилища

### 3.1. Сессия диалога (`history/chat_<session_id>.json`)
```json
{
  "id": "e3b0c442-98fc-4c14-9afe-461456529241",
  "character_id": "char_elven_maiden",
  "title": "🎭 Диалог с Элейной",
  "mode": "roleplay",
  "model_id": "L3-8B-Stheno-v3.2-Q4_K_S",
  "is_model_locked": true,
  "is_custom_titled": true,
  "created_at": "2026-10-21T11:00:00.000000",
  "updated_at": "2026-10-21T11:45:20.123456",
  "active_greeting_idx": 1,
  "messages": [
    {
      "id": "msg_01_user",
      "sender": "Вы",
      "text": "Оглядись вокруг, что ты видишь в глубине склепа?",
      "is_user": true,
      "image_path": null,
      "file_paths": [],
      "timestamp": "11:02"
    },
    {
      "id": "msg_02_asst",
      "sender": "Элейна",
      "text": "*Зажигает факел, осторожно делая шаг вперед.* Здесь сыро...",
      "is_user": false,
      "image_path": "C:\\NeonRoute\\outputs\\sessions\\e3b0c442\\images\\gen_1792471000.png",
      "file_paths": [],
      "visual_intent": {
        "prompt": "1girl, solo, holding torch, stone crypt, volumetric shadows",
        "scene_type": "exploration"
      },
      "generation_params": {
        "checkpoint": "dreamshaper_8.safetensors",
        "seed": 884729110,
        "sampler_name": "DPM++ 2M",
        "steps": 28,
        "cfg_scale": 6.0
      },
      "timestamp": "11:03"
    }
  ],
  "sampling_params": {
    "temperature": 1.10,
    "min_p": 0.05,
    "top_p": 0.95,
    "repeat_penalty": 1.02,
    "max_tokens": 4096
  }
}
```

### 3.2. Хроника мира и состояние каста (`history/cast_<session_id>.json`)
```json
{
  "session_id": "e3b0c442-98fc-4c14-9afe-461456529241",
  "focus_companion": "Элейна",
  "current_location": "Древний подземный склеп",
  "active_quest": "Найти выход к поверхности",
  "relationship_context": {
    "dynamic_tone": "напряженная сплоченность",
    "foundation": "Клятва верности у алтаря",
    "current_bond": "Надежные соратники"
  },
  "inventory": [
    "Факел",
    "Стальной кинжал",
    "Карта катакомб"
  ],
  "stats": {
    "Здоровье": "85/100",
    "Выносливость": "60/100"
  },
  "world_state": [
    "В подземелье запрещено использовать огненную магию",
    "Стены нестабильны и могут обрушиться"
  ],
  "environment": {
    "location_dna": "ancient underground stone crypt, mossy pillars, crumbling ceiling",
    "scene_ambience": "затхлый полумрак, свет факела, холодный воздух",
    "is_enclosed": true
  },
  "player_state": {
    "vital_status": "alive",
    "mutations": ["царапина на правом предплечье"]
  },
  "companion_state": {
    "vital_status": "alive",
    "visual_dna": "1girl, elf, long silver hair, emerald eyes, pointed ears",
    "current_outfit": "tattered leather tunic, dark travel trousers, boots",
    "mutations": []
  },
  "entities": {
    "elena": {
      "role": "companion",
      "canonical_name": "Элейна",
      "presence": "physical",
      "vital_status": "alive",
      "ttl": 999
    }
  }
}
```



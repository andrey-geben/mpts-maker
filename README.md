# MPTS Maker

GUI-приложение для преобразования M3U-плейлистов в **MPTS** (Multi Program Transport Stream) с отправкой по UDP multicast. Построено на PySide6 + FFmpeg.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![PySide6](https://img.shields.io/badge/PySide6-6.5+-green)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Возможности

- 📥 **Загрузка M3U/M3U8-плейлистов** (UTF-8, UTF-8-BOM, CP1251)
- 🧭 **Мастер пошагового создания MPTS** — удобно для первых настроек
- 🎛 **Несколько MPTS одновременно** — каждый со своим UDP-адресом
- 🔍 **Pre-flight проверка каналов** перед запуском — недоступные источники отсекаются заранее
- 📊 **Мониторинг в реальном времени** — статус, FPS, битрейт, CPU, RAM, GPU, сеть
- 🧠 **Человеко-понятные ошибки** — распознавание типовых проблем FFmpeg + расшифровка кодов возврата Windows NTSTATUS
- 💾 **Автосохранение сессии** — профили MPTS, последний плейлист и геометрия окна сохраняются между запусками
- ✏️ **Управление плейлистом** — редактирование и удаление каналов без изменения файла на диске
- 🎨 **Управление MPTS** — запуск, остановка, правка состава, удаление профиля
- 🇷🇺 **Полностью русский интерфейс** с подсказками

## Скриншоты

> _Добавьте сюда скриншот главного окна, мастера и диалога ошибки — это сильно улучшит восприятие репозитория._

```
screenshots/main.png
screenshots/wizard.png
screenshots/error.png
```

---

## Требования

- **Windows 10/11** (протестировано)
- **Python 3.10+** — только для запуска из исходников
- **FFmpeg** — скачивается отдельно (см. ниже)

Linux/macOS формально должны работать (PySide6 кросс-платформенный), но не тестировались.

---

## Установка FFmpeg

`ffmpeg.exe` **не входит** в репозиторий из-за размера. Скачайте его и положите в корень проекта рядом с `main.py`.

### PowerShell (одна строка)

```powershell
Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile "$env:TEMP\ffmpeg.zip"; Expand-Archive -Path "$env:TEMP\ffmpeg.zip" -DestinationPath "$env:TEMP\ffmpeg_extract" -Force; Copy-Item "$env:TEMP\ffmpeg_extract\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe" -Destination ".\ffmpeg.exe"; Remove-Item "$env:TEMP\ffmpeg.zip", "$env:TEMP\ffmpeg_extract" -Recurse -Force
```

### Вручную

1. Откройте [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases)
2. Скачайте `ffmpeg-master-latest-win64-gpl.zip` (~170 МБ)
3. Распакуйте и скопируйте `bin\ffmpeg.exe` в корень проекта

Проверка:

```powershell
.\ffmpeg.exe -version
```

---

## Установка зависимостей

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**`requirements.txt`:**

```
PySide6>=6.5
psutil>=5.9
nvidia-ml-py>=12.0
```

---

## Запуск из исходников

```powershell
python main.py
```

Откроется главное окно. Нажмите **«Загрузить M3U»** и выберите плейлист — либо сразу **«Мастер…»** для пошагового создания MPTS.

---

## Использование

### Быстрый старт

1. **Загрузите M3U** — кнопка «Загрузить M3U» на панели.
2. **Создайте MPTS через мастер** — «Мастер…» → выбрать каналы → указать имя и URL → «Создать MPTS».
3. **Запустите поток** — кнопка ▶ в строке MPTS.
4. **Проверьте в VLC** — «Медиа → Открыть URL» → `udp://@239.0.0.1:1234`.

### Управление MPTS

В каждой строке таблицы 5 кнопок:

| Кнопка | Действие |
|:---:|---|
| ▶ | Запустить поток (с pre-flight проверкой каналов) |
| ■ | Остановить поток |
| ℹ | Открыть окно с деталями (статус, ошибки, журнал FFmpeg) |
| ✎ | Правка каналов: удалить / заменить |
| ✖ | Удалить MPTS из списка (каналы остаются в плейлисте) |

### Управление плейлистом

Над деревом каналов — панель с именем файла и двумя кнопками:

| Кнопка | Действие |
|:---:|---|
| ✎ | Убрать каналы из плейлиста (в памяти) |
| ✖ | Удалить плейлист из программы |

> ⚠️ Обе операции **не изменяют файл на диске** — только текущее состояние в приложении. MPTS-профили при этом не затрагиваются.

### Ошибки FFmpeg

При падении потока статус подсвечивается красным. **Кликните по ячейке «Статус»** — откроется окно с:

- человеко-понятной причиной (например, «Источник не найден (HTTP 404)»)
- расшифровкой кода возврата (включая Windows-коды `0xC0000005`, `0xDFC37EBB` и др.)
- рекомендациями
- полным журналом FFmpeg с кнопкой «Копировать»

---

## Сборка `.exe` (Nuitka)

Требуется [Nuitka](https://nuitka.net/) и C-компилятор (MinGW или MSVC — Nuitka может скачать автоматически).

```powershell
pip install nuitka ordered-set zstandard
```

Сборка (одна строка, PowerShell):

```powershell
python -m nuitka --standalone --enable-plugin=pyside6 --windows-console-mode=disable --include-data-files=ffmpeg.exe=ffmpeg.exe --include-data-files=app.ico=app.ico --output-dir=build --assume-yes-for-downloads --windows-icon-from-ico=app.ico --company-name="Andrey Geben" --product-name="MPTS Maker" --file-version=1.0.0.0 --product-version=1.0.0.0 --file-description="M3U to MPTS converter" --copyright="© 2026 Andrey Geben" main.py
```

Готовый `.exe` появится в `build/main.dist/main.exe`. Запускать **только** из этой папки (рядом должны быть `ffmpeg.exe`, `app.ico` и Qt-библиотеки).

### `--onefile` (один файл)

Добавьте в команду `--onefile` — получите один `main.exe` (~100 МБ, стартует на 2–4 секунды дольше). Перед сборкой в одну строку убедитесь, что в `main_window.py` используется `resource_path()` для `ffmpeg.exe` и `app.ico`.

---

## Структура проекта

```
mpts-maker/
├── main.py               # точка входа
├── main_window.py        # главное окно, вся UI-логика
├── wizard.py             # мастер создания MPTS
├── m3u_parser.py         # парсер плейлистов
├── ffmpeg_manager.py     # управление процессами FFmpeg + pre-flight probe
├── system_monitor.py     # мониторинг CPU/RAM/GPU/сети (QThread)
├── storage.py            # чтение/запись config.json
├── constants.py          # метаданные приложения
├── about_dialog.py       # диалог «О программе»
├── requirements.txt
├── ffmpeg.exe            # скачивается отдельно (см. выше)
├── app.ico
├── LICENSE
└── README.md
```

---

## Конфигурация

Настройки автоматически сохраняются в:

```
%APPDATA%\Andrey Geben\MPTS Maker\config.json
```

Содержит: список MPTS-профилей (имя, URL, каналы), путь к последнему плейлисту, геометрию окна, положение сплиттера. Удаление файла сбрасывает программу к заводским настройкам.

---

## Лицензия

Проект распространяется под лицензией **MIT** — см. [LICENSE](LICENSE).

**О FFmpeg:** используется как внешний исполняемый файл. Сборка FFmpeg, рекомендуемая в README (GPL-вариант), распространяется под GPL v3. Исходный код FFmpeg доступен на [ffmpeg.org](https://ffmpeg.org). Если вы включаете `ffmpeg.exe` в свои релизы, помните, что бинарник остаётся под GPL.

---

## Автор

**Andrey Geben**

- GitHub: [@andrey-geben](https://github.com/andrey-geben)
- Проект: [VHS-Sat-Lite](https://github.com/andrey-geben/VHS-Sat-Lite)

---

## Вклад в проект

Issues и pull requests приветствуются. Для крупных изменений — сначала откройте issue для обсуждения.

```powershell
git clone https://github.com/andrey-geben/mpts-maker.git
cd mpts-maker
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# скачать ffmpeg.exe (см. раздел выше)
python main.py
```

---

## Благодарности

- [FFmpeg](https://ffmpeg.org) — за обработку и мультиплексирование потоков
- [PySide6 / Qt](https://www.qt.io) — за GUI-фреймворк
- [psutil](https://github.com/giampaolo/psutil) — за системные метрики
- [pynvml](https://github.com/gpuopenanalytics/pynvml) — за доступ к NVML
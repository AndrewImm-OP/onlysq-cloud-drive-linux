# OnlySQ Cloud Drive (Linux)

[![PyPI version](https://img.shields.io/pypi/v/onlysq-drive-linux.svg)](https://pypi.org/project/onlysq-drive-linux/)
![Version](https://img.shields.io/badge/version-1.0.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![Linux](https://img.shields.io/badge/platform-Linux-FCC624)
![FUSE3](https://img.shields.io/badge/FUSE3-required-orange)

**OnlySQ Drive** - это неофициальное Linux-приложение и CLI-инструмент, который монтирует **OnlySQ Cloud** как каталог в файловой системе через FUSE.

> Linux-порт оригинального проекта [onlysq-cloud-drive](https://github.com/fakelag28/onlysq-cloud-drive) от [fakelag28](https://github.com/fakelag28).
> Windows-версия: [onlysq-drive](https://pypi.org/project/onlysq-drive/)

После настройки у пользователя появляется полноценный каталог рядом с остальными дисками, с которым можно работать как с обычным:

- загружать файлы в облако через файловый менеджер или терминал,
- скачивать и открывать файлы,
- удалять их,
- копировать публичную ссылку через контекстное меню,
- автоматически поднимать монтирование после входа через systemd.

---

## Особенности

- **Каталог рядом с остальными дисками**
  OnlySQ Cloud монтируется в `/run/media/$USER/OnlySQCloud` и отображается в боковой панели файлового менеджера.

- **Поддержка всех основных файловых менеджеров**
  Dolphin (KDE), Nautilus (GNOME), Thunar (XFCE), Nemo (Cinnamon), Caja (MATE).

- **Простая установка через pip**
  Проект ставится как обычный Python-пакет и управляется CLI-командой `onlysq-drive`.

- **Автозапуск при входе**
  После настройки каталог поднимается автоматически через systemd user service.

- **Контекстное меню "Copy public link"**
  Можно быстро копировать публичную ссылку на файл прямо из файлового менеджера.

- **Локальный индекс и кэш**
  Приложение хранит индекс файлов и кэш на ПК, чтобы быстрее показывать содержимое и восстанавливать его после перезапуска системы.

- **SQLite внутри**
  Для метаданных используется локальная база, без необходимости поднимать отдельный сервер или БД.

- **CLI-управление**
  Всё настраивается и обслуживается понятными командами: `setup`, `doctor`, `stats`, `mount`, `config` и т.д.

---

## Как это работает

`OnlySQ Drive` создаёт пользовательскую файловую систему через FUSE3 и монтирует её как каталог.
Когда ты создаёшь или копируешь файл в этот каталог, приложение:

1. сохраняет файл во временный локальный кэш,
2. загружает его в OnlySQ Cloud,
3. сохраняет метаданные в локальный индекс,
4. делает файл доступным для дальнейшего открытия, скачивания и удаления.

### Важно

Сейчас API OnlySQ Cloud предоставляет базовые операции загрузки, скачивания и удаления файла. Из-за этого клиент хранит **локальный индекс** файлов у пользователя. Это значит:

- на **этом же ПК** после перезагрузки всё сохраняется,
- но на **другом ПК** дерево файлов автоматически не восстановится без переноса локального индекса.

---

## Стек

- **Python 3.10+**
- **FUSE3** (libfuse3)
- **pyfuse3** + **trio**
- **SQLite**
- **requests**
- **systemd** (user service)
- **KDE / GTK shell integration**

---

## Установка

### Требования

- Linux (с поддержкой FUSE3)
- Python **3.10+**
- Системные пакеты: `fuse3` (или `libfuse3-dev`), `pkg-config`

### Из PyPI

```bash
pip install onlysq-drive-linux
```

### Из исходников

```bash
pip install .
```

---

## Быстрый старт

### Установка зависимостей + пакета + настройка

#### Arch Linux / CachyOS / Manjaro

```bash
sudo pacman -S --noconfirm python python-pip fuse3 pkgconf \
  && pip install onlysq-drive-linux \
  && onlysq-drive setup --label "OnlySQ Cloud"
```

#### Ubuntu / Debian

```bash
sudo apt-get update && sudo apt-get install -y python3 python3-pip libfuse3-dev fuse3 pkg-config \
  && pip install onlysq-drive-linux \
  && onlysq-drive setup --label "OnlySQ Cloud"
```

#### Fedora

```bash
sudo dnf install -y python3 python3-pip fuse3-devel fuse3 pkgconf-pkg-config \
  && pip install onlysq-drive-linux \
  && onlysq-drive setup --label "OnlySQ Cloud"
```

После этого:

- появляется каталог рядом с остальными дисками,
- диск отображается в боковой панели файлового менеджера,
- ставится автозапуск через systemd,
- добавляется контекстное меню "Copy public link",
- после следующего входа каталог поднимается автоматически.

### Проверка

```bash
onlysq-drive doctor
onlysq-drive stats
```

---

## Использование

### Смонтировать вручную

```bash
onlysq-drive mount
```

> Эта команда полезна для ручного теста. Для постоянной работы лучше использовать `setup` или `install-autostart`.

### Проверить состояние

```bash
onlysq-drive doctor
```

### Посмотреть статистику

```bash
onlysq-drive stats
```

### Посмотреть содержимое виртуальной папки

```bash
onlysq-drive ls
onlysq-drive ls /folder
```

### Информация о файле

```bash
onlysq-drive info /example.txt
```

### Скопировать публичную ссылку

```bash
onlysq-drive copy-link /example.txt
```

### Скачать файл

```bash
onlysq-drive pull /example.txt ~/Downloads/example.txt
```

### Удалить файл

```bash
onlysq-drive rm /example.txt
```

---

## Все команды CLI

### Инициализация и настройка

#### `onlysq-drive init`

Создаёт:

- конфиг,
- SQLite-индекс,
- директорию кэша.

Пример:

```bash
onlysq-drive init --label "OnlySQ Cloud"
```

#### `onlysq-drive setup`

Полная первичная настройка:

- `init`
- создание точки монтирования
- установка контекстного меню
- добавление в боковую панель файлового менеджера
- установка автозапуска через systemd

Пример:

```bash
onlysq-drive setup --label "OnlySQ Cloud"
```

---

### Диагностика и обслуживание

#### `onlysq-drive doctor`

Показывает:

- версию Python,
- платформу,
- путь к конфигу,
- точку монтирования,
- доступность pyfuse3 и fusermount3.

#### `onlysq-drive stats`

Показывает:

- количество файлов,
- количество папок,
- общий размер,
- состояние индекса.

---

### Работа с виртуальным каталогом

#### `onlysq-drive mount`

Монтирует каталог вручную.

#### `onlysq-drive ls [path]`

Показывает содержимое папки.

#### `onlysq-drive info <path>`

Показывает информацию по файлу или папке.

#### `onlysq-drive pull <virtual_path> <local_path>`

Скачивает файл из облака/кэша.

#### `onlysq-drive rm <path>`

Удаляет файл или пустую папку.

#### `onlysq-drive copy-link <path>`

Копирует публичную ссылку файла в буфер обмена.

---

### Конфиг

#### `onlysq-drive config show`

Показывает текущий конфиг.

#### `onlysq-drive config set <key> <value>`

Меняет поле в конфиге.

Примеры:

```bash
onlysq-drive config show
onlysq-drive config set mountpoint /run/media/$USER/OnlySQCloud
onlysq-drive config set volume_label "OnlySQ Cloud"
```

---

### Интеграция с системой

#### `onlysq-drive install-context-menu`

Добавляет пункт в контекстное меню файлового менеджера:

- **OnlySQ: Copy public link**

Поддержка: Dolphin, Nautilus, Nemo, Caja.

#### `onlysq-drive uninstall-context-menu`

Удаляет этот пункт.

#### `onlysq-drive install-autostart`

Создаёт systemd user service для автоматического монтирования при входе.

#### `onlysq-drive uninstall-autostart`

Удаляет systemd user service.

---

### Полное удаление локальных данных

#### `onlysq-drive purge --yes`

Удаляет:

- конфиг,
- SQLite-индекс,
- кэш,
- systemd service,
- записи в боковой панели файлового менеджера.

Пример:

```bash
onlysq-drive purge --yes
```

---

## Автозапуск после перезагрузки

Если выполнен:

```bash
onlysq-drive install-autostart
```

или

```bash
onlysq-drive setup ...
```

то после входа пользователя каталог будет монтироваться автоматически.

Управление:

```bash
# Статус
systemctl --user status onlysq-drive

# Остановить
systemctl --user stop onlysq-drive

# Запустить
systemctl --user start onlysq-drive

# Логи
journalctl --user -u onlysq-drive -f
```

---

## Где хранятся данные

### Конфиг

```
~/.config/onlysq-drive/config.json
```

### Индекс

```
~/.local/share/onlysq-drive/index.sqlite3
```

### Кэш

```
~/.cache/onlysq-drive/files/
```

### Логи автозапуска

```
~/.local/share/onlysq-drive/logs/autostart.log
```

---

## Структура проекта

```
onlysq-drive/
├─ pyproject.toml
├─ README.md
└─ src/
   └─ onlysq_drive/
      ├─ __init__.py
      ├─ cli.py               # CLI entry point
      ├─ launcher.py           # Autostart launcher
      ├─ mount.py              # FUSE mounting via pyfuse3
      ├─ fs_ops.py             # FUSE filesystem operations
      ├─ cloud_client.py       # HTTP client for OnlySQ Cloud API
      ├─ index_db.py           # SQLite file index
      ├─ config.py             # JSON config dataclass
      ├─ autostart.py          # systemd user service management
      ├─ shell_integration.py  # Context menu (Dolphin/Nautilus/Nemo/Caja)
      ├─ sidebar.py            # Sidebar bookmarks (GTK bookmarks)
      ├─ drive_icon.py         # No-op on Linux
      ├─ clipboard.py          # xclip/xsel/wl-copy clipboard
      ├─ paths.py              # XDG path helpers
      └─ vpaths.py             # Virtual path normalization
```

---

## Ограничения

- Это **неофициальный** клиент OnlySQ Cloud.
- Проект зависит от **FUSE3** (libfuse3).
- Восстановление дерева файлов основано на **локальном индексе**.
- Если удалить локальный индекс, на другом ПК структура не восстановится.

---

## Удаление

### Удалить автозапуск и интеграции

```bash
onlysq-drive uninstall-autostart
onlysq-drive uninstall-context-menu
```

### Удалить локальные данные

```bash
onlysq-drive purge --yes
```

### Удалить пакет

```bash
pip uninstall onlysq-drive-linux
```

---

## Credits

Оригинальный проект (Windows): [fakelag28/onlysq-cloud-drive](https://github.com/fakelag28/onlysq-cloud-drive)

Linux-порт: [AndrewImm-OP/onlysq-cloud-drive-linux](https://github.com/AndrewImm-OP/onlysq-cloud-drive-linux)

---

## Лицензия

Проект распространяется под лицензией **MIT**.

---

## Disclaimer

Это **неофициальное** приложение.
Все права на бренд, API и платформу принадлежат **OnlySQ**.

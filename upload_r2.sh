#!/usr/bin/env bash
# Заливка фотографий справочника на Cloudflare R2.
#
# Бакет ksa-data отведён под справочник целиком, поэтому префикса нет —
# структура повторяет data/ один в один:
#
#   data/photos/<id>.jpg -> <bucket>/photos/<id>.jpg   (локации)
#   data/media/<id>.jpg  -> <bucket>/media/<id>.jpg    (скачанное из каналов)
#
# Пути 1-в-1 совпадают с полем listing.photo в базе, поэтому адрес картинки —
# это просто KSA_MEDIA_BASE_URL + "/" + photo.
#
# Инкрементально: rclone сверяет контрольные суммы и грузит только изменённое.
#
# Использование:
#   ./upload_r2.sh              # залить новое/изменённое (ничего не удаляет)
#   ./upload_r2.sh --dry-run    # показать, что залилось бы
#   ./upload_r2.sh photos       # только один подкаталог (photos|media)
set -euo pipefail

RCLONE="${RCLONE:-$HOME/.local/bin/rclone}"
REMOTE="${R2_REMOTE:-r2}"
BUCKET="${R2_BUCKET:-ksa-data}"
# Пустой префикс — файлы ложатся в корень бакета. Подстановка с одним дефисом,
# чтобы R2_PREFIX="" не подменялся значением по умолчанию.
PREFIX="${R2_PREFIX-}"
SRC="$(cd "$(dirname "$0")" && pwd)/data"

DRY=(); ONLY=""
for a in "$@"; do
  case "$a" in
    --dry-run)     DRY=(--dry-run) ;;
    photos|media)  ONLY="$a" ;;
    *) echo "неизвестный аргумент: $a" >&2; exit 2 ;;
  esac
done

command -v "$RCLONE" >/dev/null || { echo "rclone не найден: $RCLONE" >&2; exit 1; }

for dir in ${ONLY:-photos media}; do
  [ -d "$SRC/$dir" ] || { echo ">> $dir: папки нет, пропускаю"; continue; }
  # Собираем путь так, чтобы при пустом префиксе не получился двойной слэш:
  # в S3 он создаёт каталог с пустым именем, и файлы уезжают не туда.
  DST="$REMOTE:$BUCKET${PREFIX:+/$PREFIX}/$dir"
  echo ">> $dir -> $DST ${DRY[*]:-}"
  "$RCLONE" copy "$SRC/$dir" "$DST" \
    "${DRY[@]}" \
    --checksum \
    --transfers 16 --checkers 32 \
    --s3-no-check-bucket \
    --progress --stats-one-line
done
echo ">> готово."

#!/usr/bin/env bash
# Заливка фотографий справочника на Cloudflare R2.
#
# Кладём под префикс ksa/ в тот же бакет, что и tafsir-app: у него уже включён
# публичный доступ, отдельный бакет потребовал бы настройки в панели Cloudflare.
#
#   data/photos/<id>.jpg -> <bucket>/ksa/photos/<id>.jpg   (локации)
#   data/media/<id>.jpg  -> <bucket>/ksa/media/<id>.jpg    (скачанное из каналов)
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
BUCKET="${R2_BUCKET:-tafsir-data}"
PREFIX="${R2_PREFIX:-ksa}"
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
  echo ">> $dir -> $REMOTE:$BUCKET/$PREFIX/$dir ${DRY[*]:-}"
  "$RCLONE" copy "$SRC/$dir" "$REMOTE:$BUCKET/$PREFIX/$dir" \
    "${DRY[@]}" \
    --checksum \
    --transfers 16 --checkers 32 \
    --s3-no-check-bucket \
    --progress --stats-one-line
done
echo ">> готово."

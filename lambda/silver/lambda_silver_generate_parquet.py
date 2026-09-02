import os
import json
import logging
from datetime import datetime, timezone, timedelta
import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')
SILVER_BUCKET = os.environ.get('SILVER_BUCKET_NAME')


def epoch_to_iso(epoch_sec, offset_sec=0):
    """Преобразува Unix epoch в ISO формат (UTC или Local с офсет)."""
    if epoch_sec is None:
        return None
    try:
        dt = datetime.fromtimestamp(epoch_sec, tz=timezone.utc) + timedelta(seconds=offset_sec)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.warning(f"Failed to parse epoch timestamp {epoch_sec}: {str(e)}")
        return None


def process_s3_object(bronze_bucket, bronze_key):
    # Проверка дали файлът е JSON
    if not bronze_key.endswith('.json'):
        logger.info(f"Skipping non-JSON file: s3://{bronze_bucket}/{bronze_key}")
        return

    logger.info(f"Processing Bronze file: s3://{bronze_bucket}/{bronze_key}")

    try:
        # 1. Прочитане на суровия JSON от Bronze
        obj = s3_client.get_object(Bucket=bronze_bucket, Key=bronze_key)
        bronze_content = json.loads(obj['Body'].read().decode('utf-8'))

        ingested_at_str = bronze_content.get('ingested_at_utc')
        raw = bronze_content.get('raw_response', {})

        # Извличане на офсета за часовата зона
        tz_offset = raw.get('timezone', 0)

        # Извличане на таймстампите
        dt_epoch = raw.get('dt')
        sys_info = raw.get('sys', {})
        sunrise_epoch = sys_info.get('sunrise')
        sunset_epoch = sys_info.get('sunset')

        ingested_dt = datetime.fromisoformat(ingested_at_str) if ingested_at_str else datetime.now(timezone.utc)

        # 2. Изграждане на сплостената структура
        weather_arr = raw.get('weather', [{}])
        first_weather = weather_arr[0] if len(weather_arr) > 0 else {}

        main_info = raw.get('main', {})
        wind_info = raw.get('wind', {})
        clouds_info = raw.get('clouds', {})
        rain_info = raw.get('rain', {})
        snow_info = raw.get('snow', {})
        coord_info = raw.get('coord', {})

        row = {
            # Идентификация
            "city_id": raw.get('id'),
            "city_name": raw.get('name'),
            "country": sys_info.get('country'),
            "coord_lon": coord_info.get('lon'),
            "coord_lat": coord_info.get('lat'),

            # Метеорологични условия
            "weather_id": first_weather.get('id'),
            "weather_main": first_weather.get('main'),
            "weather_description": first_weather.get('description'),
            "weather_icon": first_weather.get('icon'),
            "base_station": raw.get('base'),

            # Показатели
            "temp": main_info.get('temp'),
            "feels_like": main_info.get('feels_like'),
            "temp_min": main_info.get('temp_min'),
            "temp_max": main_info.get('temp_max'),
            "pressure": main_info.get('pressure'),
            "sea_level_pressure": main_info.get('sea_level'),
            "ground_level_pressure": main_info.get('grnd_level'),
            "humidity": main_info.get('humidity'),
            "visibility": raw.get('visibility'),

            # Вятър
            "wind_speed": wind_info.get('speed'),
            "wind_deg": wind_info.get('deg'),
            "wind_gust": wind_info.get('gust'),

            # Облачност и валежи
            "clouds_all": clouds_info.get('all'),
            "rain_1h": rain_info.get('1h'),
            "rain_3h": rain_info.get('3h'),
            "snow_1h": snow_info.get('1h'),
            "snow_3h": snow_info.get('3h'),

            # Системни параметри
            "sys_type": sys_info.get('type'),
            "sys_id": sys_info.get('id'),
            "sys_message": sys_info.get('message'),
            "timezone_offset": tz_offset,

            # Таймстампи в UTC и Local
            "dt_utc": epoch_to_iso(dt_epoch, 0),
            "dt_local": epoch_to_iso(dt_epoch, tz_offset),
            "sunrise_utc": epoch_to_iso(sunrise_epoch, 0),
            "sunrise_local": epoch_to_iso(sunrise_epoch, tz_offset),
            "sunset_utc": epoch_to_iso(sunset_epoch, 0),
            "sunset_local": epoch_to_iso(sunset_epoch, tz_offset),
            "http_status_code": raw.get('cod'),

            # ETL Метаданни
            "ingested_at_utc": ingested_dt.strftime('%Y-%m-%d %H:%M:%S'),
            "ingested_at_local": (ingested_dt + timedelta(seconds=tz_offset)).strftime('%Y-%m-%d %H:%M:%S'),
            "source_raw_file": f"s3://{bronze_bucket}/{bronze_key}",
            "partition_date": ingested_dt.strftime('%Y-%m-%d')
        }

        # 3. Конвертиране към DataFrame и Parquet
        df = pd.DataFrame([row])

        # Валидация и каст на типовете данни
        for int_col in ['city_id', 'weather_id', 'pressure', 'humidity', 'visibility']:
            if int_col in df.columns and df[int_col].notnull().any():
                df[int_col] = df[int_col].astype('Int64')

        parquet_buffer = pa.BufferOutputStream()
        table = pa.Table.from_pandas(df)
        pq.write_table(table, parquet_buffer, compression='SNAPPY')

        # 4. Записване в Silver S3
        partition_date = row['partition_date']
        filename = os.path.basename(bronze_key).replace('_raw.json', '.parquet')
        silver_key = f"partition_date={partition_date}/{filename}"

        s3_client.put_object(
            Bucket=SILVER_BUCKET,
            Key=silver_key,
            Body=parquet_buffer.getvalue().to_pybytes(),
            ContentType='application/octet-stream'
        )
        logger.info(f"Successfully converted and saved: s3://{SILVER_BUCKET}/{silver_key}")

    except Exception as e:
        logger.error(f"Error processing file s3://{bronze_bucket}/{bronze_key}: {str(e)}", exc_info=True)
        raise e


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    if not SILVER_BUCKET:
        logger.error("Environment variable SILVER_BUCKET_NAME is not set.")
        raise ValueError("Missing SILVER_BUCKET_NAME environment variable.")

    processed_count = 0

    # 1. Парсване на EventBridge S3 събитие (Първично)
    if isinstance(event, dict) and 'detail' in event and 'bucket' in event['detail']:
        bronze_bucket = event['detail']['bucket']['name']
        bronze_key = event['detail']['object']['key']
        process_s3_object(bronze_bucket, bronze_key)
        processed_count += 1

    # 2. Парсване на Direct S3 Notification събитие (Резервно)
    elif isinstance(event, dict) and 'Records' in event:
        for record in event['Records']:
            if 's3' in record:
                bronze_bucket = record['s3']['bucket']['name']
                bronze_key = record['s3']['object']['key']
                process_s3_object(bronze_bucket, bronze_key)
                processed_count += 1

    else:
        logger.warning(f"Unrecognized or unsupported event structure: {json.dumps(event)}")

    return {
        "statusCode": 200,
        "body": json.dumps(f"Processed {processed_count} object(s) successfully.")
    }
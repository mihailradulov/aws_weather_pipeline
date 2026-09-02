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
    dt = datetime.fromtimestamp(epoch_sec, tz=timezone.utc) + timedelta(seconds=offset_sec)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def lambda_handler(event, context):
    for record in event.get('Records', []):
        bronze_bucket = record['s3']['bucket']['name']
        bronze_key = record['s3']['object']['key']

        logger.info(f"Processing Bronze file: s3://{bronze_bucket}/{bronze_key}")

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

        # Фиксиране на явни типове данни за чиста Parquet схема
        df['city_id'] = df['city_id'].astype('Int64')
        df['weather_id'] = df['weather_id'].astype('Int64')
        df['pressure'] = df['pressure'].astype('Int64')
        df['humidity'] = df['humidity'].astype('Int64')
        df['visibility'] = df['visibility'].astype('Int64')

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
        logger.info(f"Saved Parquet file to s3://{SILVER_BUCKET}/{silver_key}")

    return {"statusCode": 200, "body": "Parquet transformation successful"}
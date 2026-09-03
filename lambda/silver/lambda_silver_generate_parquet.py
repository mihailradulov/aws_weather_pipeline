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
glue_client = boto3.client('glue')

SILVER_BUCKET = os.environ.get('SILVER_BUCKET_NAME')
GLUE_DATABASE = os.environ.get('GLUE_DATABASE_NAME')
GLUE_TABLE = os.environ.get('GLUE_TABLE_NAME')


def epoch_to_iso(epoch_sec, offset_sec=0):
    """Converts a Unix epoch timestamp to an ISO-formatted string (UTC or local with offset)."""
    if epoch_sec is None:
        return None
    try:
        dt = datetime.fromtimestamp(epoch_sec, tz=timezone.utc) + timedelta(seconds=offset_sec)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.warning(f"Failed to parse epoch timestamp {epoch_sec}: {str(e)}")
        return None


def ensure_glue_partition(partition_date):
    """Dynamically adds a new partition to the Glue Data Catalog if it does not exist."""
    if not GLUE_DATABASE or not GLUE_TABLE:
        logger.warning("GLUE_DATABASE_NAME or GLUE_TABLE_NAME not set. Skipping partition registration.")
        return

    partition_location = f"s3://{SILVER_BUCKET}/partition_date={partition_date}/"

    try:
        glue_client.create_partition(
            DatabaseName=GLUE_DATABASE,
            TableName=GLUE_TABLE,
            PartitionInput={
                'Values': [partition_date],
                'StorageDescriptor': {
                    'Location': partition_location,
                    'InputFormat': 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat',
                    'OutputFormat': 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat',
                    'SerdeInfo': {
                        'SerializationLibrary': 'org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe'
                    }
                }
            }
        )
        logger.info(f"Registered new partition in Glue Catalog: partition_date={partition_date}")

    except glue_client.exceptions.AlreadyExistsException:
        # Partition already registered; no action required
        pass
    except Exception as e:
        logger.error(f"Failed to register partition {partition_date} in Glue Catalog: {str(e)}", exc_info=True)


def process_s3_object(bronze_bucket, bronze_key):
    if not bronze_key.endswith('.json'):
        logger.info(f"Skipping non-JSON file: s3://{bronze_bucket}/{bronze_key}")
        return

    logger.info(f"Processing Bronze file: s3://{bronze_bucket}/{bronze_key}")

    try:
        # 1. Read raw JSON payload from Bronze S3 bucket
        obj = s3_client.get_object(Bucket=bronze_bucket, Key=bronze_key)
        bronze_content = json.loads(obj['Body'].read().decode('utf-8'))

        ingested_at_str = bronze_content.get('ingested_at_utc')
        raw = bronze_content.get('raw_response', {})

        tz_offset = raw.get('timezone', 0)
        dt_epoch = raw.get('dt')
        sys_info = raw.get('sys', {})
        sunrise_epoch = sys_info.get('sunrise')
        sunset_epoch = sys_info.get('sunset')

        ingested_dt = datetime.fromisoformat(ingested_at_str) if ingested_at_str else datetime.now(timezone.utc)

        # 2. Build flattened schema dictionary
        weather_arr = raw.get('weather', [{}])
        first_weather = weather_arr[0] if len(weather_arr) > 0 else {}

        main_info = raw.get('main', {})
        wind_info = raw.get('wind', {})
        clouds_info = raw.get('clouds', {})
        rain_info = raw.get('rain', {})
        snow_info = raw.get('snow', {})
        coord_info = raw.get('coord', {})

        row = {
            # Location Identification
            "city_id": raw.get('id'),
            "city_name": raw.get('name'),
            "country": sys_info.get('country'),
            "coord_lon": coord_info.get('lon'),
            "coord_lat": coord_info.get('lat'),

            # Weather Overview
            "weather_id": first_weather.get('id'),
            "weather_main": first_weather.get('main'),
            "weather_description": first_weather.get('description'),
            "weather_icon": first_weather.get('icon'),
            "base_station": raw.get('base'),

            # Main Measurements
            "temp": main_info.get('temp'),
            "feels_like": main_info.get('feels_like'),
            "temp_min": main_info.get('temp_min'),
            "temp_max": main_info.get('temp_max'),
            "pressure": main_info.get('pressure'),
            "sea_level_pressure": main_info.get('sea_level'),
            "ground_level_pressure": main_info.get('grnd_level'),
            "humidity": main_info.get('humidity'),
            "visibility": raw.get('visibility'),

            # Wind
            "wind_speed": wind_info.get('speed'),
            "wind_deg": wind_info.get('deg'),
            "wind_gust": wind_info.get('gust'),

            # Cloudiness and Precipitation
            "clouds_all": clouds_info.get('all'),
            "rain_1h": rain_info.get('1h'),
            "rain_3h": rain_info.get('3h'),
            "snow_1h": snow_info.get('1h'),
            "snow_3h": snow_info.get('3h'),

            # System Metrics
            "sys_type": sys_info.get('type'),
            "sys_id": sys_info.get('id'),
            "sys_message": sys_info.get('message'),
            "timezone_offset": tz_offset,

            # UTC and Local Timestamps
            "dt_utc": epoch_to_iso(dt_epoch, 0),
            "dt_local": epoch_to_iso(dt_epoch, tz_offset),
            "sunrise_utc": epoch_to_iso(sunrise_epoch, 0),
            "sunrise_local": epoch_to_iso(sunrise_epoch, tz_offset),
            "sunset_utc": epoch_to_iso(sunset_epoch, 0),
            "sunset_local": epoch_to_iso(sunset_epoch, tz_offset),
            "http_status_code": raw.get('cod'),

            # ETL Metadata
            "ingested_at_utc": ingested_dt.strftime('%Y-%m-%d %H:%M:%S'),
            "ingested_at_local": (ingested_dt + timedelta(seconds=tz_offset)).strftime('%Y-%m-%d %H:%M:%S'),
            "source_raw_file": f"s3://{bronze_bucket}/{bronze_key}"
        }

        partition_date = ingested_dt.strftime('%Y-%m-%d')

        # 3. Convert dictionary to Pandas DataFrame with explicit data types
        df = pd.DataFrame([row])

        # Explicitly cast Integer columns
        int_cols = [
            'city_id', 'weather_id', 'pressure', 'sea_level_pressure', 
            'ground_level_pressure', 'humidity', 'visibility', 
            'sys_type', 'sys_id', 'timezone_offset', 'http_status_code'
        ]
        for col in int_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')

        # Explicitly cast Double/Float columns
        double_cols = [
            'coord_lon', 'coord_lat', 'temp', 'feels_like', 'temp_min', 'temp_max',
            'wind_speed', 'wind_deg', 'wind_gust', 'clouds_all', 
            'rain_1h', 'rain_3h', 'snow_1h', 'snow_3h', 'sys_message'
        ]
        for col in double_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype('float64')

        parquet_buffer = pa.BufferOutputStream()
        table = pa.Table.from_pandas(df)
        pq.write_table(table, parquet_buffer, compression='SNAPPY')

        # 4. Upload generated Parquet file to Silver S3 destination
        filename = os.path.basename(bronze_key).replace('_raw.json', '.parquet')
        silver_key = f"partition_date={partition_date}/{filename}"

        s3_client.put_object(
            Bucket=SILVER_BUCKET,
            Key=silver_key,
            Body=parquet_buffer.getvalue().to_pybytes(),
            ContentType='application/octet-stream'
        )
        logger.info(f"Successfully converted and saved: s3://{SILVER_BUCKET}/{silver_key}")

        # 5. Automatically register partition in Glue Data Catalog
        ensure_glue_partition(partition_date)

    except Exception as e:
        logger.error(f"Error processing file s3://{bronze_bucket}/{bronze_key}: {str(e)}", exc_info=True)
        raise e


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    if not SILVER_BUCKET:
        logger.error("Environment variable SILVER_BUCKET_NAME is not set.")
        raise ValueError("Missing SILVER_BUCKET_NAME environment variable.")

    processed_count = 0

    if isinstance(event, dict) and 'detail' in event and 'bucket' in event['detail']:
        bronze_bucket = event['detail']['bucket']['name']
        bronze_key = event['detail']['object']['key']
        process_s3_object(bronze_bucket, bronze_key)
        processed_count += 1

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
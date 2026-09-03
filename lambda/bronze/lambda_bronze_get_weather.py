import os
import json
import logging
from datetime import datetime, timezone
import urllib3
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

http = urllib3.PoolManager()
s3_client = boto3.client('s3')

BRONZE_BUCKET = os.environ.get('BRONZE_BUCKET_NAME')
API_KEY = os.environ.get('OPENWEATHER_API_KEY')
CITIES = ["Sofia", "Plovdiv", "Varna", "Burgas", "Ruse", "Munich"]


def lambda_handler(event, context):
    if not BRONZE_BUCKET or not API_KEY:
        logger.error("Missing environment variables: BRONZE_BUCKET_NAME or OPENWEATHER_API_KEY")
        raise ValueError("Missing required environment variables")

    now_utc = datetime.now(timezone.utc)
    timestamp_epoch = int(now_utc.timestamp())
    year = now_utc.strftime("%Y")
    month = now_utc.strftime("%m")
    day = now_utc.strftime("%d")

    for city in CITIES:
        try:
            url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric"
            response = http.request('GET', url)

            if response.status != 200:
                logger.error(f"Failed to fetch data for {city}. Status: {response.status}")
                continue

            raw_data = json.loads(response.data.decode('utf-8'))
            
            # Add ingestion metadata to the raw payload
            bronze_payload = {
                "ingested_at_utc": now_utc.isoformat(),
                "city_requested": city,
                "raw_response": raw_data
            }

            s3_key = f"year={year}/month={month}/day={day}/{city}_{timestamp_epoch}_raw.json"

            s3_client.put_object(
                Bucket=BRONZE_BUCKET,
                Key=s3_key,
                Body=json.dumps(bronze_payload, ensure_ascii=False),
                ContentType='application/json'
            )
            logger.info(f"Successfully saved raw data for {city} to s3://{BRONZE_BUCKET}/{s3_key}")

        except Exception as e:
            logger.error(f"Error processing city {city}: {str(e)}")

    return {
        "statusCode": 200,
        "body": json.dumps("Bronze ingestion completed successfully")
    }